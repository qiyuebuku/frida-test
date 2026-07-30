"""Prompt loading and run input construction."""
from __future__ import annotations

from datetime import datetime
from importlib.resources import files

from src.application.agents.financial_research.schemas import ResearchTaskMode


def load_financial_research_instructions() -> str:
    prompt = files("src.application.agents.financial_research").joinpath(
        "prompts/financial_research.md"
    )
    return prompt.read_text(encoding="utf-8").strip()


def build_run_input(
    *,
    prompt: str,
    task_mode: ResearchTaskMode,
    run_id: str,
    now: datetime,
    allow_writes: bool,
) -> str:
    write_policy = (
        "允许在用户明确要求持续跟踪时调用跟踪名单写工具。"
        if allow_writes
        else "本次运行只读，禁止新增、启用、停用或修改跟踪名单。"
    )
    return "\n".join(
        (
            f"task_mode: {task_mode.value}",
            f"run_id: {run_id}",
            f"current_time: {now.isoformat()}",
            f"write_policy: {write_policy}",
            "",
            "用户任务：",
            prompt.strip(),
        )
    )
