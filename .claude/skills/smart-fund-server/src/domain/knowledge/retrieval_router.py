"""Routing decisions for KG retrieval modes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from src.domain.knowledge.retrieval_anchor import QueryAnchor
from src.domain.knowledge.schemas import KnowledgeBaseModel

RetrievalMode = Literal["auto", "deterministic_plan", "agentic_arag", "openai_agents_arag"]
ResolvedRetrievalMode = Literal["deterministic_plan", "agentic_arag", "openai_agents_arag"]
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
    upgraded: bool = False
    upgrade_reason: str | None = None
    metrics: RetrievalQualityMetrics | None = None


def fast_route(query: str, anchor: QueryAnchor, requested_mode: str | None = "auto") -> QueryRoutingDecision:
    requested = _normalize_requested_mode(requested_mode)
    if requested in {"deterministic_plan", "agentic_arag", "openai_agents_arag"}:
        return QueryRoutingDecision(
            requested_mode=requested,
            initial_mode=requested,
            final_mode=requested,
            complexity="complex" if requested in {"agentic_arag", "openai_agents_arag"} else "simple",
            confidence=1.0,
            reasons=["explicit_mode"],
            max_tool_calls=10 if requested in {"agentic_arag", "openai_agents_arag"} else 6,
        )
    if _has_complex_intent(query):
        complexity: QueryComplexity = "complex"
        confidence = 0.86
        reasons = ["complex_intent"]
        max_tool_calls = 10
    elif _has_strong_constraints(anchor):
        complexity = "simple"
        confidence = 0.82
        reasons = ["strong_constraints_require_semantic_judge"]
        max_tool_calls = 6
    else:
        complexity = "unknown"
        confidence = 0.62
        reasons = ["auto_uses_agentic_semantic_judge"]
        max_tool_calls = 6
    return QueryRoutingDecision(
        requested_mode="auto",
        initial_mode="agentic_arag",
        final_mode="agentic_arag",
        complexity=complexity,
        confidence=confidence,
        reasons=reasons,
        max_tool_calls=max_tool_calls,
    )


def apply_post_check(
    decision: QueryRoutingDecision,
    metrics: RetrievalQualityMetrics,
    anchor: QueryAnchor,
) -> QueryRoutingDecision:
    if decision.requested_mode != "auto" or decision.final_mode == "agentic_arag":
        return decision.model_copy(update={"metrics": metrics})
    reason = upgrade_reason(metrics, anchor)
    if reason is None:
        return decision.model_copy(update={"metrics": metrics})
    return decision.model_copy(
        update={
            "final_mode": "agentic_arag",
            "upgraded": True,
            "upgrade_reason": reason,
            "metrics": metrics,
            "max_tool_calls": max(decision.max_tool_calls, 6),
        }
    )


def upgrade_reason(metrics: RetrievalQualityMetrics, anchor: QueryAnchor) -> str | None:
    if metrics.evidence_refs == 0:
        return "no_evidence_refs"
    if metrics.keep_candidates == 0:
        return "no_keep_candidates"
    if metrics.anchor_coverage < 0.5:
        return "low_anchor_coverage"
    if metrics.drop_ratio > 0.6:
        return "high_drop_ratio"
    if metrics.topic_conflict:
        return "topic_conflict"
    if metrics.forbidden_hit:
        return "forbidden_hit"
    if metrics.context_precision < 0.7:
        return "low_context_precision"
    if anchor.confidence < 0.65:
        return "low_anchor_confidence"
    return None


def _normalize_requested_mode(value: str | None) -> RetrievalMode:
    if value in {"deterministic_plan", "agentic_arag", "openai_agents_arag"}:
        return value
    return "auto"


def _has_strong_constraints(anchor: QueryAnchor) -> bool:
    return any(
        constraint.must_preserve
        and constraint.constraint_type
        in {"source_id", "evidence_id", "instrument_code", "exact_entity"}
        for constraint in anchor.guard_constraints
    )


def _has_complex_intent(query: str) -> bool:
    words = [
        "为什么",
        "如何影响",
        "传导",
        "归因",
        "对比",
        "复盘",
        "分别影响",
        "受益对象",
        "受损对象",
        "多跳",
        "候选冲突",
        "长文档",
        "完整梳理",
    ]
    return any(word in query for word in words)
