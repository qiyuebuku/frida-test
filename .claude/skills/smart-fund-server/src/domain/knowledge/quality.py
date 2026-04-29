"""Quality checks for generic knowledge graphs."""

from __future__ import annotations

from collections import Counter
from hashlib import sha1
from typing import Any, Literal

from pydantic import Field

from src.domain.knowledge.enums import EdgeStatus, ValidationSeverity
from src.domain.knowledge.schemas import (
    CompiledEdge,
    CompiledEvidence,
    CompiledNode,
    KnowledgeBaseModel,
)
from src.domain.knowledge.wiki import WikiPage


ReviewAction = Literal[
    "approve",
    "reject",
    "downgrade",
    "merge",
    "split",
    "deprecate",
    "request_more_evidence",
]


class QualityIssue(KnowledgeBaseModel):
    issue_id: str
    severity: ValidationSeverity
    category: str
    object_type: str
    object_id: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    review_required: bool = False


class QualityReport(KnowledgeBaseModel):
    adapter_name: str
    issues: list[QualityIssue] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(issue.severity in {ValidationSeverity.ERROR, ValidationSeverity.CRITICAL} for issue in self.issues)


class ReviewEntry(KnowledgeBaseModel):
    review_id: str
    object_type: str
    object_id: str
    severity: ValidationSeverity
    reason: str
    status: str = "open"
    payload: dict[str, Any] = Field(default_factory=dict)


class BadCaseReplay(KnowledgeBaseModel):
    case_id: str
    query: str | None = None
    expected_refs: list[str] = Field(default_factory=list)
    actual_refs: list[str] = Field(default_factory=list)
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)


class KnowledgeQualityScanner:
    def scan(
        self,
        *,
        adapter_name: str,
        nodes: list[CompiledNode],
        edges: list[CompiledEdge],
        evidence: list[CompiledEvidence],
        wiki_pages: list[WikiPage],
        hub_degree_threshold: int = 50,
    ) -> QualityReport:
        node_ids = {node.node_id for node in nodes}
        evidence_ids = {item.evidence_id for item in evidence}
        edge_ids = {edge.edge_id for edge in edges}
        issues: list[QualityIssue] = []

        for edge in edges:
            if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
                issues.append(
                    _issue(
                        "edge_endpoint_missing",
                        "edge",
                        edge.edge_id,
                        "edge endpoint does not exist",
                        ValidationSeverity.ERROR,
                        {
                            "source_node_id": edge.source_node_id,
                            "target_node_id": edge.target_node_id,
                        },
                    )
                )
            if edge.status == EdgeStatus.ACTIVE and not edge.evidence_ids:
                issues.append(
                    _issue(
                        "active_edge_missing_evidence",
                        "edge",
                        edge.edge_id,
                        "active edge requires evidence",
                        ValidationSeverity.ERROR,
                        review_required=True,
                    )
                )
            missing_evidence = sorted(set(edge.evidence_ids) - evidence_ids)
            if missing_evidence:
                issues.append(
                    _issue(
                        "edge_evidence_missing",
                        "edge",
                        edge.edge_id,
                        "edge references missing evidence",
                        ValidationSeverity.ERROR,
                        {"missing": missing_evidence},
                        review_required=True,
                    )
                )

        degree = Counter()
        for edge in edges:
            degree[edge.source_node_id] += 1
            degree[edge.target_node_id] += 1
        for node in nodes:
            if degree[node.node_id] == 0:
                issues.append(
                    _issue(
                        "orphan_node",
                        "node",
                        node.node_id,
                        "node has no relationships",
                        ValidationSeverity.WARNING,
                    )
                )
            if degree[node.node_id] > hub_degree_threshold:
                issues.append(
                    _issue(
                        "hub_node",
                        "node",
                        node.node_id,
                        "node degree exceeds threshold",
                        ValidationSeverity.WARNING,
                        {"degree": degree[node.node_id], "threshold": hub_degree_threshold},
                        review_required=True,
                    )
                )

        duplicate_names = _duplicate_name_groups(nodes)
        for key, ids in duplicate_names.items():
            issues.append(
                _issue(
                    "duplicate_canonical_name",
                    "node",
                    key,
                    "multiple nodes share the same type and name",
                    ValidationSeverity.WARNING,
                    {"node_ids": ids},
                    review_required=True,
                )
            )

        latest_version = _latest_version(nodes, edges, evidence)
        for page in wiki_pages:
            if page.version != latest_version:
                issues.append(
                    _issue(
                        "stale_wiki_page",
                        "wiki_page",
                        page.page_id,
                        "wiki page version is stale",
                        ValidationSeverity.WARNING,
                        {"page_version": page.version, "latest_version": latest_version},
                    )
                )
            missing_edges = sorted(set(page.source_edge_ids) - edge_ids)
            missing_page_evidence = sorted(set(page.source_evidence_ids) - evidence_ids)
            if missing_edges or missing_page_evidence:
                issues.append(
                    _issue(
                        "wiki_reference_missing",
                        "wiki_page",
                        page.page_id,
                        "wiki page references missing source objects",
                        ValidationSeverity.ERROR,
                        {"missing_edges": missing_edges, "missing_evidence": missing_page_evidence},
                    )
                )

        metrics = _metrics(nodes, edges, evidence, issues, degree)
        return QualityReport(adapter_name=adapter_name, issues=issues, metrics=metrics)

    def review_entries_for(self, report: QualityReport) -> list[ReviewEntry]:
        return [
            ReviewEntry(
                review_id=f"kg_review:{issue.issue_id}",
                object_type=issue.object_type,
                object_id=issue.object_id,
                severity=issue.severity,
                reason=issue.message,
                payload={"category": issue.category, "details": issue.details},
            )
            for issue in report.issues
            if issue.review_required
        ]


