"""Semantic vector index materials derived from KG facts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceStatus, NodeStatus
from src.domain.knowledge.graph_index import (
    DEFAULT_GRAPH_PROJECTIONS,
    GraphIndexVectorDocument,
    GraphProjectionProfile,
    build_graph_index,
)
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, EvidenceChunk


@dataclass(frozen=True)
class SemanticVectorDocument:
    document_id: str
    document_type: str
    collection_role: str
    source_type: str
    source_id: str
    text: str
    evidence_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


SEMANTIC_COLLECTION_CHUNK = "chunk"
SEMANTIC_COLLECTION_COGNITIVE_CARD = "cognitive_card"
SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS = "cognitive_card_focus"
SEMANTIC_COLLECTION_ENTITY = "entity"
SEMANTIC_COLLECTION_RELATION = "relation"
SEMANTIC_COLLECTION_CARD_RELATION = "card_relation"
SEMANTIC_COLLECTION_COMMUNITY = "community"
SEMANTIC_COLLECTION_COMMUNITY_INSIGHT = "community_insight"
SEMANTIC_COLLECTION_GRAPH_COMMUNITY_REPORT = "graph_community_report"
SEMANTIC_COLLECTION_GRAPH_COMMUNITY_PROJECTION = "graph_community_projection"
SEMANTIC_COLLECTION_ASSIGNMENT_BUCKET = "assignment_bucket"
SEMANTIC_COLLECTION_ROLES = (
    SEMANTIC_COLLECTION_CHUNK,
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS,
    SEMANTIC_COLLECTION_ENTITY,
    SEMANTIC_COLLECTION_RELATION,
    SEMANTIC_COLLECTION_CARD_RELATION,
    SEMANTIC_COLLECTION_COMMUNITY,
    SEMANTIC_COLLECTION_COMMUNITY_INSIGHT,
    SEMANTIC_COLLECTION_GRAPH_COMMUNITY_REPORT,
    SEMANTIC_COLLECTION_GRAPH_COMMUNITY_PROJECTION,
    SEMANTIC_COLLECTION_ASSIGNMENT_BUCKET,
)


def build_semantic_vector_documents(
    *,
    chunks: list[EvidenceChunk],
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    max_relation_preview: int = 6,
    include_community: bool = True,
    graph_projections: tuple[GraphProjectionProfile, ...] | None = None,
) -> list[SemanticVectorDocument]:
    """Build enriched vector documents from facts, without legacy retrieval docs."""

    retrievable_nodes = [node for node in nodes if _is_retrievable_node(node)]
    node_by_id = {node.node_id: node for node in retrievable_nodes}
    retrievable_edges = [
        edge
        for edge in edges
        if _is_retrievable_edge(edge)
        and edge.source_node_id in node_by_id
        and edge.target_node_id in node_by_id
    ]
    retrievable_chunks = [chunk for chunk in chunks if _is_retrievable_chunk(chunk)]
    edges_by_node = _edges_by_node(retrievable_edges)
    edges_by_evidence = _edges_by_evidence(retrievable_edges)

    documents: list[SemanticVectorDocument] = []
    documents.extend(
        _evidence_chunk_document(
            chunk,
            edges=edges_by_evidence.get(chunk.evidence_id, []),
            node_by_id=node_by_id,
            max_relation_preview=max_relation_preview,
        )
        for chunk in retrievable_chunks
    )
    documents.extend(
        _node_or_event_card(
            node,
            edges=edges_by_node.get(node.node_id, []),
            node_by_id=node_by_id,
            max_relation_preview=max_relation_preview,
        )
        for node in retrievable_nodes
    )
    documents.extend(_edge_card(edge, node_by_id=node_by_id) for edge in retrievable_edges)
    if include_community:
        graph_index = build_graph_index(
            chunks=retrievable_chunks,
            nodes=retrievable_nodes,
            edges=retrievable_edges,
            projections=graph_projections if graph_projections is not None else DEFAULT_GRAPH_PROJECTIONS,
        )
        documents.extend(_semantic_document_from_graph_index(document) for document in graph_index.documents)
    return documents


def _semantic_document_from_graph_index(document: GraphIndexVectorDocument) -> SemanticVectorDocument:
    return SemanticVectorDocument(
        document_id=document.document_id,
        document_type=document.document_type,
        collection_role=document.collection_role,
        source_type=document.source_type,
        source_id=document.source_id,
        evidence_id=document.evidence_id,
        text=document.text,
        metadata=document.metadata,
    )


def _evidence_chunk_document(
    chunk: EvidenceChunk,
    *,
    edges: list[CompiledEdge],
    node_by_id: dict[str, CompiledNode],
    max_relation_preview: int,
) -> SemanticVectorDocument:
    payload = dict(chunk.payload or {})
    relation_preview = _relation_preview(edges, node_by_id=node_by_id, max_items=max_relation_preview)
    text = _join_parts(
        [
            "Document Type: Evidence Chunk",
            f"Title: {payload.get('title') or ''}",
            f"Summary: {payload.get('summary') or ''}",
            f"Evidence: {chunk.evidence_id}",
            f"Source: {payload.get('source_type') or ''}:{payload.get('source_id') or ''}",
            f"Source Name: {payload.get('source_name') or ''}",
            _search_terms_text(payload),
            _preview_text(relation_preview),
            f"Evidence Text: {chunk.content}",
        ]
    )
    return SemanticVectorDocument(
        document_id=chunk.chunk_id,
        document_type="evidence_chunk",
        collection_role=SEMANTIC_COLLECTION_CHUNK,
        source_type="kg_evidence_chunk",
        source_id=chunk.evidence_id,
        evidence_id=chunk.evidence_id,
        text=text,
        metadata={
            **payload,
            "relation_preview": relation_preview,
        },
    )


def _node_or_event_card(
    node: CompiledNode,
    *,
    edges: list[CompiledEdge],
    node_by_id: dict[str, CompiledNode],
    max_relation_preview: int,
) -> SemanticVectorDocument:
    relation_preview = _relation_preview(edges, node_by_id=node_by_id, max_items=max_relation_preview)
    evidence_id = _first_evidence_id(edges)
    aliases = _ordered_unique([node.canonical_name, *node.aliases, *node.external_ids.values()])
    is_event = node.node_type == "event"
    document_type = "event_card" if is_event else "node_card"
    source_type = "kg_event_card" if is_event else "kg_node_card"
    label = "Event Card" if is_event else "Node Card"
    text = _join_parts(
        [
            f"Document Type: {label}",
            f"{'Event' if is_event else 'Node'} Key: {node.canonical_name}",
            f"Node Type: {node.node_type}",
            f"Aliases: {' '.join(aliases)}",
            f"Meaning: {_node_meaning(node, relation_preview)}",
            f"Properties: {_json_text(node.properties)}",
            _preview_text(relation_preview),
            f"Expandable Handles: node_id={node.node_id}",
        ]
    )
    return SemanticVectorDocument(
        document_id=f"kg_card:{document_type}:{node.node_id}",
        document_type=document_type,
        collection_role=SEMANTIC_COLLECTION_ENTITY,
        source_type=source_type,
        source_id=node.node_id,
        evidence_id=evidence_id,
        text=text,
        metadata={
            "node_type": node.node_type,
            "node_id": node.node_id,
            "canonical_name": node.canonical_name,
            "aliases": aliases,
            "relation_preview": relation_preview,
            "version": node.version,
        },
    )


def _edge_card(edge: CompiledEdge, *, node_by_id: dict[str, CompiledNode]) -> SemanticVectorDocument:
    source_name = _node_name(edge.source_node_id, node_by_id)
    target_name = _node_name(edge.target_node_id, node_by_id)
    relation = f"{source_name} --{edge.relation_type}--> {target_name}"
    direction = str(edge.properties.get("direction") or "")
    reason = str(edge.properties.get("reason") or edge.properties.get("summary") or "")
    text = _join_parts(
        [
            "Document Type: Edge Card",
            f"Edge Key: {relation}",
            f"Relation: {edge.relation_type}",
            f"Source: {source_name}",
            f"Target: {target_name}",
            f"Direction: {direction}",
            f"Meaning: {_edge_meaning(edge, source_name=source_name, target_name=target_name)}",
            f"Evidence: {' '.join(edge.evidence_ids)}",
            f"Reason: {reason}",
            f"Properties: {_json_text(edge.properties)}",
            f"Expandable Handles: edge_id={edge.edge_id} source_node_id={edge.source_node_id} target_node_id={edge.target_node_id}",
        ]
    )
    return SemanticVectorDocument(
        document_id=f"kg_card:edge:{edge.edge_id}",
        document_type="edge_card",
        collection_role=SEMANTIC_COLLECTION_RELATION,
        source_type="kg_edge_card",
        source_id=edge.edge_id,
        evidence_id=edge.evidence_ids[0] if edge.evidence_ids else "",
        text=text,
        metadata={
            "edge_id": edge.edge_id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "source_name": source_name,
            "target_name": target_name,
            "relation_type": edge.relation_type,
            "evidence_ids": edge.evidence_ids,
            "version": edge.version,
        },
    )



def _first_evidence_id(edges: list[CompiledEdge]) -> str:
    for edge in edges:
        if edge.evidence_ids:
            return edge.evidence_ids[0]
    return ""


def _is_retrievable_node(node: CompiledNode) -> bool:
    return node.status in _RETRIEVABLE_NODE_STATUSES


def _is_retrievable_edge(edge: CompiledEdge) -> bool:
    return (
        edge.status in _RETRIEVABLE_EDGE_STATUSES
        and edge.confidence_label != ConfidenceLabel.REJECTED
        and bool(edge.evidence_ids)
    )


def _is_retrievable_chunk(chunk: EvidenceChunk) -> bool:
    status = str((chunk.payload or {}).get("status") or EvidenceStatus.ACTIVE.value)
    return status == EvidenceStatus.ACTIVE.value


def _node_meaning(node: CompiledNode, relation_preview: list[str]) -> str:
    if relation_preview:
        return f"{node.canonical_name} is a {node.node_type} connected by {len(relation_preview)} preview relation(s)."
    return f"{node.canonical_name} is a {node.node_type} fact in the knowledge graph."


def _edge_meaning(edge: CompiledEdge, *, source_name: str, target_name: str) -> str:
    direction = edge.properties.get("direction")
    if direction:
        return f"{source_name} has a {edge.relation_type} relation to {target_name}; direction={direction}."
    return f"{source_name} has a {edge.relation_type} relation to {target_name}."


def _relation_preview(
    edges: list[CompiledEdge],
    *,
    node_by_id: dict[str, CompiledNode],
    max_items: int,
) -> list[str]:
    sorted_edges = sorted(
        edges,
        key=lambda edge: (
            -len(edge.evidence_ids),
            -edge.confidence_score,
            edge.relation_type,
            edge.edge_id,
        ),
    )
    preview: list[str] = []
    for edge in sorted_edges[:max_items]:
        source_name = _node_name(edge.source_node_id, node_by_id)
        target_name = _node_name(edge.target_node_id, node_by_id)
        evidence = f" evidence={','.join(edge.evidence_ids[:2])}" if edge.evidence_ids else ""
        direction = f" direction={edge.properties.get('direction')}" if edge.properties.get("direction") else ""
        preview.append(f"{source_name} --{edge.relation_type}--> {target_name}{direction}{evidence}")
    return preview


def _preview_text(relation_preview: list[str]) -> str:
    if not relation_preview:
        return ""
    return "Relation Preview: " + " | ".join(relation_preview)


def _search_terms_text(payload: dict[str, Any]) -> str:
    terms: list[str] = []
    terms.extend(_entity_search_terms(payload.get("mentioned_entities")))
    terms.extend(_entity_search_terms(payload.get("affected_entities")))
    terms.extend(_entity_search_terms([payload.get("target_ref")]))
    terms = _ordered_unique(terms)
    if not terms:
        return ""
    return "Search Terms: " + " ".join(terms)


def _entity_search_terms(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    terms: list[str] = []
    for item in values:
        if isinstance(item, str):
            terms.append(item)
            continue
        if not isinstance(item, dict):
            continue
        for name in ("name", "code", "indicator_code", "taxonomy"):
            if item.get(name):
                terms.append(str(item[name]))
        if item.get("exchange") and item.get("code"):
            terms.append(f"{item['exchange']}:{item['code']}")
    return terms


def _edges_by_node(edges: list[CompiledEdge]) -> dict[str, list[CompiledEdge]]:
    result: dict[str, list[CompiledEdge]] = {}
    for edge in edges:
        result.setdefault(edge.source_node_id, []).append(edge)
        result.setdefault(edge.target_node_id, []).append(edge)
    return result


def _edges_by_evidence(edges: list[CompiledEdge]) -> dict[str, list[CompiledEdge]]:
    result: dict[str, list[CompiledEdge]] = {}
    for edge in edges:
        for evidence_id in edge.evidence_ids:
            result.setdefault(evidence_id, []).append(edge)
    return result


def _node_name(node_id: str, node_by_id: dict[str, CompiledNode]) -> str:
    node = node_by_id.get(node_id)
    return node.canonical_name if node else node_id


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _json_text(value: Any) -> str:
    if value in (None, {}, []):
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _join_parts(parts: list[str]) -> str:
    return "\n".join(part for part in parts if part and part.strip())


_RETRIEVABLE_NODE_STATUSES = {NodeStatus.ACTIVE, NodeStatus.CANDIDATE, NodeStatus.AMBIGUOUS}
_RETRIEVABLE_EDGE_STATUSES = {EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE}
