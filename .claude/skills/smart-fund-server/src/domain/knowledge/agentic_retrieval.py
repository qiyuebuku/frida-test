"""Agentic retrieval controller for whitelisted KG retrieval tools."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from src.domain.knowledge.retrieval import (
    RetrievalHit,
    RetrievalTrace,
    _inherit_evidence_scores,
    dedupe_hits,
)
from src.domain.knowledge.retrieval_tools import (
    RetrievalToolCall,
    RetrievalToolRegistry,
    RetrievalToolResult,
)
from src.domain.knowledge.schemas import KnowledgeBaseModel


class AgenticRetrievalConstraints(KnowledgeBaseModel):
    max_turns: int = Field(default=6, ge=1)
    max_tool_calls: int = Field(default=8, ge=1)
    max_hits: int = Field(default=30, ge=1)


class AgenticRetrievalDecision(KnowledgeBaseModel):
    tool_call: RetrievalToolCall | None = None
    stop: bool = False
    stop_reason: str | None = None


class AgenticRetrievalResult(KnowledgeBaseModel):
    query: str
    hits: list[RetrievalHit] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    trace: RetrievalTrace
    stop_reason: str


class AgenticRetrievalStrategy(Protocol):
    async def next_decision(
        self,
        *,
        query: str,
        observations: list[RetrievalToolResult],
        constraints: AgenticRetrievalConstraints,
    ) -> AgenticRetrievalDecision:
        """Return the next tool call or stop decision."""


class AgenticRetrievalController:
    """Runs an A-RAG style tool loop with fixed tool whitelist and trace output."""

    def __init__(
        self,
        registry: RetrievalToolRegistry,
        strategy: AgenticRetrievalStrategy,
        constraints: AgenticRetrievalConstraints | None = None,
    ):
        self.registry = registry
        self.strategy = strategy
        self.constraints = constraints or AgenticRetrievalConstraints()

    async def run(self, query: str) -> AgenticRetrievalResult:
        observations: list[RetrievalToolResult] = []
        hits: list[RetrievalHit] = []
        stop_reason = "max_turns"

        for _turn in range(self.constraints.max_turns):
            if len(observations) >= self.constraints.max_tool_calls:
                stop_reason = "max_tool_calls"
                break
            decision = await self.strategy.next_decision(
                query=query,
                observations=observations,
                constraints=self.constraints,
            )
            if decision.stop:
                forced_call = _forced_call_before_stop(
                    query=query,
                    observations=observations,
                    registry=self.registry,
                )
                if forced_call is not None:
                    result = await self.registry.execute(forced_call)
                    observations.append(result)
                    hits.extend(result.hits)
                    continue
                stop_reason = decision.stop_reason or "strategy_stop"
                break
            if decision.tool_call is None:
                stop_reason = "empty_decision"
                break
            result = await self.registry.execute(decision.tool_call)
            if decision.tool_call.tool == "chunk_read":
                result = result.model_copy(
                    update={"hits": _inherit_evidence_scores(result.hits, hits)}
                )
            observations.append(result)
            hits.extend(result.hits)
            if len(hits) >= self.constraints.max_hits:
                stop_reason = "max_hits"
                break

        selected_hits = dedupe_hits(hits)[: self.constraints.max_hits]
        return AgenticRetrievalResult(
            query=query,
            hits=selected_hits,
            evidence_refs=_ordered_unique(
                evidence_id for hit in selected_hits for evidence_id in hit.evidence_refs
            ),
            trace=RetrievalTrace(
                mode="agentic_arag",
                channels_enabled=list(self.registry.available_tools),
                channels_used=_ordered_unique(item.tool for item in observations),
                semantic_enabled=bool(
                    getattr(self.registry.runtime.semantic_retriever, "enabled", False)
                ),
                milvus_enabled=(
                    bool(getattr(self.registry.runtime.semantic_retriever, "enabled", False))
                    and getattr(self.registry.runtime.semantic_retriever, "backend_name", "none")
                    == "milvus"
                ),
                agentic_enabled=True,
                planner_enabled=False,
                steps=[item.step for item in observations],
                warnings=[],
            ),
            stop_reason=stop_reason,
        )


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _forced_call_before_stop(
    *,
    query: str,
    observations: list[RetrievalToolResult],
    registry: RetrievalToolRegistry,
) -> RetrievalToolCall | None:
    used_tools = {item.tool for item in observations}
    semantic_enabled = bool(getattr(registry.runtime.semantic_retriever, "enabled", False))
    if (
        semantic_enabled
        and "semantic_hybrid_search" in registry.available_tools
        and "semantic_hybrid_search" not in used_tools
    ):
        return RetrievalToolCall(
            tool="semantic_hybrid_search",
            query=query,
            limit=registry.options.semantic_hybrid_limit,
        )
    return None