def replay_bad_case(
    *,
    case_id: str,
    expected_refs: list[str],
    actual_refs: list[str],
    query: str | None = None,
) -> BadCaseReplay:
    missing = [ref for ref in expected_refs if ref not in actual_refs]
    return BadCaseReplay(
        case_id=case_id,
        query=query,
        expected_refs=expected_refs,
        actual_refs=actual_refs,
        passed=not missing,
        details={"missing": missing},
    )


def _issue(
    category: str,
    object_type: str,
    object_id: str,
    message: str,
    severity: ValidationSeverity,
    details: dict[str, Any] | None = None,
    *,
    review_required: bool = False,
) -> QualityIssue:
    digest = sha1(f"{category}:{object_type}:{object_id}:{message}".encode("utf-8")).hexdigest()[:16]
    return QualityIssue(
        issue_id=digest,
        severity=severity,
        category=category,
        object_type=object_type,
        object_id=object_id,
        message=message,
        details=details or {},
        review_required=review_required,
    )


def _duplicate_name_groups(nodes: list[CompiledNode]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for node in nodes:
        key = f"{node.node_type}:{node.canonical_name.lower()}"
        groups.setdefault(key, []).append(node.node_id)
    return {key: ids for key, ids in groups.items() if len(set(ids)) > 1}


def _latest_version(
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    evidence: list[CompiledEvidence],
) -> str:
    versions = sorted(
        {
            item.version
            for item in [*nodes, *edges, *evidence]
            if item.version
        }
    )
    return versions[-1] if versions else "v1"


def _metrics(
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    evidence: list[CompiledEvidence],
    issues: list[QualityIssue],
    degree: Counter,
) -> dict[str, Any]:
    active_edges = [edge for edge in edges if edge.status == EdgeStatus.ACTIVE]
    active_with_evidence = [edge for edge in active_edges if edge.evidence_ids]
    review_count = len([issue for issue in issues if issue.review_required])
    orphan_count = len([node for node in nodes if degree[node.node_id] == 0])
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "evidence_count": len(evidence),
        "active_edge_evidence_coverage": (
            len(active_with_evidence) / len(active_edges) if active_edges else 1.0
        ),
        "orphan_node_ratio": orphan_count / len(nodes) if nodes else 0.0,
        "issue_count": len(issues),
        "review_required_count": review_count,
    }
