"""Routing decisions for KG retrieval modes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from src.domain.knowledge.retrieval_anchor import QueryAnchor
from src.domain.knowledge.schemas import KnowledgeBaseModel

RetrievalMode = Literal["auto", "deterministic_plan"]
ResolvedRetrievalMode = Literal["deterministic_plan"]
QueryComplexity = Literal["simple", "medium", "complex", "unknown"]


class RetrievalQualityMetrics(KnowledgeBaseModel):
    anchor_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    keep_candidates: int = 0
    drop_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: int = 0
    topic_conflict: bool = False
    forbidden_hit: bool = False
    context_precision: float = Field(default=1.0, ge=0.0, le=1.0)


class QueryRoutingDecision(KnowledgeBaseModel):
    requested_mode: RetrievalMode = "auto"
    initial_mode: ResolvedRetrievalMode = "deterministic_plan"
    final_mode: ResolvedRetrievalMode = "deterministic_plan"
    complexity: QueryComplexity = "unknown"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    max_tool_calls: int = Field(default=6, ge=1)
    metrics: RetrievalQualityMetrics | None = None


def fast_route(query: str, anchor: QueryAnchor, requested_mode: str | None = "auto") -> QueryRoutingDecision:
    requested = _normalize_requested_mode(requested_mode)
    if requested == "deterministic_plan":
        return QueryRoutingDecision(
            requested_mode=requested,
            initial_mode=requested,
            final_mode=requested,
            complexity="simple",
            confidence=1.0,
            reasons=["explicit_mode"],
            max_tool_calls=6,
        )
    if _has_strong_constraints(anchor):
        complexity = "simple"
        confidence = 0.82
        reasons = ["strong_constraints"]
    else:
        complexity = "unknown"
        confidence = 0.7
        reasons = ["auto_uses_deterministic_plan"]
    return QueryRoutingDecision(
        requested_mode="auto",
        initial_mode="deterministic_plan",
        final_mode="deterministic_plan",
        complexity=complexity,
        confidence=confidence,
        reasons=reasons,
        max_tool_calls=6,
    )


def apply_post_check(
    decision: QueryRoutingDecision,
    metrics: RetrievalQualityMetrics,
    anchor: QueryAnchor,
) -> QueryRoutingDecision:
    return decision.model_copy(update={"metrics": metrics})


def _normalize_requested_mode(value: str | None) -> RetrievalMode:
    if value == "deterministic_plan":
        return value
    return "auto"


def _has_strong_constraints(anchor: QueryAnchor) -> bool:
    return any(
        constraint.must_preserve
        and constraint.constraint_type
        in {"source_id", "evidence_id", "instrument_code", "exact_entity"}
        for constraint in anchor.guard_constraints
    )
