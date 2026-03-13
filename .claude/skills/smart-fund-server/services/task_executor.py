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
from services.event_bus import event_bus
import services.handlers as handlers

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

            skill_name = task.get("skill_name") or ""
            command_id = task.get("command_id") or task.get("task_type", "")

            # 路径 1：executor=claude → 通用 claude 执行
            if self._is_claude_command(skill_name, command_id):
                self._execute_claude(task, skill_name, command_id)
            else:
                # 路径 2：查 handler 注册表
                handler = handlers.get_handler(skill_name, command_id)
                if not handler:
                    raise ValueError(
                        f"未注册 handler: ({skill_name}, {command_id})。"
                        f"如果需要 Claude 执行，请在 SKILL.md 中标记 executor: claude"
                    )
                handler(self, task)

            elapsed = int(time.time() - start_time)
            # 检查是否已被标记为 failed（handler 内部可能已标记）
            current = task_db.get_task(task_id)
            if current and current["status"] == "processing":
                task_db.update_task(task_id,
                    status="completed", progress=100, progress_msg=None,
                    partial_result=None,
                    completed_at=datetime.now(), duration_sec=elapsed
                )
                event_bus.emit(task_id, {
                    "type": "done", "status": "completed",
                    "result": current.get("result"),
                })

        except Exception as e:
            logger.exception(f"Task {task_id} failed")
            error_msg = str(e)[:500]
            task_db.update_task(task_id, status="failed", error_msg=error_msg)
            event_bus.emit(task_id, {
                "type": "done", "status": "failed", "error_msg": error_msg,
            })
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
        self._progress(task_id, 22, "正在结构化数据...")

        prompt = f"""请将以下 OCR 识别的文本转换为结构化 JSON。

根据内容自动判断数据类型，提取关键字段。输出严格的 JSON（不要 markdown 代码块包裹）。

常见场景示例：

1. 基金/股票持仓截图：
{{
  "data_type": "fund_holdings",
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
}}

2. 其他类型数据：根据内容自行设计合理的 JSON 结构，顶层必须包含 "data_type" 字段标识数据类型（如 "table"、"receipt"、"invoice"、"chat_record" 等），其余字段根据实际内容提取。

OCR 文本：
{markdown or raw_text}"""

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
        ocr_db.update_ocr_structured_data(ocr_id, structured_str, structured_str)

        # 同步更新 sa_tasks.result_data（阶段性结果）
        task_db.update_task(task_id, result_data=structured)
        self._progress(task_id, 35, "数据结构化完成，开始深度分析...")

        return structured

    # ==================== Skill claude 执行 ====================

    def _is_claude_command(self, skill_name: str, command_id: str) -> bool:
        """检查 SKILL.md 中该命令是否标记为 executor: claude"""
        import services.skill_registry as sr
        if not sr.skill_registry or not skill_name:
            return False
        skill = sr.skill_registry.get_skill(skill_name)
        if not skill:
            return False
        command = skill.get_command(command_id)
        return command is not None and command.executor == "claude"

    def _execute_claude(self, task: dict, skill_name: str, command_id: str):
        """通用 claude 执行：在 skill 目录下 claude -p"""
        import services.skill_registry as sr
        skill = sr.skill_registry.get_skill(skill_name)
        command = skill.get_command(command_id)
        task_id = task["id"]
        config = task.get("config") or {}
        if isinstance(config, str):
            config = json.loads(config)

        prompt = f"执行命令: {command.name}\n{command.description}"
        if config.get("args"):
            prompt += f"\n参数: {' '.join(f'{k} {v}' for k, v in config['args'].items())}"
        if config.get("input_data"):
            prompt += f"\n输入: {config['input_data']}"
        prompt = self._apply_custom_prompt(prompt, task)

        report = self._run_claude_streaming(task_id, prompt,
            cwd=skill.path, timeout=600, progress_range=(5, 90), estimated_tools=30)

        if not report:
            task_db.update_task(task_id, status="failed", error_msg="执行超时或失败")
            return

        summary = self._extract_summary(report)
        task_db.update_task(task_id,
            status="completed", progress=100, summary=summary,
            result=report, completed_at=datetime.now())

    # ==================== 自定义配置 ====================

    def _get_custom_config(self, task: dict) -> tuple[str | None, str | None]:
        """从 task.config 中读取自定义 system_prompt 和 rules"""
        config = task.get("config")
        if not config:
            return None, None
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except json.JSONDecodeError:
                return None, None
        return config.get("system_prompt"), config.get("rules")

    def _apply_custom_prompt(self, default_prompt: str, task: dict) -> str:
        """将自定义提示词和规则拼接到默认 prompt"""
        system_prompt, rules = self._get_custom_config(task)
        if system_prompt:
            default_prompt = f"{system_prompt}\n\n{default_prompt}"
        if rules:
            default_prompt = f"{default_prompt}\n\n额外规则：\n{rules}"
        return default_prompt

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

    # 工具命令 → 可读描述的映射
    TOOL_DISPLAY_MAP = {
        "market_overview": "采集市场环境数据",
        "hot_board": "查看热门板块",
        "news_overview": "获取新闻快讯",
        "sector_rank": "查看板块排名",
        "headlines": "获取推荐头条",
        "flash_news": "获取快讯",
        "evaluate": "计算量化信号",
        "snapshot": "获取持仓快照",
        "scan-summary": "扫描基金概况",
    }

    def _text_step_display(self, text: str) -> str | None:
        """从 Claude 文本输出中提取第一行有意义的内容作为步骤标题"""
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            # 去掉 markdown 格式
            clean = line.lstrip("#").strip().strip("*").strip()
            if len(clean) > 3:
                return clean[:80]
        return None

    def _tool_display(self, tool_name: str, tool_input: dict) -> str:
        """将工具调用转为可读描述"""
        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            # python client.py <code> <action>
            if "client.py" in cmd:
                parts = cmd.split()
                idx = next((i for i, p in enumerate(parts) if p.endswith("client.py")), -1)
                if idx >= 0:
                    args = parts[idx + 1:]
                    # 命令映射
                    for key, desc in self.TOOL_DISPLAY_MAP.items():
                        if key in args:
                            return desc
                    # 基金代码 + 动作
                    if len(args) >= 2 and args[0].isdigit():
                        action_map = {"detail": "基金详情", "rank": "排名数据",
                                      "holdings": "持仓信息", "news": "相关资讯",
                                      "nav": "历史净值", "rsi": "RSI 指标"}
                        desc = action_map.get(args[1], args[1])
                        return f"查询 {args[0]} {desc}"
                    if args:
                        return f"执行 {' '.join(args[:3])}"
            if "news_overview" in cmd or "news" in cmd:
                return "获取新闻快讯"
            if "curl" in cmd:
                return "请求外部数据"
            return f"执行命令"
        elif tool_name == "Read":
            path = tool_input.get("file_path", "")
            fname = path.split("/")[-1] if "/" in path else path
            return f"读取 {fname}"
        elif tool_name in ("Glob", "Grep"):
            return "搜索文件"
        return tool_name

    def _tool_detail(self, tool_name: str, tool_input: dict) -> str:
        """将工具调用的输入转为可读详情文本"""
        if tool_name == "Bash":
            return tool_input.get("command", "")
        elif tool_name == "Read":
            return tool_input.get("file_path", "")
        elif tool_name == "Glob":
            return tool_input.get("pattern", "")
        elif tool_name == "Grep":
            return tool_input.get("pattern", "")
        return json.dumps(tool_input, ensure_ascii=False)[:500] if tool_input else ""

    def _run_claude_streaming(self, task_id: int, prompt: str,
                              timeout: int = 600,
                              progress_range: tuple = (35, 90),
                              estimated_tools: int = 20,
                              cwd: str = None) -> str | None:
        """启动 claude -p 并实时解析 stream-json 事件，推送到 EventBus"""
        env = os.environ.copy()
        env["FUND_API_BASE"] = "http://127.0.0.1:8900"

        # 确定工作目录：优先使用传入的 cwd，否则使用默认 SKILL_DIR
        work_dir = cwd or SKILL_DIR
        work_dir = work_dir if Path(work_dir).exists() else None

        start_pct, end_pct = progress_range
        try:
            process = subprocess.Popen(
                ["claude", "-p", prompt,
                 "--allowedTools", "Bash,Read,Glob,Grep",
                 "--output-format", "stream-json", "--verbose"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
                cwd=work_dir,
                env=env
            )

            tool_calls = []
            partial_text = []
            tool_count = 0
            last_db_write = time.time()
            start_time = time.time()
            # tool_use_id → tool_calls 列表索引，用于关联 tool_result
            tool_id_map = {}

            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue

                if time.time() - start_time > timeout:
                    process.kill()
                    logger.warning(f"claude -p streaming killed after {timeout}s")
                    event_bus.emit(task_id, {"type": "done", "status": "failed",
                                             "error_msg": f"处理超时（{timeout}s）"})
                    return None

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")

                if event_type == "assistant":
                    content_list = event.get("message", {}).get("content", [])
                    for block in content_list:
                        block_type = block.get("type")

                        if block_type == "text":
                            text = block.get("text", "")
                            if text:
                                partial_text.append(text)
                                total_len = sum(len(t) for t in partial_text)
                                pct = max(start_pct, min(end_pct, int(end_pct - 5)))
                                event_bus.emit(task_id, {
                                    "type": "text_delta",
                                    "text": text,
                                    "total_len": total_len,
                                    "progress": pct,
                                })
                                # 有意义的文本也作为步骤记录
                                stripped = text.strip()
                                if len(stripped) > 10:
                                    display = self._text_step_display(stripped)
                                    if display:
                                        text_entry = {
                                            "tool": "_text",
                                            "display": display,
                                            "detail": stripped,
                                            "output": None,
                                            "at": round(time.time() - start_time, 1),
                                        }
                                        tool_calls.append(text_entry)
                                        event_bus.emit(task_id, {
                                            "type": "tool_call",
                                            "tool": "_text",
                                            "display": display,
                                            "detail": stripped,
                                            "progress": pct,
                                            "is_text": True,
                                        })

                        elif block_type == "tool_use":
                            tool_name = block.get("name", "")
                            tool_input = block.get("input", {})
                            tool_use_id = block.get("id", "")
                            # 跳过 Claude 内部工具（对用户无意义）
                            if tool_name in ("ToolSearch", "Skill", "TodoWrite"):
                                if tool_use_id:
                                    tool_id_map[tool_use_id] = -1  # 标记为跳过
                                continue
                            tool_count += 1

                            # Bash 的 description 字段比自动推断更准确
                            if tool_name == "Bash" and tool_input.get("description"):
                                display = tool_input["description"]
                            else:
                                display = self._tool_display(tool_name, tool_input)
                            detail = self._tool_detail(tool_name, tool_input)

                            tool_entry = {
                                "tool": tool_name,
                                "display": display,
                                "detail": detail,
                                "output": None,
                                "at": round(time.time() - start_time, 1),
                            }
                            tool_calls.append(tool_entry)
                            if tool_use_id:
                                tool_id_map[tool_use_id] = len(tool_calls) - 1

                            pct = min(end_pct - 5,
                                      start_pct + int((end_pct - start_pct) * tool_count / estimated_tools))
                            self._progress(task_id, pct, display)
                            event_bus.emit(task_id, {
                                "type": "tool_call",
                                "tool": tool_name,
                                "display": display,
                                "detail": detail,
                                "progress": pct,
                            })

                elif event_type == "user":
                    # 捕获 tool_result，关联到对应的 tool_call
                    content_list = event.get("message", {}).get("content", [])
                    for block in content_list:
                        if block.get("type") == "tool_result":
                            tool_use_id = block.get("tool_use_id", "")
                            result_content = block.get("content", "")
                            is_error = block.get("is_error", False)
                            # content 可能是数组（如 ToolSearch 返回），需转为字符串
                            if isinstance(result_content, list):
                                parts = []
                                for item in result_content:
                                    if isinstance(item, dict):
                                        if item.get("type") == "text":
                                            parts.append(item.get("text", ""))
                                        else:
                                            parts.append(json.dumps(item, ensure_ascii=False))
                                    elif isinstance(item, str):
                                        parts.append(item)
                                result_content = "\n".join(parts) if parts else ""
                            elif not isinstance(result_content, str):
                                result_content = str(result_content) if result_content else ""
                            idx = tool_id_map.get(tool_use_id)
                            if idx is not None and idx >= 0 and idx < len(tool_calls):
                                tool_calls[idx]["output"] = result_content
                                if is_error:
                                    tool_calls[idx]["is_error"] = True
                                # 推送 tool_result 事件给客户端
                                event_bus.emit(task_id, {
                                    "type": "tool_result",
                                    "tool": tool_calls[idx]["tool"],
                                    "display": tool_calls[idx]["display"],
                                    "output": result_content,
                                    "is_error": is_error,
                                })

                elif event_type == "result":
                    final_result = event.get("result", "")
                    task_db.update_task(task_id, partial_result=None, tool_calls=tool_calls)
                    event_bus.emit(task_id, {
                        "type": "done",
                        "status": "completed",
                        "result": final_result,
                    })
                    return final_result

                # 定期写 DB
                now = time.time()
                if now - last_db_write >= 3:
                    accumulated = "".join(partial_text)
                    if accumulated or tool_calls:
                        task_db.update_task(task_id,
                            partial_result=accumulated if accumulated else None,
                            tool_calls=tool_calls if tool_calls else None,
                        )
                    last_db_write = now

            # 进程结束但没收到 result 事件
            process.wait()
            if process.returncode != 0:
                stderr = process.stderr.read()
                logger.warning(f"claude -p streaming failed (rc={process.returncode}): {stderr[:300]}")
                return None

            result = "".join(partial_text).strip()
            # 去掉与最终 result 重复的末尾 _text 步骤
            while (tool_calls and tool_calls[-1].get("tool") == "_text"
                   and tool_calls[-1].get("detail", "").strip() in result):
                tool_calls.pop()
            return result or None

        except FileNotFoundError:
            logger.warning("claude command not found")
            return None
        except Exception as e:
            logger.warning(f"claude -p streaming error: {e}")
            return None

    def _run_claude_with_progress(self, task_id: int, prompt: str,
                                  timeout: int = 600,
                                  progress_range: tuple = (35, 90),
                                  messages: list = None) -> str | None:
        """降级方案：时间估算进度（stream-json 不可用时使用）"""
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
            estimate_total = timeout * 0.5

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
