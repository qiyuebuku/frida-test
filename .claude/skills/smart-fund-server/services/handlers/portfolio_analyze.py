"""通用持仓分析 handler：从数据库读取 OCR 持仓数据 → claude 采集市场数据并综合分析

固定步骤仅负责 OCR 数据读取和结构化，后续的市场数据采集与分析由 claude skill 完成。
"""

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


def _read_ocr_from_db() -> str | None:
    """从数据库读取最新的 fund_holdings OCR 记录"""
    import subprocess
    cwd = SKILL_DIR if Path(SKILL_DIR).exists() else None
    env = os.environ.copy()
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(k, None)
    try:
        r = subprocess.run(
            ["python", "client.py", "ocr-latest", "fund_holdings"],
            capture_output=True, text=True, timeout=30, cwd=cwd, env=env
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


@register("fund-trade", "portfolio-analyze")
def handle_portfolio_analyze(executor, task: dict):
    """通用持仓分析：读取 OCR 数据 → claude 采集市场数据并分析"""
    task_id = task["id"]

    # Stage 1: 从数据库读取截图 OCR 持仓数据（固定步骤）
    executor._progress(task_id, 5, "正在读取截图持仓数据...")

    ocr_data = _read_ocr_from_db()

    if not ocr_data or "无OCR记录" in ocr_data:
        task_db.update_task(task_id, status="failed",
                           error_msg="无截图持仓数据，请先在截屏助手中触发持仓截图")
        return

    executor._progress(task_id, 15, "OCR 数据就绪，启动 Claude 分析...")

    # Stage 2: 组装 prompt，把 OCR 数据 + flow 指引交给 claude
    flow_prompt = _read_flow_prompt()

    prompt = f"""{flow_prompt}

---

## 以下是 handler 预读的 OCR 持仓数据

{ocr_data}

---

请按照上述流程执行：先采集市场数据和基金详情，再综合分析并输出报告。"""

    # Stage 3: Claude 执行分析（采集市场数据 + 综合分析都由 claude 完成）
    report = executor._run_claude_streaming(task_id, prompt,
        timeout=600, progress_range=(15, 90), estimated_tools=20
    )

    if not report:
        task_db.update_task(task_id, status="failed", error_msg="Claude 分析超时或失败")
        return

    # Stage 4: 写入结果
    summary = executor._extract_summary(report)
    task_db.update_task(task_id,
        status="completed", progress=100, progress_msg=None,
        summary=summary, result=report,
        completed_at=datetime.now()
    )
