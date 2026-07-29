"""Read models for the relationship-first Graph Community explorer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import asc, desc, func, or_, select

from src.domain.knowledge.card_fact import default_card_fact_id
from src.infrastructure.connections import get_session
from src.infrastructure.connections.database import Target
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCardRelation,
    KnowledgeCognitiveCard,
    KnowledgeGraphCommunity,
    KnowledgeGraphCommunityRelation,
)


@dataclass(frozen=True)
class ExplorerCommunityRecord:
    community_id: str
    adapter_name: str
    identity_anchor_card_id: str
    member_card_ids: tuple[str, ...]
    member_edge_ids: tuple[str, ...]
    graph_fingerprint: str
    graph_version: int
    graph_status: str
    title: str
    fact_report: str
    fact_report_version: int
    fact_report_status: str
    conditional_projections: tuple[dict, ...]
    projection_version: int
    projection_status: str
    graph_changed_at: datetime | None
    fact_report_generated_at: datetime | None
    projection_generated_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ExplorerCardRecord:
    card_id: str
    source_type: str
    source_id: str
    evidence_id: str
    primary_chunk_id: str
    chunk_ids: tuple[str, ...]
    focus_evidence_refs: tuple[str, ...]
    relation_probes: tuple[dict, ...]
    status: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ExplorerEdgeRecord:
    edge_id: str
    source_card_id: str
    target_card_id: str
    relation_kind: str
    relation_type: str
    direction: str
    decision_class: str
    basis: str
    source_evidence_refs: tuple[str, ...]
    target_evidence_refs: tuple[str, ...]
    relation_evidence_refs: tuple[str, ...]
    inference_mechanism: str
    confidence: float
    status: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ExplorerCommunitySnapshot:
    community: ExplorerCommunityRecord
    cards: tuple[ExplorerCardRecord, ...]
    edges: tuple[ExplorerEdgeRecord, ...]


@dataclass(frozen=True)
class ExplorerCommunityRelationRecord:
    relation_id: str
    source_community_id: str
    target_community_id: str
    relation_kind: str
    supporting_edge_ids: tuple[str, ...]
    observed_edge_count: int
    inferred_edge_count: int
    relation_fingerprint: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ExplorerCommunityRelationSnapshot:
    relation: ExplorerCommunityRelationRecord
    edges: tuple[ExplorerEdgeRecord, ...]


class RelationGraphExplorerRepository:
    """Loads current Community, Card manifests, and verified Edges for inspection."""

    def __init__(self, *, target: Target = "prod") -> None:
        self.target = target

    def list_communities(
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
        limit: int,
        offset: int,
    ) -> tuple[int, list[ExplorerCommunitySnapshot]]:
        filters = [
            KnowledgeGraphCommunity.adapter_name == adapter_name,
            KnowledgeGraphCommunity.graph_status == graph_status,
        ]
        if query:
            pattern = f"%{query}%"
            filters.append(
                or_(
                    KnowledgeGraphCommunity.community_id.ilike(pattern),
                    KnowledgeGraphCommunity.title.ilike(pattern),
                    KnowledgeGraphCommunity.fact_report.ilike(pattern),
                )
            )

        with get_session(self.target) as session:
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeGraphCommunity)
                    .where(*filters)
                )
                or 0
            )
            communities = list(
                session.scalars(
                    select(KnowledgeGraphCommunity)
                    .where(*filters)
                    .order_by(*_community_order_by(sort_by, sort_order))
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
            return total, self._load_snapshots(session, communities)

    def list_community_records(
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
        limit: int,
        offset: int,
    ) -> tuple[int, list[ExplorerCommunityRecord]]:
        filters = [
            KnowledgeGraphCommunity.adapter_name == adapter_name,
            KnowledgeGraphCommunity.graph_status == graph_status,
        ]
        if query:
            pattern = f"%{query}%"
            filters.append(
                or_(
                    KnowledgeGraphCommunity.community_id.ilike(pattern),
                    KnowledgeGraphCommunity.title.ilike(pattern),
                    KnowledgeGraphCommunity.fact_report.ilike(pattern),
                )
            )

        with get_session(self.target) as session:
            total = int(
                session.scalar(
                    select(func.count())
                    .select_from(KnowledgeGraphCommunity)
                    .where(*filters)
                )
                or 0
            )
            communities = list(
                session.scalars(
                    select(KnowledgeGraphCommunity)
                    .where(*filters)
                    .order_by(*_community_order_by(sort_by, sort_order))
                    .offset(offset)
                    .limit(limit)
                ).all()
            )
        return total, [_community_record(row) for row in communities]

    def list_community_relations(
        self,
        *,
        community_ids: list[str],
        relation_kind: str = "",
    ) -> list[ExplorerCommunityRelationRecord]:
        identities = sorted(
            {
                str(community_id).strip()
                for community_id in community_ids
                if str(community_id).strip()
            }
        )
        if not identities:
            return []
        filters = [
            KnowledgeGraphCommunityRelation.status == "active",
            KnowledgeGraphCommunityRelation.source_community_id.in_(
                identities
            ),
            KnowledgeGraphCommunityRelation.target_community_id.in_(
                identities
            ),
        ]
        normalized_kind = str(relation_kind or "").strip()
        if normalized_kind:
            filters.append(
                KnowledgeGraphCommunityRelation.relation_kind
                == normalized_kind
            )
        with get_session(self.target) as session:
            rows = list(
                session.scalars(
                    select(KnowledgeGraphCommunityRelation)
                    .where(*filters)
                    .order_by(
                        KnowledgeGraphCommunityRelation.source_community_id,
                        KnowledgeGraphCommunityRelation.target_community_id,
                        KnowledgeGraphCommunityRelation.relation_kind,
                    )
                ).all()
            )
        return [_community_relation_record(row) for row in rows]

    def load_community_relation(
        self,
        *,
        relation_id: str,
        adapter_name: str,
    ) -> ExplorerCommunityRelationSnapshot | None:
        with get_session(self.target) as session:
            relation = session.scalar(
                select(KnowledgeGraphCommunityRelation).where(
                    KnowledgeGraphCommunityRelation.id == relation_id,
                    KnowledgeGraphCommunityRelation.status == "active",
                )
            )
            if relation is None:
                return None
            endpoint_communities = list(
                session.scalars(
                    select(KnowledgeGraphCommunity).where(
                        KnowledgeGraphCommunity.community_id.in_(
                            [
                                relation.source_community_id,
                                relation.target_community_id,
                            ]
                        ),
                        KnowledgeGraphCommunity.adapter_name == adapter_name,
                        KnowledgeGraphCommunity.graph_status == "active",
                    )
                ).all()
            )
            if len(endpoint_communities) != 2:
                return None
            edge_rows = list(
                session.scalars(
                    select(KnowledgeCardRelation)
                    .where(
                        KnowledgeCardRelation.id.in_(
                            list(relation.supporting_edge_ids or [])
                        ),
                        KnowledgeCardRelation.status == "active",
                    )
                    .order_by(KnowledgeCardRelation.id)
                ).all()
            )
        return ExplorerCommunityRelationSnapshot(
            relation=_community_relation_record(relation),
            edges=tuple(_edge_record(edge) for edge in edge_rows),
        )

    def load_community(
        self,
        *,
        community_id: str,
        graph_status: str = "active",
    ) -> ExplorerCommunitySnapshot | None:
        with get_session(self.target) as session:
            community = session.scalar(
                select(KnowledgeGraphCommunity).where(
                    KnowledgeGraphCommunity.community_id == community_id,
                    KnowledgeGraphCommunity.graph_status == graph_status,
                )
            )
            if community is None:
                return None
            return self._load_snapshots(session, [community])[0]

    def _load_snapshots(
        self,
        session,
        communities: list[KnowledgeGraphCommunity],
    ) -> list[ExplorerCommunitySnapshot]:
        card_ids = {
            str(card_id)
            for community in communities
            for card_id in (community.member_card_ids or [])
            if str(card_id)
        }
        edge_ids = {
            str(edge_id)
            for community in communities
            for edge_id in (community.member_edge_ids or [])
            if str(edge_id)
        }
        member_cards = (
            list(
                session.scalars(
                    select(KnowledgeCognitiveCard).where(
                        KnowledgeCognitiveCard.cognitive_card_id.in_(sorted(card_ids)),
                        KnowledgeCognitiveCard.status == "active",
                    )
                ).all()
            )
            if card_ids
            else []
        )
        edge_rows = (
            list(
                session.scalars(
                    select(KnowledgeCardRelation).where(
                        KnowledgeCardRelation.id.in_(sorted(edge_ids)),
                        KnowledgeCardRelation.status == "active",
                    )
                ).all()
            )
            if edge_ids
            else []
        )
        endpoint_card_ids = {
            card_id
            for edge in edge_rows
            for card_id in (edge.source_card_id, edge.target_card_id)
        }
        missing_endpoint_ids = endpoint_card_ids - {
            row.cognitive_card_id for row in member_cards
        }
        endpoint_cards = (
            list(
                session.scalars(
                    select(KnowledgeCognitiveCard).where(
                        KnowledgeCognitiveCard.cognitive_card_id.in_(
                            sorted(missing_endpoint_ids)
                        ),
                        KnowledgeCognitiveCard.status == "active",
                    )
                ).all()
            )
            if missing_endpoint_ids
            else []
        )
        card_row_by_id = {
            row.cognitive_card_id: row
            for row in [*member_cards, *endpoint_cards]
        }
        edge_row_by_id = {row.id: row for row in edge_rows}

        return [
            _community_snapshot(
                community,
                card_row_by_id=card_row_by_id,
                edge_row_by_id=edge_row_by_id,
            )
            for community in communities
        ]


def _community_order_by(
    sort_by: Literal[
        "edge_count",
        "card_count",
        "relation_count",
        "updated_at",
    ],
    sort_order: Literal["asc", "desc"],
):
    relation_count = (
        select(func.count())
        .select_from(KnowledgeGraphCommunityRelation)
        .where(
            KnowledgeGraphCommunityRelation.status == "active",
            or_(
                KnowledgeGraphCommunityRelation.source_community_id
                == KnowledgeGraphCommunity.community_id,
                KnowledgeGraphCommunityRelation.target_community_id
                == KnowledgeGraphCommunity.community_id,
            ),
        )
        .correlate(KnowledgeGraphCommunity)
        .scalar_subquery()
    )
    expressions = {
        "edge_count": func.jsonb_array_length(
            KnowledgeGraphCommunity.member_edge_ids
        ),
        "card_count": func.jsonb_array_length(
            KnowledgeGraphCommunity.member_card_ids
        ),
        "relation_count": relation_count,
        "updated_at": KnowledgeGraphCommunity.updated_at,
    }
    direction = asc if sort_order == "asc" else desc
    return (
        direction(expressions[sort_by]).nulls_last(),
        KnowledgeGraphCommunity.community_id.asc(),
    )


def _community_record(row: KnowledgeGraphCommunity) -> ExplorerCommunityRecord:
    return ExplorerCommunityRecord(
        community_id=row.community_id,
        adapter_name=row.adapter_name,
        identity_anchor_card_id=row.identity_anchor_card_id,
        member_card_ids=tuple(row.member_card_ids or []),
        member_edge_ids=tuple(row.member_edge_ids or []),
        graph_fingerprint=row.graph_fingerprint,
        graph_version=int(row.graph_version or 0),
        graph_status=row.graph_status,
        title=row.title or "",
        fact_report=row.fact_report or "",
        fact_report_version=int(row.fact_report_version or 0),
        fact_report_status=row.fact_report_status or "missing",
        conditional_projections=tuple(
            item
            for item in (row.conditional_projections or [])
            if isinstance(item, dict)
        ),
        projection_version=int(row.projection_version or 0),
        projection_status=row.projection_status or "missing",
        graph_changed_at=row.graph_changed_at,
        fact_report_generated_at=row.fact_report_generated_at,
        projection_generated_at=row.projection_generated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _card_record(row: KnowledgeCognitiveCard) -> ExplorerCardRecord:
    return ExplorerCardRecord(
        card_id=row.cognitive_card_id,
        source_type=row.source_type or "",
        source_id=row.source_id or "",
        evidence_id=row.evidence_id,
        primary_chunk_id=row.primary_chunk_id,
        chunk_ids=tuple(row.chunk_ids or []),
        focus_evidence_refs=tuple(row.focus_evidence_refs or []),
        relation_probes=tuple(
            item for item in (row.relation_probes or []) if isinstance(item, dict)
        ),
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _community_snapshot(
    community: KnowledgeGraphCommunity,
    *,
    card_row_by_id: dict[str, KnowledgeCognitiveCard],
    edge_row_by_id: dict[str, KnowledgeCardRelation],
) -> ExplorerCommunitySnapshot:
    member_card_ids = tuple(community.member_card_ids or [])
    member_card_id_set = set(member_card_ids)
    member_card_rows = [
        card_row_by_id[card_id]
        for card_id in member_card_ids
        if card_id in card_row_by_id
    ]
    representative_by_fact: dict[str, str] = {}
    for card in sorted(
        member_card_rows,
        key=lambda item: (
            item.created_at is None,
            item.created_at,
            item.cognitive_card_id,
        ),
    ):
        representative_by_fact.setdefault(
            card.fact_id or default_card_fact_id(card.cognitive_card_id),
            card.cognitive_card_id,
        )

    projected_edges: list[ExplorerEdgeRecord] = []
    for edge_id in community.member_edge_ids or []:
        edge = edge_row_by_id.get(edge_id)
        if edge is None:
            continue
        source_card_id = _project_card_id_to_member(
            edge.source_card_id,
            card_row_by_id=card_row_by_id,
            representative_by_fact=representative_by_fact,
        )
        target_card_id = _project_card_id_to_member(
            edge.target_card_id,
            card_row_by_id=card_row_by_id,
            representative_by_fact=representative_by_fact,
        )
        if (
            source_card_id not in member_card_id_set
            or target_card_id not in member_card_id_set
            or source_card_id == target_card_id
        ):
            continue
        projected_edges.append(
            _edge_record(
                edge,
                source_card_id=source_card_id,
                target_card_id=target_card_id,
            )
        )

    return ExplorerCommunitySnapshot(
        community=_community_record(community),
        cards=tuple(_card_record(card) for card in member_card_rows),
        edges=tuple(projected_edges),
    )


def _project_card_id_to_member(
    card_id: str,
    *,
    card_row_by_id: dict[str, KnowledgeCognitiveCard],
    representative_by_fact: dict[str, str],
) -> str:
    card = card_row_by_id.get(card_id)
    if card is None:
        return card_id
    fact_id = card.fact_id or default_card_fact_id(card.cognitive_card_id)
    return representative_by_fact.get(fact_id, card_id)


def _edge_record(
    row: KnowledgeCardRelation,
    *,
    source_card_id: str | None = None,
    target_card_id: str | None = None,
) -> ExplorerEdgeRecord:
    return ExplorerEdgeRecord(
        edge_id=row.id,
        source_card_id=source_card_id or row.source_card_id,
        target_card_id=target_card_id or row.target_card_id,
        relation_kind=row.relation_kind,
        relation_type=row.relation_type,
        direction=row.direction or "",
        decision_class=row.decision_class,
        basis=row.basis or "",
        source_evidence_refs=tuple(row.source_evidence_refs or []),
        target_evidence_refs=tuple(row.target_evidence_refs or []),
        relation_evidence_refs=tuple(row.relation_evidence_refs or []),
        inference_mechanism=row.inference_mechanism or "",
        confidence=float(row.confidence or 0.0),
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _community_relation_record(
    row: KnowledgeGraphCommunityRelation,
) -> ExplorerCommunityRelationRecord:
    return ExplorerCommunityRelationRecord(
        relation_id=row.id,
        source_community_id=row.source_community_id,
        target_community_id=row.target_community_id,
        relation_kind=row.relation_kind,
        supporting_edge_ids=tuple(row.supporting_edge_ids or []),
        observed_edge_count=int(row.observed_edge_count or 0),
        inferred_edge_count=int(row.inferred_edge_count or 0),
        relation_fingerprint=row.relation_fingerprint,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
