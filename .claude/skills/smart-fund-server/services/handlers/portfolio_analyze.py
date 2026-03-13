"""持仓分析 handler：采集持仓+市场数据 → claude -p 综合分析"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from services import task_db
from services.handlers import register

SKILL_DIR = os.getenv("SKILL_DIR", "/home/yuyangruan/claude-skills/.claude/skills/fund-trade")


def _run_client(*args, timeout=30) -> str | None:
    """调用 fund-trade client.py，返回 stdout"""
    cwd = SKILL_DIR if Path(SKILL_DIR).exists() else None
    env = os.environ.copy()
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(k, None)
    try:
        r = subprocess.run(
            ["python", "client.py", *args],
            capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


@register("fund-trade", "ocr-analyze")
def handle_portfolio_analyze(executor, task: dict):
    """持仓分析：采集持仓+市场数据 → claude 综合分析"""
    task_id = task["id"]
    args = task.get("args") or {}

    # Stage 1: 从 DB 读取截图持仓数据
    executor._progress(task_id, 5, "正在读取截图持仓数据...")

    ocr_data = _run_client("ocr-latest", "fund_holdings")

    if not ocr_data:
        task_db.update_task(task_id, status="failed",
                           error_msg="无截图持仓数据，请先在截屏助手中触发持仓截图")
        return

    # Stage 2: 采集公开市场数据（不使用个人持仓/复盘等私有数据）
    executor._progress(task_id, 20, "正在采集市场数据...")

    market_data = _run_client("market_overview")
    hotboard_data = _run_client("hot_board")
    news_data = _run_client("news_overview", timeout=60)

    executor._progress(task_id, 35, "数据采集完成，开始分析...")

    # Stage 3: 组装 prompt（只用截图数据 + 公开市场数据）
    sections = [f"## 截图持仓数据\n{ocr_data}"]
    if market_data:
        sections.append(f"## 市场环境\n{market_data}")
    if hotboard_data:
        sections.append(f"## 热门板块\n{hotboard_data}")
    if news_data:
        sections.append(f"## 新闻快讯\n{news_data}")

    data_bundle = "\n\n".join(sections)

    prompt = f"""请基于以下数据进行持仓综合分析。

{data_bundle}

分析要求：
1. **持仓全貌**：总资产、基金数量、收益情况
2. **配置分析**：按类型（QDII/A股/债券/商品）分组，计算占比
3. **风险提示**：集中度过高、单一市场风险、汇率风险
4. **市场关联**：当前市场热点与持仓的关联性
5. **操作建议**：哪些基金建议加仓/减仓/持有，附理由

输出完整的 Markdown 分析报告。"""

    # Stage 4: Claude 分析
    report = executor._run_claude_streaming(task_id, prompt,
        timeout=600, progress_range=(35, 90), estimated_tools=5
    )

    if not report:
        task_db.update_task(task_id, status="failed", error_msg="Claude 分析超时或失败")
        return

    # Stage 5: 写入结果
    summary = executor._extract_summary(report)
    task_db.update_task(task_id,
        status="completed", progress=100, progress_msg=None,
        summary=summary, result=report,
        completed_at=datetime.now()
    )
