"""Deterministic generated knowledge pages."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha1

from pydantic import Field

from src.domain.knowledge.enums import ValidationSeverity
from src.domain.knowledge.schemas import (
    CompiledEdge,
    CompiledEvidence,
    CompiledNode,
    KnowledgeBaseModel,
    ValidationIssue,
)


class WikiPage(KnowledgeBaseModel):
    page_id: str
    adapter_name: str
    page_type: str
    subject_type: str | None = None
    subject_id: str | None = None
    title: str
    summary: str
    content: str
    source_node_ids: list[str] = Field(default_factory=list)
    source_edge_ids: list[str] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    version: str


@dataclass(frozen=True)
class WikiBuildResult:
    pages: list[WikiPage] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


class KnowledgeWikiBuilder:
    def build(
        self,
        *,
        adapter_name: str,
        version: str,
        nodes: list[CompiledNode],
        edges: list[CompiledEdge],
        evidence: list[CompiledEvidence],
    ) -> WikiBuildResult:
        evidence_ids = {item.evidence_id for item in evidence}
        node_by_id = {node.node_id: node for node in nodes}
        evidence_by_id = {item.evidence_id: item for item in evidence}
        pages = [
            _entity_page(adapter_name, version, node, edges, node_by_id, evidence_by_id)
            for node in sorted(nodes, key=lambda item: item.node_id)
        ]
        pages.extend(
            _relation_page(adapter_name, version, relation_type, relation_edges, node_by_id, evidence_by_id)
            for relation_type, relation_edges in _edges_by_relation_type(edges).items()
        )
        timeline_page = _timeline_page(adapter_name, version, nodes, edges, evidence_by_id)
        if timeline_page is not None:
            pages.append(timeline_page)
        if nodes:
            pages.append(_index_page(adapter_name, version, nodes, edges, evidence))
        return WikiBuildResult(pages=pages, issues=lint_wiki_pages(pages, nodes, edges, evidence))


def lint_wiki_pages(
    pages: list[WikiPage],
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    evidence: list[CompiledEvidence],
) -> list[ValidationIssue]:
    node_ids = {item.node_id for item in nodes}
    edge_ids = {item.edge_id for item in edges}
    evidence_ids = {item.evidence_id for item in evidence}
    issues: list[ValidationIssue] = []

    for page in pages:
        if page.page_type != "index_page" and not page.subject_id:
            issues.append(_issue("page subject is required", page.page_id))
        missing_nodes = sorted(set(page.source_node_ids) - node_ids)
        missing_edges = sorted(set(page.source_edge_ids) - edge_ids)
        missing_evidence = sorted(set(page.source_evidence_ids) - evidence_ids)
        if missing_nodes:
            issues.append(_issue("page references missing nodes", page.page_id, missing_nodes))
        if missing_edges:
            issues.append(_issue("page references missing edges", page.page_id, missing_edges))
        if missing_evidence:
            issues.append(_issue("page references missing evidence", page.page_id, missing_evidence))
        if page.page_type == "entity_page" and page.source_edge_ids and not page.source_evidence_ids:
            issues.append(_issue("page with relationships requires evidence", page.page_id))

    return issues


def _entity_page(
    adapter_name: str,
    version: str,
    node: CompiledNode,
    edges: list[CompiledEdge],
    node_by_id: dict[str, CompiledNode],
    evidence_by_id: dict[str, CompiledEvidence],
) -> WikiPage:
    related = [
        edge
        for edge in edges
        if edge.source_node_id == node.node_id or edge.target_node_id == node.node_id
    ]
    related_evidence = sorted(
        {
            evidence_id
            for edge in related
            for evidence_id in edge.evidence_ids
            if evidence_id in evidence_by_id
        }
    )
    lines = [
        f"# {node.canonical_name}",
        "",
        f"Type: {node.node_type}",
        f"Status: {node.status.value}",
        f"Aliases: {', '.join(node.aliases) if node.aliases else 'none'}",
        f"External IDs: {_json_line(node.external_ids) if node.external_ids else 'none'}",
        f"Properties: {_json_line(node.properties) if node.properties else 'none'}",
        "",
        "Relationships:",
    ]
    if related:
        for edge in sorted(related, key=lambda item: item.edge_id):
            source_name = _node_name(edge.source_node_id, node_by_id)
            target_name = _node_name(edge.target_node_id, node_by_id)
            lines.append(
                f"- {edge.relation_type}: {source_name} -> {target_name} "
                f"(confidence={edge.confidence_score:.2f}, status={edge.status.value})"
            )
    else:
        lines.append("- none")
    if related_evidence:
        lines.extend(["", "Evidence:"])
        for evidence_id in related_evidence[:10]:
            evidence = evidence_by_id[evidence_id]
            lines.append(f"- {evidence.source_type}:{evidence.source_id} - {_evidence_preview(evidence)}")

    return WikiPage(
        page_id=_page_id(adapter_name, "entity_page", node.node_id),
        adapter_name=adapter_name,
        page_type="entity_page",
        subject_type=node.node_type,
        subject_id=node.node_id,
        title=node.canonical_name,
        summary=_entity_summary(node, related, node_by_id),
        content="\n".join(lines),
        source_node_ids=[node.node_id],
        source_edge_ids=sorted(edge.edge_id for edge in related),
        source_evidence_ids=related_evidence,
        version=version,
    )


def _relation_page(
    adapter_name: str,
    version: str,
    relation_type: str,
    edges: list[CompiledEdge],
    node_by_id: dict[str, CompiledNode],
    evidence_by_id: dict[str, CompiledEvidence],
) -> WikiPage:
    title = f"relation: {relation_type}"
    evidence_ids = sorted(
        {
            evidence_id
            for edge in edges
            for evidence_id in edge.evidence_ids
            if evidence_id in evidence_by_id
        }
    )
    node_ids = sorted(
        {
            node_id
            for edge in edges
            for node_id in [edge.source_node_id, edge.target_node_id]
        }
    )
    lines = [
        f"# {title}",
        "",
        f"Relationship count: {len(edges)}",
        "",
        "Edges:",
    ]
    for edge in sorted(edges, key=lambda item: item.edge_id)[:50]:
        lines.append(
            f"- {_node_name(edge.source_node_id, node_by_id)} -> "
            f"{_node_name(edge.target_node_id, node_by_id)} "
            f"(confidence={edge.confidence_score:.2f}, status={edge.status.value})"
        )
    if evidence_ids:
        lines.extend(["", "Evidence:"])
        for evidence_id in evidence_ids[:20]:
            evidence = evidence_by_id[evidence_id]
            lines.append(f"- {evidence.source_type}:{evidence.source_id} - {_evidence_preview(evidence)}")
    return WikiPage(
        page_id=_page_id(adapter_name, "relation_page", relation_type),
        adapter_name=adapter_name,
        page_type="relation_page",
        subject_type="relation_type",
        subject_id=relation_type,
        title=title,
        summary=f"{relation_type} has {len(edges)} relationships.",
        content="\n".join(lines),
        source_node_ids=node_ids,
        source_edge_ids=sorted(edge.edge_id for edge in edges),
        source_evidence_ids=evidence_ids,
        version=version,
    )


def _timeline_page(
    adapter_name: str,
    version: str,
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    evidence_by_id: dict[str, CompiledEvidence],
) -> WikiPage | None:
    event_node_ids = {
        node.node_id
        for node in nodes
        if node.node_type in {"event"}
    }
    if not event_node_ids:
        return None
    related_edges = [
        edge
        for edge in edges
        if edge.source_node_id in event_node_ids or edge.target_node_id in event_node_ids
    ]
    evidence_ids = sorted(
        {
            evidence_id
            for edge in related_edges
            for evidence_id in edge.evidence_ids
            if evidence_id in evidence_by_id
        }
    )
    timeline_items = sorted(
        ((
            _evidence_time(evidence_by_id[evidence_id]),
            evidence_by_id[evidence_id],
        ) for evidence_id in evidence_ids),
        key=lambda item: (item[0] or datetime.min.replace(tzinfo=timezone.utc), item[1].evidence_id),
        reverse=True,
    )
    title = f"{adapter_name} event timeline"
    lines = [
        f"# {title}",
        "",
        f"Events: {len(event_node_ids)}",
        f"Evidence: {len(evidence_ids)}",
        "",
        "Timeline:",
    ]
    for observed_at, evidence in timeline_items[:50]:
        timestamp = observed_at.isoformat() if observed_at is not None else "unknown_time"
        lines.append(f"- {timestamp} {evidence.source_type}:{evidence.source_id} - {_evidence_preview(evidence)}")
    if not timeline_items:
        lines.append("- none")
    return WikiPage(
        page_id=_page_id(adapter_name, "timeline_page", adapter_name),
        adapter_name=adapter_name,
        page_type="timeline_page",
        subject_type="timeline",
        subject_id=adapter_name,
        title=title,
        summary=f"{len(event_node_ids)} event nodes and {len(evidence_ids)} evidence records.",
        content="\n".join(lines),
        source_node_ids=sorted(event_node_ids),
        source_edge_ids=sorted(edge.edge_id for edge in related_edges),
        source_evidence_ids=evidence_ids,
        version=version,
    )


def _index_page(
    adapter_name: str,
    version: str,
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    evidence: list[CompiledEvidence],
) -> WikiPage:
    title = f"{adapter_name} knowledge index"
    lines = [
        f"# {title}",
        "",
        f"Nodes: {len(nodes)}",
        f"Relationships: {len(edges)}",
        f"Evidence: {len(evidence)}",
        "",
        "Node types:",
        *_count_lines(node.node_type for node in nodes),
        "",
        "Relationship types:",
        *_count_lines(edge.relation_type for edge in edges),
    ]
    return WikiPage(
        page_id=_page_id(adapter_name, "index_page", adapter_name),
        adapter_name=adapter_name,
        page_type="index_page",
        title=title,
        summary=f"{len(nodes)} nodes, {len(edges)} relationships, {len(evidence)} evidence records.",
        content="\n".join(lines),
        source_node_ids=sorted(node.node_id for node in nodes),
        source_edge_ids=sorted(edge.edge_id for edge in edges),
        source_evidence_ids=sorted(item.evidence_id for item in evidence),
        version=version,
    )


def _page_id(adapter_name: str, page_type: str, subject_id: str) -> str:
    digest = sha1(f"{adapter_name}:{page_type}:{subject_id}".encode("utf-8")).hexdigest()[:16]
    return f"kg_wiki:{adapter_name}:{page_type}:{digest}"


def _edges_by_relation_type(edges: list[CompiledEdge]) -> dict[str, list[CompiledEdge]]:
    result: dict[str, list[CompiledEdge]] = {}
    for edge in sorted(edges, key=lambda item: item.edge_id):
        result.setdefault(edge.relation_type, []).append(edge)
    return result


def _node_name(node_id: str, node_by_id: dict[str, CompiledNode]) -> str:
    node = node_by_id.get(node_id)
    return node.canonical_name if node is not None else node_id


def _entity_summary(
    node: CompiledNode,
    related: list[CompiledEdge],
    node_by_id: dict[str, CompiledNode],
) -> str:
    neighbors = [
        _node_name(edge.target_node_id if edge.source_node_id == node.node_id else edge.source_node_id, node_by_id)
        for edge in related[:5]
    ]
    neighbor_text = f" Related: {', '.join(neighbors)}." if neighbors else ""
    return f"{node.canonical_name} is a {node.node_type} node with {len(related)} relationships.{neighbor_text}"


def _evidence_preview(evidence: CompiledEvidence) -> str:
    if evidence.content and evidence.content.strip():
        return _clip(evidence.content, 160)
    if evidence.payload:
        title = evidence.payload.get("title") if isinstance(evidence.payload, dict) else None
        if title:
            return _clip(str(title), 160)
        return _clip(json.dumps(evidence.payload, ensure_ascii=False, sort_keys=True), 160)
    return evidence.evidence_id


def _evidence_time(evidence: CompiledEvidence) -> datetime | None:
    if not isinstance(evidence.payload, dict):
        return None
    value = (
        evidence.payload.get("published_at")
        or evidence.payload.get("observed_at")
        or evidence.payload.get("event_time")
        or evidence.payload.get("period")
    )
    if isinstance(value, datetime):
        return _aware_datetime(value)
    if not value:
        return None
    try:
        return _aware_datetime(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _json_line(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _count_lines(values) -> list[str]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return [f"- {key}: {counts[key]}" for key in sorted(counts)]


def _clip(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _issue(message: str, page_id: str, missing: list[str] | None = None) -> ValidationIssue:
    return ValidationIssue(
        severity=ValidationSeverity.ERROR,
        message=message,
        object_type="wiki_page",
        object_ref=page_id,
        details={"missing": missing or []},
    )
