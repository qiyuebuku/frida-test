"""持仓分析 handler：OCR → 结构化 → claude skill 深度分析

固定步骤：OCR 识别 + 结构化写入 DB
分析步骤：交给 claude 按 prompts/portfolio_analyze_flow.md 自主执行
"""

import json
import os
from datetime import datetime
from pathlib import Path

from services import task_db
from services.handlers import register

SKILL_DIR = os.getenv("SKILL_DIR", "/home/yuyangruan/claude-skills/.claude/skills/fund-trade")


def _read_flow_prompt() -> str:
    """读取 portfolio_analyze_flow.md 作为 claude 的执行指引"""
    flow_path = Path(SKILL_DIR) / "prompts" / "portfolio_analyze_flow.md"
    if flow_path.exists():
        return flow_path.read_text(encoding="utf-8")
    return ""


@register("", "fund_holdings")
@register("screenshot", "fund_holdings")
def handle_fund_holdings(executor, task: dict):
    """持仓分析：OCR → 结构化 → claude skill 深度分析"""
    task_id = task["id"]

    # Stage 2: OCR（固定步骤）
    raw_text, markdown, ocr_id = executor._do_ocr(
        task_id, task["image_path"], "fund_holdings", task.get("client_id")
    )

    # Stage 3: 结构化写入 DB（固定步骤）
    structured = executor._do_structurize(task_id, ocr_id, raw_text, markdown)

    # Stage 4: 深度分析（交给 claude skill）
    executor._progress(task_id, 40, "启动 Claude 分析...")

    data_desc = json.dumps(structured, ensure_ascii=False, indent=2) if structured else (markdown or raw_text)
    flow_prompt = _read_flow_prompt()

    prompt = f"""{flow_prompt}

---

## 以下是 handler 预处理的 OCR 持仓数据

{data_desc}

---

请按照上述流程执行：先采集市场数据和基金详情，再综合分析并输出报告。"""
    prompt = executor._apply_custom_prompt(prompt, task)

    report = executor._run_claude_streaming(task_id, prompt,
        timeout=600, progress_range=(40, 90), estimated_tools=20
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
