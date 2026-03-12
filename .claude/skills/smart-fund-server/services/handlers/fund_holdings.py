"""持仓分析 handler：OCR → 结构化 → claude -p 深度分析"""

import json
from datetime import datetime

from services import task_db
from services.handlers import register


@register("", "fund_holdings")
@register("screenshot", "fund_holdings")
def handle_fund_holdings(executor, task: dict):
    """持仓分析：OCR → 结构化 → claude -p 深度分析"""
    task_id = task["id"]

    # Stage 2: OCR
    raw_text, markdown, ocr_id = executor._do_ocr(
        task_id, task["image_path"], "fund_holdings", task.get("client_id")
    )

    # Stage 3: 结构化（立即写入 DB）
    structured = executor._do_structurize(task_id, ocr_id, raw_text, markdown)

    # Stage 4: 深度分析
    executor._progress(task_id, 40, "正在采集市场数据...")

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
    analysis_prompt = executor._apply_custom_prompt(analysis_prompt, task)

    # 启动 claude -p 流式输出
    report = executor._run_claude_streaming(task_id, analysis_prompt,
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
