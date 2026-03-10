"""后台异步任务执行器 - 每个任务在独立线程中运行"""

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from services import task_db
from services import db as ocr_db
from services.ocr_service import OCRService

logger = logging.getLogger(__name__)

# 轻量任务类型（走同步 SSE，不经过 TaskExecutor）
SYNC_TASK_TYPES = {"ocr", "table", "search"}

# claude -p 工作目录（远端 skill 所在位置）
SKILL_DIR = os.getenv("SKILL_DIR", "/home/yuyangruan/claude-skills/fund-trade")

OCR_URL = os.getenv("OCR_URL", "http://119.23.227.187:8675/glmocr/parse")


class TaskExecutor:
    MAX_CONCURRENT = 2

    def __init__(self):
        self._semaphore = threading.Semaphore(self.MAX_CONCURRENT)
        self._ocr = OCRService(OCR_URL)

    def submit(self, task_id: int):
        thread = threading.Thread(target=self._run, args=(task_id,), daemon=True)
        thread.start()

    def _run(self, task_id: int):
        acquired = self._semaphore.acquire(timeout=300)
        if not acquired:
            task_db.update_task(task_id, status="failed", error_msg="服务器繁忙，请稍后重试")
            return

        try:
            task = task_db.get_task(task_id)
            if not task:
                return

            task_db.update_task(task_id, status="processing", started_at=datetime.now())
            start_time = time.time()

            task_type = task["task_type"]
            if task_type == "fund_holdings":
                self._handle_fund_holdings(task)
            elif task_type == "chat_reply":
                self._handle_chat_reply(task)
            elif task_type in ("fund_trade_run", "fund_review"):
                self._handle_skill_command(task)
            elif task_type == "full_page":
                self._handle_full_page(task)
            else:
                self._handle_simple_ocr(task)

            elapsed = int(time.time() - start_time)
            # 检查是否已被标记为 failed（handler 内部可能已标记）
            current = task_db.get_task(task_id)
            if current and current["status"] == "processing":
                task_db.update_task(task_id,
                    status="completed", progress=100, progress_msg=None,
                    completed_at=datetime.now(), duration_sec=elapsed
                )

        except Exception as e:
            logger.exception(f"Task {task_id} failed")
            task_db.update_task(task_id, status="failed", error_msg=str(e)[:500])
        finally:
            self._semaphore.release()

    def _progress(self, task_id: int, progress: int, msg: str):
        task_db.update_task(task_id, progress=progress, progress_msg=msg)

    # ==================== OCR 公共步骤 ====================

    def _do_ocr(self, task_id: int, image_path: str, action: str, client_id: str = None):
        """执行 OCR 并保存到 sa_ocr_records，返回 (raw_text, markdown, ocr_id)"""
        self._progress(task_id, 5, "正在 OCR 识别...")

        loop = asyncio.new_event_loop()
        try:
            ocr_result = loop.run_until_complete(self._ocr.recognize(image_path))
        finally:
            loop.close()

        raw_text = ocr_result.get("raw_ocr_result", "")
        markdown = ocr_result.get("markdown_result", "")

        ocr_id = ocr_db.save_ocr_record(
            action=action, raw_text=raw_text, markdown_text=markdown,
            image_path=image_path, client_id=client_id
        )
        task_db.update_task(task_id, ocr_record_id=ocr_id)
        self._progress(task_id, 20, "OCR 识别完成")

        return raw_text, markdown, ocr_id

    def _do_structurize(self, task_id: int, ocr_id: int, raw_text: str, markdown: str):
        """用 claude -p 结构化 OCR 文本，立即写入 sa_ocr_records，返回 structured dict"""
        self._progress(task_id, 22, "正在结构化持仓数据...")

        prompt = f"""请将以下 OCR 识别的基金持仓文本转换为 JSON 格式。

OCR 文本：
{markdown or raw_text}

输出严格的 JSON（不要 markdown 代码块包裹）：
{{
  "total_assets": 数字或null,
  "total_profit": 数字或null,
  "daily_pnl": 数字或null,
  "holdings": [
    {{
      "fund_name": "基金名称",
      "fund_code": "基金代码6位数字或null",
      "current_value": 数字或null,
      "daily_profit": 数字或null,
      "total_profit": 数字或null,
      "profit_rate": 小数或null
    }}
  ]
}}"""

        result = self._run_claude(prompt, timeout=60)
        if not result:
            self._progress(task_id, 35, "结构化跳过，继续分析...")
            return None

        # 解析 JSON
        structured = self._extract_json(result)
        if not structured:
            # 原文当作结构化结果保存
            ocr_db.update_ocr_structured_data(ocr_id, result, None)
            self._progress(task_id, 35, "结构化完成")
            return None

        # ★ 立即写入 sa_ocr_records
        structured_str = json.dumps(structured, ensure_ascii=False)
        flat_data = json.dumps(self._flatten_holdings(structured), ensure_ascii=False)
        ocr_db.update_ocr_structured_data(ocr_id, structured_str, flat_data)

        # 同步更新 sa_tasks.result_data（阶段性结果）
        task_db.update_task(task_id, result_data=structured)
        self._progress(task_id, 35, "数据结构化完成，开始深度分析...")

        return structured

    # ==================== 任务处理器 ====================

    def _handle_fund_holdings(self, task: dict):
        """持仓分析：OCR → 结构化 → claude -p 深度分析"""
        task_id = task["id"]

        # Stage 2: OCR
        raw_text, markdown, ocr_id = self._do_ocr(
            task_id, task["image_path"], "fund_holdings", task.get("client_id")
        )

        # Stage 3: 结构化（立即写入 DB）
        structured = self._do_structurize(task_id, ocr_id, raw_text, markdown)

        # Stage 4: 深度分析
        self._progress(task_id, 40, "正在采集市场数据...")

        data_desc = json.dumps(structured, ensure_ascii=False, indent=2) if structured else (markdown or raw_text)

        analysis_prompt = f"""请执行基金持仓分析。

已有 OCR 结构化数据：
{data_desc}

分析步骤：
1. 执行 `python client.py market_overview` 采集市场环境
2. 执行 `python client.py hot_board` 查看热门板块
3. 执行 `curl -s --noproxy '*' http://127.0.0.1:8900/api/news_overview` 获取新闻
4. 对持仓中的基金，执行 `python client.py <代码> detail` 和 `python client.py <代码> rank` 获取详情
5. 综合分析持仓配置、行业分布、风险点
6. 给出操作建议（加仓/减仓/持有）

输出完整的 Markdown 分析报告，包含：
- 持仓全貌（总资产、基金数量、收益）
- 配置分析（各类资产占比）
- 市场关联（热点与持仓的关联）
- 风险提示
- 操作建议（按优先级排序）

只输出最终的 Markdown 报告。"""

        # 启动 claude -p 并用时间估算更新进度
        report = self._run_claude_with_progress(task_id, analysis_prompt,
            timeout=600, progress_range=(40, 90),
            messages=[
                (40, "正在采集市场数据..."),
                (50, "正在分析基金详情..."),
                (60, "正在分析持仓配置..."),
                (75, "正在生成操作建议..."),
                (85, "正在整理分析报告..."),
            ]
        )

        if not report:
            task_db.update_task(task_id, status="failed", error_msg="Claude 分析超时或失败")
            return

        # Stage 5: 写入结果
        summary = self._extract_summary(report)
        task_db.update_task(task_id,
            status="completed", progress=100, progress_msg=None,
            summary=summary, result=report,
            completed_at=datetime.now()
        )

    def _handle_chat_reply(self, task: dict):
        """智能回复：OCR → claude -p 生成回复"""
        task_id = task["id"]

        raw_text, markdown, ocr_id = self._do_ocr(
            task_id, task["image_path"], "chat_reply", task.get("client_id")
        )

        self._progress(task_id, 40, "正在生成回复建议...")

        prompt = f"""分析以下聊天截图内容，给出 3 个回复建议。

聊天内容：
{markdown or raw_text}

输出格式：
# 智能回复建议

## 对话摘要
（一句话总结对方说了什么）

## 推荐回复
1. **正式回复**：...
2. **轻松回复**：...
3. **简短回复**：...

只输出 Markdown 格式的回复建议。"""

        result = self._run_claude(prompt, timeout=120)
        if not result:
            task_db.update_task(task_id, status="failed", error_msg="生成回复失败")
            return

        summary = self._extract_summary(result, max_len=100)
        task_db.update_task(task_id,
            status="completed", progress=100, progress_msg=None,
            summary=summary, result=result,
            completed_at=datetime.now()
        )

    def _handle_skill_command(self, task: dict):
        """执行 fund-trade skill 命令（run/review）"""
        task_id = task["id"]
        task_type = task["task_type"]

        cmd_map = {
            "fund_trade_run": "请执行 /fund-trade run 的完整流程，输出今日操作汇总报告。",
            "fund_review": "请执行 /fund-trade review 的完整流程，输出持仓审视报告。",
        }
        prompt = cmd_map.get(task_type, f"执行 {task_type}")

        report = self._run_claude_with_progress(task_id, prompt,
            timeout=600, progress_range=(5, 90),
            messages=[
                (5, "正在初始化..."),
                (20, "正在采集数据..."),
                (40, "正在分析..."),
                (60, "正在生成决策..."),
                (80, "正在整理报告..."),
            ]
        )

        if not report:
            task_db.update_task(task_id, status="failed", error_msg="Skill 执行超时或失败")
            return

        summary = self._extract_summary(report)
        task_db.update_task(task_id,
            status="completed", progress=100, progress_msg=None,
            summary=summary, result=report,
            completed_at=datetime.now()
        )

    def _handle_full_page(self, task: dict):
        """完整页面：仅 OCR"""
        task_id = task["id"]
        raw_text, markdown, ocr_id = self._do_ocr(
            task_id, task["image_path"], "full_page", task.get("client_id")
        )

        result_text = markdown or raw_text
        summary = result_text[:200] if result_text else "（空白页面）"

        task_db.update_task(task_id,
            status="completed", progress=100, progress_msg=None,
            summary=summary, result=result_text,
            completed_at=datetime.now()
        )

    def _handle_simple_ocr(self, task: dict):
        """简单 OCR（兜底）"""
        task_id = task["id"]
        raw_text, markdown, ocr_id = self._do_ocr(
            task_id, task["image_path"], task["task_type"], task.get("client_id")
        )

        result_text = markdown or raw_text
        summary = result_text[:200] if result_text else ""

        task_db.update_task(task_id,
            status="completed", progress=100, progress_msg=None,
            summary=summary, result=result_text,
            completed_at=datetime.now()
        )

    # ==================== Claude 工具方法 ====================

    def _run_claude(self, prompt: str, timeout: int = 120) -> str | None:
        """调用 claude -p，返回输出文本"""
        env = os.environ.copy()
        env["FUND_API_BASE"] = "http://127.0.0.1:8900"

        try:
            result = subprocess.run(
                ["claude", "-p", prompt,
                 "--allowedTools", "Bash,Read,Glob,Grep",
                 "--output-format", "text"],
                capture_output=True, text=True, timeout=timeout,
                cwd=SKILL_DIR if Path(SKILL_DIR).exists() else None,
                env=env
            )
            if result.returncode != 0:
                logger.warning(f"claude -p failed (rc={result.returncode}): {result.stderr[:300]}")
                return None
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.warning(f"claude -p timed out ({timeout}s)")
            return None
        except FileNotFoundError:
            logger.warning("claude command not found")
            return None
        except Exception as e:
            logger.warning(f"claude -p error: {e}")
            return None

    def _run_claude_with_progress(self, task_id: int, prompt: str,
                                  timeout: int = 600,
                                  progress_range: tuple = (35, 90),
                                  messages: list = None) -> str | None:
        """启动 claude -p 子进程，同时按时间估算更新进度"""
        env = os.environ.copy()
        env["FUND_API_BASE"] = "http://127.0.0.1:8900"

        start_pct, end_pct = progress_range
        if not messages:
            messages = [(start_pct, "处理中...")]

        try:
            process = subprocess.Popen(
                ["claude", "-p", prompt,
                 "--allowedTools", "Bash,Read,Glob,Grep",
                 "--output-format", "text"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
                cwd=SKILL_DIR if Path(SKILL_DIR).exists() else None,
                env=env
            )

            start_time = time.time()
            estimate_total = timeout * 0.5  # 预估用一半超时时间

            while process.poll() is None:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    process.kill()
                    logger.warning(f"claude -p killed after {timeout}s")
                    return None

                ratio = min(1.0, elapsed / estimate_total)
                pct = int(start_pct + (end_pct - start_pct) * ratio)
                msg = next((m for p, m in reversed(messages) if pct >= p), messages[0][1])
                self._progress(task_id, pct, msg)
                time.sleep(3)

            stdout = process.stdout.read()
            stderr = process.stderr.read()

            if process.returncode != 0:
                logger.warning(f"claude -p failed (rc={process.returncode}): {stderr[:300]}")
                return None

            return stdout.strip()

        except FileNotFoundError:
            logger.warning("claude command not found")
            return None
        except Exception as e:
            logger.warning(f"claude -p error: {e}")
            return None

    # ==================== 辅助方法 ====================

    def _extract_json(self, text: str) -> dict | None:
        """从文本中提取 JSON"""
        import re
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        json_str = m.group(1).strip() if m else text.strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    def _flatten_holdings(self, data: dict) -> dict:
        """扁平化持仓数据"""
        flat = {
            "total_assets": data.get("total_assets"),
            "total_profit": data.get("total_profit"),
            "daily_pnl": data.get("daily_pnl"),
        }
        holdings = data.get("holdings", [])
        flat["holdings"] = holdings
        flat["holdings_count"] = len(holdings)
        return flat

    def _extract_summary(self, markdown: str, max_len: int = 200) -> str:
        """从 Markdown 报告中提取摘要"""
        if not markdown:
            return ""
        lines = []
        for line in markdown.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 取有实质内容的行
            if line.startswith("-") or line.startswith("*") or line.startswith(">") or len(line) > 5:
                clean = line.lstrip("-*> ").strip()
                if clean:
                    lines.append(clean)
            if len("；".join(lines)) > max_len:
                break
        summary = "；".join(lines)
        return summary[:max_len] if summary else markdown[:max_len]


# 全局单例
executor = TaskExecutor()
