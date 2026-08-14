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
    # Only aliases observed through market evidence tools are admissible as
    # citations. Aliases created while projecting a previous report remain
    # navigation text and must not masquerade as evidence opened in this run.
    opened_market_aliases: set[str] = field(default_factory=set)
    # Model-authored, run-local decision state. This is deliberately separate
    # from factual evidence and from cross-run Research Memory: it exists only
    # to preserve the current investigation across context compaction.
    working_memory: dict[str, Any] | None = None
    working_memory_revision: int = 0
    # Set only after Runtime has replaced the transcript with a reversible
    # Research Notebook.  It prevents the model from guessing notebook orders
    # before such an index actually exists.
    notebook_compacted: bool = False
    # LLM compaction is a semantic index only. The exact source remains in
    # tool_invocations and can be recovered through run_evidence_reopen.
    context_summary: dict[str, Any] | None = None
    context_summary_fingerprint: str | None = None
    context_summary_source_operation_count: int = 0
    # Replaceable model-visible projection over the SDK's complete history.
    # The checkpoint shadows raw items before this index; the recent tail stays
    # byte-for-byte intact for immediate continuation.
    surface_checkpoint: str | None = None
    # Exact call/result pairs selected by the compactor as immediately needed
    # for the next action. They remain model-visible alongside the checkpoint
    # so final evidence audit does not reopen data that was just compacted.
    surface_hot_items: list[Any] = field(default_factory=list)
    surface_shadowed_item_count: int = 0
    surface_generation: int = 0
    surface_last_before_tokens: int = 0
    surface_last_after_tokens: int = 0
    # Latest model-authored submission draft. Repair turns may send only the
    # fields they changed; omitted fields retain their preceding value.
    submission_draft: dict[str, Any] | None = None
    llm_calls: int = 0
