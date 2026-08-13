"""Deterministic construction of a bounded Research Agent context pack."""

from __future__ import annotations

from dataclasses import dataclass

from src.application.agents.financial_research.schemas import (
    ActiveViewSnapshot,
    MarketStateFrame,
    ResearchContextPack,
    ResearchMemoryItem,
    ResearchTriggerEnvelope,
)


@dataclass(frozen=True, slots=True)
class ResearchContextLimits:
    max_active_views: int = 20
    max_memory_items: int = 12
    max_serialized_chars: int = 60_000


class ResearchContextBuilder:
    """Apply cutoff, identity, expiry, and size gates before model execution.

    It does not query dashboard HTTP endpoints.  Market State Frame construction
    and role-memory retrieval remain separate application capabilities; this
    builder is the final deterministic boundary before the Agents SDK run.
    """

    def __init__(self, limits: ResearchContextLimits | None = None) -> None:
        self._limits = limits or ResearchContextLimits()

    def build(
        self,
        *,
        trigger: ResearchTriggerEnvelope,
        market_state: MarketStateFrame,
        current_report_revision_id: str | None,
        active_views: list[ActiveViewSnapshot],
        memory_items: list[ResearchMemoryItem],
        research_question: str | None = None,
    ) -> ResearchContextPack:
        if len(active_views) > self._limits.max_active_views:
            raise ValueError(
                "Active View Pack exceeds limit: "
                f"{len(active_views)} > {self._limits.max_active_views}"
            )
        self._require_unique_ids(active_views, "view_id", "Active View Pack")

        effective_memories = [
            item
            for item in memory_items
            if item.expires_at is None or item.expires_at > trigger.cutoff_at
        ]
        if len(effective_memories) > self._limits.max_memory_items:
            raise ValueError(
                "Research Memory Pack exceeds limit after expiry filtering: "
                f"{len(effective_memories)} > {self._limits.max_memory_items}"
            )
        self._require_unique_ids(
            effective_memories,
            "memory_id",
            "Research Memory Pack",
        )

        pack = ResearchContextPack(
            trigger=trigger,
            market_state=market_state,
            current_report_revision_id=current_report_revision_id,
            active_views=active_views,
            memory_items=effective_memories,
            research_question=research_question,
        )
        serialized_chars = len(pack.model_dump_json())
        if serialized_chars > self._limits.max_serialized_chars:
            raise ValueError(
                "Research Context Pack exceeds serialized size limit: "
                f"{serialized_chars} > {self._limits.max_serialized_chars}"
            )
        return pack

    @staticmethod
    def _require_unique_ids(items: list[object], field_name: str, label: str) -> None:
        values = [getattr(item, field_name) for item in items]
        if len(values) != len(set(values)):
            raise ValueError(f"{label} contains duplicate {field_name}")
