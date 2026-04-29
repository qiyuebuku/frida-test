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
            if step.tool == "entity_resolve":
                result, entity_warnings = _filter_entity_result_for_plan(result, plan)
                execution_warnings.extend(entity_warnings)
            observations.append(result)
            hits.extend(result.hits)

            if step.tool == "entity_resolve" and plan.direction in {"incoming", "outgoing", "path"}:
                graph_result, graph_warnings = await self._execute_graph_if_possible(
                    result.hits,
                    plan,
                )
                execution_warnings.extend(graph_warnings)
                if graph_result is not None:
                    observations.append(graph_result)
                    hits.extend(graph_result.hits)

        chunk_result = await self._execute_chunk_read_if_possible(hits)
        if chunk_result is not None:
            chunk_result = chunk_result.model_copy(
                update={
                    "hits": _inherit_evidence_scores(chunk_result.hits, hits),
                }
            )
            observations.append(chunk_result)
            hits.extend(chunk_result.hits)

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

    async def _execute_graph_if_possible(
        self,
        hits: list[RetrievalHit],
        plan: RetrievalPlan,
    ) -> tuple[RetrievalToolResult | None, list[str]]:
        seed_node_ids = _ordered_unique(node_id for hit in hits for node_id in hit.node_refs)
        if not seed_node_ids:
            return None, []
        planned_seed_node_ids = _seed_node_ids_for_plan(hits, plan)
        if not planned_seed_node_ids and _has_typed_entities(plan):
            return None, []
        return await self.registry.execute(
            RetrievalToolCall(
                tool="graph_search",
                seed_node_ids=planned_seed_node_ids or seed_node_ids,
                depth=self.registry.options.graph_depth,
                limit=self.registry.options.graph_limit,
                direction=plan.direction,
                relation_filters=plan.relation_filters,
                time_start=self.registry.options.graph_time_start,
                time_end=self.registry.options.graph_time_end,
            )
        ), []

    async def _execute_chunk_read_if_possible(
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
                tool="chunk_read",
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


def _seed_node_ids_for_plan(
    hits: list[RetrievalHit],
    plan: RetrievalPlan,
) -> list[str]:
    typed_entities = [
        (entity.text.lower(), entity.type_hint.lower())
        for entity in plan.entities
        if entity.text.strip() and entity.type_hint
    ]
    untyped_terms = [
        entity.text.lower()
        for entity in plan.entities
        if entity.text.strip() and not entity.type_hint
    ]
    if not typed_entities and not untyped_terms:
        return []
    typed_matched: list[str] = []
    untyped_matched: list[str] = []
    for hit in hits:
        haystack = f"{hit.title}\n{hit.snippet}".lower()
        if any(
            term in haystack and f'"type": "{type_hint}"' in haystack
            for term, type_hint in typed_entities
        ):
            typed_matched.extend(hit.node_refs)
        elif any(term in haystack for term in untyped_terms):
            untyped_matched.extend(hit.node_refs)
    return _ordered_unique(typed_matched or untyped_matched)


def _filter_entity_result_for_plan(
    result: RetrievalToolResult,
    plan: RetrievalPlan,
) -> tuple[RetrievalToolResult, list[str]]:
    if result.tool != "entity_resolve" or not plan.entities:
        return result, []
    typed_hits = _matched_hits_for_typed_entities(result.hits, plan)
    if typed_hits:
        return _result_with_hits(result, typed_hits), []
    if _has_typed_entities(plan):
        return _result_with_hits(result, []), [
            "planned typed entities unresolved: "
            + ", ".join(entity.text for entity in plan.entities if entity.type_hint)
        ]
    untyped_hits = _matched_hits_for_untyped_entities(result.hits, plan)
    return _result_with_hits(result, untyped_hits or result.hits), []


def _matched_hits_for_typed_entities(
    hits: list[RetrievalHit],
    plan: RetrievalPlan,
) -> list[RetrievalHit]:
    typed_entities = [
        (entity.text.lower(), entity.type_hint.lower())
        for entity in plan.entities
        if entity.text.strip() and entity.type_hint
    ]
    if not typed_entities:
        return []
    return [
        hit
        for hit in hits
        if any(
            term in _hit_text(hit) and f'"type": "{type_hint}"' in _hit_text(hit)
            for term, type_hint in typed_entities
        )
    ]


def _matched_hits_for_untyped_entities(
    hits: list[RetrievalHit],
    plan: RetrievalPlan,
) -> list[RetrievalHit]:
    terms = [
        entity.text.lower()
        for entity in plan.entities
        if entity.text.strip() and not entity.type_hint
    ]
    if not terms:
        return []
    return [hit for hit in hits if any(term in _hit_text(hit) for term in terms)]


def _result_with_hits(
    result: RetrievalToolResult,
    hits: list[RetrievalHit],
) -> RetrievalToolResult:
    return result.model_copy(
        update={
            "hits": hits,
            "step": result.step.model_copy(
                update={
                    "output_refs": [hit.hit_id for hit in hits],
                    "hit_count": len(hits),
                }
            ),
        }
    )


def _hit_text(hit: RetrievalHit) -> str:
    return f"{hit.title}\n{hit.snippet}".lower()


def _has_typed_entities(plan: RetrievalPlan) -> bool:
    return any(entity.text.strip() and entity.type_hint for entity in plan.entities)
