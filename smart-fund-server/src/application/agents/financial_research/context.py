"""Local-only state carried through one Agents SDK run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.application.agents.financial_research.schemas import (
    ResearchContextPack,
    ResearchTaskMode,
)


@dataclass(slots=True)
class ToolInvocation:
    name: str
    call_id: str
    arguments: Any = None
    result: Any = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


@dataclass(slots=True)
class AgentRunContext:
    run_id: str
    session_id: str
    task_mode: ResearchTaskMode
    research_context: ResearchContextPack | None = None
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    evidence_aliases: dict[str, str] = field(default_factory=dict)
    llm_calls: int = 0
