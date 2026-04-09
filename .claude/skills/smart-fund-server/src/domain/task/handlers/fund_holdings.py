"""持仓分析 handler：OCR → 结构化 → /fund-trade portfolio-analyze

固定步骤：OCR 识别 + 结构化写入 DB
分析步骤：调用 /fund-trade portfolio-analyze skill，claude 自主采集市场数据并分析
"""

import json
from datetime import datetime

from src.infrastructure.db import task_db
from src.domain.task.handlers import register


@register("", "fund_holdings")
@register("screenshot", "fund_holdings")
def handle_fund_holdings(executor, task: dict):
    """持仓分析：OCR → 结构化 → /fund-trade portfolio-analyze"""
    task_id = task["id"]

    # Stage 2: OCR（固定步骤）
    raw_text, markdown, ocr_id = executor._do_ocr(
        task_id, task["image_path"], "fund_holdings", task.get("client_id")
    )

    # Stage 3: 结构化写入 DB（固定步骤）
    structured = executor._do_structurize(task_id, ocr_id, raw_text, markdown)

    # Stage 4: 调用 /fund-trade portfolio-analyze skill 分析
    if not structured:
        task_db.update_task(task_id, status="failed", error_msg="结构化数据为空，无法分析")
        return

    data_desc = json.dumps(structured, ensure_ascii=False, indent=2)

    prompt = f"/fund-trade portfolio-analyze\n\n{data_desc}"
    prompt = executor._apply_custom_prompt(prompt, task)

    executor._emit_step(task_id, "启动深度分析", "调用 /fund-trade portfolio-analyze",
                        progress=40, output=prompt)

    report = executor._run_claude_streaming(task_id, prompt,
        timeout=600, progress_range=(40, 90), estimated_tools=20,
        emit_done=False)

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
