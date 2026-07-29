"""Evaluation helpers and durable quality records for knowledge retrieval."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import Field, model_validator

from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, KnowledgeBaseModel


def retrieval_query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()


class RetrievalTraceSnapshot(KnowledgeBaseModel):
    snapshot_id: str = Field(default_factory=lambda: f"kg_rt_snapshot:{uuid4()}")
    adapter_name: str
    target: str = "prod"
    query: str
    query_hash: str = ""
    strategy_name: str = "baseline_rule_priority"
    strategy_version: str = "v1"
    query_snapshot: dict[str, Any] = Field(default_factory=dict)
    recall_snapshot: dict[str, Any] = Field(default_factory=dict)
    package_snapshot: dict[str, Any] = Field(default_factory=dict)
    ranking_snapshot: dict[str, Any] = Field(default_factory=dict)
    judge_snapshot: dict[str, Any] = Field(default_factory=dict)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    stop_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None

    @model_validator(mode="after")
    def _fill_query_hash(self) -> "RetrievalTraceSnapshot":
        if not self.query_hash:
            self.query_hash = retrieval_query_hash(self.query)
        return self


class RetrievalLabel(KnowledgeBaseModel):
    label_id: str = Field(default_factory=lambda: f"kg_rt_label:{uuid4()}")
    snapshot_id: str | None = None
    case_id: str | None = None
    query: str
    expected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    expected_answers: list[dict[str, Any]] = Field(default_factory=list)
    expected_evidence_refs: list[str] = Field(default_factory=list)
    coverage_requirements: dict[str, Any] = Field(default_factory=dict)
    failure_stage: str | None = None
    notes: str = ""
    created_by: str = ""
    created_at: datetime | None = None


class RetrievalEvalRun(KnowledgeBaseModel):
    run_id: str = Field(default_factory=lambda: f"kg_rt_eval:{uuid4()}")
    strategy_name: str
    strategy_version: str
    status: str = "running"
    config: dict[str, Any] = Field(default_factory=dict)
    aggregate_metrics: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RetrievalEvalMetric(KnowledgeBaseModel):
    metric_id: str = Field(default_factory=lambda: f"kg_rt_metric:{uuid4()}")
    run_id: str
    case_id: str
    query: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    failure_stage: str | None = None
    failure_details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class RetrievalPreselectEvaluation(KnowledgeBaseModel):
    case_id: str
    query: str
    k: int
    expected_candidate_ids: list[str] = Field(default_factory=list)
    selected_candidate_ids: list[str] = Field(default_factory=list)
    hit_candidate_ids: list[str] = Field(default_factory=list)
    missed_candidate_ids: list[str] = Field(default_factory=list)
    wasted_candidate_ids: list[str] = Field(default_factory=list)
    preselect_recall_at_k: float = 0.0
    preselect_precision_at_k: float = 0.0
    wasted_slots_at_k: int = 0


def evaluate_preselect_quality(
    *,
    case_id: str,
    query: str,
    expected_candidate_ids: list[str],
    selected_candidate_ids: list[str],
    k: int | None = None,
) -> RetrievalPreselectEvaluation:
    selected_top_k = _ordered_unique(selected_candidate_ids[: max(1, k or len(selected_candidate_ids))])
    expected = _ordered_unique(expected_candidate_ids)
    expected_set = set(expected)
    selected_set = set(selected_top_k)
    hit_candidate_ids = [candidate_id for candidate_id in expected if candidate_id in selected_set]
    missed_candidate_ids = [candidate_id for candidate_id in expected if candidate_id not in selected_set]
    wasted_candidate_ids = [candidate_id for candidate_id in selected_top_k if candidate_id not in expected_set]
    denominator = len(expected) or 1
    selected_denominator = len(selected_top_k) or 1
    return RetrievalPreselectEvaluation(
        case_id=case_id,
        query=query,
        k=len(selected_top_k),
        expected_candidate_ids=expected,
        selected_candidate_ids=selected_top_k,
        hit_candidate_ids=hit_candidate_ids,
        missed_candidate_ids=missed_candidate_ids,
        wasted_candidate_ids=wasted_candidate_ids,
        preselect_recall_at_k=len(hit_candidate_ids) / denominator,
        preselect_precision_at_k=len(hit_candidate_ids) / selected_denominator,
        wasted_slots_at_k=len(wasted_candidate_ids),
    )


def preselect_evaluation_metric(
    *,
    run_id: str,
    evaluation: RetrievalPreselectEvaluation,
) -> RetrievalEvalMetric:
    failure_stage = "preselect" if evaluation.missed_candidate_ids else None
    return RetrievalEvalMetric(
        run_id=run_id,
        case_id=evaluation.case_id,
        query=evaluation.query,
        metrics={
            "preselect_recall_at_k": evaluation.preselect_recall_at_k,
            "preselect_precision_at_k": evaluation.preselect_precision_at_k,
            "wasted_slots_at_k": evaluation.wasted_slots_at_k,
            "expected_candidates": len(evaluation.expected_candidate_ids),
            "selected_candidates": len(evaluation.selected_candidate_ids),
            "hit_candidates": len(evaluation.hit_candidate_ids),
            "missed_candidates": len(evaluation.missed_candidate_ids),
        },
        failure_stage=failure_stage,
        failure_details={
            "missed_candidate_ids": evaluation.missed_candidate_ids,
            "wasted_candidate_ids": evaluation.wasted_candidate_ids,
        },
    )


def build_preselect_eval_metrics(
    *,
    run_id: str,
    snapshots: list[RetrievalTraceSnapshot],
    labels: list[RetrievalLabel],
    k_values: tuple[int, ...] = (8, 12, 15),
) -> list[RetrievalEvalMetric]:
    """Build durable preselect metrics from saved trace snapshots and labels."""

    snapshots_by_id = {
        snapshot.snapshot_id: snapshot
        for snapshot in snapshots
        if snapshot.snapshot_id
    }
    snapshots_by_query_hash: dict[str, RetrievalTraceSnapshot] = {}
    for snapshot in snapshots:
        snapshots_by_query_hash.setdefault(snapshot.query_hash or retrieval_query_hash(snapshot.query), snapshot)

    metrics: list[RetrievalEvalMetric] = []
    for label in labels:
        snapshot = (
            snapshots_by_id.get(label.snapshot_id or "")
            or snapshots_by_query_hash.get(retrieval_query_hash(label.query))
        )
        case_id = label.case_id or label.label_id
        if snapshot is None:
            metrics.append(
                RetrievalEvalMetric(
                    run_id=run_id,
                    case_id=case_id,
                    query=label.query,
                    metrics={"snapshot_found": 0},
                    failure_stage="snapshot",
                    failure_details={"reason": "retrieval_trace_snapshot_not_found"},
                )
            )
            continue
        expected_ids = _expected_candidate_ids(label)
        selected_ids = _selected_candidate_ids(snapshot)
        if not expected_ids:
            metrics.append(
                RetrievalEvalMetric(
                    run_id=run_id,
                    case_id=case_id,
                    query=label.query,
                    metrics={"snapshot_found": 1, "expected_candidates": 0},
                    failure_stage="label",
                    failure_details={"reason": "expected_candidates_empty"},
                )
            )
            continue
        for k in k_values:
            metric = preselect_evaluation_metric(
                run_id=run_id,
                evaluation=evaluate_preselect_quality(
                    case_id=case_id,
                    query=label.query,
                    expected_candidate_ids=expected_ids,
                    selected_candidate_ids=selected_ids,
                    k=k,
                ),
            )
            metric.case_id = f"{case_id}@{k}"
            metric.metrics["snapshot_found"] = 1
            metric.metrics["k"] = k
            metric.failure_details["base_case_id"] = case_id
            metric.failure_details["snapshot_id"] = snapshot.snapshot_id
            metrics.append(metric)
    return metrics


def aggregate_eval_metrics(metrics: list[RetrievalEvalMetric]) -> dict[str, Any]:
    if not metrics:
        return {"case_count": 0, "metric_count": 0}
    numeric: dict[str, list[float]] = {}
    for metric in metrics:
        for key, value in metric.metrics.items():
            if isinstance(value, (int, float)):
                numeric.setdefault(key, []).append(float(value))
    aggregates = {
        f"avg_{key}": sum(values) / len(values)
        for key, values in numeric.items()
        if values
    }
    return {
        "case_count": len({_base_metric_case_id(metric.case_id) for metric in metrics}),
        "metric_count": len(metrics),
        "failure_count": sum(1 for metric in metrics if metric.failure_stage),
        **aggregates,
    }


def _base_metric_case_id(case_id: str) -> str:
    return case_id.rsplit("@", 1)[0] if "@" in case_id else case_id


class RetrievalBadCase(KnowledgeBaseModel):
    case_id: str
    query: str
    expected_evidence_refs: list[str] = Field(default_factory=list)
    expected_hit_titles: list[str] = Field(default_factory=list)
    expected_top_hit_titles: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1)
    expected_node_names: list[str] = Field(default_factory=list)
    expected_relation_types: list[str] = Field(default_factory=list)
    expected_channels_used: list[str] = Field(default_factory=list)
    forbidden_node_names: list[str] = Field(default_factory=list)
    forbidden_evidence_refs: list[str] = Field(default_factory=list)
    forbidden_topics: list[str] = Field(default_factory=list)
    min_hits: int = Field(default=0, ge=0)
    min_evidence_refs: int = Field(default=0, ge=0)
    min_matched_nodes: int = Field(default=0, ge=0)
    min_matched_edges: int = Field(default=0, ge=0)
    max_forbidden_hits: int = Field(default=0, ge=0)


class RetrievalBadCaseReplayResult(KnowledgeBaseModel):
    case_id: str
    query: str
    passed: bool
    missing_evidence_refs: list[str] = Field(default_factory=list)
    missing_hit_titles: list[str] = Field(default_factory=list)
    missing_top_hit_titles: list[str] = Field(default_factory=list)
    missing_node_names: list[str] = Field(default_factory=list)
    missing_relation_types: list[str] = Field(default_factory=list)
    missing_channels_used: list[str] = Field(default_factory=list)
    forbidden_node_names_hit: list[str] = Field(default_factory=list)
    forbidden_evidence_refs_hit: list[str] = Field(default_factory=list)
    forbidden_topics_hit: list[str] = Field(default_factory=list)
    metric_failures: dict[str, dict[str, int]] = Field(default_factory=dict)
    actual_evidence_refs: list[str] = Field(default_factory=list)
    actual_hit_titles: list[str] = Field(default_factory=list)
    actual_node_names: list[str] = Field(default_factory=list)
    actual_relation_types: list[str] = Field(default_factory=list)
    actual_channels_used: list[str] = Field(default_factory=list)
    metrics: dict[str, int] = Field(default_factory=dict)


def evaluate_retrieval_bad_case(
    case: RetrievalBadCase,
    *,
    evidence_refs: list[str],
    hit_titles: list[str] | None = None,
    channels_used: list[str] | None = None,
    matched_nodes: list[CompiledNode],
    matched_edges: list[CompiledEdge],
) -> RetrievalBadCaseReplayResult:
    actual_node_names = _ordered_unique(node.canonical_name for node in matched_nodes)
    actual_relation_types = _ordered_unique(edge.relation_type for edge in matched_edges)
    actual_evidence_refs = _ordered_unique(evidence_refs)
    actual_hit_titles = _ordered_unique(hit_titles or [])
    actual_channels_used = _ordered_unique(channels_used or [])
    actual_text = "\n".join(
        [
            *actual_hit_titles,
            *actual_node_names,
            *actual_relation_types,
            *actual_evidence_refs,
        ]
    )
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
    missing_channels_used = [
        channel for channel in case.expected_channels_used if channel not in actual_channels_used
    ]
    forbidden_node_names_hit = [
        name for name in case.forbidden_node_names if name in actual_node_names
    ]
    forbidden_evidence_refs_hit = [
        ref for ref in case.forbidden_evidence_refs if ref in actual_evidence_refs
    ]
    forbidden_topics_hit = [
        topic for topic in case.forbidden_topics if topic and topic in actual_text
    ]
    metrics = {
        "hits": len(hit_titles or []),
        "evidence_refs": len(actual_evidence_refs),
        "matched_nodes": len(matched_nodes),
        "matched_edges": len(matched_edges),
        "channels_used": len(actual_channels_used),
        "forbidden_hits": len(forbidden_node_names_hit)
        + len(forbidden_evidence_refs_hit)
        + len(forbidden_topics_hit),
    }
    metric_failures = _metric_failures(
        case,
        metrics=metrics,
    )
    return RetrievalBadCaseReplayResult(
        case_id=case.case_id,
        query=case.query,
        passed=not (
            missing_evidence_refs
            or missing_hit_titles
            or missing_top_hit_titles
            or missing_node_names
            or missing_relation_types
            or missing_channels_used
            or forbidden_node_names_hit
            or forbidden_evidence_refs_hit
            or forbidden_topics_hit
            or metric_failures
        ),
        missing_evidence_refs=missing_evidence_refs,
        missing_hit_titles=missing_hit_titles,
        missing_top_hit_titles=missing_top_hit_titles,
        missing_node_names=missing_node_names,
        missing_relation_types=missing_relation_types,
        missing_channels_used=missing_channels_used,
        forbidden_node_names_hit=forbidden_node_names_hit,
        forbidden_evidence_refs_hit=forbidden_evidence_refs_hit,
        forbidden_topics_hit=forbidden_topics_hit,
        metric_failures=metric_failures,
        actual_evidence_refs=actual_evidence_refs,
        actual_hit_titles=actual_hit_titles,
        actual_node_names=actual_node_names,
        actual_relation_types=actual_relation_types,
        actual_channels_used=actual_channels_used,
        metrics=metrics,
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


def _expected_candidate_ids(label: RetrievalLabel) -> list[str]:
    values: list[str] = []
    for item in [*label.expected_candidates, *label.expected_answers]:
        if isinstance(item, dict):
            values.extend(
                str(item.get(key) or "")
                for key in ("id", "candidate_id", "source_fact_id", "node_id", "edge_id", "evidence_id")
            )
        else:
            values.append(str(item or ""))
    values.extend(label.expected_evidence_refs)
    return _ordered_unique(value for value in values if value)


def _selected_candidate_ids(snapshot: RetrievalTraceSnapshot) -> list[str]:
    roots = [
        snapshot.recall_snapshot,
        snapshot.ranking_snapshot,
        snapshot.package_snapshot,
        snapshot.judge_snapshot,
        snapshot.context_snapshot,
    ]
    values: list[str] = []
    for root in roots:
        values.extend(_candidate_ids_from_value(root))
    return _ordered_unique(values)


def _candidate_ids_from_value(value: Any) -> list[str]:
    if isinstance(value, dict):
        result: list[str] = []
        for key in ("candidate_id", "id", "hit_id", "source_fact_id"):
            item = value.get(key)
            if isinstance(item, str) and item:
                result.append(item)
        for key in (
            "selected_candidate_ids",
            "selected",
            "selected_hits",
            "judge_candidates",
            "kept_candidates",
            "accepted_candidates",
            "hits",
        ):
            if key in value:
                result.extend(_candidate_ids_from_value(value[key]))
        return result
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_candidate_ids_from_value(item))
        return result
    if isinstance(value, str) and value.startswith(("kg:", "kg_", "kg_ev:", "kg_edge:")):
        return [value]
    return []


def _metric_failures(
    case: RetrievalBadCase,
    *,
    metrics: dict[str, int],
) -> dict[str, dict[str, int]]:
    requirements = {
        "hits": case.min_hits,
        "evidence_refs": case.min_evidence_refs,
        "matched_nodes": case.min_matched_nodes,
        "matched_edges": case.min_matched_edges,
    }
    failures = {
        name: {"actual": metrics.get(name, 0), "expected_min": expected}
        for name, expected in requirements.items()
        if expected and metrics.get(name, 0) < expected
    }
    if metrics.get("forbidden_hits", 0) > case.max_forbidden_hits:
        failures["forbidden_hits"] = {
            "actual": metrics.get("forbidden_hits", 0),
            "expected_max": case.max_forbidden_hits,
        }
    return failures
