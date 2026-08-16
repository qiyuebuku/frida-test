"""Relationship-first flat Graph Community domain contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import networkx as nx
from graspologic_native import leiden

from src.domain.knowledge.card_relation import (
    RELATION_KINDS,
    SYMMETRIC_RELATION_KINDS,
)


RELATION_GRAPH_COMMUNITY_SCHEMA_VERSION = "relation_graph_community_v2"
RELATION_GRAPH_COMMUNITY_RELATION_SCHEMA_VERSION = (
    "relation_graph_community_relation_v1"
)


@dataclass(frozen=True)
class RelationGraphEdge:
    edge_id: str
    source_card_id: str
    target_card_id: str
    relation_kind: str
    decision_class: str
    content_version: str


@dataclass(frozen=True)
class ExistingRelationGraphCommunity:
    community_id: str
    identity_anchor_card_id: str
    member_card_ids: tuple[str, ...]


@dataclass(frozen=True)
class RelationGraphClusteringConfig:
    node_threshold: int = 40
    edge_threshold: int = 80
    resolution: float = 1.0
    randomness: float = 0.001
    iterations: int = 2
    trials: int = 3
    seed: int = 42
    min_modularity: float = 0.05
    max_cross_edge_ratio: float = 0.35


@dataclass(frozen=True)
class RelationGraphCommunityComponent:
    community_id: str
    identity_anchor_card_id: str
    member_card_ids: tuple[str, ...]
    member_edge_ids: tuple[str, ...]
    graph_fingerprint: str


@dataclass(frozen=True)
class RelationGraphCommunityRelation:
    relation_id: str
    source_community_id: str
    target_community_id: str
    relation_kind: str
    supporting_edge_ids: tuple[str, ...]
    observed_edge_count: int
    inferred_edge_count: int
    relation_fingerprint: str


@dataclass(frozen=True)
class RelationGraphPartition:
    communities: tuple[RelationGraphCommunityComponent, ...]
    community_relations: tuple[RelationGraphCommunityRelation, ...]
    connected_region_count: int
    clustered_region_count: int
    retained_region_count: int


@dataclass(frozen=True)
class AffectedRelationGraph:
    adapter_name: str
    seed_card_ids: tuple[str, ...]
    touched_community_ids: tuple[str, ...]
    edges: tuple[RelationGraphEdge, ...]
    rejected_edge_ids: tuple[str, ...] = ()
    existing_communities: tuple[ExistingRelationGraphCommunity, ...] = ()


def project_edges_to_fact_representatives(
    edges: list[RelationGraphEdge],
    *,
    representative_by_card_id: dict[str, str],
) -> list[RelationGraphEdge]:
    """Collapse equivalent-fact endpoints while retaining all business relations."""

    projected: list[RelationGraphEdge] = []
    for edge in edges:
        if edge.relation_kind == "same_fact":
            continue
        source_card_id = representative_by_card_id.get(
            edge.source_card_id,
            edge.source_card_id,
        )
        target_card_id = representative_by_card_id.get(
            edge.target_card_id,
            edge.target_card_id,
        )
        if source_card_id == target_card_id:
            continue
        projected.append(
            RelationGraphEdge(
                edge_id=edge.edge_id,
                source_card_id=source_card_id,
                target_card_id=target_card_id,
                relation_kind=edge.relation_kind,
                decision_class=edge.decision_class,
                content_version=edge.content_version,
            )
        )
    return projected


def relation_graph_community_id(adapter_name: str, anchor_card_id: str) -> str:
    adapter = str(adapter_name or "").strip()
    anchor = str(anchor_card_id or "").strip()
    if not adapter or not anchor:
        raise ValueError("Graph Community adapter 和 identity anchor 不能为空")
    digest = hashlib.sha256(anchor.encode("utf-8")).hexdigest()[:20]
    return f"kgc:{adapter}:relation:{digest}"


def discover_relation_graph_components(
    affected_graph: AffectedRelationGraph,
    *,
    config: RelationGraphClusteringConfig | None = None,
) -> list[RelationGraphCommunityComponent]:
    """Compatibility wrapper returning the flat Community partition."""

    return list(
        discover_relation_graph_partition(
            affected_graph,
            config=config,
        ).communities
    )


def discover_relation_graph_partition(
    affected_graph: AffectedRelationGraph,
    *,
    config: RelationGraphClusteringConfig | None = None,
) -> RelationGraphPartition:
    """Partition active verified Edge regions and derive cross-Community relations."""

    clustering = config or RelationGraphClusteringConfig()
    _validate_clustering_config(clustering)
    graph = nx.Graph()
    edge_by_id: dict[str, RelationGraphEdge] = {}
    for edge in affected_graph.edges:
        _validate_graph_edge(edge)
        if graph.has_edge(edge.source_card_id, edge.target_card_id):
            graph[edge.source_card_id][edge.target_card_id]["weight"] += 1.0
        else:
            graph.add_edge(
                edge.source_card_id,
                edge.target_card_id,
                weight=1.0,
            )
        edge_by_id[edge.edge_id] = edge

    regions = [
        set(member_cards)
        for member_cards in nx.connected_components(graph)
        if len(member_cards) >= 2
    ]
    partitions: list[set[str]] = []
    existing_label_by_card = {
        card_id: index
        for index, community in enumerate(
            sorted(
                affected_graph.existing_communities,
                key=lambda item: item.community_id,
            )
        )
        for card_id in community.member_card_ids
    }
    clustered_region_count = 0
    retained_region_count = 0
    for member_cards in sorted(regions, key=lambda item: min(item)):
        edge_ids = tuple(
            sorted(
                edge_id
                for edge_id, edge in edge_by_id.items()
                if edge.source_card_id in member_cards
                and edge.target_card_id in member_cards
            )
        )
        if not edge_ids:
            continue
        region_graph = _canonical_subgraph(
            graph,
            member_cards=member_cards,
        )
        region_partitions = _partition_connected_region(
            region_graph,
            edge_count=len(edge_ids),
            config=clustering,
            starting_communities=_starting_communities_for_region(
                member_cards,
                existing_label_by_card=existing_label_by_card,
            ),
        )
        if len(region_partitions) > 1:
            clustered_region_count += 1
        else:
            retained_region_count += 1
        partitions.extend(region_partitions)

    assigned_identities = _assign_stable_community_identities(
        partitions,
        existing_communities=affected_graph.existing_communities,
        adapter_name=affected_graph.adapter_name,
    )
    components: list[RelationGraphCommunityComponent] = []
    for member_cards, community_id, identity_anchor in assigned_identities:
        card_ids = tuple(sorted(member_cards))
        internal_edge_ids = tuple(
            sorted(
                edge_id
                for edge_id, edge in edge_by_id.items()
                if edge.source_card_id in member_cards
                and edge.target_card_id in member_cards
            )
        )
        if not internal_edge_ids:
            continue
        components.append(
            RelationGraphCommunityComponent(
                community_id=community_id,
                identity_anchor_card_id=identity_anchor,
                member_card_ids=card_ids,
                member_edge_ids=internal_edge_ids,
                graph_fingerprint=_graph_fingerprint(
                    card_ids=card_ids,
                    edges=[
                        edge_by_id[edge_id] for edge_id in internal_edge_ids
                    ],
                ),
            )
        )
    components.sort(
        key=lambda item: (
            item.identity_anchor_card_id,
            item.community_id,
        )
    )
    community_relations = _derive_community_relations(
        components=components,
        edges=edge_by_id.values(),
    )
    return RelationGraphPartition(
        communities=tuple(components),
        community_relations=tuple(community_relations),
        connected_region_count=len(regions),
        clustered_region_count=clustered_region_count,
        retained_region_count=retained_region_count,
    )


def relation_graph_community_relation_id(
    source_community_id: str,
    target_community_id: str,
    relation_kind: str,
) -> str:
    payload = {
        "source_community_id": source_community_id,
        "target_community_id": target_community_id,
        "relation_kind": relation_kind,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"kgcr:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _partition_connected_region(
    graph: nx.Graph,
    *,
    edge_count: int,
    config: RelationGraphClusteringConfig,
    starting_communities: dict[str, int] | None = None,
) -> list[set[str]]:
    nodes = set(graph.nodes)
    if (
        len(nodes) < config.node_threshold
        and edge_count < config.edge_threshold
    ):
        return [nodes]

    weighted_edges = [
        (
            source,
            target,
            float(graph[source][target].get("weight") or 1.0),
        )
        for source, target in sorted(
            tuple(sorted((str(source), str(target))))
            for source, target in graph.edges()
        )
    ]
    initial_communities = starting_communities or {
        node_id: index
        for index, node_id in enumerate(sorted(str(node) for node in nodes))
    }
    _, assignments = leiden(
        weighted_edges,
        starting_communities=initial_communities,
        resolution=config.resolution,
        randomness=config.randomness,
        iterations=config.iterations,
        use_modularity=True,
        seed=config.seed,
        trials=config.trials,
    )
    grouped: dict[int, set[str]] = {}
    for node_id in sorted(nodes):
        grouped.setdefault(int(assignments[node_id]), set()).add(node_id)
    candidate = sorted(grouped.values(), key=lambda item: min(item))
    if not _accept_candidate_partition(
        graph,
        candidate,
        config=config,
    ):
        return [nodes]

    partitions: list[set[str]] = []
    for members in candidate:
        child_graph = _canonical_subgraph(
            graph,
            member_cards=members,
        )
        partitions.extend(
            _partition_connected_region(
                child_graph,
                edge_count=_weighted_edge_count(child_graph),
                config=config,
                starting_communities=None,
            )
        )
    return partitions


def _starting_communities_for_region(
    member_cards: set[str],
    *,
    existing_label_by_card: dict[str, int],
) -> dict[str, int]:
    labels = {
        card_id: existing_label_by_card[card_id]
        for card_id in member_cards
        if card_id in existing_label_by_card
    }
    next_label = max(labels.values(), default=-1) + 1
    for card_id in sorted(member_cards - set(labels)):
        labels[card_id] = next_label
        next_label += 1
    return labels


def _canonical_subgraph(
    graph: nx.Graph,
    *,
    member_cards: set[str],
) -> nx.Graph:
    canonical = nx.Graph()
    canonical.add_nodes_from(sorted(member_cards))
    edge_pairs = sorted(
        tuple(sorted((str(source), str(target))))
        for source, target in graph.subgraph(member_cards).edges()
    )
    for source, target in edge_pairs:
        canonical.add_edge(
            source,
            target,
            weight=float(
                graph[source][target].get("weight") or 1.0
            ),
        )
    return canonical


def _weighted_edge_count(graph: nx.Graph) -> int:
    return int(
        round(
            sum(
                float(data.get("weight") or 1.0)
                for _, _, data in graph.edges(data=True)
            )
        )
    )


def _accept_candidate_partition(
    graph: nx.Graph,
    candidate: list[set[str]],
    *,
    config: RelationGraphClusteringConfig,
) -> bool:
    if len(candidate) < 2 or any(len(members) < 2 for members in candidate):
        return False
    if any(
        graph.subgraph(members).number_of_edges() == 0
        or not nx.is_connected(graph.subgraph(members))
        for members in candidate
    ):
        return False
    membership = {
        node_id: index
        for index, members in enumerate(candidate)
        for node_id in members
    }
    cross_weight = sum(
        float(data.get("weight") or 1.0)
        for source, target, data in graph.edges(data=True)
        if membership[source] != membership[target]
    )
    total_weight = sum(
        float(data.get("weight") or 1.0)
        for _, _, data in graph.edges(data=True)
    )
    if total_weight <= 0:
        return False
    if cross_weight / total_weight > config.max_cross_edge_ratio:
        return False
    modularity = nx.community.modularity(
        graph,
        candidate,
        weight="weight",
        resolution=config.resolution,
    )
    return modularity >= config.min_modularity


def _assign_stable_community_identities(
    partitions: list[set[str]],
    *,
    existing_communities: tuple[ExistingRelationGraphCommunity, ...],
    adapter_name: str,
) -> list[tuple[set[str], str, str]]:
    assignments: dict[int, ExistingRelationGraphCommunity] = {}
    used_existing_ids: set[str] = set()
    candidates: list[
        tuple[int, float, int, str, int, ExistingRelationGraphCommunity]
    ] = []
    for index, members in enumerate(partitions):
        for existing in existing_communities:
            previous_members = set(existing.member_card_ids)
            intersection = len(members & previous_members)
            if intersection == 0:
                continue
            union = len(members | previous_members)
            candidates.append(
                (
                    int(existing.identity_anchor_card_id in members),
                    intersection / union if union else 0.0,
                    intersection,
                    existing.community_id,
                    index,
                    existing,
                )
            )
    for _, _, _, _, index, existing in sorted(
        candidates,
        key=lambda item: (
            -item[0],
            -item[1],
            -item[2],
            item[3],
            item[4],
        ),
    ):
        if index in assignments or existing.community_id in used_existing_ids:
            continue
        assignments[index] = existing
        used_existing_ids.add(existing.community_id)

    result: list[tuple[set[str], str, str]] = []
    for index, members in enumerate(partitions):
        existing = assignments.get(index)
        if existing is not None:
            anchor = (
                existing.identity_anchor_card_id
                if existing.identity_anchor_card_id in members
                else min(members)
            )
            result.append((members, existing.community_id, anchor))
            continue
        anchor = min(members)
        result.append(
            (
                members,
                relation_graph_community_id(adapter_name, anchor),
                anchor,
            )
        )
    return result


def _derive_community_relations(
    *,
    components: list[RelationGraphCommunityComponent],
    edges,
) -> list[RelationGraphCommunityRelation]:
    community_by_card = {
        card_id: component.community_id
        for component in components
        for card_id in component.member_card_ids
    }
    return derive_community_relations_from_membership(
        edges=edges,
        community_by_card=community_by_card,
    )


def derive_community_relations_from_membership(
    *,
    edges,
    community_by_card: dict[str, str],
) -> list[RelationGraphCommunityRelation]:
    """Aggregate cross-Community relations from normalized membership."""

    grouped: dict[tuple[str, str, str], list[RelationGraphEdge]] = {}
    for edge in edges:
        source_community_id = community_by_card.get(edge.source_card_id)
        target_community_id = community_by_card.get(edge.target_card_id)
        if (
            not source_community_id
            or not target_community_id
            or source_community_id == target_community_id
        ):
            continue
        if edge.relation_kind in SYMMETRIC_RELATION_KINDS:
            source_community_id, target_community_id = sorted(
                (source_community_id, target_community_id)
            )
        grouped.setdefault(
            (
                source_community_id,
                target_community_id,
                edge.relation_kind,
            ),
            [],
        ).append(edge)

    relations: list[RelationGraphCommunityRelation] = []
    for key, supporting_edges in sorted(grouped.items()):
        source_community_id, target_community_id, relation_kind = key
        edge_ids = tuple(sorted(edge.edge_id for edge in supporting_edges))
        fingerprint_payload = {
            "schema_version": RELATION_GRAPH_COMMUNITY_RELATION_SCHEMA_VERSION,
            "source_community_id": source_community_id,
            "target_community_id": target_community_id,
            "relation_kind": relation_kind,
            "edges": [
                {
                    "edge_id": edge.edge_id,
                    "decision_class": edge.decision_class,
                    "content_version": edge.content_version,
                }
                for edge in sorted(
                    supporting_edges,
                    key=lambda item: item.edge_id,
                )
            ],
        }
        raw = json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        relations.append(
            RelationGraphCommunityRelation(
                relation_id=relation_graph_community_relation_id(*key),
                source_community_id=source_community_id,
                target_community_id=target_community_id,
                relation_kind=relation_kind,
                supporting_edge_ids=edge_ids,
                observed_edge_count=sum(
                    edge.decision_class == "observed"
                    for edge in supporting_edges
                ),
                inferred_edge_count=sum(
                    edge.decision_class == "inferred"
                    for edge in supporting_edges
                ),
                relation_fingerprint=hashlib.sha256(
                    raw.encode("utf-8")
                ).hexdigest(),
            )
        )
    return relations


def _validate_clustering_config(
    config: RelationGraphClusteringConfig,
) -> None:
    if config.node_threshold < 2 or config.edge_threshold < 1:
        raise ValueError("Graph Community 聚类阈值非法")
    if config.resolution <= 0 or config.iterations < 1 or config.trials < 1:
        raise ValueError("Graph Community Leiden 参数非法")
    if not 0 <= config.min_modularity <= 1:
        raise ValueError("Graph Community min_modularity 必须位于 [0, 1]")
    if not 0 <= config.max_cross_edge_ratio <= 1:
        raise ValueError(
            "Graph Community max_cross_edge_ratio 必须位于 [0, 1]"
        )


def _validate_graph_edge(edge: RelationGraphEdge) -> None:
    if not edge.edge_id:
        raise ValueError("Graph Community Edge ID 不能为空")
    if not edge.source_card_id or not edge.target_card_id:
        raise ValueError(f"Graph Community Edge 端点不能为空: {edge.edge_id}")
    if edge.source_card_id == edge.target_card_id:
        raise ValueError(f"Graph Community Edge 不能自连接: {edge.edge_id}")
    if edge.relation_kind not in RELATION_KINDS:
        raise ValueError(
            f"Graph Community Edge relation_kind 非法: {edge.edge_id}={edge.relation_kind}"
        )
    if edge.decision_class not in {"observed", "inferred"}:
        raise ValueError(
            f"Graph Community Edge decision_class 非法: "
            f"{edge.edge_id}={edge.decision_class}"
        )
    if not edge.content_version:
        raise ValueError(f"Graph Community Edge content_version 不能为空: {edge.edge_id}")


def _graph_fingerprint(
    *,
    card_ids: tuple[str, ...],
    edges: list[RelationGraphEdge],
) -> str:
    payload = {
        "schema_version": RELATION_GRAPH_COMMUNITY_SCHEMA_VERSION,
        "card_ids": list(card_ids),
        "edges": [
            {
                "edge_id": edge.edge_id,
                "source_card_id": edge.source_card_id,
                "target_card_id": edge.target_card_id,
                "relation_kind": edge.relation_kind,
                "decision_class": edge.decision_class,
                "content_version": edge.content_version,
            }
            for edge in sorted(edges, key=lambda item: item.edge_id)
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
