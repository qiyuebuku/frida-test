"""Replay recorded retrieval traces without invoking a planner or agent."""

from __future__ import annotations

from pydantic import Field

from src.domain.knowledge.retrieval import (
    RetrievalHit,
    RetrievalTrace,
    _inherit_evidence_scores,
    dedupe_hits,
)
from src.domain.knowledge.retrieval_tools import RetrievalToolCall, RetrievalToolRegistry
from src.domain.knowledge.schemas import KnowledgeBaseModel


class RetrievalTraceReplayMismatch(KnowledgeBaseModel):
    step_index: int
    tool: str
    expected_output_refs: list[str] = Field(default_factory=list)
    actual_output_refs: list[str] = Field(default_factory=list)


class RetrievalTraceReplayResult(KnowledgeBaseModel):
    query: str
    hits: list[RetrievalHit] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    trace: RetrievalTrace
    mismatches: list[RetrievalTraceReplayMismatch] = Field(default_factory=list)


async def replay_retrieval_trace(
    *,
    query: str,
    recorded_trace: RetrievalTrace,
    registry: RetrievalToolRegistry,
) -> RetrievalTraceReplayResult:
    hits: list[RetrievalHit] = []
    replay_steps = []
    mismatches: list[RetrievalTraceReplayMismatch] = []

    for index, recorded_step in enumerate(recorded_trace.steps):
        call = RetrievalToolCall.model_validate(recorded_step.input)
        result = await registry.execute(call)
        if call.tool == "open":
            result = result.model_copy(
                update={"hits": _inherit_evidence_scores(result.hits, hits)}
            )
        actual_refs = [hit.hit_id for hit in result.hits]
        if actual_refs != recorded_step.output_refs:
            mismatches.append(
                RetrievalTraceReplayMismatch(
                    step_index=index,
                    tool=recorded_step.tool,
                    expected_output_refs=recorded_step.output_refs,
                    actual_output_refs=actual_refs,
                )
            )
        replay_steps.append(result.step)
        hits.extend(result.hits)

    selected_hits = dedupe_hits(hits)[: registry.options.max_hits]
    trace = recorded_trace.model_copy(
        update={
            "mode": f"{recorded_trace.mode}_trace_replay",
            "channels_enabled": list(registry.available_tools),
            "channels_used": _ordered_unique(
                step.tool for step in replay_steps if step.hit_count > 0
            ),
            "semantic_enabled": bool(
                getattr(registry.runtime.semantic_retriever, "enabled", False)
            ),
            "milvus_enabled": (
                bool(getattr(registry.runtime.semantic_retriever, "enabled", False))
                and getattr(registry.runtime.semantic_retriever, "backend_name", "none")
                == "milvus"
            ),
            "steps": replay_steps,
            "warnings": [
                *recorded_trace.warnings,
                *(
                    [f"trace replay output mismatch: {len(mismatches)} step(s)"]
                    if mismatches
                    else []
                ),
            ],
        }
    )
    return RetrievalTraceReplayResult(
        query=query,
        hits=selected_hits,
        evidence_refs=_ordered_unique(
            evidence_id for hit in selected_hits for evidence_id in hit.evidence_refs
        ),
        trace=trace,
        mismatches=mismatches,
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
