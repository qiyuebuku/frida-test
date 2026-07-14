"""正式 Card Relation Edge 当前态仓储。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.knowledge.card_relation import (
    CardRelationEdge,
    card_pair_key,
    inactive_content_version,
)
from src.infrastructure.connections import get_session
from src.infrastructure.connections.database import Target
from src.infrastructure.persistence.models.knowledge import KnowledgeCardRelation


@dataclass(frozen=True)
class CardRelationSyncResult:
    touched_edge_ids: list[str]
    changed_edge_ids: list[str]
    active_edges_to_publish: list[CardRelationEdge]
    inactive_edge_ids_to_delete: list[str]
    affected_card_ids: list[str]


class CardRelationRepository:
    """幂等同步核验结果、Milvus 同步状态和图事件状态。"""

    def __init__(self, *, target: Target = "prod") -> None:
        self.target = target

    def synchronize_batch(
        self,
        *,
        accepted_edges: list[CardRelationEdge],
        rejected_pairs: list[tuple[str, str]],
    ) -> CardRelationSyncResult:
        accepted_by_id = {edge.id: edge for edge in accepted_edges}
        accepted_by_pair: dict[str, set[str]] = {}
        for edge in accepted_edges:
            accepted_by_pair.setdefault(edge.pair_key, set()).add(edge.id)
        touched_pairs = set(accepted_by_pair)
        touched_pairs.update(card_pair_key(left, right) for left, right in rejected_pairs)
        if not touched_pairs:
            return CardRelationSyncResult([], [], [], [], [])

        now = datetime.now(timezone.utc)
        changed_ids: set[str] = set()
        inactive_ids: set[str] = set()
        affected_cards: set[str] = set()
        with get_session(self.target) as session:
            existing_rows = list(
                session.scalars(
                    select(KnowledgeCardRelation).where(
                        KnowledgeCardRelation.pair_key.in_(sorted(touched_pairs))
                    )
                ).all()
            )
            existing_by_id = {row.id: row for row in existing_rows}
            for row in existing_rows:
                keep_ids = accepted_by_pair.get(row.pair_key, set())
                if row.status == "active" and row.id not in keep_ids:
                    row.status = "inactive"
                    row.invalidated_at = now
                    row.content_version = inactive_content_version(row.id, row.content_version)
                    row.updated_at = now
                    changed_ids.add(row.id)
                    inactive_ids.add(row.id)
                    affected_cards.update((row.source_card_id, row.target_card_id))

            if accepted_edges:
                values = [_edge_values(edge, now=now) for edge in accepted_edges]
                stmt = pg_insert(KnowledgeCardRelation).values(values)
                excluded = stmt.excluded
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={
                            "pair_key": excluded.pair_key,
                            "source_card_id": excluded.source_card_id,
                            "target_card_id": excluded.target_card_id,
                            "relation_kind": excluded.relation_kind,
                            "relation_type": excluded.relation_type,
                            "direction": excluded.direction,
                            "decision_class": excluded.decision_class,
                            "basis": excluded.basis,
                            "source_evidence_refs": excluded.source_evidence_refs,
                            "target_evidence_refs": excluded.target_evidence_refs,
                            "inference_mechanism": excluded.inference_mechanism,
                            "confidence": excluded.confidence,
                            "pipeline_version": excluded.pipeline_version,
                            "model_name": excluded.model_name,
                            "prompt_version": excluded.prompt_version,
                            "schema_version": excluded.schema_version,
                            "content_version": excluded.content_version,
                            "status": "active",
                            "invalidated_at": None,
                            "updated_at": now,
                        },
                    )
                )
                for edge in accepted_edges:
                    previous = existing_by_id.get(edge.id)
                    if (
                        previous is None
                        or previous.content_version != edge.content_version
                        or previous.status != "active"
                    ):
                        changed_ids.add(edge.id)
                    affected_cards.update((edge.source_card_id, edge.target_card_id))

            touched_ids = sorted(set(existing_by_id) | set(accepted_by_id))
            session.flush()
            session.expire_all()
            current_rows = list(
                session.scalars(
                    select(KnowledgeCardRelation).where(
                        KnowledgeCardRelation.id.in_(touched_ids)
                    )
                ).all()
            )
            active_to_publish = [
                _edge_from_row(row)
                for row in current_rows
                if row.status == "active" and row.semantic_synced_version != row.content_version
            ]
            inactive_to_delete = sorted(
                row.id
                for row in current_rows
                if row.status == "inactive" and row.semantic_synced_version != row.content_version
            )
        return CardRelationSyncResult(
            touched_edge_ids=touched_ids,
            changed_edge_ids=sorted(changed_ids),
            active_edges_to_publish=active_to_publish,
            inactive_edge_ids_to_delete=inactive_to_delete,
            affected_card_ids=sorted(affected_cards),
        )

    def invalidate_cards(self, card_ids: list[str]) -> CardRelationSyncResult:
        unique_ids = sorted({str(item) for item in card_ids if str(item).strip()})
        if not unique_ids:
            return CardRelationSyncResult([], [], [], [], [])
        now = datetime.now(timezone.utc)
        changed_ids: list[str] = []
        affected_cards: set[str] = set(unique_ids)
        with get_session(self.target) as session:
            rows = list(
                session.scalars(
                    select(KnowledgeCardRelation).where(
                        KnowledgeCardRelation.status == "active",
                        or_(
                            KnowledgeCardRelation.source_card_id.in_(unique_ids),
                            KnowledgeCardRelation.target_card_id.in_(unique_ids),
                        ),
                    )
                ).all()
            )
            for row in rows:
                row.status = "inactive"
                row.invalidated_at = now
                row.content_version = inactive_content_version(row.id, row.content_version)
                row.updated_at = now
                changed_ids.append(row.id)
                affected_cards.update((row.source_card_id, row.target_card_id))
        return CardRelationSyncResult(
            touched_edge_ids=sorted(changed_ids),
            changed_edge_ids=sorted(changed_ids),
            active_edges_to_publish=[],
            inactive_edge_ids_to_delete=sorted(changed_ids),
            affected_card_ids=sorted(affected_cards),
        )

    def mark_semantic_synced(self, edge_ids: list[str]) -> int:
        unique_ids = sorted(set(edge_ids))
        if not unique_ids:
            return 0
        with get_session(self.target) as session:
            result = session.execute(
                update(KnowledgeCardRelation)
                .where(KnowledgeCardRelation.id.in_(unique_ids))
                .values(semantic_synced_version=KnowledgeCardRelation.content_version)
            )
            return result.rowcount or 0

    def list_pending_graph_events(self, edge_ids: list[str]) -> list[KnowledgeCardRelation]:
        unique_ids = sorted(set(edge_ids))
        if not unique_ids:
            return []
        with get_session(self.target) as session:
            return list(
                session.scalars(
                    select(KnowledgeCardRelation).where(
                        KnowledgeCardRelation.id.in_(unique_ids),
                        KnowledgeCardRelation.semantic_synced_version
                        == KnowledgeCardRelation.content_version,
                        KnowledgeCardRelation.graph_event_published_version
                        != KnowledgeCardRelation.content_version,
                    )
                ).all()
            )

    def mark_graph_events_published(self, edge_ids: list[str]) -> int:
        unique_ids = sorted(set(edge_ids))
        if not unique_ids:
            return 0
        with get_session(self.target) as session:
            result = session.execute(
                update(KnowledgeCardRelation)
                .where(KnowledgeCardRelation.id.in_(unique_ids))
                .values(
                    graph_event_published_version=KnowledgeCardRelation.content_version
                )
            )
            return result.rowcount or 0

    def list_active_edges(
        self,
        *,
        edge_ids: list[str] | None = None,
        card_ids: list[str] | None = None,
    ) -> list[CardRelationEdge]:
        with get_session(self.target) as session:
            query = select(KnowledgeCardRelation).where(
                KnowledgeCardRelation.status == "active"
            )
            unique_edge_ids = sorted({str(item) for item in edge_ids or [] if str(item)})
            if unique_edge_ids:
                query = query.where(KnowledgeCardRelation.id.in_(unique_edge_ids))
            unique_card_ids = sorted({str(item) for item in card_ids or [] if str(item)})
            if unique_card_ids:
                query = query.where(
                    or_(
                        KnowledgeCardRelation.source_card_id.in_(unique_card_ids),
                        KnowledgeCardRelation.target_card_id.in_(unique_card_ids),
                    )
                )
            rows = list(
                session.scalars(
                    query.order_by(
                        KnowledgeCardRelation.source_card_id,
                        KnowledgeCardRelation.target_card_id,
                        KnowledgeCardRelation.relation_kind,
                    )
                ).all()
            )
            return [_edge_from_row(row) for row in rows]


def _edge_values(edge: CardRelationEdge, *, now: datetime) -> dict:
    return {
        **edge.as_dict(),
        "semantic_synced_version": "",
        "graph_event_published_version": "",
        "invalidated_at": None,
        "updated_at": now,
    }


def _edge_from_row(row: KnowledgeCardRelation) -> CardRelationEdge:
    return CardRelationEdge(
        id=row.id,
        pair_key=row.pair_key,
        source_card_id=row.source_card_id,
        target_card_id=row.target_card_id,
        relation_kind=row.relation_kind,  # type: ignore[arg-type]
        relation_type=row.relation_type,
        direction=row.direction,
        decision_class=row.decision_class,  # type: ignore[arg-type]
        basis=row.basis,
        source_evidence_refs=list(row.source_evidence_refs or []),
        target_evidence_refs=list(row.target_evidence_refs or []),
        inference_mechanism=row.inference_mechanism,
        confidence=float(row.confidence),
        pipeline_version=row.pipeline_version,
        model_name=row.model_name,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
        content_version=row.content_version,
        status=row.status,
    )
