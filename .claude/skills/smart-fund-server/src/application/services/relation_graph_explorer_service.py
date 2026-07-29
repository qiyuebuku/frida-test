"""Read-only Graph Community explorer use cases."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime
from typing import Any, Literal

from src.infrastructure.connections.database import Target
from src.infrastructure.persistence.repositories.relation_graph_explorer_repository import (
    ExplorerCommunityRecord,
    ExplorerCommunitySnapshot,
    ExplorerCommunityRelationRecord,
    ExplorerEdgeRecord,
    RelationGraphExplorerRepository,
)
from src.infrastructure.vector_store.relation_candidate_store import (
    MilvusRelationCandidateStore,
    RelationCardText,
)


class RelationGraphExplorerService:
    """Builds API-ready graph views without changing graph or cognition state."""

    def __init__(
        self,
        *,
        target: Target = "prod",
        repository: RelationGraphExplorerRepository | Any | None = None,
        card_store: MilvusRelationCandidateStore | Any | None = None,
    ) -> None:
        self.target = target
        self._repository = repository or RelationGraphExplorerRepository(target=target)
        self._card_store = card_store

    async def list_communities(
        self,
        *,
        adapter_name: str = "financial",
        graph_status: str = "active",
        query: str = "",
        sort_by: Literal[
            "edge_count",
            "card_count",
            "relation_count",
            "updated_at",
        ] = "updated_at",
        sort_order: Literal["asc", "desc"] = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        adapter = _required_text(adapter_name, "adapter_name")
        status = _required_text(graph_status, "graph_status")
        total, snapshots = await asyncio.to_thread(
            self._repository.list_communities,
            adapter_name=adapter,
            graph_status=status,
            query=str(query or "").strip(),
            sort_by=sort_by,
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
        return {
            "items": [_community_overview(snapshot) for snapshot in snapshots],
            "total": total,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
        }

    async def get_overview(
        self,
        *,
        adapter_name: str = "financial",
        graph_status: str = "active",
        query: str = "",
        relation_kind: str = "",
        sort_by: Literal[
            "edge_count",
            "card_count",
            "relation_count",
            "updated_at",
        ] = "relation_count",
        sort_order: Literal["asc", "desc"] = "desc",
        limit: int = 0,
        offset: int = 0,
    ) -> dict[str, Any]:
        adapter = _required_text(adapter_name, "adapter_name")
        status = _required_text(graph_status, "graph_status")
        list_kwargs = {
            "adapter_name": adapter,
            "graph_status": status,
            "query": str(query or "").strip(),
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        if limit == 0:
            total, communities = await asyncio.to_thread(
                self._list_all_community_records,
                offset=offset,
                **list_kwargs,
            )
        else:
            total, communities = await asyncio.to_thread(
                self._repository.list_community_records,
                limit=limit,
                offset=offset,
                **list_kwargs,
            )
        community_ids = [
            community.community_id for community in communities
        ]
        relations = await asyncio.to_thread(
            self._repository.list_community_relations,
            community_ids=community_ids,
            relation_kind=str(relation_kind or "").strip(),
        )
        relation_counts = Counter()
        for relation in relations:
            relation_counts[relation.source_community_id] += 1
            relation_counts[relation.target_community_id] += 1

        items = [
            _lightweight_community_overview(community)
            for community in communities
        ]
        representative_ids = [
            item["core_card_id"]
            for item in items
            if item["core_card_id"]
        ]
        summaries = await self._get_card_summaries(
            list(dict.fromkeys(representative_ids)),
            adapter_name=adapter,
        )
        nodes = []
        for item in items:
            summary = summaries.get(item["core_card_id"])
            nodes.append(
                {
                    **item,
                    "community_relation_count": relation_counts[
                        item["community_id"]
                    ],
                    "representative_summary": (
                        summary.text if summary else ""
                    ),
                    "representative_card_id": (
                        summary.card_id if summary else ""
                    ),
                }
            )
        return {
            "nodes": nodes,
            "edges": [
                _community_relation_payload(relation)
                for relation in relations
            ],
            "total": total,
            "visible_community_count": len(nodes),
            "visible_relation_count": len(relations),
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
        }

    def _list_all_community_records(
        self,
        *,
        adapter_name: str,
        graph_status: str,
        query: str,
        sort_by: Literal[
            "edge_count",
            "card_count",
            "relation_count",
            "updated_at",
        ],
        sort_order: Literal["asc", "desc"],
        offset: int,
    ) -> tuple[int, list[ExplorerCommunityRecord]]:
        page_size = 500
        total, communities = self._repository.list_community_records(
            adapter_name=adapter_name,
            graph_status=graph_status,
            query=query,
            sort_by=sort_by,
            sort_order=sort_order,
            limit=page_size,
            offset=offset,
        )
        loaded = len(communities)
        while offset + loaded < total:
            _, page = self._repository.list_community_records(
                adapter_name=adapter_name,
                graph_status=graph_status,
                query=query,
                sort_by=sort_by,
                sort_order=sort_order,
                limit=page_size,
                offset=offset + loaded,
            )
            if not page:
                break
            communities.extend(page)
            loaded += len(page)
        return total, communities

    async def get_community(
        self,
        *,
        community_id: str,
    ) -> dict[str, Any] | None:
        identity = _required_text(community_id, "community_id")
        snapshot = await asyncio.to_thread(
            self._repository.load_community,
            community_id=identity,
        )
        if snapshot is None:
            return None

        card_ids = list(snapshot.community.member_card_ids)
        owns_store = self._card_store is None
        store = self._card_store or await asyncio.to_thread(
            MilvusRelationCandidateStore
        )
        try:
            summaries, focus_evidence = await asyncio.gather(
                store.get_summaries(
                    card_ids,
                    adapter_name=snapshot.community.adapter_name,
                    target=self.target,
                ),
                store.get_focus_evidence(
                    card_ids,
                    adapter_name=snapshot.community.adapter_name,
                    target=self.target,
                ),
            )
        finally:
            if owns_store:
                await asyncio.to_thread(store.store.close)
        return _community_detail(
            snapshot,
            summaries=summaries,
            focus_evidence=focus_evidence,
        )

    async def get_community_relation(
        self,
        *,
        relation_id: str,
        adapter_name: str = "financial",
    ) -> dict[str, Any] | None:
        identity = _required_text(relation_id, "relation_id")
        adapter = _required_text(adapter_name, "adapter_name")
        snapshot = await asyncio.to_thread(
            self._repository.load_community_relation,
            relation_id=identity,
            adapter_name=adapter,
        )
        if snapshot is None:
            return None
        card_ids = sorted(
            {
                card_id
                for edge in snapshot.edges
                for card_id in (
                    edge.source_card_id,
                    edge.target_card_id,
                )
            }
        )
        summaries = await self._get_card_summaries(
            card_ids,
            adapter_name=adapter,
        )
        return {
            "relation": _community_relation_payload(snapshot.relation),
            "supporting_edges": [
                {
                    **_edge_payload(edge, {}),
                    "source_summary": (
                        summaries[edge.source_card_id].text
                        if edge.source_card_id in summaries
                        else ""
                    ),
                    "target_summary": (
                        summaries[edge.target_card_id].text
                        if edge.target_card_id in summaries
                        else ""
                    ),
                }
                for edge in snapshot.edges
            ],
        }

    async def _get_card_summaries(
        self,
        card_ids: list[str],
        *,
        adapter_name: str,
    ) -> dict[str, RelationCardText]:
        if not card_ids:
            return {}
        owns_store = self._card_store is None
        store = self._card_store or await asyncio.to_thread(
            MilvusRelationCandidateStore
        )
        try:
            return await store.get_summaries(
                card_ids,
                adapter_name=adapter_name,
                target=self.target,
            )
        finally:
            if owns_store:
                await asyncio.to_thread(store.store.close)


def create_relation_graph_explorer_service(
    *,
    target: Target = "prod",
) -> RelationGraphExplorerService:
    return RelationGraphExplorerService(target=target)


def _community_overview(snapshot: ExplorerCommunitySnapshot) -> dict[str, Any]:
    community = snapshot.community
    degree = Counter()
    decision_counts = Counter()
    relation_kind_counts = Counter()
    for edge in snapshot.edges:
        degree[edge.source_card_id] += 1
        degree[edge.target_card_id] += 1
        decision_counts[edge.decision_class] += 1
        relation_kind_counts[edge.relation_kind] += 1
    core_card_id = (
        min(degree, key=lambda card_id: (-degree[card_id], card_id))
        if degree
        else community.identity_anchor_card_id
    )
    return {
        "community_id": community.community_id,
        "adapter_name": community.adapter_name,
        "title": community.title,
        "graph_status": community.graph_status,
        "graph_version": community.graph_version,
        "card_count": len(community.member_card_ids),
        "edge_count": len(community.member_edge_ids),
        "loaded_card_count": len(snapshot.cards),
        "loaded_edge_count": len(snapshot.edges),
        "missing_card_manifest_count": max(
            0,
            len(community.member_card_ids) - len(snapshot.cards),
        ),
        "missing_edge_record_count": max(
            0,
            len(community.member_edge_ids) - len(snapshot.edges),
        ),
        "graph_consistent": (
            len(community.member_card_ids) == len(snapshot.cards)
            and len(community.member_edge_ids) == len(snapshot.edges)
        ),
        "observed_edge_count": decision_counts["observed"],
        "inferred_edge_count": decision_counts["inferred"],
        "relation_kind_counts": dict(sorted(relation_kind_counts.items())),
        "identity_anchor_card_id": community.identity_anchor_card_id,
        "core_card_id": core_card_id,
        "core_card_degree": int(degree.get(core_card_id, 0)),
        "fact_report_status": community.fact_report_status,
        "fact_report_version": community.fact_report_version,
        "projection_status": community.projection_status,
        "projection_version": community.projection_version,
        "projection_count": len(community.conditional_projections),
        "graph_changed_at": community.graph_changed_at,
        "updated_at": community.updated_at,
    }


def _lightweight_community_overview(
    community: ExplorerCommunityRecord,
) -> dict[str, Any]:
    return {
        "community_id": community.community_id,
        "adapter_name": community.adapter_name,
        "title": community.title,
        "graph_status": community.graph_status,
        "graph_version": community.graph_version,
        "card_count": len(community.member_card_ids),
        "edge_count": len(community.member_edge_ids),
        "identity_anchor_card_id": community.identity_anchor_card_id,
        "core_card_id": community.identity_anchor_card_id,
        "fact_report_status": community.fact_report_status,
        "fact_report_version": community.fact_report_version,
        "projection_status": community.projection_status,
        "projection_version": community.projection_version,
        "projection_count": len(community.conditional_projections),
        "graph_changed_at": community.graph_changed_at,
        "updated_at": community.updated_at,
    }


def _community_relation_payload(
    relation: ExplorerCommunityRelationRecord,
) -> dict[str, Any]:
    return {
        "relation_id": relation.relation_id,
        "source": relation.source_community_id,
        "target": relation.target_community_id,
        "relation_kind": relation.relation_kind,
        "supporting_edge_ids": list(relation.supporting_edge_ids),
        "supporting_edge_count": len(relation.supporting_edge_ids),
        "observed_edge_count": relation.observed_edge_count,
        "inferred_edge_count": relation.inferred_edge_count,
        "relation_fingerprint": relation.relation_fingerprint,
        "status": relation.status,
        "created_at": relation.created_at,
        "updated_at": relation.updated_at,
    }


def _community_detail(
    snapshot: ExplorerCommunitySnapshot,
    *,
    summaries: dict[str, RelationCardText],
    focus_evidence: dict[str, RelationCardText],
) -> dict[str, Any]:
    overview = _community_overview(snapshot)
    community = snapshot.community
    card_by_id = {card.card_id: card for card in snapshot.cards}
    degrees = _degree_metrics(snapshot.edges)

    nodes: list[dict[str, Any]] = []
    published_values: list[str] = []
    for card_id in community.member_card_ids:
        card = card_by_id.get(card_id)
        summary_hit = summaries.get(card_id)
        focus_hit = focus_evidence.get(card_id)
        metadata = dict(summary_hit.metadata) if summary_hit else {}
        published_at = str(
            metadata.get("source_published_at")
            or metadata.get("published_at")
            or ""
        ).strip()
        if published_at:
            published_values.append(published_at)
        metrics = degrees.get(card_id, {"degree": 0, "in_degree": 0, "out_degree": 0})
        nodes.append(
            {
                "card_id": card_id,
                "summary": summary_hit.text if summary_hit else "",
                "focus_evidence": focus_hit.text if focus_hit else "",
                "source_type": (
                    card.source_type
                    if card
                    else str(metadata.get("original_source_type") or "")
                ),
                "source_id": (
                    card.source_id
                    if card
                    else str(metadata.get("original_source_id") or "")
                ),
                "evidence_id": (
                    card.evidence_id
                    if card
                    else str(metadata.get("evidence_id") or "")
                ),
                "primary_chunk_id": (
                    card.primary_chunk_id
                    if card
                    else str(metadata.get("primary_chunk_id") or "")
                ),
                "chunk_ids": (
                    list(card.chunk_ids)
                    if card
                    else list(metadata.get("cited_chunk_ids") or [])
                ),
                "focus_evidence_refs": (
                    list(card.focus_evidence_refs) if card else []
                ),
                "relation_probes": (
                    list(card.relation_probes) if card else []
                ),
                "source_published_at": published_at,
                "chunk_summary": str(metadata.get("chunk_summary") or ""),
                "degree": metrics["degree"],
                "in_degree": metrics["in_degree"],
                "out_degree": metrics["out_degree"],
                "is_identity_anchor": card_id == community.identity_anchor_card_id,
                "is_core": card_id == overview["core_card_id"],
                "content_available": bool(summary_hit),
                "focus_evidence_available": bool(focus_hit),
            }
        )

    edges = [_edge_payload(edge, card_by_id) for edge in snapshot.edges]
    n = len(nodes)
    undirected_pairs = {
        tuple(sorted((edge.source_card_id, edge.target_card_id)))
        for edge in snapshot.edges
    }
    density = (
        round((2 * len(undirected_pairs)) / (n * (n - 1)), 6)
        if n > 1
        else 0.0
    )
    overview["source_published_at_start"] = min(published_values, default="")
    overview["source_published_at_end"] = max(published_values, default="")
    overview["graph_density"] = density
    overview["missing_card_content_count"] = sum(
        not node["content_available"] for node in nodes
    )
    overview["missing_focus_evidence_count"] = sum(
        not node["focus_evidence_available"] for node in nodes
    )

    return {
        "community": {
            **overview,
            "graph_fingerprint": community.graph_fingerprint,
            "graph_changed_at": community.graph_changed_at,
            "created_at": community.created_at,
            "updated_at": community.updated_at,
            "fact_report": community.fact_report,
            "fact_report_generated_at": community.fact_report_generated_at,
            "conditional_projections": list(community.conditional_projections),
            "projection_generated_at": community.projection_generated_at,
        },
        "nodes": nodes,
        "edges": edges,
    }


def _degree_metrics(
    edges: tuple[ExplorerEdgeRecord, ...],
) -> dict[str, dict[str, int]]:
    metrics: dict[str, dict[str, int]] = {}
    for edge in edges:
        source = metrics.setdefault(
            edge.source_card_id,
            {"degree": 0, "in_degree": 0, "out_degree": 0},
        )
        target = metrics.setdefault(
            edge.target_card_id,
            {"degree": 0, "in_degree": 0, "out_degree": 0},
        )
        source["degree"] += 1
        source["out_degree"] += 1
        target["degree"] += 1
        target["in_degree"] += 1
    return metrics


def _edge_payload(
    edge: ExplorerEdgeRecord,
    card_by_id: dict[str, Any],
) -> dict[str, Any]:
    source = card_by_id.get(edge.source_card_id)
    target = card_by_id.get(edge.target_card_id)
    source_chunk = source.primary_chunk_id if source else ""
    target_chunk = target.primary_chunk_id if target else ""
    return {
        "edge_id": edge.edge_id,
        "source": edge.source_card_id,
        "target": edge.target_card_id,
        "relation_kind": edge.relation_kind,
        "relation_type": edge.relation_type,
        "direction": edge.direction,
        "decision_class": edge.decision_class,
        "basis": edge.basis,
        "source_evidence_refs": list(edge.source_evidence_refs),
        "target_evidence_refs": list(edge.target_evidence_refs),
        "relation_evidence_refs": list(edge.relation_evidence_refs),
        "inference_mechanism": edge.inference_mechanism,
        "confidence": edge.confidence,
        "source_primary_chunk_id": source_chunk,
        "target_primary_chunk_id": target_chunk,
        "cross_chunk": bool(
            source_chunk and target_chunk and source_chunk != target_chunk
        ),
        "created_at": edge.created_at,
        "updated_at": edge.updated_at,
    }


def _required_text(value: str, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} 不能为空")
    return cleaned
