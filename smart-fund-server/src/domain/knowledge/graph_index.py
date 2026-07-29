"""Graph Index construction for KG community and finding targets."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

import graspologic_native as gn
import networkx as nx

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceStatus, NodeStatus
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, EvidenceChunk


@dataclass(frozen=True)
class GraphIndexVectorDocument:
    document_id: str
    document_type: str
    collection_role: str
    source_type: str
    source_id: str
    text: str
    evidence_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphProjectionProfile:
    projection: str
    description: str
    relation_weights: dict[str, float] = field(default_factory=dict)
    node_type_weights: dict[str, float] = field(default_factory=dict)
    source_weights: dict[str, float] = field(default_factory=dict)
    resolution: float = 1.0
    max_cluster_size: int = 12
    max_depth: int = 3
    min_edges: int = 1
    min_community_evidence_count: int = 2
    min_community_source_count: int = 1
    min_community_chunk_count: int = 2
    min_community_edge_count: int = 2
    min_non_mentions_edge_count: int = 1
    min_community_node_count: int = 3
    min_split_evidence_count: int = 3
    min_split_chunk_count: int = 4


@dataclass(frozen=True)
class GraphIndexCommunity:
    community_id: str
    version_id: str
    adapter_name: str
    projection: str
    level: int
    parent_community_id: str
    title: str
    summary: str
    member_node_ids: list[str]
    member_edge_ids: list[str]
    evidence_ids: list[str]
    chunk_ids: list[str]
    metrics: dict[str, Any]
    status: str = "active"
    previous_version_id: str = ""
    change_reason: str = "build"
    lineage_id: str = ""
    previous_community_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GraphIndexFinding:
    finding_id: str
    community_id: str
    adapter_name: str
    projection: str
    finding_type: str
    title: str
    statement: str
    cited_chunk_ids: list[str]
    cited_evidence_ids: list[str]
    supporting_edge_ids: list[str]
    node_ids: list[str]
    confidence: float
    status: str = "active"
    version: str = "v1"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphIndexDelta:
    delta_id: str
    adapter_name: str
    projection: str
    window_name: str
    started_at: datetime
    ended_at: datetime
    title: str
    summary: str
    community_ids: list[str]
    finding_ids: list[str]
    cited_chunk_ids: list[str]
    cited_evidence_ids: list[str]
    supporting_edge_ids: list[str]
    node_ids: list[str]
    metrics: dict[str, Any]
    status: str = "active"
    version: str = "v1"


@dataclass(frozen=True)
class GraphIndexUnassignedSignal:
    signal_id: str
    adapter_name: str
    projection: str
    title: str
    reason: str
    node_ids: list[str]
    edge_ids: list[str]
    evidence_ids: list[str]
    chunk_ids: list[str]
    topic_tags: list[str]
    impact_tags: list[str]
    event_type_tags: list[str]
    relation_types: list[str]
    support_score: float
    metrics: dict[str, Any]
    status: str = "active"
    promoted_community_id: str = ""
    promotion_attempts: int = 0
    last_checked_at: datetime | None = None


@dataclass(frozen=True)
class GraphIndexBuildResult:
    communities: list[GraphIndexCommunity]
    findings: list[GraphIndexFinding]
    deltas: list[GraphIndexDelta]
    documents: list[GraphIndexVectorDocument]
    diagnostics: dict[str, Any]
    unassigned_signals: list[GraphIndexUnassignedSignal] = field(default_factory=list)


@dataclass(frozen=True)
class GraphIndexRefreshPlan:
    """Dirty graph impact summary before rebuilding Graph Index targets."""

    action: str
    score: float
    affected_community_ids: list[str]
    affected_projection_counts: dict[str, int]
    changed_counts: dict[str, int]
    metrics: dict[str, Any]
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "score": self.score,
            "affected_community_ids": self.affected_community_ids,
            "affected_projection_counts": self.affected_projection_counts,
            "changed_counts": self.changed_counts,
            "metrics": self.metrics,
            "reasons": self.reasons,
        }


@dataclass(frozen=True)
class GraphIndexDirtyRefs:
    node_ids: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EdgeSupportStats:
    edge_id: str
    evidence_count: int
    chunk_count: int
    source_count: int
    recent_score: float
    strength: float
    support_weight: float


@dataclass(frozen=True)
class GraphSignalCandidate:
    """Internal signal extracted from a graph component before community publish."""

    community: GraphIndexCommunity
    graph: nx.Graph


DEFAULT_GRAPH_PROJECTIONS: tuple[GraphProjectionProfile, ...] = (
    GraphProjectionProfile(
        projection="default_graph_projection",
        description="通用事实图自动发现 community 的基线视角",
    ),
)

CANONICAL_GRAPH_PROJECTION = "default_graph_projection"

GRAPH_INDEX_LENS_PROFILES: tuple[GraphProjectionProfile, ...] = (
    GraphProjectionProfile(
        projection="narrative",
        description="叙事、主题、共识主线",
        relation_weights={
            "mentions": 0.75,
            "related_to": 1.05,
            "causal_hint": 1.2,
            "affects": 1.25,
            "benefits_from": 1.2,
        },
        node_type_weights={"event": 1.2, "theme": 1.15, "domain": 1.1},
    ),
    GraphProjectionProfile(
        projection="chain",
        description="链路、上下游、对象联动",
        relation_weights={
            "mentions": 0.45,
            "related_to": 1.05,
            "affects": 1.25,
            "benefits_from": 1.25,
            "depends_on": 1.3,
            "supply_chain": 1.5,
        },
        node_type_weights={"domain": 1.35, "resource": 1.2, "target": 1.2, "theme": 1.1},
    ),
    GraphProjectionProfile(
        projection="impact",
        description="规则、约束和影响链条",
        relation_weights={
            "mentions": 0.40,
            "related_to": 1.0,
            "affects": 1.45,
            "benefits_from": 1.35,
            "regulates": 1.45,
        },
        node_type_weights={"rule": 1.5, "theme": 1.15, "domain": 1.2, "event": 1.1},
    ),
    GraphProjectionProfile(
        projection="risk",
        description="风险聚集、负面事件、短期冲击",
        relation_weights={
            "mentions": 0.30,
            "related_to": 0.9,
            "affects": 1.35,
            "risk": 1.6,
            "negative_impact": 1.6,
            "hurt_by": 1.55,
            "causal_hint": 1.25,
        },
        node_type_weights={"event": 1.35, "theme": 1.1, "domain": 1.1, "target": 1.15},
    ),
)


def expand_community_scope(
    communities: list[GraphIndexCommunity],
    seed_community_ids: list[str],
) -> list[str]:
    """Return seed communities plus ancestor/descendant closure."""

    by_id = {community.community_id: community for community in communities}
    children_by_parent: dict[str, list[str]] = {}
    for community in communities:
        if community.parent_community_id:
            children_by_parent.setdefault(community.parent_community_id, []).append(community.community_id)

    result: list[str] = []
    stack = list(_ordered_unique(seed_community_ids))
    while stack:
        community_id = stack.pop(0)
        if not community_id or community_id in result:
            continue
        result.append(community_id)
        community = by_id.get(community_id)
        if community and community.parent_community_id and community.parent_community_id not in result:
            stack.append(community.parent_community_id)
        for child_id in children_by_parent.get(community_id, []):
            if child_id not in result:
                stack.append(child_id)
    return result


def select_replacement_communities(
    communities: list[GraphIndexCommunity],
    dirty_refs: GraphIndexDirtyRefs,
) -> list[GraphIndexCommunity]:
    """Select rebuilt communities that should replace the old dirty scope."""

    seed_ids = [
        community.community_id
        for community in communities
        if _community_matches_dirty_refs(community, dirty_refs)
    ]
    scoped_ids = set(expand_community_scope(communities, seed_ids))
    return [community for community in communities if community.community_id in scoped_ids]


def plan_graph_index_refresh(
    *,
    existing_communities: list[GraphIndexCommunity],
    changed_node_ids: list[str] | None = None,
    changed_edge_ids: list[str] | None = None,
    changed_evidence_ids: list[str] | None = None,
    changed_chunk_ids: list[str] | None = None,
    total_node_count: int = 0,
    total_edge_count: int = 0,
    total_chunk_count: int = 0,
) -> GraphIndexRefreshPlan:
    """Estimate dirty graph impact for Graph Index refresh orchestration.

    This planner is intentionally deterministic. It does not claim to perform
    local community recomputation; it tells the application which communities
    are dirty and how large the change is, so the current implementation can
    refresh safely and future versions can replace only the affected subgraph.
    """

    changed_nodes = set(_ordered_unique(changed_node_ids or []))
    changed_edges = set(_ordered_unique(changed_edge_ids or []))
    changed_evidence = set(_ordered_unique(changed_evidence_ids or []))
    changed_chunks = set(_ordered_unique(changed_chunk_ids or []))
    changed_counts = {
        "nodes": len(changed_nodes),
        "edges": len(changed_edges),
        "evidence": len(changed_evidence),
        "chunks": len(changed_chunks),
    }
    if not any(changed_counts.values()):
        return GraphIndexRefreshPlan(
            action="noop",
            score=0.0,
            affected_community_ids=[],
            affected_projection_counts={},
            changed_counts=changed_counts,
            metrics={},
            reasons=["no_changed_refs"],
        )
    if not existing_communities:
        return GraphIndexRefreshPlan(
            action="full_rebuild",
            score=1.0,
            affected_community_ids=[],
            affected_projection_counts={},
            changed_counts=changed_counts,
            metrics={"existing_communities": 0},
            reasons=["no_existing_graph_index"],
        )

    affected: list[GraphIndexCommunity] = []
    for community in existing_communities:
        if (
            changed_nodes.intersection(community.member_node_ids)
            or changed_edges.intersection(community.member_edge_ids)
            or changed_evidence.intersection(community.evidence_ids)
            or changed_chunks.intersection(community.chunk_ids)
        ):
            affected.append(community)

    affected_ids = _ordered_unique([community.community_id for community in affected])
    projection_counts: dict[str, int] = {}
    for community in affected:
        projection_counts[community.projection] = projection_counts.get(community.projection, 0) + 1

    all_projections = {community.projection for community in existing_communities}
    affected_ratio = len(affected_ids) / max(len(existing_communities), 1)
    affected_projection_ratio = len(projection_counts) / max(len(all_projections), 1)
    node_ratio = len(changed_nodes) / max(total_node_count, len(changed_nodes), 1)
    edge_ratio = len(changed_edges) / max(total_edge_count, len(changed_edges), 1)
    chunk_ratio = len(changed_chunks) / max(total_chunk_count, len(changed_chunks), 1)
    orphan_delta = 1.0 if not affected and any(changed_counts.values()) else 0.0
    member_delta_score = min(1.0, node_ratio * 0.45 + chunk_ratio * 0.35 + affected_ratio * 0.20)
    edge_weight_delta_score = min(1.0, edge_ratio)
    cross_community_ambiguity_score = min(1.0, affected_projection_ratio)
    evidence_delta_score = min(1.0, max(chunk_ratio, len(changed_evidence) / max(total_chunk_count, len(changed_evidence), 1)))
    deletion_score = 0.0
    recency_concentration_score = 1.0 if len(changed_chunks) >= 3 and affected_ratio <= 0.12 else 0.0
    accumulated_light_update_score = 0.0
    score = min(
        1.0,
        round(
            member_delta_score * 0.24
            + edge_weight_delta_score * 0.22
            + cross_community_ambiguity_score * 0.16
            + evidence_delta_score * 0.14
            + deletion_score * 0.10
            + recency_concentration_score * 0.08
            + accumulated_light_update_score * 0.06
            + orphan_delta * 0.35,
            4,
        ),
    )

    reasons: list[str] = []
    if orphan_delta:
        reasons.append("changed_refs_not_attached_to_existing_community")
    if affected_projection_ratio >= 0.6:
        reasons.append("changes_span_many_projections")
    if affected_ratio >= 0.25:
        reasons.append("many_existing_communities_affected")
    if edge_ratio >= 0.2:
        reasons.append("large_edge_delta")
    if not reasons:
        reasons.append("localized_dirty_subgraph")

    if score >= 0.35 or orphan_delta:
        action = "full_rebuild"
    elif score >= 0.18:
        action = "local_recompute_required"
    elif score >= 0.08:
        action = "local_review_required"
    else:
        action = "light_refresh_required"

    return GraphIndexRefreshPlan(
        action=action,
        score=score,
        affected_community_ids=affected_ids,
        affected_projection_counts=projection_counts,
        changed_counts=changed_counts,
        metrics={
            "existing_communities": len(existing_communities),
            "total_node_count": total_node_count,
            "total_edge_count": total_edge_count,
            "total_chunk_count": total_chunk_count,
            "affected_community_ratio": round(affected_ratio, 4),
            "affected_projection_ratio": round(affected_projection_ratio, 4),
            "node_change_ratio": round(node_ratio, 4),
            "edge_change_ratio": round(edge_ratio, 4),
            "chunk_change_ratio": round(chunk_ratio, 4),
            "member_delta_score": round(member_delta_score, 4),
            "edge_weight_delta_score": round(edge_weight_delta_score, 4),
            "cross_community_ambiguity_score": round(cross_community_ambiguity_score, 4),
            "evidence_delta_score": round(evidence_delta_score, 4),
            "deletion_score": round(deletion_score, 4),
            "recency_concentration_score": round(recency_concentration_score, 4),
            "accumulated_light_update_score": round(accumulated_light_update_score, 4),
        },
        reasons=reasons,
    )


def _community_matches_dirty_refs(community: GraphIndexCommunity, dirty_refs: GraphIndexDirtyRefs) -> bool:
    return bool(
        set(dirty_refs.node_ids).intersection(community.member_node_ids)
        or set(dirty_refs.edge_ids).intersection(community.member_edge_ids)
        or set(dirty_refs.evidence_ids).intersection(community.evidence_ids)
        or set(dirty_refs.chunk_ids).intersection(community.chunk_ids)
    )


def build_graph_index(
    *,
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    chunks: list[EvidenceChunk],
    projections: tuple[GraphProjectionProfile, ...] = DEFAULT_GRAPH_PROJECTIONS,
) -> GraphIndexBuildResult:
    """Build projection-aware community reports and findings from KG facts.

    The builder may use preconfigured projections, but it never receives a
    prewritten community list. Every community is derived from the graph
    topology and weighted facts.
    """

    retrievable_nodes = [node for node in nodes if _is_retrievable_node(node)]
    node_by_id = {node.node_id: node for node in retrievable_nodes}
    retrievable_edges = [
        edge
        for edge in edges
        if _is_retrievable_edge(edge)
        and edge.source_node_id in node_by_id
        and edge.target_node_id in node_by_id
    ]
    chunks_by_evidence = _chunks_by_evidence([chunk for chunk in chunks if _is_retrievable_chunk(chunk)])
    edge_support_by_id = _edge_support_stats_by_id(retrievable_edges, chunks_by_evidence=chunks_by_evidence)

    active_profiles = tuple(profile for profile in projections if profile.projection == CANONICAL_GRAPH_PROJECTION)
    if not active_profiles:
        active_profiles = DEFAULT_GRAPH_PROJECTIONS
    ignored_profiles = [
        profile.projection for profile in projections if profile.projection != CANONICAL_GRAPH_PROJECTION
    ]

    communities: list[GraphIndexCommunity] = []
    unassigned_signals: list[GraphIndexUnassignedSignal] = []
    projection_diagnostics: dict[str, Any] = {}
    for profile in active_profiles:
        projection_edges = _project_edges_for_profile(
            retrievable_edges,
            profile=profile,
            node_by_id=node_by_id,
        )
        graph = _build_weighted_graph(
            projection_edges,
            node_by_id=node_by_id,
            profile=profile,
            edge_support_by_id=edge_support_by_id,
        )
        projection_communities, projection_unassigned = _detect_hierarchical_communities(
            graph,
            profile=profile,
            node_by_id=node_by_id,
            chunks_by_evidence=chunks_by_evidence,
            edge_support_by_id=edge_support_by_id,
        )
        communities.extend(projection_communities)
        unassigned_signals.extend(projection_unassigned)
        projection_diagnostics[profile.projection] = {
            "input_edges": len(retrievable_edges),
            "projected_edges": len(projection_edges),
            "communities": len(projection_communities),
            "unassigned_signals": len(projection_unassigned),
        }
    findings = [
        finding
        for community in communities
        if (finding := _finding_from_community(community, node_by_id=node_by_id)) is not None
    ]
    deltas = build_rolling_delta_index(communities=communities, findings=findings, chunks=chunks)
    documents: list[GraphIndexVectorDocument] = []
    documents.extend(_community_document(community, node_by_id=node_by_id) for community in communities)
    documents.extend(_finding_document(finding) for finding in findings)
    documents.extend(_delta_document(delta) for delta in deltas)

    return GraphIndexBuildResult(
        communities=communities,
        findings=findings,
        deltas=deltas,
        documents=documents,
        diagnostics={
            "community_algorithm": "hierarchical_leiden",
            "projection_mode": "canonical_graph_with_projection_scores",
            "projection_profiles": [profile.projection for profile in active_profiles],
            "ignored_independent_projection_profiles": ignored_profiles,
            "projection_diagnostics": projection_diagnostics,
            "community_count": len(communities),
            "finding_count": len(findings),
            "delta_count": len(deltas),
            "unassigned_signal_count": len(unassigned_signals),
            "input_nodes": len(nodes),
            "input_edges": len(edges),
            "input_chunks": len(chunks),
            "retrievable_nodes": len(retrievable_nodes),
            "retrievable_edges": len(retrievable_edges),
            "edge_support": _edge_support_diagnostics(edge_support_by_id),
        },
        unassigned_signals=unassigned_signals,
    )


def _project_edges_for_profile(
    edges: list[CompiledEdge],
    *,
    profile: GraphProjectionProfile,
    node_by_id: dict[str, CompiledNode],
) -> list[CompiledEdge]:
    if profile.projection == CANONICAL_GRAPH_PROJECTION:
        return list(edges)
    return [
        edge
        for edge in edges
        if _edge_matches_projection(edge, profile=profile, node_by_id=node_by_id)
    ]


def _edge_matches_projection(
    edge: CompiledEdge,
    *,
    profile: GraphProjectionProfile,
    node_by_id: dict[str, CompiledNode],
) -> bool:
    source = node_by_id.get(edge.source_node_id)
    target = node_by_id.get(edge.target_node_id)
    node_types = {node.node_type for node in (source, target) if node is not None}
    relation_type = edge.relation_type
    direction = str(edge.properties.get("direction") or edge.properties.get("sentiment") or "").lower()

    if profile.projection == "risk":
        return (
            relation_type in {"risk", "negative_impact", "hurt_by", "regulatory_risk"}
            or direction in {"negative", "risk", "bearish"}
        )
    return (
        relation_type in profile.relation_weights
        or any(node_type in profile.node_type_weights for node_type in node_types)
    )


def _node_has_taxonomy(node: CompiledNode | None, taxonomy: str) -> bool:
    if node is None:
        return False
    value = str(node.properties.get("taxonomy") or node.properties.get("category") or "").lower()
    return taxonomy.lower() in value


def _projection_activation(
    profile: GraphProjectionProfile,
    *,
    edges: list[CompiledEdge],
    node_by_id: dict[str, CompiledNode],
) -> float:
    if profile.projection == CANONICAL_GRAPH_PROJECTION:
        return 1.0
    if not edges:
        return 0.0
    relation_types = {edge.relation_type for edge in edges}
    node_types = {
        node.node_type
        for edge in edges
        for node in (node_by_id.get(edge.source_node_id), node_by_id.get(edge.target_node_id))
        if node is not None
    }
    if profile.projection == "risk":
        if relation_types.intersection({"risk", "negative_impact", "hurt_by", "regulatory_risk"}):
            return 1.0
        if any(str(edge.properties.get("direction") or "").lower() in {"negative", "risk"} for edge in edges):
            return 0.85
        return 0.0
    relation_hits = sum(1 for edge in edges if profile.relation_weights.get(edge.relation_type, 1.0) > 1.0)
    node_hits = sum(1 for node_type in node_types if profile.node_type_weights.get(node_type, 1.0) > 1.0)
    relation_score = min(1.0, relation_hits / max(len(edges), 1))
    node_score = min(1.0, node_hits / max(len(node_types), 1))
    return round(min(1.0, relation_score * 0.65 + node_score * 0.35), 4)


def _build_weighted_graph(
    edges: list[CompiledEdge],
    *,
    node_by_id: dict[str, CompiledNode],
    profile: GraphProjectionProfile,
    edge_support_by_id: dict[str, EdgeSupportStats],
) -> nx.Graph:
    graph = nx.Graph()
    for node_id, node in node_by_id.items():
        graph.add_node(
            node_id,
            name=node.canonical_name,
            node_type=node.node_type,
            weight=profile.node_type_weights.get(node.node_type, 1.0),
        )
    for edge in edges:
        support = edge_support_by_id.get(edge.edge_id)
        if not _edge_can_define_community_boundary(edge, support=support):
            continue
        source = edge.source_node_id
        target = edge.target_node_id
        weight = _edge_weight(
            edge,
            node_by_id=node_by_id,
            profile=profile,
            support=support,
        )
        if graph.has_edge(source, target):
            graph[source][target]["weight"] += weight
            graph[source][target]["edge_ids"].append(edge.edge_id)
            graph[source][target]["edges"].append(edge)
        else:
            graph.add_edge(source, target, weight=weight, edge_ids=[edge.edge_id], edges=[edge])
    return graph


def _edge_can_define_community_boundary(edge: CompiledEdge, *, support: EdgeSupportStats | None) -> bool:
    """Decide whether an edge can create community topology.

    Every extracted fact edge should be able to enter the community organization
    layer. Weak mention edges are penalized by edge weights and maturity scoring;
    they are not discarded here because that would make early topics invisible to
    Graph Index lineage.
    """

    return True


def _component_graphs_after_weak_bridge_split(
    component_graph: nx.Graph,
    *,
    profile: GraphProjectionProfile,
    node_by_id: dict[str, CompiledNode],
) -> list[nx.Graph]:
    """Split a connected component when weak/generic bridges are the only glue."""

    if _graph_evidence_count(component_graph) <= 1:
        return [component_graph]

    boundary_graph = component_graph.copy()
    for source, target, data in list(boundary_graph.edges(data=True)):
        edges = data.get("edges") or []
        if edges and all(_edge_is_weak_community_bridge(edge, node_by_id=node_by_id) for edge in edges):
            boundary_graph.remove_edge(source, target)

    split_graphs = _valid_split_subgraphs(component_graph, boundary_graph, profile=profile)
    if len(split_graphs) > 1:
        return split_graphs
    if len(split_graphs) == 1:
        return [component_graph]

    evidence_graphs = _evidence_scoped_subgraphs(component_graph, profile=profile)
    if len(evidence_graphs) > 1:
        return evidence_graphs
    return [component_graph]


def _valid_split_subgraphs(
    source_graph: nx.Graph,
    boundary_graph: nx.Graph,
    *,
    profile: GraphProjectionProfile,
) -> list[nx.Graph]:
    result: list[nx.Graph] = []
    for node_ids in sorted(nx.connected_components(boundary_graph), key=lambda item: (-len(item), sorted(item)[0])):
        subgraph = source_graph.subgraph(node_ids).copy()
        if subgraph.number_of_edges() >= profile.min_edges:
            result.append(subgraph)
    return result


def _evidence_scoped_subgraphs(
    component_graph: nx.Graph,
    *,
    profile: GraphProjectionProfile,
) -> list[nx.Graph]:
    evidence_to_edges: dict[str, list[tuple[str, str]]] = {}
    for source, target, data in component_graph.edges(data=True):
        edges = data.get("edges") or []
        for edge in edges:
            for evidence_id in edge.evidence_ids:
                if evidence_id:
                    evidence_to_edges.setdefault(evidence_id, []).append((str(source), str(target)))
    result: list[nx.Graph] = []
    for evidence_id in sorted(evidence_to_edges):
        selected_edges = evidence_to_edges[evidence_id]
        subgraph = nx.Graph()
        for source, target in selected_edges:
            if not component_graph.has_edge(source, target):
                continue
            if not subgraph.has_node(source):
                subgraph.add_node(source, **component_graph.nodes[source])
            if not subgraph.has_node(target):
                subgraph.add_node(target, **component_graph.nodes[target])
            subgraph.add_edge(source, target, **component_graph[source][target])
        if subgraph.number_of_edges() >= profile.min_edges:
            result.append(subgraph)
    return result


def _graph_evidence_count(graph: nx.Graph) -> int:
    return len(
        _ordered_unique(
            evidence_id
            for _, _, data in graph.edges(data=True)
            for edge in (data.get("edges") or [])
            for evidence_id in edge.evidence_ids
            if evidence_id
        )
    )


def _detect_hierarchical_communities(
    graph: nx.Graph,
    *,
    profile: GraphProjectionProfile,
    node_by_id: dict[str, CompiledNode],
    chunks_by_evidence: dict[str, list[EvidenceChunk]],
    edge_support_by_id: dict[str, EdgeSupportStats],
) -> tuple[list[GraphIndexCommunity], list[GraphIndexUnassignedSignal]]:
    if graph.number_of_edges() < profile.min_edges:
        return [], []
    communities: list[GraphIndexCommunity] = []
    unassigned_signals: list[GraphIndexUnassignedSignal] = []
    signal_candidates: list[GraphSignalCandidate] = []
    for component_nodes in sorted(nx.connected_components(graph), key=lambda item: (-len(item), sorted(item)[0])):
        raw_component_graph = graph.subgraph(component_nodes).copy()
        component_graphs = (
            _component_graphs_after_weak_bridge_split(
                raw_component_graph,
                profile=profile,
                node_by_id=node_by_id,
            )
        )
        for component_graph in component_graphs:
            root = _community_from_subgraph(
                component_graph,
                profile=profile,
                node_by_id=node_by_id,
                chunks_by_evidence=chunks_by_evidence,
                edge_support_by_id=edge_support_by_id,
                level=0,
                parent_community_id="",
            )
            if root is None:
                continue
            if not _signal_candidate_has_minimum_footprint(root):
                signal = _unassigned_signal_from_community(root, reason="insufficient_signal_footprint")
                if signal is not None:
                    unassigned_signals.append(signal)
                continue
            signal_candidates.append(GraphSignalCandidate(community=root, graph=component_graph))

    fusion_groups = _signal_fusion_groups(
        signal_candidates,
        profile=profile,
        node_by_id=node_by_id,
    )
    consumed_signal_indexes: set[int] = set()
    for group_indexes, fusion_metrics in fusion_groups:
        consumed_signal_indexes.update(group_indexes)
        group_candidates = [signal_candidates[index] for index in group_indexes]
        component_graph = _compose_signal_candidate_graphs(group_candidates)
        root = _community_from_subgraph(
            component_graph,
            profile=profile,
            node_by_id=node_by_id,
            chunks_by_evidence=chunks_by_evidence,
            edge_support_by_id=edge_support_by_id,
            level=0,
            parent_community_id="",
        )
        if root is None:
            continue
        root = _with_signal_fusion_metrics(root, group_candidates=group_candidates, fusion_metrics=fusion_metrics)
        if not _formal_community_has_enough_support(root, profile=profile):
            signal = _unassigned_signal_from_community(root, reason="insufficient_fused_community_support")
            if signal is not None:
                unassigned_signals.append(signal)
            continue
        component_nodes = set(component_graph.nodes)
        split_allowed = _component_has_enough_support_to_split(root, profile=profile)
        root = replace(
            root,
            metrics={
                **root.metrics,
                "split_rule": {
                    "eligible": split_allowed,
                    "min_split_evidence_count": profile.min_split_evidence_count,
                    "min_split_chunk_count": profile.min_split_chunk_count,
                    "reason": "eligible" if split_allowed else "insufficient_cross_evidence_support",
                },
            },
        )
        communities.append(root)
        if component_graph.number_of_nodes() <= profile.max_cluster_size or profile.max_depth <= 1:
            continue
        if not split_allowed:
            continue
        clusters = _hierarchical_leiden_clusters(component_graph, profile=profile)
        communities_by_native_key: dict[tuple[int, int], GraphIndexCommunity] = {}
        for native_level, cluster_id, parent_cluster, node_ids in clusters:
            level = native_level + 1
            if level >= profile.max_depth:
                continue
            if set(node_ids) == set(component_nodes):
                continue
            parent_community_id = root.community_id
            if native_level > 0 and parent_cluster is not None:
                parent = communities_by_native_key.get((native_level - 1, parent_cluster))
                if parent is not None:
                    parent_community_id = parent.community_id
            community = _community_from_subgraph(
                component_graph.subgraph(node_ids).copy(),
                profile=profile,
                node_by_id=node_by_id,
                chunks_by_evidence=chunks_by_evidence,
                edge_support_by_id=edge_support_by_id,
                level=level,
                parent_community_id=parent_community_id,
            )
            if community is not None and _community_has_enough_support(community, profile=profile):
                communities_by_native_key[(native_level, cluster_id)] = community
                communities.append(community)

    for index, candidate in enumerate(signal_candidates):
        if index in consumed_signal_indexes:
            continue
        candidate_community = _with_signal_fusion_metrics(
            candidate.community,
            group_candidates=[candidate],
            fusion_metrics={
                "signal_fusion_algorithm": "native_component",
                "signal_count": 1,
                "fusion_edge_count": 0,
                "avg_fusion_score": 1.0 if len(candidate.community.evidence_ids) >= 2 else 0.0,
                "min_fusion_score": 1.0 if len(candidate.community.evidence_ids) >= 2 else 0.0,
                "source_signal_ids": [_signal_candidate_id(candidate.community)],
                "fusion_reasons": ["native_cross_evidence_component"] if len(candidate.community.evidence_ids) >= 2 else [],
            },
        )
        if _formal_community_has_enough_support(candidate_community, profile=profile):
            split_allowed = _component_has_enough_support_to_split(candidate_community, profile=profile)
            communities.append(
                replace(
                    candidate_community,
                    metrics={
                        **candidate_community.metrics,
                        "split_rule": {
                            "eligible": split_allowed,
                            "min_split_evidence_count": profile.min_split_evidence_count,
                            "min_split_chunk_count": profile.min_split_chunk_count,
                            "reason": "eligible" if split_allowed else "insufficient_cross_evidence_support",
                        },
                    },
                )
            )
            continue
        signal = _unassigned_signal_from_community(candidate.community, reason="insufficient_signal_fusion")
        if signal is not None:
            unassigned_signals.append(signal)
    return (
        sorted(
            communities,
            key=lambda item: (item.projection, item.level, item.parent_community_id, item.community_id),
        ),
        sorted(unassigned_signals, key=lambda item: (item.projection, item.signal_id)),
    )


def _unassigned_signal_from_community(
    community: GraphIndexCommunity,
    *,
    reason: str,
) -> GraphIndexUnassignedSignal | None:
    if not community.chunk_ids or not community.evidence_ids or not community.member_edge_ids:
        return None
    metrics = dict(community.metrics or {})
    topic_digest = str(metrics.get("topic_fingerprint_digest") or "").strip()
    signal_digest = topic_digest or _digest([
        *community.member_node_ids,
        *community.member_edge_ids,
        *community.evidence_ids,
        *community.chunk_ids,
    ])
    signal_id = f"kg_graph_signal:{community.projection}:{signal_digest}"
    return GraphIndexUnassignedSignal(
        signal_id=signal_id,
        adapter_name=community.adapter_name,
        projection=community.projection,
        title=community.title,
        reason=reason,
        node_ids=list(community.member_node_ids),
        edge_ids=list(community.member_edge_ids),
        evidence_ids=list(community.evidence_ids),
        chunk_ids=list(community.chunk_ids),
        topic_tags=[str(item) for item in metrics.get("topic_tags") or [] if item],
        impact_tags=[str(item) for item in metrics.get("impact_tags") or [] if item],
        event_type_tags=[str(item) for item in metrics.get("event_type_tags") or [] if item],
        relation_types=[str(item) for item in metrics.get("relation_types") or [] if item],
        support_score=_coerce_float(metrics.get("support_score")) or 0.0,
        metrics={
            "node_count": metrics.get("node_count", len(community.member_node_ids)),
            "edge_count": metrics.get("edge_count", len(community.member_edge_ids)),
            "evidence_count": metrics.get("evidence_count", len(community.evidence_ids)),
            "chunk_count": metrics.get("chunk_count", len(community.chunk_ids)),
            "non_mentions_edge_count": metrics.get("non_mentions_edge_count", 0),
            "strong_edge_count": metrics.get("strong_edge_count", 0),
            "topic_cohesion_score": metrics.get("topic_cohesion_score", 0.0),
            "maturity_level": metrics.get("maturity_level", ""),
            "topic_fingerprint": metrics.get("topic_fingerprint", {}),
            "topic_fingerprint_digest": metrics.get("topic_fingerprint_digest", ""),
            "reason": reason,
        },
    )


def _community_has_enough_support(
    community: GraphIndexCommunity,
    *,
    profile: GraphProjectionProfile,
) -> bool:
    evidence_count = int(community.metrics.get("evidence_count") or 0)
    source_count = int(community.metrics.get("source_count") or 0)
    chunk_count = int(community.metrics.get("chunk_count") or 0)
    edge_count = int(community.metrics.get("edge_count") or 0)
    non_mentions_edge_count = int(community.metrics.get("non_mentions_edge_count") or 0)
    node_count = int(community.metrics.get("node_count") or 0)
    return (
        evidence_count >= profile.min_community_evidence_count
        and source_count >= profile.min_community_source_count
        and chunk_count >= profile.min_community_chunk_count
        and edge_count >= profile.min_community_edge_count
        and non_mentions_edge_count >= profile.min_non_mentions_edge_count
        and node_count >= profile.min_community_node_count
    )


def _component_has_enough_support_to_split(
    community: GraphIndexCommunity,
    *,
    profile: GraphProjectionProfile,
) -> bool:
    """Only split communities that have enough cross-evidence support.

    A single long article can mention many entities, but that does not mean it
    has enough graph evidence to create multiple durable communities.
    Hierarchical Leiden should separate recurring multi-source structures, not
    overfit one evidence item into several low-value subtopics.
    """

    evidence_count = int(community.metrics.get("evidence_count") or 0)
    chunk_count = int(community.metrics.get("chunk_count") or 0)
    source_count = int(community.metrics.get("source_count") or 0)
    enough_evidence = evidence_count >= profile.min_split_evidence_count
    enough_chunks = chunk_count >= profile.min_split_chunk_count
    enough_sources = source_count >= 2
    return enough_evidence and enough_chunks and enough_sources


def _hierarchical_leiden_clusters(
    graph: nx.Graph,
    *,
    profile: GraphProjectionProfile,
) -> list[tuple[int, int, int | None, list[str]]]:
    edge_list: list[tuple[str, str, float]] = []
    for source, target, data in graph.edges(data=True):
        lo, hi = sorted([str(source), str(target)])
        edge_list.append((lo, hi, float(data.get("weight") or 1.0)))
    edge_list = sorted(edge_list)
    if not edge_list:
        return []
    partitions = gn.hierarchical_leiden(
        edges=edge_list,
        max_cluster_size=profile.max_cluster_size,
        seed=42,
        starting_communities=None,
        resolution=profile.resolution,
        randomness=0.001,
        use_modularity=True,
        iterations=1,
    )
    clusters: dict[tuple[int, int], set[str]] = {}
    parents: dict[tuple[int, int], int | None] = {}
    for partition in partitions:
        level = int(partition.level)
        cluster = int(partition.cluster)
        key = (level, cluster)
        clusters.setdefault(key, set()).add(str(partition.node))
        parents[key] = int(partition.parent_cluster) if partition.parent_cluster is not None else None
    result = [
        (level, cluster, parents.get((level, cluster)), sorted(node_ids))
        for (level, cluster), node_ids in clusters.items()
        if len(node_ids) >= 2
    ]
    return sorted(result, key=lambda item: (item[0], item[1], item[3]))


def _signal_candidate_has_minimum_footprint(community: GraphIndexCommunity) -> bool:
    return (
        bool(community.evidence_ids)
        and bool(community.chunk_ids)
        and bool(community.member_edge_ids)
        and int(community.metrics.get("edge_count") or 0) >= 1
        and int(community.metrics.get("node_count") or 0) >= 2
    )


def _signal_fusion_groups(
    candidates: list[GraphSignalCandidate],
    *,
    profile: GraphProjectionProfile,
    node_by_id: dict[str, CompiledNode],
) -> list[tuple[list[int], dict[str, Any]]]:
    if len(candidates) < 2:
        return []

    fusion_graph = nx.Graph()
    for index, candidate in enumerate(candidates):
        fusion_graph.add_node(index, signal_id=_signal_candidate_id(candidate.community))

    for left_index, left in enumerate(candidates):
        for right_index in range(left_index + 1, len(candidates)):
            right = candidates[right_index]
            score, detail = _signal_fusion_score(left.community, right.community, node_by_id=node_by_id)
            if score < _signal_fusion_threshold(left.community, right.community, profile=profile):
                continue
            fusion_graph.add_edge(left_index, right_index, weight=score, detail=detail)

    groups: list[tuple[list[int], dict[str, Any]]] = []
    for component in sorted(nx.connected_components(fusion_graph), key=lambda item: (-len(item), sorted(item)[0])):
        group_indexes = sorted(component)
        if len(group_indexes) < 2:
            continue
        group_candidates = [candidates[index] for index in group_indexes]
        if len(_ordered_unique(evidence_id for item in group_candidates for evidence_id in item.community.evidence_ids)) < 2:
            continue
        edges = [
            data
            for left, right, data in fusion_graph.subgraph(group_indexes).edges(data=True)
            if left in group_indexes and right in group_indexes
        ]
        avg_score = round(_avg([float(data.get("weight") or 0.0) for data in edges]), 6)
        groups.append(
            (
                group_indexes,
                {
                    "signal_fusion_algorithm": "topic_fingerprint_graph",
                    "signal_count": len(group_indexes),
                    "fusion_edge_count": len(edges),
                    "avg_fusion_score": avg_score,
                    "min_fusion_score": round(min([float(data.get("weight") or 0.0) for data in edges] or [0.0]), 6),
                    "source_signal_ids": [_signal_candidate_id(candidate.community) for candidate in group_candidates],
                    "fusion_reasons": _ordered_unique(
                        reason
                        for data in edges
                        for reason in ((data.get("detail") or {}).get("reasons") or [])
                    )[:12],
                },
            )
        )
    return groups


def _signal_fusion_threshold(
    left: GraphIndexCommunity,
    right: GraphIndexCommunity,
    *,
    profile: GraphProjectionProfile,
) -> float:
    """Structured threshold for fusing early graph signals into a community.

    Small single-evidence signals need stronger thematic agreement than already
    multi-evidence roots. This keeps weak generic graph bridges from becoming a
    long-lived community boundary.
    """

    left_evidence = len(left.evidence_ids)
    right_evidence = len(right.evidence_ids)
    if left_evidence >= profile.min_community_evidence_count or right_evidence >= profile.min_community_evidence_count:
        return 0.34
    return 0.42


def _signal_fusion_score(
    left: GraphIndexCommunity,
    right: GraphIndexCommunity,
    *,
    node_by_id: dict[str, CompiledNode],
) -> tuple[float, dict[str, Any]]:
    if set(left.evidence_ids) & set(right.evidence_ids):
        return 0.0, {"reasons": ["same_evidence_not_fused"]}

    fingerprint_score = _topic_fingerprint_similarity(
        left.metrics.get("topic_fingerprint"),
        right.metrics.get("topic_fingerprint"),
    )
    signal_score = _signal_metric_similarity(left.metrics, right.metrics)
    relation_score = _jaccard(
        _metric_values(left.metrics, "relation_types", limit=24),
        _metric_values(right.metrics, "relation_types", limit=24),
    )
    projection_score = _jaccard(
        _metric_values(left.metrics, "projection_tags", limit=24),
        _metric_values(right.metrics, "projection_tags", limit=24),
    )
    core_entity_score = _jaccard(
        _non_generic_node_names(left.member_node_ids, node_by_id=node_by_id),
        _non_generic_node_names(right.member_node_ids, node_by_id=node_by_id),
    )
    strong_signal_score = max(
        _jaccard(_metric_values(left.metrics, "topic_tags", limit=24), _metric_values(right.metrics, "topic_tags", limit=24)),
        _jaccard(
            _metric_values(left.metrics, "event_type_tags", limit=24),
            _metric_values(right.metrics, "event_type_tags", limit=24),
        ),
        _jaccard(_metric_values(left.metrics, "impact_tags", limit=24), _metric_values(right.metrics, "impact_tags", limit=24)),
    )

    reasons: list[str] = []
    if fingerprint_score >= 0.35:
        reasons.append("topic_fingerprint_overlap")
    if signal_score >= 0.25:
        reasons.append("signal_metric_overlap")
    if core_entity_score > 0:
        reasons.append("non_generic_entity_overlap")
    if relation_score >= 0.5:
        reasons.append("relation_type_overlap")

    if strong_signal_score <= 0 and core_entity_score <= 0:
        return 0.0, {
            "reasons": ["no_strong_topic_or_entity_overlap"],
            "fingerprint_score": fingerprint_score,
            "signal_score": signal_score,
            "relation_score": relation_score,
            "projection_score": projection_score,
            "core_entity_score": core_entity_score,
        }

    score = (
        fingerprint_score * 0.42
        + signal_score * 0.26
        + relation_score * 0.10
        + projection_score * 0.06
        + core_entity_score * 0.16
    )
    if _topic_digest_matches(left, right):
        score = max(score, 0.72)
        reasons.append("same_topic_fingerprint_digest")
    if _generic_only_overlap(left, right, node_by_id=node_by_id):
        score = min(score, 0.30)
        reasons.append("generic_only_overlap_penalty")
    return round(max(0.0, min(1.0, score)), 6), {
        "reasons": _ordered_unique(reasons),
        "fingerprint_score": fingerprint_score,
        "signal_score": signal_score,
        "relation_score": relation_score,
        "projection_score": projection_score,
        "core_entity_score": core_entity_score,
        "strong_signal_score": strong_signal_score,
    }


def _compose_signal_candidate_graphs(candidates: list[GraphSignalCandidate]) -> nx.Graph:
    combined = nx.Graph()
    for candidate in candidates:
        combined = nx.compose(combined, candidate.graph)
    return combined


def _with_signal_fusion_metrics(
    community: GraphIndexCommunity,
    *,
    group_candidates: list[GraphSignalCandidate],
    fusion_metrics: dict[str, Any],
) -> GraphIndexCommunity:
    signal_count = len(group_candidates)
    metrics = {
        **community.metrics,
        **fusion_metrics,
        "signal_count": signal_count,
        "maturity_level": _community_maturity_level(
            evidence_count=int(community.metrics.get("evidence_count") or 0),
            source_count=int(community.metrics.get("source_count") or 0),
            chunk_count=int(community.metrics.get("chunk_count") or 0),
            edge_count=int(community.metrics.get("edge_count") or 0),
            non_mentions_edge_count=int(community.metrics.get("non_mentions_edge_count") or 0),
            strong_edge_count=int(community.metrics.get("strong_edge_count") or 0),
            support_score=_coerce_float(community.metrics.get("support_score")) or 0.0,
            topic_cohesion_score=_coerce_float(community.metrics.get("topic_cohesion_score")) or 0.0,
        ),
    }
    return replace(community, metrics=metrics)


def _formal_community_has_enough_support(
    community: GraphIndexCommunity,
    *,
    profile: GraphProjectionProfile,
) -> bool:
    evidence_count = int(community.metrics.get("evidence_count") or 0)
    chunk_count = int(community.metrics.get("chunk_count") or 0)
    signal_count = int(community.metrics.get("signal_count") or 0)
    topic_cohesion_score = _coerce_float(community.metrics.get("topic_cohesion_score")) or 0.0
    if evidence_count < profile.min_community_evidence_count:
        return False
    if chunk_count < profile.min_community_chunk_count:
        return False
    if signal_count < 2 and evidence_count < 2:
        return False
    if topic_cohesion_score < 0.20:
        return False
    return _community_has_enough_support(community, profile=profile)


def _signal_candidate_id(community: GraphIndexCommunity) -> str:
    digest = str(community.metrics.get("topic_fingerprint_digest") or "").strip()
    if not digest:
        digest = _digest([*community.member_node_ids, *community.member_edge_ids, *community.evidence_ids, *community.chunk_ids])
    return f"kg_graph_signal:{community.projection}:{digest}"


def _topic_digest_matches(left: GraphIndexCommunity, right: GraphIndexCommunity) -> bool:
    left_digest = str(left.metrics.get("topic_fingerprint_digest") or "").strip()
    right_digest = str(right.metrics.get("topic_fingerprint_digest") or "").strip()
    return bool(left_digest and right_digest and left_digest == right_digest)


def _non_generic_node_names(node_ids: list[str], *, node_by_id: dict[str, CompiledNode]) -> list[str]:
    return _ordered_unique(
        _node_name(node_id, node_by_id).lower()
        for node_id in node_ids
        if node_by_id.get(node_id) is not None and not _is_generic_bridge_node(node_by_id.get(node_id))
    )


def _generic_only_overlap(
    left: GraphIndexCommunity,
    right: GraphIndexCommunity,
    *,
    node_by_id: dict[str, CompiledNode],
) -> bool:
    shared_node_ids = set(left.member_node_ids) & set(right.member_node_ids)
    if not shared_node_ids:
        return False
    return all(_is_generic_bridge_node(node_by_id.get(node_id)) for node_id in shared_node_ids)


def _community_from_subgraph(
    graph: nx.Graph,
    *,
    profile: GraphProjectionProfile,
    node_by_id: dict[str, CompiledNode],
    chunks_by_evidence: dict[str, list[EvidenceChunk]],
    edge_support_by_id: dict[str, EdgeSupportStats],
    level: int,
    parent_community_id: str,
) -> GraphIndexCommunity | None:
    edge_ids: list[str] = []
    edges: list[CompiledEdge] = []
    for _, _, data in graph.edges(data=True):
        edge_ids.extend(data.get("edge_ids") or [])
        edges.extend(data.get("edges") or [])
    edge_ids = _ordered_unique(edge_ids)
    edges = _unique_edges(edges)
    if len(edge_ids) < profile.min_edges:
        return None
    evidence_ids = _ordered_unique([evidence_id for edge in edges for evidence_id in edge.evidence_ids])
    chunk_ids = _chunk_ids_for_evidence(evidence_ids, chunks_by_evidence)
    if not chunk_ids:
        return None
    support_stats = [edge_support_by_id.get(edge.edge_id) for edge in edges if edge_support_by_id.get(edge.edge_id)]
    node_ids = sorted(str(node_id) for node_id in graph.nodes)
    top_names = _top_node_names(node_ids, edges, node_by_id=node_by_id)
    relation_types = _ordered_unique([edge.relation_type for edge in edges])
    signal_metrics = _community_signal_metrics(edges)
    projection_scores = _projection_scores(edges, node_by_id=node_by_id, signal_metrics=signal_metrics)
    projection_tags = [
        projection for projection, score in projection_scores.items() if projection != "default_graph_projection" and score > 0
    ]
    strong_edge_count = sum(1 for edge in edges if _is_strong_relation(edge.relation_type))
    non_mentions_edge_count = sum(1 for edge in edges if edge.relation_type != "mentions")
    weak_bridge_count = sum(1 for edge in edges if _edge_is_weak_community_bridge(edge, node_by_id=node_by_id))
    generic_bridge_count = sum(1 for edge in edges if _edge_has_generic_endpoint(edge, node_by_id=node_by_id))
    topic_cohesion_score = _community_topic_cohesion_score(edges, node_by_id=node_by_id)
    evidence_count = len(evidence_ids)
    chunk_count = len(chunk_ids)
    source_count = len(_source_keys_for_chunks(chunk_ids, chunks_by_evidence=chunks_by_evidence))
    support_score = _community_support_score(
        evidence_count=evidence_count,
        source_count=source_count,
        chunk_count=chunk_count,
        edge_count=len(edge_ids),
        non_mentions_edge_count=non_mentions_edge_count,
        strong_edge_count=strong_edge_count,
        avg_edge_support_weight=_avg([item.support_weight for item in support_stats]),
        signal_count=sum(
            len(value)
            for value in signal_metrics.values()
            if isinstance(value, list)
        ),
        topic_cohesion_score=topic_cohesion_score,
    )
    maturity_level = _community_maturity_level(
        evidence_count=evidence_count,
        source_count=source_count,
        chunk_count=chunk_count,
        edge_count=len(edge_ids),
        non_mentions_edge_count=non_mentions_edge_count,
        strong_edge_count=strong_edge_count,
        support_score=support_score,
        topic_cohesion_score=topic_cohesion_score,
    )
    topic_fingerprint = _community_topic_fingerprint(
        top_names=top_names,
        relation_types=relation_types,
        signal_metrics=signal_metrics,
        edges=edges,
        node_by_id=node_by_id,
        chunk_ids=chunk_ids,
        evidence_ids=evidence_ids,
    )
    digest = _digest([profile.projection, str(level), *node_ids, *edge_ids])
    version_digest = _digest([profile.projection, str(level), *node_ids, *edge_ids, *chunk_ids])
    community_id = f"kg_community:{profile.projection}:l{level}:{digest}"
    title = _community_title(
        level=level,
        top_names=top_names,
        relation_types=relation_types,
        signal_metrics=signal_metrics,
        profile=profile,
    )
    summary = _community_summary(
        title=title,
        profile=profile,
        level=level,
        relation_types=relation_types,
        edges=edges,
        node_by_id=node_by_id,
    )
    return GraphIndexCommunity(
        community_id=community_id,
        version_id=f"{community_id}:v:{version_digest}",
        adapter_name=_first_adapter_name(edges, node_by_id),
        projection=profile.projection,
        level=level,
        parent_community_id=parent_community_id,
        title=title,
        summary=summary,
        member_node_ids=node_ids,
        member_edge_ids=edge_ids,
        evidence_ids=evidence_ids,
        chunk_ids=chunk_ids,
        metrics={
            "node_count": len(node_ids),
            "edge_count": len(edge_ids),
            "non_mentions_edge_count": non_mentions_edge_count,
            "strong_edge_count": strong_edge_count,
            "weak_bridge_count": weak_bridge_count,
            "generic_bridge_count": generic_bridge_count,
            "topic_cohesion_score": topic_cohesion_score,
            "evidence_count": evidence_count,
            "chunk_count": chunk_count,
            "source_count": source_count,
            "support_score": support_score,
            "maturity_level": maturity_level,
            "topic_fingerprint": topic_fingerprint,
            "topic_fingerprint_digest": _topic_fingerprint_digest(topic_fingerprint),
            "broad_title": title,
            "title_scope": "topic_container" if level == 0 else "subtopic_specific",
            "edge_support_chunk_refs": sum(item.chunk_count for item in support_stats),
            "edge_support_evidence_refs": sum(item.evidence_count for item in support_stats),
            "support_source_count": len(
                _ordered_unique(
                    source_key
                    for edge in edges
                    for source_key in _source_keys_for_edge(edge, chunks_by_evidence=chunks_by_evidence)
                )
            ),
            "avg_edge_support_weight": round(_avg([item.support_weight for item in support_stats]), 4),
            "avg_recent_support_score": round(_avg([item.recent_score for item in support_stats]), 4),
            "relation_types": relation_types,
            "projection_description": profile.description,
            "projection_scores": projection_scores,
            "projection_tags": projection_tags,
            **signal_metrics,
            "avg_edge_confidence": round(_avg([edge.confidence_score for edge in edges]), 4),
        },
    )


def _finding_from_community(
    community: GraphIndexCommunity,
    *,
    node_by_id: dict[str, CompiledNode],
) -> GraphIndexFinding | None:
    if not community.chunk_ids or not community.evidence_ids:
        return None
    relation_types = [str(item) for item in community.metrics.get("relation_types") or [] if item]
    finding_type = _finding_type(community.projection, relation_types)
    digest = _digest([community.community_id, community.version_id, finding_type])
    title = f"{community.title} 的{_finding_type_label(finding_type)}"
    maturity_level = str(community.metrics.get("maturity_level") or "")
    support_score = _coerce_float(community.metrics.get("support_score")) or 0.0
    maturity_note = "该线索仍处于单源早期阶段，" if maturity_level == "single_source_signal" else ""
    statement = (
        f"{community.title} 在 {community.projection} 视角下形成可检索的{_finding_type_label(finding_type)}，"
        f"{maturity_note}需要回到 cited chunks 精读确认。"
    )
    confidence = min(0.9, 0.50 + support_score * 0.35 + math.log1p(len(community.chunk_ids)) / 12)
    if maturity_level == "single_source_signal":
        confidence = min(confidence, 0.62)
    return GraphIndexFinding(
        finding_id=f"kg_finding:{digest}",
        community_id=community.community_id,
        adapter_name=community.adapter_name,
        projection=community.projection,
        finding_type=finding_type,
        title=title,
        statement=statement,
        cited_chunk_ids=community.chunk_ids,
        cited_evidence_ids=community.evidence_ids,
        supporting_edge_ids=community.member_edge_ids,
        node_ids=community.member_node_ids,
        confidence=round(confidence, 4),
        version=community.version_id,
        payload={
            "community_level": community.level,
            "maturity_level": maturity_level,
            "support_score": support_score,
            "metrics": community.metrics,
        },
    )


def _community_document(
    community: GraphIndexCommunity,
    *,
    node_by_id: dict[str, CompiledNode],
) -> GraphIndexVectorDocument:
    relation_types = [str(item) for item in community.metrics.get("relation_types") or [] if item]
    projection_tags = [str(item) for item in community.metrics.get("projection_tags") or [] if item]
    maturity_level = str(community.metrics.get("maturity_level") or "")
    support_score = community.metrics.get("support_score")
    topic_fingerprint_digest = str(community.metrics.get("topic_fingerprint_digest") or "")
    topic_tags = [str(item) for item in community.metrics.get("topic_tags") or [] if item]
    event_type_tags = [str(item) for item in community.metrics.get("event_type_tags") or [] if item]
    impact_tags = [str(item) for item in community.metrics.get("impact_tags") or [] if item]
    risk_tags = [str(item) for item in community.metrics.get("risk_tags") or [] if item]
    narrative_tags = [str(item) for item in community.metrics.get("narrative_tags") or [] if item]
    key_entities = [_node_name(node_id, node_by_id) for node_id in community.member_node_ids[:16]]
    text = _join_parts(
        [
            "Document Type: Community Report",
            f"Community: {community.title}",
            f"Projection: {community.projection}",
            f"Projection Tags: {'；'.join(projection_tags)}" if projection_tags else "",
            f"Community Level: {community.level}",
            f"Lineage: {community.lineage_id or community.community_id}",
            f"Maturity: {maturity_level}" if maturity_level else "",
            f"Support Score: {support_score}" if support_score is not None else "",
            f"Topic Cohesion: {community.metrics.get('topic_cohesion_score')}"
            if community.metrics.get("topic_cohesion_score") is not None
            else "",
            f"Topic Fingerprint: {topic_fingerprint_digest}" if topic_fingerprint_digest else "",
            f"Summary: {community.summary}",
            f"Topic Tags: {'；'.join(topic_tags)}" if topic_tags else "",
            f"Event Type Tags: {'；'.join(event_type_tags)}" if event_type_tags else "",
            f"Impact Tags: {'；'.join(impact_tags)}" if impact_tags else "",
            f"Risk Tags: {'；'.join(risk_tags)}" if risk_tags else "",
            f"Narrative Tags: {'；'.join(narrative_tags)}" if narrative_tags else "",
            f"Key Entities: {'；'.join(key_entities)}",
            f"Relation Families: {'；'.join(relation_types)}",
            f"Cited Evidence: {' '.join(community.evidence_ids[:16])}",
            f"Cited Chunks: {' '.join(community.chunk_ids[:16])}",
            f"Expandable Handles: community_id={community.community_id}",
        ]
    )
    return GraphIndexVectorDocument(
        document_id=community.community_id,
        document_type="community_report",
        collection_role="community",
        source_type="kg_community_report",
        source_id=community.community_id,
        evidence_id=community.evidence_ids[0] if community.evidence_ids else "",
        text=text,
        metadata={
            "community_id": community.community_id,
            "community_version_id": community.version_id,
            "community_title": community.title,
            "community_level": community.level,
            "lineage_id": community.lineage_id,
            "previous_community_ids": community.previous_community_ids,
            "change_reason": community.change_reason,
            "projection": community.projection,
            "parent_community_id": community.parent_community_id,
            "cited_evidence_ids": community.evidence_ids,
            "cited_chunk_ids": community.chunk_ids,
            "edge_ids": community.member_edge_ids,
            "node_ids": community.member_node_ids,
            "metrics": community.metrics,
            "maturity_level": maturity_level,
            "support_score": support_score,
            "topic_fingerprint_digest": topic_fingerprint_digest,
        },
    )


def _finding_document(finding: GraphIndexFinding) -> GraphIndexVectorDocument:
    text = _join_parts(
        [
            "Document Type: Community Finding",
            f"Finding: {finding.title}",
            f"Finding Type: {finding.finding_type}",
            f"Projection: {finding.projection}",
            f"Statement: {finding.statement}",
            f"Source Community: {finding.community_id}",
            f"Cited Evidence: {' '.join(finding.cited_evidence_ids[:16])}",
            f"Cited Chunks: {' '.join(finding.cited_chunk_ids[:16])}",
            f"Expandable Handles: finding_id={finding.finding_id} community_id={finding.community_id}",
        ]
    )
    return GraphIndexVectorDocument(
        document_id=finding.finding_id,
        document_type="finding",
        collection_role="community",
        source_type="kg_finding",
        source_id=finding.finding_id,
        evidence_id=finding.cited_evidence_ids[0] if finding.cited_evidence_ids else "",
        text=text,
        metadata={
            "finding_id": finding.finding_id,
            "finding_title": finding.title,
            "finding_type": finding.finding_type,
            "community_id": finding.community_id,
            "projection": finding.projection,
            "cited_evidence_ids": finding.cited_evidence_ids,
            "cited_chunk_ids": finding.cited_chunk_ids,
            "edge_ids": finding.supporting_edge_ids,
            "node_ids": finding.node_ids,
            "support_count": len(finding.cited_chunk_ids),
            "confidence": finding.confidence,
        },
    )


def build_graph_index_documents(
    *,
    communities: list[GraphIndexCommunity],
    findings: list[GraphIndexFinding],
    deltas: list[GraphIndexDelta] | None = None,
    nodes: list[CompiledNode],
) -> list[GraphIndexVectorDocument]:
    """Build Milvus-ready documents from persisted graph-index objects."""

    node_by_id = {node.node_id: node for node in nodes if _is_retrievable_node(node)}
    documents: list[GraphIndexVectorDocument] = []
    documents.extend(_community_document(community, node_by_id=node_by_id) for community in communities)
    documents.extend(_finding_document(finding) for finding in findings)
    documents.extend(_delta_document(delta) for delta in deltas or [])
    return documents


def resolve_graph_index_lineage(
    *,
    communities: list[GraphIndexCommunity],
    existing_communities: list[GraphIndexCommunity],
) -> list[GraphIndexCommunity]:
    """Assign stable lineage and change events by community member overlap."""

    if not existing_communities:
        return [
            replace(
                community,
                lineage_id=community.lineage_id or f"kg_community_lineage:{_digest([community.community_id])}",
                change_reason=community.change_reason or "new",
            )
            for community in communities
        ]
    existing_by_projection: dict[str, list[GraphIndexCommunity]] = {}
    for existing in existing_communities:
        existing_by_projection.setdefault(existing.projection, []).append(existing)

    best_new_for_old: dict[str, list[str]] = {}
    matched: list[GraphIndexCommunity] = []
    for community in communities:
        candidates = existing_by_projection.get(community.projection, [])
        scored = [
            (existing, _community_similarity(community, existing) * _lineage_level_factor(community.level, existing.level))
            for existing in candidates
        ]
        scored = [(existing, score) for existing, score in scored if score >= 0.34]
        scored.sort(key=lambda item: (-item[1], item[0].community_id))
        if not scored:
            fingerprint_digest = str(community.metrics.get("topic_fingerprint_digest") or "").strip()
            lineage_seed = [community.projection, str(community.level), fingerprint_digest]
            if not fingerprint_digest:
                lineage_seed = [community.projection, str(community.level), *community.member_node_ids]
            matched.append(
                replace(
                    community,
                    lineage_id=f"kg_community_lineage:{_digest(lineage_seed)}",
                    previous_community_ids=[],
                    previous_version_id="",
                    change_reason="new",
                )
            )
            continue
        primary = scored[0][0]
        previous_ids = [existing.community_id for existing, score in scored if score >= 0.34][:6]
        for previous_id in previous_ids:
            best_new_for_old.setdefault(previous_id, []).append(community.community_id)
        change_reason = "continued"
        if len(previous_ids) > 1:
            change_reason = "merge"
        elif primary.title.strip() != community.title.strip():
            change_reason = "rename"
        matched.append(
            replace(
                community,
                lineage_id=primary.lineage_id or f"kg_community_lineage:{_digest([primary.community_id])}",
                previous_community_ids=previous_ids,
                previous_version_id=primary.version_id,
                change_reason=change_reason,
            )
        )

    split_old_ids = {old_id for old_id, new_ids in best_new_for_old.items() if len(set(new_ids)) > 1}
    if not split_old_ids:
        return matched
    result: list[GraphIndexCommunity] = []
    for community in matched:
        if set(community.previous_community_ids).intersection(split_old_ids) and community.change_reason == "continued":
            result.append(replace(community, change_reason="split"))
        else:
            result.append(community)
    return result


def build_rolling_delta_index(
    *,
    communities: list[GraphIndexCommunity],
    findings: list[GraphIndexFinding],
    chunks: list[EvidenceChunk],
    now: datetime | None = None,
) -> list[GraphIndexDelta]:
    """Build rolling 24h/7d/30d delta views from community chunk refs."""

    if not communities:
        return []
    ended_at = now or datetime.now(timezone.utc)
    windows = {
        "rolling_24h": timedelta(hours=24),
        "rolling_7d": timedelta(days=7),
        "rolling_30d": timedelta(days=30),
    }
    chunk_time_by_id = {chunk.chunk_id: _chunk_timestamp(chunk) for chunk in chunks}
    finding_ids_by_community: dict[str, list[str]] = {}
    for finding in findings:
        finding_ids_by_community.setdefault(finding.community_id, []).append(finding.finding_id)

    deltas: list[GraphIndexDelta] = []
    for window_name, duration in windows.items():
        started_at = ended_at - duration
        for community in communities:
            selected_chunk_ids = [
                chunk_id
                for chunk_id in community.chunk_ids
                if (timestamp := chunk_time_by_id.get(chunk_id)) is not None and started_at <= timestamp <= ended_at
            ]
            if not selected_chunk_ids:
                continue
            digest = _digest([community.community_id, window_name, *selected_chunk_ids])
            deltas.append(
                GraphIndexDelta(
                    delta_id=f"kg_delta:{window_name}:{digest}",
                    adapter_name=community.adapter_name,
                    projection=community.projection,
                    window_name=window_name,
                    started_at=started_at,
                    ended_at=ended_at,
                    title=f"{community.title} {window_name} 变化",
                    summary=(
                        f"{community.title} 在 {window_name} 窗口内新增或仍活跃的证据片段 {len(selected_chunk_ids)} 条，"
                        "用于回答近期变化、风险聚集和叙事增强问题。"
                    ),
                    community_ids=[community.community_id],
                    finding_ids=finding_ids_by_community.get(community.community_id, []),
                    cited_chunk_ids=selected_chunk_ids,
                    cited_evidence_ids=community.evidence_ids,
                    supporting_edge_ids=community.member_edge_ids,
                    node_ids=community.member_node_ids,
                    metrics={
                        "window_seconds": int(duration.total_seconds()),
                        "chunk_count": len(selected_chunk_ids),
                        "community_level": community.level,
                        "community_version_id": community.version_id,
                    },
                    version=community.version_id,
                )
            )
    return deltas


def _delta_document(delta: GraphIndexDelta) -> GraphIndexVectorDocument:
    text = _join_parts(
        [
            "Document Type: Rolling Delta",
            f"Delta: {delta.title}",
            f"Window: {delta.window_name}",
            f"Projection: {delta.projection}",
            f"Summary: {delta.summary}",
            f"Communities: {' '.join(delta.community_ids[:10])}",
            f"Findings: {' '.join(delta.finding_ids[:10])}",
            f"Cited Evidence: {' '.join(delta.cited_evidence_ids[:16])}",
            f"Cited Chunks: {' '.join(delta.cited_chunk_ids[:16])}",
            f"Expandable Handles: delta_id={delta.delta_id}",
        ]
    )
    return GraphIndexVectorDocument(
        document_id=delta.delta_id,
        document_type="rolling_delta",
        collection_role="community",
        source_type="kg_rolling_delta",
        source_id=delta.delta_id,
        evidence_id=delta.cited_evidence_ids[0] if delta.cited_evidence_ids else "",
        text=text,
        metadata={
            "delta_id": delta.delta_id,
            "window_name": delta.window_name,
            "projection": delta.projection,
            "started_at": delta.started_at.isoformat(),
            "ended_at": delta.ended_at.isoformat(),
            "community_ids": delta.community_ids,
            "finding_ids": delta.finding_ids,
            "cited_evidence_ids": delta.cited_evidence_ids,
            "cited_chunk_ids": delta.cited_chunk_ids,
            "edge_ids": delta.supporting_edge_ids,
            "node_ids": delta.node_ids,
            "metrics": delta.metrics,
        },
    )


def _community_similarity(left: GraphIndexCommunity, right: GraphIndexCommunity) -> float:
    node_score = _jaccard(left.member_node_ids, right.member_node_ids)
    edge_score = _jaccard(left.member_edge_ids, right.member_edge_ids)
    chunk_score = _jaccard(left.chunk_ids, right.chunk_ids)
    fingerprint_score = _topic_fingerprint_similarity(
        left.metrics.get("topic_fingerprint"),
        right.metrics.get("topic_fingerprint"),
    )
    signal_score = _signal_metric_similarity(left.metrics, right.metrics)
    score = (
        node_score * 0.40
        + edge_score * 0.30
        + chunk_score * 0.10
        + fingerprint_score * 0.15
        + signal_score * 0.05
    )
    left_digest = str(left.metrics.get("topic_fingerprint_digest") or "").strip()
    right_digest = str(right.metrics.get("topic_fingerprint_digest") or "").strip()
    if left_digest and left_digest == right_digest:
        score = max(score, 0.62)
    return round(score, 6)


def _topic_fingerprint_similarity(left: Any, right: Any) -> float:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return 0.0
    field_weights = {
        "core_entities": 0.24,
        "core_relation_types": 0.16,
        "topic_tags": 0.18,
        "event_type_tags": 0.12,
        "impact_tags": 0.12,
        "risk_tags": 0.10,
        "narrative_tags": 0.08,
        "domain_tags": 0.04,
        "governance_tags": 0.02,
    }
    score = 0.0
    for field, weight in field_weights.items():
        score += _jaccard(_fingerprint_values(left, field), _fingerprint_values(right, field)) * weight
    return round(score, 6)


def _fingerprint_values(fingerprint: dict[str, Any], field: str) -> list[str]:
    value = fingerprint.get(field)
    if not isinstance(value, list):
        return []
    return _ordered_unique(str(item).strip().lower() for item in value if str(item or "").strip())


def _signal_metric_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    fields = (
        "topic_tags",
        "event_type_tags",
        "impact_tags",
        "risk_tags",
        "narrative_tags",
        "domain_tags",
        "affected_domains",
        "target_tags",
    )
    scores = [
        _jaccard(_metric_values(left, field, limit=24), _metric_values(right, field, limit=24))
        for field in fields
    ]
    return round(_avg(scores), 6)


def _lineage_level_factor(left_level: int, right_level: int) -> float:
    delta = abs(int(left_level) - int(right_level))
    if delta == 0:
        return 1.0
    if delta == 1:
        return 0.85
    return 0.65


def _jaccard(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _chunk_timestamp(chunk: EvidenceChunk) -> datetime | None:
    payload = chunk.payload if isinstance(chunk.payload, dict) else {}
    for key in ("published_at", "event_time", "source_time", "created_at"):
        value = payload.get(key)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _edge_weight(
    edge: CompiledEdge,
    *,
    node_by_id: dict[str, CompiledNode],
    profile: GraphProjectionProfile,
    support: EdgeSupportStats | None = None,
) -> float:
    relation_weight = profile.relation_weights.get(edge.relation_type, 1.0)
    relation_weight *= _relation_global_weight(edge.relation_type)
    source_node = node_by_id.get(edge.source_node_id)
    target_node = node_by_id.get(edge.target_node_id)
    source_weight = profile.node_type_weights.get(source_node.node_type, 1.0) if source_node else 1.0
    target_weight = profile.node_type_weights.get(target_node.node_type, 1.0) if target_node else 1.0
    support_weight = support.support_weight if support is not None else 1.0 + min(len(edge.evidence_ids), 5) * 0.08
    strength_weight = support.strength if support is not None else _edge_strength(edge)
    confidence_weight = max(0.1, edge.confidence_score)
    generic_penalty = _generic_node_penalty(source_node) * _generic_node_penalty(target_node)
    signal_weight = _edge_signal_weight(edge)
    return round(
        confidence_weight
        * relation_weight
        * ((source_weight + target_weight) / 2)
        * support_weight
        * strength_weight
        * signal_weight
        * generic_penalty,
        6,
    )


def _relation_global_weight(relation_type: str) -> float:
    if relation_type == "mentions":
        return 0.45
    if relation_type == "related_to":
        return 0.85
    if relation_type in {"affects", "benefits_from", "hurt_by", "risk", "negative_impact"}:
        return 1.25
    if relation_type in {"depends_on", "supply_chain", "regulates", "causal_hint", "drives", "constrains"}:
        return 1.15
    return 1.0


def _is_strong_relation(relation_type: str) -> bool:
    return relation_type in {
        "affects",
        "benefits_from",
        "hurt_by",
        "risk",
        "negative_impact",
        "depends_on",
        "supply_chain",
        "regulates",
        "causal_hint",
        "drives",
        "constrains",
    }


def _community_support_score(
    *,
    evidence_count: int,
    source_count: int,
    chunk_count: int,
    edge_count: int,
    non_mentions_edge_count: int,
    strong_edge_count: int,
    avg_edge_support_weight: float,
    signal_count: int,
    topic_cohesion_score: float,
) -> float:
    """Score how well a community is supported, without deciding existence."""

    score = (
        min(evidence_count, 6) * 0.10
        + min(source_count, 4) * 0.12
        + min(chunk_count, 8) * 0.055
        + min(edge_count, 12) * 0.035
        + min(non_mentions_edge_count, 8) * 0.055
        + min(strong_edge_count, 6) * 0.08
        + min(max(avg_edge_support_weight - 1.0, 0.0), 0.8) * 0.16
        + min(signal_count, 16) * 0.012
        + max(0.0, min(topic_cohesion_score, 1.0)) * 0.16
    )
    if source_count <= 1 and evidence_count <= 1 and chunk_count <= 1:
        score = min(score, 0.65)
    elif source_count <= 1 and evidence_count <= 1:
        score = min(score, 0.72)
    if evidence_count >= 2 and topic_cohesion_score < 0.20:
        score = min(score, 0.46)
    return round(min(1.0, max(0.0, score)), 4)


def _community_maturity_level(
    *,
    evidence_count: int,
    source_count: int,
    chunk_count: int,
    edge_count: int,
    non_mentions_edge_count: int,
    strong_edge_count: int,
    support_score: float,
    topic_cohesion_score: float,
) -> str:
    if source_count <= 1 and evidence_count <= 1:
        return "single_source_signal"
    if (
        source_count >= 3
        and evidence_count >= 5
        and chunk_count >= 6
        and strong_edge_count >= 3
        and support_score >= 0.78
        and topic_cohesion_score >= 0.55
    ):
        return "mature_theme"
    if (
        source_count >= 2
        and evidence_count >= 2
        and chunk_count >= 2
        and non_mentions_edge_count >= 2
        and support_score >= 0.48
        and topic_cohesion_score >= 0.35
    ):
        return "stable_multi_source_topic"
    if evidence_count >= 2 or chunk_count >= 2 or strong_edge_count >= 2 or edge_count >= 4 or support_score >= 0.32:
        return "growing_topic"
    return "single_source_signal"


def _edge_signal_weight(edge: CompiledEdge) -> float:
    tags = _edge_signal_values(edge)
    if not tags:
        return 1.0
    return round(min(1.25, 1.0 + min(len(tags), 10) * 0.025), 6)


def _edge_support_stats_by_id(
    edges: list[CompiledEdge],
    *,
    chunks_by_evidence: dict[str, list[EvidenceChunk]],
) -> dict[str, EdgeSupportStats]:
    return {
        edge.edge_id: _edge_support_stats(edge, chunks_by_evidence=chunks_by_evidence)
        for edge in edges
    }


def _edge_support_stats(
    edge: CompiledEdge,
    *,
    chunks_by_evidence: dict[str, list[EvidenceChunk]],
) -> EdgeSupportStats:
    chunks = [
        chunk
        for evidence_id in edge.evidence_ids
        for chunk in chunks_by_evidence.get(evidence_id, [])
    ]
    evidence_count = len(_ordered_unique(edge.evidence_ids))
    chunk_count = len(_ordered_unique(chunk.chunk_id for chunk in chunks))
    source_count = len(_ordered_unique(_source_key(chunk) for chunk in chunks))
    recent_score = _recent_support_score(chunks)
    strength = _edge_strength(edge)
    support_weight = round(
        1.0
        + min(evidence_count, 8) * 0.06
        + min(chunk_count, 12) * 0.035
        + min(source_count, 6) * 0.08
        + recent_score * 0.10,
        6,
    )
    return EdgeSupportStats(
        edge_id=edge.edge_id,
        evidence_count=evidence_count,
        chunk_count=chunk_count,
        source_count=source_count,
        recent_score=recent_score,
        strength=strength,
        support_weight=support_weight,
    )


def _edge_strength(edge: CompiledEdge) -> float:
    for key in ("relationship_strength", "relation_strength", "strength", "weight"):
        value = edge.properties.get(key)
        number = _coerce_float(value)
        if number is None:
            continue
        if number > 1.0:
            number = number / 10.0 if number <= 10.0 else number / 100.0
        return round(min(1.3, max(0.7, 0.75 + number * 0.45)), 6)
    return 1.0


def _recent_support_score(chunks: list[EvidenceChunk]) -> float:
    timestamps = [_chunk_timestamp(chunk) for chunk in chunks]
    timestamps = [item for item in timestamps if item is not None]
    if not timestamps:
        return 0.0
    newest = max(timestamps)
    now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - newest).total_seconds() / 86400)
    if age_days <= 1:
        return 1.0
    if age_days <= 7:
        return 0.75
    if age_days <= 30:
        return 0.45
    if age_days <= 90:
        return 0.20
    return 0.0


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _source_key(chunk: EvidenceChunk) -> str:
    payload = chunk.payload if isinstance(chunk.payload, dict) else {}
    source_type = str(payload.get("source_type") or "").strip()
    source_id = str(payload.get("source_id") or "").strip()
    if source_type or source_id:
        return f"{source_type}:{source_id}"
    return chunk.evidence_id


def _source_keys_for_chunks(
    chunk_ids: list[str],
    *,
    chunks_by_evidence: dict[str, list[EvidenceChunk]],
) -> list[str]:
    wanted = set(chunk_ids)
    return _ordered_unique(
        _source_key(chunk)
        for chunks in chunks_by_evidence.values()
        for chunk in chunks
        if chunk.chunk_id in wanted
    )


def _source_keys_for_edge(
    edge: CompiledEdge,
    *,
    chunks_by_evidence: dict[str, list[EvidenceChunk]],
) -> list[str]:
    return _ordered_unique(
        _source_key(chunk)
        for evidence_id in edge.evidence_ids
        for chunk in chunks_by_evidence.get(evidence_id, [])
    )


def _edge_support_diagnostics(edge_support_by_id: dict[str, EdgeSupportStats]) -> dict[str, Any]:
    stats = list(edge_support_by_id.values())
    return {
        "edges": len(stats),
        "avg_evidence_count": round(_avg([float(item.evidence_count) for item in stats]), 4),
        "avg_chunk_count": round(_avg([float(item.chunk_count) for item in stats]), 4),
        "avg_source_count": round(_avg([float(item.source_count) for item in stats]), 4),
        "avg_recent_score": round(_avg([item.recent_score for item in stats]), 4),
        "avg_support_weight": round(_avg([item.support_weight for item in stats]), 4),
    }


def _community_signal_metrics(edges: list[CompiledEdge]) -> dict[str, Any]:
    field_map = {
        "topic_tags": "topic_tags",
        "impact_tags": "impact_tags",
        "risk_tags": "risk_tags",
        "narrative_tags": "narrative_tags",
        "event_type_tags": "event_type_tags",
        "governance_tags": "governance_tags",
        "target_tags": "target_tags",
        "domain_tags": "domain_tags",
        "affected_entities": "affected_entities",
        "affected_targets": "affected_targets",
        "affected_domains": "affected_domains",
    }
    metrics: dict[str, Any] = {}
    for output_field, property_field in field_map.items():
        values = _ordered_unique(
            value
            for edge in edges
            for value in _edge_property_list(edge, property_field)
        )
        if values:
            metrics[output_field] = values[:24]
    sentiments = _ordered_unique(
        value for edge in edges for value in _edge_signal_scalar_values(edge, {"sentiment", "impact_direction"})
    )
    if sentiments:
        metrics["signal_sentiments"] = sentiments[:12]
    signal_types = _ordered_unique(value for edge in edges for value in _edge_signal_scalar_values(edge, {"signal_type"}))
    if signal_types:
        metrics["signal_types"] = signal_types[:12]
    support_roles = _ordered_unique(value for edge in edges for value in _edge_signal_scalar_values(edge, {"support_role"}))
    if support_roles:
        metrics["support_roles"] = support_roles[:12]
    boundary_strengths = _ordered_unique(value for edge in edges for value in _edge_signal_scalar_values(edge, {"boundary_strength"}))
    if boundary_strengths:
        metrics["boundary_strengths"] = boundary_strengths[:12]
    return metrics


def _community_topic_fingerprint(
    *,
    top_names: list[str],
    relation_types: list[str],
    signal_metrics: dict[str, Any],
    edges: list[CompiledEdge],
    node_by_id: dict[str, CompiledNode],
    chunk_ids: list[str],
    evidence_ids: list[str],
) -> dict[str, Any]:
    return {
        "core_entities": top_names[:12],
        "core_relation_types": relation_types[:12],
        "topic_tags": _metric_values(signal_metrics, "topic_tags", limit=16),
        "event_type_tags": _metric_values(signal_metrics, "event_type_tags", limit=16),
        "impact_tags": _metric_values(signal_metrics, "impact_tags", limit=16),
        "risk_tags": _metric_values(signal_metrics, "risk_tags", limit=16),
        "narrative_tags": _metric_values(signal_metrics, "narrative_tags", limit=16),
        "domain_tags": _ordered_unique(
            [
                *_metric_values(signal_metrics, "domain_tags", limit=16),
                *_metric_values(signal_metrics, "affected_domains", limit=16),
            ]
        )[:16],
        "governance_tags": _ordered_unique(
            value
            for value in [
                *_metric_values(signal_metrics, "governance_tags", limit=16),
                *_governance_node_names(edges, node_by_id=node_by_id),
            ]
            if value
        )[:16],
        "top_edge_descriptions": _top_edge_descriptions(edges, node_by_id=node_by_id, limit=12),
        "representative_chunk_ids": chunk_ids[:12],
        "representative_evidence_ids": evidence_ids[:12],
    }


def _governance_node_names(edges: list[CompiledEdge], *, node_by_id: dict[str, CompiledNode]) -> list[str]:
    values: list[str] = []
    for edge in edges:
        for node_id in (edge.source_node_id, edge.target_node_id):
            node = node_by_id.get(node_id)
            if node is None:
                continue
            taxonomy = str(node.properties.get("taxonomy") or node.node_type).lower()
            if taxonomy in {"governance", "rule"}:
                values.append(_node_name(node_id, node_by_id))
    return _ordered_unique(values)


def _metric_values(metrics: dict[str, Any], field: str, *, limit: int) -> list[str]:
    value = metrics.get(field)
    if not isinstance(value, list):
        return []
    return _ordered_unique(str(item).strip() for item in value if str(item or "").strip())[:limit]


def _top_edge_descriptions(
    edges: list[CompiledEdge],
    *,
    node_by_id: dict[str, CompiledNode],
    limit: int,
) -> list[str]:
    descriptions: list[str] = []
    for edge in sorted(edges, key=lambda item: (-item.confidence_score, item.edge_id))[:limit]:
        description = str(
            edge.properties.get("relationship_description")
            or edge.properties.get("description")
            or ""
        ).strip()
        if not description:
            description = (
                f"{_node_name(edge.source_node_id, node_by_id)} "
                f"{edge.relation_type} "
                f"{_node_name(edge.target_node_id, node_by_id)}"
            )
        descriptions.append(description)
    return _ordered_unique(descriptions)


def _topic_fingerprint_digest(fingerprint: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "core_entities",
        "core_relation_types",
        "topic_tags",
        "event_type_tags",
        "impact_tags",
        "risk_tags",
        "narrative_tags",
        "domain_tags",
        "governance_tags",
    ):
        raw = fingerprint.get(key)
        if isinstance(raw, list):
            values.extend(str(item).strip().lower() for item in raw if str(item or "").strip())
    return _digest(sorted(set(values)))


def _projection_scores(
    edges: list[CompiledEdge],
    *,
    node_by_id: dict[str, CompiledNode],
    signal_metrics: dict[str, Any],
) -> dict[str, float]:
    scores: dict[str, float] = {"default_graph_projection": 1.0 if edges else 0.0}
    for lens in GRAPH_INDEX_LENS_PROFILES:
        matched_weight = sum(
            _edge_lens_score(edge, lens=lens, node_by_id=node_by_id)
            for edge in edges
        )
        structural_score = min(1.0, matched_weight / max(len(edges), 1))
        signal_score = _lens_signal_score(lens.projection, signal_metrics)
        score = round(min(1.0, structural_score * 0.7 + signal_score * 0.3), 4)
        scores[lens.projection] = score
    return scores


def _edge_lens_score(
    edge: CompiledEdge,
    *,
    lens: GraphProjectionProfile,
    node_by_id: dict[str, CompiledNode],
) -> float:
    if not _edge_matches_projection(edge, profile=lens, node_by_id=node_by_id):
        return 0.0
    relation_weight = lens.relation_weights.get(edge.relation_type, 1.0) * _relation_global_weight(edge.relation_type)
    return max(0.0, min(1.0, relation_weight * edge.confidence_score))


def _lens_signal_score(projection: str, metrics: dict[str, Any]) -> float:
    if projection == "risk":
        risk_tags = metrics.get("risk_tags") if isinstance(metrics.get("risk_tags"), list) else []
        sentiments = {str(item).lower() for item in metrics.get("signal_sentiments", [])}
        return min(1.0, len(risk_tags) * 0.25 + (0.45 if sentiments.intersection({"negative", "risk"}) else 0.0))
    if projection == "impact":
        governance_tags = metrics.get("governance_tags") if isinstance(metrics.get("governance_tags"), list) else []
        impact_tags = metrics.get("impact_tags") if isinstance(metrics.get("impact_tags"), list) else []
        return min(1.0, len(governance_tags) * 0.25 + len(impact_tags) * 0.12)
    if projection == "chain":
        domain_tags = metrics.get("domain_tags") if isinstance(metrics.get("domain_tags"), list) else []
        target_tags = metrics.get("target_tags") if isinstance(metrics.get("target_tags"), list) else []
        return min(1.0, len(domain_tags) * 0.18 + len(target_tags) * 0.10)
    if projection == "narrative":
        topic_tags = metrics.get("topic_tags") if isinstance(metrics.get("topic_tags"), list) else []
        narrative_tags = metrics.get("narrative_tags") if isinstance(metrics.get("narrative_tags"), list) else []
        return min(1.0, len(topic_tags) * 0.16 + len(narrative_tags) * 0.18)
    return 0.0


def _edge_signal_values(edge: CompiledEdge) -> list[str]:
    fields = (
        "topic_tags",
        "impact_tags",
        "risk_tags",
        "narrative_tags",
        "event_type_tags",
        "governance_tags",
        "target_tags",
        "domain_tags",
        "affected_entities",
        "affected_targets",
        "affected_domains",
    )
    return _ordered_unique(value for field in fields for value in _edge_property_list(edge, field))


def _edge_property_list(edge: CompiledEdge, field: str) -> list[str]:
    values: list[str] = []
    direct = edge.properties.get(field)
    if isinstance(direct, list):
        values.extend(str(item).strip() for item in direct if str(item or "").strip())
    signals = edge.properties.get("fact_signals")
    if isinstance(signals, list):
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            signal_value = signal.get(field)
            if isinstance(signal_value, list):
                values.extend(str(item).strip() for item in signal_value if str(item or "").strip())
            elif isinstance(signal_value, str) and signal_value.strip():
                values.append(signal_value.strip())
    return _ordered_unique(values)


def _edge_signal_scalar_values(edge: CompiledEdge, fields: set[str]) -> list[str]:
    values: list[str] = []
    for field in fields:
        value = edge.properties.get(field)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    signals = edge.properties.get("fact_signals")
    if isinstance(signals, list):
        for signal in signals:
            if not isinstance(signal, dict):
                continue
            for field in fields:
                value = signal.get(field)
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
    return _ordered_unique(values)


def _edge_boundary_strength(edge: CompiledEdge) -> str:
    values = [item.lower() for item in _edge_signal_scalar_values(edge, {"boundary_strength"})]
    if "strong" in values:
        return "strong"
    if "medium" in values:
        return "medium"
    if "weak" in values:
        return "weak"
    if _is_strong_relation(edge.relation_type):
        return "medium"
    return "weak"


def _edge_support_role(edge: CompiledEdge) -> str:
    values = [item.lower() for item in _edge_signal_scalar_values(edge, {"support_role"})]
    if "core" in values:
        return "core"
    if "context" in values:
        return "context"
    if "mention" in values:
        return "mention"
    if _is_strong_relation(edge.relation_type):
        return "core"
    if edge.relation_type == "mentions":
        return "mention"
    return "context"


def _edge_has_boundary_signal(edge: CompiledEdge) -> bool:
    if _edge_boundary_strength(edge) in {"strong", "medium"} and _edge_support_role(edge) == "core":
        return True
    return bool(
        _ordered_unique(
            value
            for field in ("topic_tags", "event_type_tags", "impact_tags", "risk_tags", "narrative_tags")
            for value in _edge_property_list(edge, field)
        )
    )


def _edge_has_generic_endpoint(edge: CompiledEdge, *, node_by_id: dict[str, CompiledNode]) -> bool:
    return _is_generic_bridge_node(node_by_id.get(edge.source_node_id)) or _is_generic_bridge_node(
        node_by_id.get(edge.target_node_id)
    )


def _edge_is_weak_community_bridge(edge: CompiledEdge, *, node_by_id: dict[str, CompiledNode]) -> bool:
    boundary_strength = _edge_boundary_strength(edge)
    support_role = _edge_support_role(edge)
    if boundary_strength == "strong" and support_role == "core":
        return False
    if _is_strong_relation(edge.relation_type) and boundary_strength != "weak":
        return False
    if edge.relation_type == "mentions":
        return True
    if edge.relation_type == "related_to" and _edge_has_generic_endpoint(edge, node_by_id=node_by_id):
        return not _edge_has_boundary_signal(edge)
    return boundary_strength == "weak" and support_role in {"context", "mention"}


def _is_generic_bridge_node(node: CompiledNode | None) -> bool:
    if node is None:
        return False
    name = node.canonical_name.strip()
    if not name:
        return True
    if name in {
        "市场",
        "政策",
        "行业",
        "公司",
        "企业",
        "投资",
        "风险",
        "机会",
        "产业链",
        "供应链",
        "生态链",
        "新质生产力",
        "价值投资",
        "半导体",
    }:
        return True
    if node.node_type in {"domain", "region", "macro_indicator"}:
        return True
    if node.node_type == "topic" and len(name) <= 4:
        return True
    return False


def _community_topic_cohesion_score(
    edges: list[CompiledEdge],
    *,
    node_by_id: dict[str, CompiledNode],
) -> float:
    evidence_groups: dict[str, list[CompiledEdge]] = {}
    for edge in edges:
        for evidence_id in edge.evidence_ids:
            if evidence_id:
                evidence_groups.setdefault(evidence_id, []).append(edge)
    if len(evidence_groups) <= 1:
        return 1.0

    group_items = sorted(evidence_groups.items())
    pair_scores: list[float] = []
    for index, (_, left_edges) in enumerate(group_items):
        for _, right_edges in group_items[index + 1 :]:
            signal_score = _evidence_signal_similarity(left_edges, right_edges)
            entity_score = _jaccard(
                _evidence_entity_names(left_edges, node_by_id=node_by_id),
                _evidence_entity_names(right_edges, node_by_id=node_by_id),
            )
            relation_score = _jaccard(
                _evidence_boundary_relation_types(left_edges),
                _evidence_boundary_relation_types(right_edges),
            )
            pair_scores.append(signal_score * 0.55 + entity_score * 0.25 + relation_score * 0.20)

    raw_score = _avg(pair_scores)
    weak_ratio = sum(1 for edge in edges if _edge_is_weak_community_bridge(edge, node_by_id=node_by_id)) / max(len(edges), 1)
    generic_ratio = sum(1 for edge in edges if _edge_has_generic_endpoint(edge, node_by_id=node_by_id)) / max(len(edges), 1)
    strong_ratio = sum(1 for edge in edges if _is_strong_relation(edge.relation_type)) / max(len(edges), 1)
    score = raw_score + strong_ratio * 0.12 - weak_ratio * 0.18 - generic_ratio * 0.10
    return round(max(0.0, min(1.0, score)), 4)


def _evidence_signal_similarity(left_edges: list[CompiledEdge], right_edges: list[CompiledEdge]) -> float:
    fields = (
        "topic_tags",
        "event_type_tags",
        "impact_tags",
        "risk_tags",
        "narrative_tags",
        "governance_tags",
        "domain_tags",
        "affected_targets",
        "affected_domains",
    )
    scores = [
        _jaccard(_edge_group_values(left_edges, field), _edge_group_values(right_edges, field))
        for field in fields
    ]
    return round(_avg(scores), 6)


def _edge_group_values(edges: list[CompiledEdge], field: str) -> list[str]:
    return _ordered_unique(value.lower() for edge in edges for value in _edge_property_list(edge, field))


def _evidence_entity_names(edges: list[CompiledEdge], *, node_by_id: dict[str, CompiledNode]) -> list[str]:
    names: list[str] = []
    for edge in edges:
        for node_id in (edge.source_node_id, edge.target_node_id):
            node = node_by_id.get(node_id)
            if node is not None and not _is_generic_bridge_node(node):
                names.append(_node_name(node_id, node_by_id).lower())
    return _ordered_unique(names)


def _evidence_boundary_relation_types(edges: list[CompiledEdge]) -> list[str]:
    return _ordered_unique(edge.relation_type for edge in edges if not _edge_is_weak_community_bridge(edge, node_by_id={}))


def _generic_node_penalty(node: CompiledNode | None) -> float:
    if node is None:
        return 1.0
    name = node.canonical_name.strip()
    if _is_generic_bridge_node(node):
        return 0.6
    if node.node_type in {"topic"} and len(name) <= 2:
        return 0.75
    return 1.0


def _community_summary(
    *,
    title: str,
    profile: GraphProjectionProfile,
    level: int,
    relation_types: list[str],
    edges: list[CompiledEdge],
    node_by_id: dict[str, CompiledNode],
) -> str:
    relation_preview = [
        f"{_node_name(edge.source_node_id, node_by_id)} --{edge.relation_type}--> {_node_name(edge.target_node_id, node_by_id)}"
        for edge in sorted(edges, key=lambda item: (-item.confidence_score, item.edge_id))[:6]
    ]
    signal_metrics = _community_signal_metrics(edges)
    topic_tags = signal_metrics.get("topic_tags") if isinstance(signal_metrics.get("topic_tags"), list) else []
    impact_tags = signal_metrics.get("impact_tags") if isinstance(signal_metrics.get("impact_tags"), list) else []
    risk_tags = signal_metrics.get("risk_tags") if isinstance(signal_metrics.get("risk_tags"), list) else []
    return _join_parts(
        [
            f"{title} 是 {profile.description} 下自动发现的 level {level} community。",
            f"主要关系类型包括：{'、'.join(relation_types[:8])}。" if relation_types else "",
            f"主题信号：{'、'.join(topic_tags[:8])}。" if topic_tags else "",
            f"影响信号：{'、'.join(impact_tags[:8])}。" if impact_tags else "",
            f"风险信号：{'、'.join(risk_tags[:8])}。" if risk_tags else "",
            f"代表关系：{'；'.join(relation_preview)}。" if relation_preview else "",
        ]
    )


def _community_title(
    *,
    level: int,
    top_names: list[str],
    relation_types: list[str],
    signal_metrics: dict[str, Any],
    profile: GraphProjectionProfile,
) -> str:
    if level > 0:
        return " / ".join(top_names[:4]) or profile.projection

    topic_tags = _metric_values(signal_metrics, "topic_tags", limit=8)
    narrative_tags = _metric_values(signal_metrics, "narrative_tags", limit=8)
    event_type_tags = _metric_values(signal_metrics, "event_type_tags", limit=8)
    impact_tags = _metric_values(signal_metrics, "impact_tags", limit=8)
    domain_tags = _ordered_unique(
        [
            *_metric_values(signal_metrics, "domain_tags", limit=8),
            *_metric_values(signal_metrics, "affected_domains", limit=8),
            *_metric_values(signal_metrics, "target_tags", limit=8),
        ]
    )

    broad_topics = [tag for tag in [*topic_tags, *narrative_tags] if _is_broad_l0_title_candidate(tag)]
    if broad_topics:
        return " / ".join(broad_topics[:2])

    domain = _first_broad_title_part(domain_tags)
    mechanism = _first_broad_title_part([*impact_tags, *event_type_tags, *narrative_tags])
    if domain and mechanism:
        return _compact_title_parts([domain, mechanism])
    if mechanism:
        return mechanism
    if domain:
        return domain

    non_event_names = [name for name in top_names if _is_broad_l0_title_candidate(name)]
    if non_event_names:
        return " / ".join(non_event_names[:2])
    return _community_title_from_relations(relation_types) or profile.description or profile.projection


def _first_broad_title_part(values: list[str]) -> str:
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and _is_broad_l0_title_candidate(cleaned):
            return cleaned
    return ""


def _compact_title_parts(parts: list[str]) -> str:
    cleaned = _ordered_unique(parts)
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    left, right = cleaned[0], cleaned[1]
    if left in right:
        return right
    if right in left:
        return left
    return f"{left}{right}" if len(left) + len(right) <= 12 else f"{left} / {right}"


def _is_broad_l0_title_candidate(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if len(text) > 18:
        return False
    if any(marker in text for marker in _L0_SPECIFIC_TITLE_MARKERS):
        return False
    return True


def _community_title_from_relations(relation_types: list[str]) -> str:
    if any(item in {"risk", "negative_impact", "hurt_by", "regulatory_risk"} for item in relation_types):
        return "风险事件聚集"
    if any(item in {"affects", "benefits_from", "drives", "causal_hint"} for item in relation_types):
        return "产业影响链条"
    if any(item in {"depends_on", "supply_chain"} for item in relation_types):
        return "产业链联动"
    return "市场叙事主题"


_L0_SPECIFIC_TITLE_MARKERS = {
    "计划",
    "项目",
    "建厂",
    "签约",
    "订单",
    "中标",
    "公告",
    "业绩",
    "一季度",
    "二季度",
    "三季度",
    "四季度",
    "上半年",
    "下半年",
    "发布",
    "披露",
    "增持",
    "减持",
    "股东",
    "董事",
}


def _top_node_names(
    node_ids: list[str],
    edges: list[CompiledEdge],
    *,
    node_by_id: dict[str, CompiledNode],
) -> list[str]:
    scores: dict[str, float] = {node_id: 0.0 for node_id in node_ids}
    for edge in edges:
        scores[edge.source_node_id] = scores.get(edge.source_node_id, 0.0) + edge.confidence_score
        scores[edge.target_node_id] = scores.get(edge.target_node_id, 0.0) + edge.confidence_score
    sorted_ids = sorted(node_ids, key=lambda node_id: (-scores.get(node_id, 0.0), _node_name(node_id, node_by_id)))
    return _ordered_unique([_node_name(node_id, node_by_id) for node_id in sorted_ids])


def _finding_type(projection: str, relation_types: list[str]) -> str:
    if projection == "risk" or any(item in {"risk", "negative_impact"} for item in relation_types):
        return "risk_concentration"
    if projection == "impact":
        return "impact_driver"
    if projection == "chain":
        return "chain_change"
    if any(item in {"affects", "benefits_from", "causal_hint"} for item in relation_types):
        return "asset_impact"
    return "narrative_strengthening"


def _finding_type_label(finding_type: str) -> str:
    return {
        "risk_concentration": "风险聚集线索",
        "impact_driver": "影响驱动线索",
        "chain_change": "链路变化线索",
        "asset_impact": "影响链条线索",
        "narrative_strengthening": "市场叙事线索",
    }.get(finding_type, "市场叙事线索")


def _chunk_ids_for_evidence(
    evidence_ids: list[str],
    chunks_by_evidence: dict[str, list[EvidenceChunk]],
) -> list[str]:
    return _ordered_unique(
        chunk.chunk_id
        for evidence_id in evidence_ids
        for chunk in chunks_by_evidence.get(evidence_id, [])
    )


def _chunks_by_evidence(chunks: list[EvidenceChunk]) -> dict[str, list[EvidenceChunk]]:
    result: dict[str, list[EvidenceChunk]] = {}
    for chunk in chunks:
        result.setdefault(chunk.evidence_id, []).append(chunk)
    return result


def _unique_edges(edges: list[CompiledEdge]) -> list[CompiledEdge]:
    result: list[CompiledEdge] = []
    seen: set[str] = set()
    for edge in edges:
        if edge.edge_id in seen:
            continue
        seen.add(edge.edge_id)
        result.append(edge)
    return result


def _first_adapter_name(edges: list[CompiledEdge], node_by_id: dict[str, CompiledNode]) -> str:
    if edges:
        return edges[0].adapter_name
    for node in node_by_id.values():
        return node.adapter_name
    return ""


def _is_retrievable_node(node: CompiledNode) -> bool:
    return node.status in {NodeStatus.ACTIVE, NodeStatus.CANDIDATE, NodeStatus.AMBIGUOUS}


def _is_retrievable_edge(edge: CompiledEdge) -> bool:
    return (
        edge.status in {EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE}
        and edge.confidence_label != ConfidenceLabel.REJECTED
        and bool(edge.evidence_ids)
    )


def _is_retrievable_chunk(chunk: EvidenceChunk) -> bool:
    status = str((chunk.payload or {}).get("status") or EvidenceStatus.ACTIVE.value)
    return status == EvidenceStatus.ACTIVE.value


def _node_name(node_id: str, node_by_id: dict[str, CompiledNode]) -> str:
    node = node_by_id.get(node_id)
    return node.canonical_name if node else node_id


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _digest(values: list[str]) -> str:
    return hashlib.sha1("|".join(values).encode("utf-8")).hexdigest()[:16]


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _join_parts(parts: list[str]) -> str:
    return "\n".join(part for part in parts if part and part.strip())
