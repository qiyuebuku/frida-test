"""Evaluation helpers for research-context retrieval bad cases."""

from __future__ import annotations

from pydantic import Field

from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, KnowledgeBaseModel


class RetrievalBadCase(KnowledgeBaseModel):
    case_id: str
    query: str
    expected_evidence_refs: list[str] = Field(default_factory=list)
    expected_hit_titles: list[str] = Field(default_factory=list)
    expected_top_hit_titles: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1)
    expected_node_names: list[str] = Field(default_factory=list)
    expected_relation_types: list[str] = Field(default_factory=list)


class RetrievalBadCaseReplayResult(KnowledgeBaseModel):
    case_id: str
    query: str
    passed: bool
    missing_evidence_refs: list[str] = Field(default_factory=list)
    missing_hit_titles: list[str] = Field(default_factory=list)
    missing_top_hit_titles: list[str] = Field(default_factory=list)
    missing_node_names: list[str] = Field(default_factory=list)
    missing_relation_types: list[str] = Field(default_factory=list)
    actual_evidence_refs: list[str] = Field(default_factory=list)
    actual_hit_titles: list[str] = Field(default_factory=list)
    actual_node_names: list[str] = Field(default_factory=list)
    actual_relation_types: list[str] = Field(default_factory=list)
    metrics: dict[str, int] = Field(default_factory=dict)


def evaluate_retrieval_bad_case(
    case: RetrievalBadCase,
    *,
    evidence_refs: list[str],
    hit_titles: list[str] | None = None,
    matched_nodes: list[CompiledNode],
    matched_edges: list[CompiledEdge],
) -> RetrievalBadCaseReplayResult:
    actual_node_names = _ordered_unique(node.canonical_name for node in matched_nodes)
    actual_relation_types = _ordered_unique(edge.relation_type for edge in matched_edges)
    actual_evidence_refs = _ordered_unique(evidence_refs)
    actual_hit_titles = _ordered_unique(hit_titles or [])
    missing_evidence_refs = [
        ref for ref in case.expected_evidence_refs if ref not in actual_evidence_refs
    ]
    missing_hit_titles = [
        title for title in case.expected_hit_titles if title not in actual_hit_titles
    ]
    top_titles = actual_hit_titles[: case.top_k]
    missing_top_hit_titles = [
        title for title in case.expected_top_hit_titles if title not in top_titles
    ]
    missing_node_names = [
        name for name in case.expected_node_names if name not in actual_node_names
    ]
    missing_relation_types = [
        relation
        for relation in case.expected_relation_types
        if relation not in actual_relation_types
    ]
    return RetrievalBadCaseReplayResult(
        case_id=case.case_id,
        query=case.query,
        passed=not (
            missing_evidence_refs
            or missing_hit_titles
            or missing_top_hit_titles
            or missing_node_names
            or missing_relation_types
        ),
        missing_evidence_refs=missing_evidence_refs,
        missing_hit_titles=missing_hit_titles,
        missing_top_hit_titles=missing_top_hit_titles,
        missing_node_names=missing_node_names,
        missing_relation_types=missing_relation_types,
        actual_evidence_refs=actual_evidence_refs,
        actual_hit_titles=actual_hit_titles,
        actual_node_names=actual_node_names,
        actual_relation_types=actual_relation_types,
        metrics={
            "evidence_refs": len(actual_evidence_refs),
            "matched_nodes": len(matched_nodes),
            "matched_edges": len(matched_edges),
        },
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
