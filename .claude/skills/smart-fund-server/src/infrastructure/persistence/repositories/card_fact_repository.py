"""Persist the fact_id projection derived from active observed same_fact relations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select

from src.domain.knowledge.card_fact import CardFactState, project_card_fact_ids
from src.infrastructure.connections import get_session
from src.infrastructure.connections.database import Target
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCardRelation,
    KnowledgeCognitiveCard,
)


@dataclass(frozen=True)
class CardFactProjectionResult:
    affected_card_ids: tuple[str, ...]
    changed_card_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]
    fact_by_card_id: dict[str, str]


class CardFactRepository:
    """Refresh only the previous/current equivalent-fact closure around changed Cards."""

    def __init__(self, *, target: Target = "prod") -> None:
        self.target = target

    def refresh_affected(
        self,
        *,
        adapter_name: str,
        seed_card_ids: list[str],
    ) -> CardFactProjectionResult:
        adapter = str(adapter_name or "").strip()
        seeds = {
            str(card_id).strip()
            for card_id in seed_card_ids
            if str(card_id).strip()
        }
        if not adapter or not seeds:
            return CardFactProjectionResult((), (), (), {})

        with get_session(self.target) as session:
            card_by_id: dict[str, KnowledgeCognitiveCard] = {}
            same_fact_edges: dict[str, KnowledgeCardRelation] = {}
            pending_card_ids = set(seeds)
            pending_fact_ids: set[str] = set()
            expanded_card_ids: set[str] = set()
            expanded_fact_ids: set[str] = set()
            attempted_card_ids: set[str] = set()

            while True:
                card_ids_to_load = pending_card_ids - attempted_card_ids
                fact_ids_to_load = pending_fact_ids - expanded_fact_ids
                if card_ids_to_load or fact_ids_to_load:
                    predicates = []
                    if card_ids_to_load:
                        predicates.append(
                            KnowledgeCognitiveCard.cognitive_card_id.in_(
                                sorted(card_ids_to_load)
                            )
                        )
                    if fact_ids_to_load:
                        predicates.append(
                            KnowledgeCognitiveCard.fact_id.in_(
                                sorted(fact_ids_to_load)
                            )
                        )
                    rows = list(
                        session.scalars(
                            select(KnowledgeCognitiveCard)
                            .where(
                                KnowledgeCognitiveCard.adapter_name == adapter,
                                KnowledgeCognitiveCard.status == "active",
                                or_(*predicates),
                            )
                            .order_by(
                                KnowledgeCognitiveCard.cognitive_card_id
                            )
                            .with_for_update()
                        ).all()
                    )
                    for row in rows:
                        card_by_id[row.cognitive_card_id] = row
                        if row.fact_id:
                            pending_fact_ids.add(row.fact_id)
                    attempted_card_ids.update(card_ids_to_load)
                    expanded_fact_ids.update(fact_ids_to_load)

                edge_frontier = set(card_by_id) - expanded_card_ids
                if edge_frontier:
                    rows = list(
                        session.scalars(
                            select(KnowledgeCardRelation).where(
                                KnowledgeCardRelation.status == "active",
                                KnowledgeCardRelation.relation_kind == "same_fact",
                                KnowledgeCardRelation.decision_class == "observed",
                                or_(
                                    KnowledgeCardRelation.source_card_id.in_(
                                        sorted(edge_frontier)
                                    ),
                                    KnowledgeCardRelation.target_card_id.in_(
                                        sorted(edge_frontier)
                                    ),
                                ),
                            )
                        ).all()
                    )
                    for row in rows:
                        same_fact_edges[row.id] = row
                        pending_card_ids.update(
                            (row.source_card_id, row.target_card_id)
                        )
                    expanded_card_ids.update(edge_frontier)

                if (
                    pending_card_ids.issubset(attempted_card_ids)
                    and pending_fact_ids.issubset(expanded_fact_ids)
                    and set(card_by_id).issubset(expanded_card_ids)
                ):
                    break

            cards = [
                CardFactState(
                    card_id=card_id,
                    fact_id=row.fact_id,
                )
                for card_id, row in sorted(card_by_id.items())
            ]
            pairs = [
                (edge.source_card_id, edge.target_card_id)
                for edge in same_fact_edges.values()
                if edge.source_card_id in card_by_id
                and edge.target_card_id in card_by_id
            ]
            fact_by_card_id = project_card_fact_ids(
                cards,
                same_fact_pairs=pairs,
            )
            changed_card_ids: list[str] = []
            for card_id, fact_id in fact_by_card_id.items():
                row = card_by_id[card_id]
                if row.fact_id == fact_id:
                    continue
                row.fact_id = fact_id
                changed_card_ids.append(card_id)

            return CardFactProjectionResult(
                affected_card_ids=tuple(sorted(card_by_id)),
                changed_card_ids=tuple(sorted(changed_card_ids)),
                fact_ids=tuple(sorted(set(fact_by_card_id.values()))),
                fact_by_card_id=fact_by_card_id,
            )
