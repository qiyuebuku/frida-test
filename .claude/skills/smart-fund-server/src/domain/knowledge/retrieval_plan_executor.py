"""Deterministic retrieval plan execution through the retrieval tool registry."""

from __future__ import annotations

from pydantic import Field

from src.domain.knowledge.retrieval import (
    RetrievalHit,
    RetrievalTrace,
    _inherit_evidence_scores,
    dedupe_hits,
)
from src.domain.knowledge.retrieval_plan import RetrievalPlan
from src.domain.knowledge.retrieval_tools import (
    RetrievalToolCall,
    RetrievalToolRegistry,
    RetrievalToolResult,
)
from src.domain.knowledge.schemas import KnowledgeBaseModel


class RetrievalPlanExecutionResult(KnowledgeBaseModel):
    query: str
    plan: RetrievalPlan
    hits: list[RetrievalHit] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    trace: RetrievalTrace
    tool_results: list[RetrievalToolResult] = Field(default_factory=list)


class RetrievalPlanExecutor:
    """Executes a structured plan with deterministic tool expansion.

    Planner steps intentionally omit deferred inputs such as graph seed nodes and
    evidence ids. The executor fills those inputs from previous observations.
    """

    def __init__(self, registry: RetrievalToolRegistry):
        self.registry = registry

    async def execute(self, *, query: str, plan: RetrievalPlan) -> RetrievalPlanExecutionResult:
        observations: list[RetrievalToolResult] = []
        hits: list[RetrievalHit] = []
        execution_warnings: list[str] = []

        for step in plan.steps:
            result = await self.registry.execute(step)
            observations.append(result)
            hits.extend(result.hits)

        open_result = await self._execute_open_if_possible(hits)
        if open_result is not None:
            open_result = open_result.model_copy(
                update={
                    "hits": _inherit_evidence_scores(open_result.hits, hits),
                }
            )
            observations.append(open_result)
            hits.extend(open_result.hits)

        selected_hits = dedupe_hits(hits)[: self.registry.options.max_hits]
        evidence_refs = _ordered_unique(
            evidence_id for hit in selected_hits for evidence_id in hit.evidence_refs
        )
        semantic_enabled = bool(getattr(self.registry.runtime.semantic_retriever, "enabled", False))
        milvus_enabled = (
            semantic_enabled
            and getattr(self.registry.runtime.semantic_retriever, "backend_name", "none")
            == "milvus"
        )
        return RetrievalPlanExecutionResult(
            query=query,
            plan=plan,
            hits=selected_hits,
            evidence_refs=evidence_refs,
            trace=RetrievalTrace(
                mode="deterministic_plan",
                channels_enabled=list(self.registry.available_tools),
                channels_used=_ordered_unique(result.tool for result in observations),
                semantic_enabled=semantic_enabled,
                milvus_enabled=milvus_enabled,
                agentic_enabled=False,
                planner_enabled=True,
                steps=[result.step for result in observations],
                warnings=_warnings_for(
                    plan,
                    graph_time_enabled=(
                        self.registry.options.graph_time_start is not None
                        or self.registry.options.graph_time_end is not None
                    ),
                )
                + execution_warnings,
            ),
            tool_results=observations,
        )

    async def _execute_open_if_possible(
        self,
        hits: list[RetrievalHit],
    ) -> RetrievalToolResult | None:
        evidence_ids = _ordered_unique(
            evidence_id for hit in hits for evidence_id in hit.evidence_refs
        )
        if not evidence_ids:
            return None
        return await self.registry.execute(
            RetrievalToolCall(
                tool="open",
                evidence_ids=evidence_ids,
                limit=self.registry.options.evidence_limit,
            )
        )


def _warnings_for(
    plan: RetrievalPlan,
    *,
    graph_time_enabled: bool,
) -> list[str]:
    warnings: list[str] = []
    if (
        plan.time_range.preset or plan.time_range.start or plan.time_range.end
    ) and not graph_time_enabled:
        warnings.append(
            "planner time_range has no explicit graph time window"
        )
    return warnings


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
