"""PostgreSQL reads for Agent-facing relationship graph retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from src.infrastructure.connections import get_session
from src.infrastructure.connections.database import Target
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCardRelation,
    KnowledgeCognitiveCard,
    KnowledgeGraphCommunity,
    KnowledgeGraphCommunityRelation,
)


@dataclass(frozen=True)
class AgentGraphCardRecord:
    card_id: str
    source_type: str
    source_id: str
    evidence_id: str
    primary_chunk_id: str
    chunk_ids: tuple[str, ...]
    focus_evidence_refs: tuple[str, ...]
    created_at: datetime | None
    updated_at: datetime | None
    fact_id: str = ""


@dataclass(frozen=True)
class AgentGraphEdgeRecord:
    edge_id: str
    source_card_id: str
    target_card_id: str
    relation_kind: str
    relation_type: str
    direction: str
    decision_class: str
    basis: str
    inference_mechanism: str
    confidence: float
    source_evidence_refs: tuple[str, ...]
    target_evidence_refs: tuple[str, ...]
    relation_evidence_refs: tuple[dict, ...]
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class AgentGraphCommunityRecord:
    community_id: str
    title: str
    identity_anchor_card_id: str
    member_card_ids: tuple[str, ...]
    member_edge_ids: tuple[str, ...]
    graph_version: int
    graph_changed_at: datetime | None


@dataclass(frozen=True)
class AgentGraphCommunityRelationRecord:
    relation_id: str
    source_community_id: str
    target_community_id: str
    relation_kind: str
    supporting_edge_ids: tuple[str, ...]
    observed_edge_count: int
    inferred_edge_count: int


@dataclass(frozen=True)
class AgentGraphSnapshot:
    cards: tuple[AgentGraphCardRecord, ...]
    edges: tuple[AgentGraphEdgeRecord, ...]
    communities: tuple[AgentGraphCommunityRecord, ...]
    community_relations: tuple[AgentGraphCommunityRelationRecord, ...]
    hop_by_card_id: dict[str, int]
    truncated: bool
    community_ids_by_card_id: dict[str, tuple[str, ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class AgentCommunityGraphSnapshot:
    communities: tuple[AgentGraphCommunityRecord, ...]
    relations: tuple[AgentGraphCommunityRelationRecord, ...]
    hop_by_community_id: dict[str, int]
    truncated: bool


class RelationGraphAgentRepository:
    """Loads a bounded, verified Card subgraph around semantic seed Cards."""

    def __init__(self, *, target: Target = "prod") -> None:
        self.target = target

    def load_subgraph(
        self,
        *,
        adapter_name: str,
        seed_card_ids: list[str],
        hop_limit: int,
        node_limit: int,
        edge_limit: int,
        relation_kinds: list[str],
        decision_classes: list[str],
        min_confidence: float,
        cutoff_at: datetime | None = None,
    ) -> AgentGraphSnapshot:
        seed_ids = _ordered_unique(seed_card_ids)[:node_limit]
        if not seed_ids:
            return AgentGraphSnapshot((), (), (), (), {}, False)

        with get_session(self.target) as session:
            seed_rows = list(
                session.scalars(
                    select(KnowledgeCognitiveCard).where(
                        KnowledgeCognitiveCard.adapter_name == adapter_name,
                        KnowledgeCognitiveCard.status == "active",
                        KnowledgeCognitiveCard.cognitive_card_id.in_(seed_ids),
                        *_created_before(KnowledgeCognitiveCard, cutoff_at),
                    )
                ).all()
            )
            card_by_id = {
                row.cognitive_card_id: row
                for row in seed_rows
            }
            hop_by_card_id = {
                card_id: 0
                for card_id in seed_ids
                if card_id in card_by_id
            }
            frontier = set(hop_by_card_id)
            edge_by_id: dict[str, KnowledgeCardRelation] = {}
            truncated = len(seed_rows) < len(seed_ids)

            for hop in range(1, hop_limit + 1):
                if not frontier or len(card_by_id) >= node_limit or len(edge_by_id) >= edge_limit:
                    break
                filters = [
                    KnowledgeCardRelation.status == "active",
                    KnowledgeCardRelation.confidence >= min_confidence,
                    *_created_before(KnowledgeCardRelation, cutoff_at),
                    or_(
                        KnowledgeCardRelation.source_card_id.in_(sorted(frontier)),
                        KnowledgeCardRelation.target_card_id.in_(sorted(frontier)),
                    ),
                ]
                if relation_kinds:
                    filters.append(
                        KnowledgeCardRelation.relation_kind.in_(relation_kinds)
                    )
                if decision_classes:
                    filters.append(
                        KnowledgeCardRelation.decision_class.in_(decision_classes)
                    )
                candidates = list(
                    session.scalars(
                        select(KnowledgeCardRelation)
                        .where(*filters)
                        .order_by(
                            KnowledgeCardRelation.confidence.desc(),
                            KnowledgeCardRelation.updated_at.desc().nulls_last(),
                            KnowledgeCardRelation.id.asc(),
                        )
                        .limit(max(1, edge_limit - len(edge_by_id)) * 3)
                    ).all()
                )
                endpoint_ids = sorted(
                    {
                        card_id
                        for edge in candidates
                        for card_id in (edge.source_card_id, edge.target_card_id)
                        if card_id not in card_by_id
                    }
                )
                endpoint_rows = list(
                    session.scalars(
                        select(KnowledgeCognitiveCard).where(
                            KnowledgeCognitiveCard.adapter_name == adapter_name,
                            KnowledgeCognitiveCard.status == "active",
                            KnowledgeCognitiveCard.cognitive_card_id.in_(endpoint_ids),
                            *_created_before(KnowledgeCognitiveCard, cutoff_at),
                        )
                    ).all()
                )
                valid_endpoint_by_id = {
                    row.cognitive_card_id: row
                    for row in endpoint_rows
                }
                next_frontier: set[str] = set()
                for edge in candidates:
                    if len(edge_by_id) >= edge_limit:
                        truncated = True
                        break
                    endpoints = (edge.source_card_id, edge.target_card_id)
                    missing = [
                        card_id
                        for card_id in endpoints
                        if card_id not in card_by_id
                    ]
                    if any(card_id not in valid_endpoint_by_id for card_id in missing):
                        continue
                    if len(card_by_id) + len(missing) > node_limit:
                        truncated = True
                        continue
                    for card_id in missing:
                        card_by_id[card_id] = valid_endpoint_by_id[card_id]
                        hop_by_card_id[card_id] = hop
                        next_frontier.add(card_id)
                    edge_by_id[edge.id] = edge
                frontier = next_frontier

            card_ids = sorted(card_by_id)
            fact_ids = sorted(
                {
                    row.fact_id
                    for row in card_by_id.values()
                    if row.fact_id
                }
            )
            representative_ids: set[str] = set()
            representative_by_fact: dict[str, str] = {}
            if fact_ids:
                fact_rows = list(
                    session.scalars(
                        select(KnowledgeCognitiveCard)
                        .where(
                            KnowledgeCognitiveCard.adapter_name
                            == adapter_name,
                            KnowledgeCognitiveCard.status == "active",
                            KnowledgeCognitiveCard.fact_id.in_(fact_ids),
                            *_created_before(KnowledgeCognitiveCard, cutoff_at),
                        )
                        .order_by(
                            KnowledgeCognitiveCard.created_at.asc().nulls_last(),
                            KnowledgeCognitiveCard.cognitive_card_id.asc(),
                        )
                    ).all()
                )
                seen_facts: set[str] = set()
                for row in fact_rows:
                    if row.fact_id in seen_facts:
                        continue
                    seen_facts.add(row.fact_id)
                    representative_ids.add(row.cognitive_card_id)
                    representative_by_fact[row.fact_id] = (
                        row.cognitive_card_id
                    )
            community_lookup_card_ids = sorted(
                set(card_ids) | representative_ids
            )
            community_filters = [
                KnowledgeGraphCommunity.adapter_name == adapter_name,
                KnowledgeGraphCommunity.graph_status == "active",
                *_community_before(cutoff_at),
            ]
            community_filters.append(
                or_(
                    *[
                        KnowledgeGraphCommunity.member_card_ids.contains([card_id])
                        for card_id in community_lookup_card_ids
                    ]
                )
            )
            community_rows = list(
                session.scalars(
                    select(KnowledgeGraphCommunity)
                    .where(*community_filters)
                    .order_by(KnowledgeGraphCommunity.community_id)
                ).all()
            )
            community_ids = [row.community_id for row in community_rows]
            community_ids_by_member: dict[str, list[str]] = {}
            for community in community_rows:
                for card_id in community.member_card_ids or []:
                    community_ids_by_member.setdefault(
                        str(card_id),
                        [],
                    ).append(community.community_id)
            community_ids_by_card_id = {
                card_id: tuple(
                    sorted(
                        set(
                            community_ids_by_member.get(card_id, [])
                        )
                        | set(
                            community_ids_by_member.get(
                                representative_by_fact.get(
                                    row.fact_id,
                                    "",
                                ),
                                [],
                            )
                        )
                    )
                )
                for card_id, row in card_by_id.items()
            }
            community_relation_rows = (
                list(
                    session.scalars(
                        select(KnowledgeGraphCommunityRelation)
                        .where(
                            KnowledgeGraphCommunityRelation.status == "active",
                            KnowledgeGraphCommunityRelation.source_community_id.in_(
                                community_ids
                            ),
                            KnowledgeGraphCommunityRelation.target_community_id.in_(
                                community_ids
                            ),
                            *_created_before(
                                KnowledgeGraphCommunityRelation,
                                cutoff_at,
                            ),
                        )
                        .order_by(KnowledgeGraphCommunityRelation.id)
                    ).all()
                )
                if community_ids
                else []
            )

        return AgentGraphSnapshot(
            cards=tuple(
                _card_record(card_by_id[card_id])
                for card_id in sorted(
                    card_by_id,
                    key=lambda card_id: (hop_by_card_id[card_id], card_id),
                )
            ),
            edges=tuple(
                _edge_record(edge)
                for edge in sorted(
                    edge_by_id.values(),
                    key=lambda edge: (
                        -float(edge.confidence or 0.0),
                        edge.id,
                    ),
                )
            ),
            communities=tuple(_community_record(row) for row in community_rows),
            community_relations=tuple(
                _community_relation_record(row)
                for row in community_relation_rows
            ),
            hop_by_card_id=hop_by_card_id,
            truncated=truncated,
            community_ids_by_card_id=community_ids_by_card_id,
        )

    def load_cards(
        self,
        *,
        adapter_name: str,
        card_ids: list[str],
        cutoff_at: datetime | None = None,
    ) -> list[AgentGraphCardRecord]:
        identities = _ordered_unique(card_ids)
        if not identities:
            return []
        with get_session(self.target) as session:
            rows = list(
                session.scalars(
                    select(KnowledgeCognitiveCard).where(
                        KnowledgeCognitiveCard.adapter_name == adapter_name,
                        KnowledgeCognitiveCard.status == "active",
                        KnowledgeCognitiveCard.cognitive_card_id.in_(identities),
                        *_created_before(KnowledgeCognitiveCard, cutoff_at),
                    )
                ).all()
            )
        by_id = {row.cognitive_card_id: row for row in rows}
        return [
            _card_record(by_id[card_id])
            for card_id in identities
            if card_id in by_id
        ]

    def load_fact_card_counts(
        self,
        *,
        adapter_name: str,
        fact_ids: list[str],
        cutoff_at: datetime | None = None,
    ) -> dict[str, int]:
        identities = _ordered_unique(fact_ids)
        if not identities:
            return {}
        with get_session(self.target) as session:
            rows = session.execute(
                select(
                    KnowledgeCognitiveCard.fact_id,
                    func.count(KnowledgeCognitiveCard.cognitive_card_id),
                )
                .where(
                    KnowledgeCognitiveCard.adapter_name == adapter_name,
                    KnowledgeCognitiveCard.status == "active",
                    KnowledgeCognitiveCard.fact_id.in_(identities),
                    *_created_before(KnowledgeCognitiveCard, cutoff_at),
                )
                .group_by(KnowledgeCognitiveCard.fact_id)
            ).all()
        return {
            str(fact_id): int(count)
            for fact_id, count in rows
            if fact_id
        }

    def load_edges(
        self,
        *,
        adapter_name: str,
        edge_ids: list[str],
        cutoff_at: datetime | None = None,
    ) -> list[AgentGraphEdgeRecord]:
        identities = _ordered_unique(edge_ids)
        if not identities:
            return []
        source_card = aliased(KnowledgeCognitiveCard)
        target_card = aliased(KnowledgeCognitiveCard)
        with get_session(self.target) as session:
            rows = list(
                session.scalars(
                    select(KnowledgeCardRelation)
                    .join(
                        source_card,
                        source_card.cognitive_card_id
                        == KnowledgeCardRelation.source_card_id,
                    )
                    .join(
                        target_card,
                        target_card.cognitive_card_id
                        == KnowledgeCardRelation.target_card_id,
                    )
                    .where(
                        KnowledgeCardRelation.id.in_(identities),
                        KnowledgeCardRelation.status == "active",
                        source_card.adapter_name == adapter_name,
                        source_card.status == "active",
                        target_card.adapter_name == adapter_name,
                        target_card.status == "active",
                        *_created_before(KnowledgeCardRelation, cutoff_at),
                        *_created_before(source_card, cutoff_at),
                        *_created_before(target_card, cutoff_at),
                    )
                ).all()
            )
        by_id = {row.id: row for row in rows}
        return [
            _edge_record(by_id[edge_id])
            for edge_id in identities
            if edge_id in by_id
        ]

    def load_communities(
        self,
        *,
        adapter_name: str,
        community_ids: list[str],
        cutoff_at: datetime | None = None,
    ) -> list[AgentGraphCommunityRecord]:
        identities = _ordered_unique(community_ids)
        if not identities:
            return []
        with get_session(self.target) as session:
            rows = list(
                session.scalars(
                    select(KnowledgeGraphCommunity).where(
                        KnowledgeGraphCommunity.adapter_name == adapter_name,
                        KnowledgeGraphCommunity.graph_status == "active",
                        KnowledgeGraphCommunity.community_id.in_(identities),
                        *_community_before(cutoff_at),
                    )
                ).all()
            )
        by_id = {row.community_id: row for row in rows}
        return [
            _community_record(by_id[community_id])
            for community_id in identities
            if community_id in by_id
        ]

    def load_community_neighborhood(
        self,
        *,
        adapter_name: str,
        seed_community_ids: list[str],
        hop_limit: int,
        community_limit: int,
        relation_limit: int,
        relation_kinds: list[str],
        cutoff_at: datetime | None = None,
    ) -> AgentCommunityGraphSnapshot:
        seed_ids = _ordered_unique(seed_community_ids)[:community_limit]
        if not seed_ids:
            return AgentCommunityGraphSnapshot((), (), {}, False)
        with get_session(self.target) as session:
            seed_rows = list(
                session.scalars(
                    select(KnowledgeGraphCommunity).where(
                        KnowledgeGraphCommunity.adapter_name == adapter_name,
                        KnowledgeGraphCommunity.graph_status == "active",
                        KnowledgeGraphCommunity.community_id.in_(seed_ids),
                        *_community_before(cutoff_at),
                    )
                ).all()
            )
            community_by_id = {row.community_id: row for row in seed_rows}
            hop_by_community_id = {
                community_id: 0
                for community_id in seed_ids
                if community_id in community_by_id
            }
            relation_by_id: dict[str, KnowledgeGraphCommunityRelation] = {}
            frontier = set(hop_by_community_id)
            truncated = len(seed_rows) < len(seed_ids)
            for hop in range(1, hop_limit + 1):
                if (
                    not frontier
                    or len(community_by_id) >= community_limit
                    or len(relation_by_id) >= relation_limit
                ):
                    break
                filters = [
                    KnowledgeGraphCommunityRelation.status == "active",
                    *_created_before(
                        KnowledgeGraphCommunityRelation,
                        cutoff_at,
                    ),
                    or_(
                        KnowledgeGraphCommunityRelation.source_community_id.in_(
                            sorted(frontier)
                        ),
                        KnowledgeGraphCommunityRelation.target_community_id.in_(
                            sorted(frontier)
                        ),
                    ),
                ]
                if relation_kinds:
                    filters.append(
                        KnowledgeGraphCommunityRelation.relation_kind.in_(
                            relation_kinds
                        )
                    )
                candidates = list(
                    session.scalars(
                        select(KnowledgeGraphCommunityRelation)
                        .where(*filters)
                        .order_by(
                            KnowledgeGraphCommunityRelation.observed_edge_count.desc(),
                            KnowledgeGraphCommunityRelation.inferred_edge_count.desc(),
                            KnowledgeGraphCommunityRelation.id.asc(),
                        )
                        .limit(max(1, relation_limit - len(relation_by_id)) * 3)
                    ).all()
                )
                endpoint_ids = sorted(
                    {
                        community_id
                        for relation in candidates
                        for community_id in (
                            relation.source_community_id,
                            relation.target_community_id,
                        )
                        if community_id not in community_by_id
                    }
                )
                endpoint_rows = list(
                    session.scalars(
                        select(KnowledgeGraphCommunity).where(
                            KnowledgeGraphCommunity.adapter_name == adapter_name,
                            KnowledgeGraphCommunity.graph_status == "active",
                            KnowledgeGraphCommunity.community_id.in_(endpoint_ids),
                            *_community_before(cutoff_at),
                        )
                    ).all()
                )
                valid_endpoint_by_id = {
                    row.community_id: row
                    for row in endpoint_rows
                }
                next_frontier: set[str] = set()
                for relation in candidates:
                    if len(relation_by_id) >= relation_limit:
                        truncated = True
                        break
                    endpoints = (
                        relation.source_community_id,
                        relation.target_community_id,
                    )
                    missing = [
                        community_id
                        for community_id in endpoints
                        if community_id not in community_by_id
                    ]
                    if any(
                        community_id not in valid_endpoint_by_id
                        for community_id in missing
                    ):
                        continue
                    if len(community_by_id) + len(missing) > community_limit:
                        truncated = True
                        continue
                    for community_id in missing:
                        community_by_id[community_id] = valid_endpoint_by_id[
                            community_id
                        ]
                        hop_by_community_id[community_id] = hop
                        next_frontier.add(community_id)
                    relation_by_id[relation.id] = relation
                frontier = next_frontier

        return AgentCommunityGraphSnapshot(
            communities=tuple(
                _community_record(community_by_id[community_id])
                for community_id in sorted(
                    community_by_id,
                    key=lambda community_id: (
                        hop_by_community_id[community_id],
                        community_id,
                    ),
                )
            ),
            relations=tuple(
                _community_relation_record(relation)
                for relation in sorted(
                    relation_by_id.values(),
                    key=lambda relation: (
                        -int(relation.observed_edge_count or 0),
                        -int(relation.inferred_edge_count or 0),
                        relation.id,
                    ),
                )
            ),
            hop_by_community_id=hop_by_community_id,
            truncated=truncated,
        )


def _card_record(row: KnowledgeCognitiveCard) -> AgentGraphCardRecord:
    return AgentGraphCardRecord(
        card_id=row.cognitive_card_id,
        source_type=row.source_type,
        source_id=row.source_id,
        evidence_id=row.evidence_id,
        primary_chunk_id=row.primary_chunk_id,
        chunk_ids=tuple(row.chunk_ids or []),
        focus_evidence_refs=tuple(row.focus_evidence_refs or []),
        created_at=row.created_at,
        updated_at=row.updated_at,
        fact_id=row.fact_id,
    )


def _edge_record(row: KnowledgeCardRelation) -> AgentGraphEdgeRecord:
    return AgentGraphEdgeRecord(
        edge_id=row.id,
        source_card_id=row.source_card_id,
        target_card_id=row.target_card_id,
        relation_kind=row.relation_kind,
        relation_type=row.relation_type,
        direction=row.direction,
        decision_class=row.decision_class,
        basis=row.basis,
        inference_mechanism=row.inference_mechanism,
        confidence=float(row.confidence or 0.0),
        source_evidence_refs=tuple(row.source_evidence_refs or []),
        target_evidence_refs=tuple(row.target_evidence_refs or []),
        relation_evidence_refs=tuple(
            item
            for item in (row.relation_evidence_refs or [])
            if isinstance(item, dict)
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _community_record(row: KnowledgeGraphCommunity) -> AgentGraphCommunityRecord:
    return AgentGraphCommunityRecord(
        community_id=row.community_id,
        title=row.title or "",
        identity_anchor_card_id=row.identity_anchor_card_id,
        member_card_ids=tuple(row.member_card_ids or []),
        member_edge_ids=tuple(row.member_edge_ids or []),
        graph_version=int(row.graph_version or 0),
        graph_changed_at=row.graph_changed_at,
    )


def _community_relation_record(
    row: KnowledgeGraphCommunityRelation,
) -> AgentGraphCommunityRelationRecord:
    return AgentGraphCommunityRelationRecord(
        relation_id=row.id,
        source_community_id=row.source_community_id,
        target_community_id=row.target_community_id,
        relation_kind=row.relation_kind,
        supporting_edge_ids=tuple(row.supporting_edge_ids or []),
        observed_edge_count=int(row.observed_edge_count or 0),
        inferred_edge_count=int(row.inferred_edge_count or 0),
    )


def _created_before(model: object, cutoff_at: datetime | None) -> list:
    """Return a strict knowledge-ingestion cutoff predicate when requested."""
    if cutoff_at is None:
        return []
    return [model.created_at <= cutoff_at]


def _community_before(cutoff_at: datetime | None) -> list:
    if cutoff_at is None:
        return []
    return [
        func.coalesce(
            KnowledgeGraphCommunity.graph_changed_at,
            KnowledgeGraphCommunity.created_at,
        )
        <= cutoff_at
    ]


def _ordered_unique(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if str(value).strip()
        )
    )
