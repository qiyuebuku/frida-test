"""Persistence for relationship-first Graph Community current state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import array, insert as pg_insert

from src.domain.knowledge.card_relation import RELATION_KINDS
from src.domain.knowledge.atomic_cognitive_card import default_card_fact_id
from src.domain.knowledge.relation_graph_community import (
    AffectedRelationGraph,
    ExistingRelationGraphCommunity,
    RelationGraphCommunityComponent,
    RelationGraphCommunityRelation,
    RelationGraphEdge,
    derive_community_relations_from_membership,
    project_edges_to_fact_representatives,
)
from src.infrastructure.connections import get_session
from src.infrastructure.connections.database import Target
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCardRelation,
    KnowledgeCognitiveCard,
    KnowledgeGraphCommunity,
    KnowledgeGraphCommunityMembership,
    KnowledgeGraphCommunityRelation,
)


@dataclass(frozen=True)
class GraphCommunityApplyResult:
    created_community_ids: tuple[str, ...]
    updated_community_ids: tuple[str, ...]
    unchanged_community_ids: tuple[str, ...]
    deleted_community_ids: tuple[str, ...]
    dirty_community_ids: tuple[str, ...]
    created_community_relation_ids: tuple[str, ...] = ()
    updated_community_relation_ids: tuple[str, ...] = ()
    unchanged_community_relation_ids: tuple[str, ...] = ()
    deleted_community_relation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoredCommunityEdge:
    edge_id: str
    source_card_id: str
    target_card_id: str
    relation_kind: str
    relation_type: str
    direction: str
    decision_class: str
    basis: str
    inference_mechanism: str


@dataclass(frozen=True)
class StoredCommunityCard:
    card_id: str
    source_type: str
    source_id: str
    primary_chunk_id: str
    fact_id: str
    fact_card_count: int


@dataclass(frozen=True)
class CommunityDerivationSnapshot:
    community_id: str
    adapter_name: str
    graph_fingerprint: str
    graph_version: int
    member_card_ids: tuple[str, ...]
    member_edge_ids: tuple[str, ...]
    cards: tuple[StoredCommunityCard, ...]
    edges: tuple[StoredCommunityEdge, ...]
    title: str
    fact_report: str
    fact_report_version: int
    fact_report_generator_version: str
    fact_report_graph_fingerprint: str
    fact_report_status: str
    fact_referenced_card_ids: tuple[str, ...]
    fact_referenced_edge_ids: tuple[str, ...]
    fact_semantic_synced_version: str
    conditional_projections: tuple[dict, ...]
    projection_version: int
    projection_generator_version: str
    projection_graph_fingerprint: str
    projection_fact_report_version: int
    projection_status: str
    projection_semantic_synced_version: str
    projection_task_dispatched_version: int


@dataclass(frozen=True)
class CommunityReportDispatch:
    community_id: str
    graph_fingerprint: str
    has_previous_report: bool


class RelationGraphCommunityRepository:
    """Loads an affected active Edge closure and applies its current components."""

    def __init__(self, *, target: Target = "prod") -> None:
        self.target = target

    def load_affected_graph(
        self,
        *,
        adapter_name: str,
        seed_card_ids: list[str],
    ) -> AffectedRelationGraph:
        adapter = str(adapter_name or "").strip()
        seeds = {str(item).strip() for item in seed_card_ids if str(item).strip()}
        if not adapter:
            raise ValueError("Graph Community adapter_name 不能为空")
        if not seeds:
            return AffectedRelationGraph(
                adapter_name=adapter,
                seed_card_ids=(),
                touched_community_ids=(),
                edges=(),
            )

        touched_communities: dict[str, KnowledgeGraphCommunity] = {}
        valid_edges: dict[str, RelationGraphEdge] = {}
        rejected_edge_ids: set[str] = set()
        card_cache: dict[str, KnowledgeCognitiveCard | None] = {}

        with get_session(self.target) as session:
            seed_representatives = _fact_representatives_for_cards(
                session,
                adapter_name=adapter,
                card_ids=seeds,
            )
            representative_seeds = set(seed_representatives.values()) | seeds
            touched_ids = _community_ids_for_cards(
                session,
                adapter_name=adapter,
                card_ids=representative_seeds,
            )

            # Unassigned/new seeds may directly bridge an existing Community.
            # Inspect one incident hop only; never traverse existing cross-
            # Community edges recursively.
            seed_edges = _load_active_edges_touching_cards(
                session,
                card_ids=sorted(seeds),
            )
            direct_endpoint_ids = {
                card_id
                for edge in seed_edges
                for card_id in (edge.source_card_id, edge.target_card_id)
            }
            direct_representatives = _fact_representatives_for_cards(
                session,
                adapter_name=adapter,
                card_ids=direct_endpoint_ids,
            )
            touched_ids.update(
                _community_ids_for_cards(
                    session,
                    adapter_name=adapter,
                    card_ids=set(direct_representatives.values()),
                )
            )
            touched_communities.update(
                _load_communities_by_ids(
                    session,
                    adapter_name=adapter,
                    community_ids=touched_ids,
                )
            )

            representative_scope = set(representative_seeds)
            for community in touched_communities.values():
                representative_scope.update(community.member_card_ids or [])
            raw_scope = _raw_card_ids_for_representatives(
                session,
                adapter_name=adapter,
                representative_card_ids=representative_scope,
            )
            edge_rows = _load_active_edges_within_cards(
                session,
                card_ids=raw_scope,
            )
            endpoint_ids = {
                card_id
                for edge in edge_rows
                for card_id in (edge.source_card_id, edge.target_card_id)
            }
            if endpoint_ids:
                rows = list(
                    session.scalars(
                        select(KnowledgeCognitiveCard).where(
                            KnowledgeCognitiveCard.cognitive_card_id.in_(endpoint_ids)
                        )
                    ).all()
                )
                card_cache.update({row.cognitive_card_id: row for row in rows})
            for edge in edge_rows:
                source = card_cache.get(edge.source_card_id)
                target = card_cache.get(edge.target_card_id)
                if not _is_valid_graph_edge(
                    edge,
                    source=source,
                    target=target,
                    adapter_name=adapter,
                ):
                    rejected_edge_ids.add(edge.id)
                    continue
                valid_edges[edge.id] = RelationGraphEdge(
                    edge_id=edge.id,
                    source_card_id=edge.source_card_id,
                    target_card_id=edge.target_card_id,
                    relation_kind=edge.relation_kind,
                    decision_class=edge.decision_class,
                    content_version=edge.content_version,
                )

            representative_by_card = _fact_representatives_for_cards(
                session,
                adapter_name=adapter,
                card_ids={
                    card_id
                    for edge in valid_edges.values()
                    for card_id in (
                        edge.source_card_id,
                        edge.target_card_id,
                    )
                }
                | seeds,
            )
            projected_edges = {
                edge.edge_id: edge
                for edge in project_edges_to_fact_representatives(
                    list(valid_edges.values()),
                    representative_by_card_id=representative_by_card,
                )
            }

            projected_card_ids = {
                card_id
                for edge in projected_edges.values()
                for card_id in (
                    edge.source_card_id,
                    edge.target_card_id,
                )
            }
            for community in _load_communities_containing_cards(
                session,
                adapter_name=adapter,
                card_ids=projected_card_ids,
            ):
                touched_communities[community.community_id] = community

        return AffectedRelationGraph(
            adapter_name=adapter,
            seed_card_ids=tuple(
                sorted(
                    {
                        representative_by_card.get(card_id, card_id)
                        for card_id in seeds
                    }
                )
            ),
            touched_community_ids=tuple(sorted(touched_communities)),
            edges=tuple(
                projected_edges[edge_id]
                for edge_id in sorted(projected_edges)
            ),
            rejected_edge_ids=tuple(sorted(rejected_edge_ids)),
            existing_communities=tuple(
                ExistingRelationGraphCommunity(
                    community_id=row.community_id,
                    identity_anchor_card_id=row.identity_anchor_card_id,
                    member_card_ids=tuple(row.member_card_ids or []),
                )
                for row in sorted(
                    touched_communities.values(),
                    key=lambda item: item.community_id,
                )
            ),
        )

    def load_full_graph(self, *, adapter_name: str) -> AffectedRelationGraph:
        """Load a compact current graph for explicit offline reconciliation."""

        adapter = str(adapter_name or "").strip()
        if not adapter:
            raise ValueError("Graph Community adapter_name 不能为空")
        with get_session(self.target) as session:
            card_rows = session.execute(
                select(
                    KnowledgeCognitiveCard.cognitive_card_id,
                    KnowledgeCognitiveCard.fact_id,
                    KnowledgeCognitiveCard.created_at,
                ).where(
                    KnowledgeCognitiveCard.adapter_name == adapter,
                    KnowledgeCognitiveCard.status == "active",
                )
            ).all()
            representative_by_fact: dict[str, str] = {}
            for card_id, fact_id, _created_at in sorted(
                card_rows,
                key=lambda row: (row[2] is None, row[2], row[0]),
            ):
                representative_by_fact.setdefault(fact_id or card_id, card_id)
            representative_by_card = {
                card_id: representative_by_fact[fact_id or card_id]
                for card_id, fact_id, _created_at in card_rows
            }
            edge_rows = session.execute(
                select(
                    KnowledgeCardRelation.id,
                    KnowledgeCardRelation.source_card_id,
                    KnowledgeCardRelation.target_card_id,
                    KnowledgeCardRelation.relation_kind,
                    KnowledgeCardRelation.decision_class,
                    KnowledgeCardRelation.content_version,
                ).where(KnowledgeCardRelation.status == "active")
            ).all()
            projected = project_edges_to_fact_representatives(
                [
                    RelationGraphEdge(
                        edge_id=edge_id,
                        source_card_id=source_card_id,
                        target_card_id=target_card_id,
                        relation_kind=relation_kind,
                        decision_class=decision_class,
                        content_version=content_version,
                    )
                    for (
                        edge_id,
                        source_card_id,
                        target_card_id,
                        relation_kind,
                        decision_class,
                        content_version,
                    ) in edge_rows
                    if source_card_id in representative_by_card
                    and target_card_id in representative_by_card
                    and relation_kind in RELATION_KINDS
                    and decision_class in {"observed", "inferred"}
                    and content_version
                ],
                representative_by_card_id=representative_by_card,
            )
            communities = list(
                session.scalars(
                    select(KnowledgeGraphCommunity).where(
                        KnowledgeGraphCommunity.adapter_name == adapter,
                        KnowledgeGraphCommunity.graph_status == "active",
                    )
                ).all()
            )
        return AffectedRelationGraph(
            adapter_name=adapter,
            seed_card_ids=(),
            touched_community_ids=tuple(
                sorted(item.community_id for item in communities)
            ),
            edges=tuple(sorted(projected, key=lambda item: item.edge_id)),
            existing_communities=tuple(
                ExistingRelationGraphCommunity(
                    community_id=item.community_id,
                    identity_anchor_card_id=item.identity_anchor_card_id,
                    member_card_ids=tuple(item.member_card_ids or []),
                )
                for item in sorted(communities, key=lambda item: item.community_id)
            ),
        )

    def apply_components(
        self,
        *,
        adapter_name: str,
        touched_community_ids: list[str],
        components: list[RelationGraphCommunityComponent],
        community_relations: list[RelationGraphCommunityRelation] | None = None,
        rebuild_boundary_relations: bool = True,
    ) -> GraphCommunityApplyResult:
        adapter = str(adapter_name or "").strip()
        desired_by_id = {item.community_id: item for item in components}
        desired_relations_by_id = {
            item.relation_id: item for item in (community_relations or [])
        }
        touched_ids = {
            str(item).strip()
            for item in touched_community_ids
            if str(item).strip()
        }
        scoped_ids = sorted(touched_ids | set(desired_by_id))
        now = datetime.now(timezone.utc)

        created: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []
        dirty: list[str] = []
        created_relations: list[str] = []
        updated_relations: list[str] = []
        unchanged_relations: list[str] = []
        deleted_relations: list[str] = []

        with get_session(self.target) as session:
            existing_rows = (
                list(
                    session.scalars(
                        select(KnowledgeGraphCommunity).where(
                            KnowledgeGraphCommunity.adapter_name == adapter,
                            KnowledgeGraphCommunity.community_id.in_(scoped_ids),
                        )
                    ).all()
                )
                if scoped_ids
                else []
            )
            existing_by_id = {row.community_id: row for row in existing_rows}
            existing_relation_rows = (
                list(
                    session.scalars(
                        select(KnowledgeGraphCommunityRelation).where(
                            KnowledgeGraphCommunityRelation.status == "active",
                            and_(
                                KnowledgeGraphCommunityRelation.source_community_id.in_(scoped_ids),
                                KnowledgeGraphCommunityRelation.target_community_id.in_(scoped_ids),
                            ),
                        )
                    ).all()
                )
                if scoped_ids
                else []
            )
            existing_relations_by_id = {
                row.id: row for row in existing_relation_rows
            }
            obsolete_relation_ids = sorted(
                set(existing_relations_by_id) - set(desired_relations_by_id)
            )
            if obsolete_relation_ids:
                session.execute(
                    delete(KnowledgeGraphCommunityRelation).where(
                        KnowledgeGraphCommunityRelation.id.in_(
                            obsolete_relation_ids
                        )
                    )
                )
                deleted_relations.extend(obsolete_relation_ids)

            obsolete_ids = sorted(touched_ids - set(desired_by_id))
            if obsolete_ids:
                session.execute(
                    delete(KnowledgeGraphCommunity).where(
                        KnowledgeGraphCommunity.adapter_name == adapter,
                        KnowledgeGraphCommunity.community_id.in_(obsolete_ids),
                    )
                )

            changed_values: list[dict] = []
            for community_id, component in desired_by_id.items():
                existing = existing_by_id.get(community_id)
                if _component_matches_row(component, existing):
                    unchanged.append(community_id)
                    continue
                if existing is None:
                    created.append(community_id)
                else:
                    updated.append(community_id)
                dirty.append(community_id)
                changed_values.append(
                    _community_values(
                        adapter_name=adapter,
                        component=component,
                        existing=existing,
                        now=now,
                    )
                )

            if changed_values:
                statement = pg_insert(KnowledgeGraphCommunity).values(changed_values)
                excluded = statement.excluded
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[KnowledgeGraphCommunity.community_id],
                        set_={
                            "adapter_name": excluded.adapter_name,
                            "identity_anchor_card_id": excluded.identity_anchor_card_id,
                            "member_card_ids": excluded.member_card_ids,
                            "member_edge_ids": excluded.member_edge_ids,
                            "graph_fingerprint": excluded.graph_fingerprint,
                            "graph_version": excluded.graph_version,
                            "graph_status": excluded.graph_status,
                            "title": excluded.title,
                            "fact_report": excluded.fact_report,
                            "fact_referenced_card_ids": excluded.fact_referenced_card_ids,
                            "fact_referenced_edge_ids": excluded.fact_referenced_edge_ids,
                            "fact_report_version": excluded.fact_report_version,
                            "fact_report_generator_version": excluded.fact_report_generator_version,
                            "fact_report_graph_fingerprint": excluded.fact_report_graph_fingerprint,
                            "fact_report_status": excluded.fact_report_status,
                            "fact_report_error": excluded.fact_report_error,
                            "report_task_dispatched_fingerprint": excluded.report_task_dispatched_fingerprint,
                            "fact_semantic_synced_version": excluded.fact_semantic_synced_version,
                            "conditional_projections": excluded.conditional_projections,
                            "projection_version": excluded.projection_version,
                            "projection_generator_version": excluded.projection_generator_version,
                            "projection_graph_fingerprint": excluded.projection_graph_fingerprint,
                            "projection_fact_report_version": excluded.projection_fact_report_version,
                            "projection_status": excluded.projection_status,
                            "projection_error": excluded.projection_error,
                            "projection_task_dispatched_version": excluded.projection_task_dispatched_version,
                            "projection_semantic_synced_version": excluded.projection_semantic_synced_version,
                            "graph_changed_at": excluded.graph_changed_at,
                            "fact_report_generated_at": excluded.fact_report_generated_at,
                            "projection_generated_at": excluded.projection_generated_at,
                            "updated_at": now,
                        },
                    )
                )

            desired_memberships = [
                {
                    "adapter_name": adapter,
                    "card_id": card_id,
                    "community_id": component.community_id,
                    "updated_at": now,
                }
                for component in components
                for card_id in component.member_card_ids
            ]
            desired_card_ids = [item["card_id"] for item in desired_memberships]
            membership_scope = []
            if scoped_ids:
                membership_scope.append(
                    KnowledgeGraphCommunityMembership.community_id.in_(scoped_ids)
                )
            if desired_card_ids:
                membership_scope.append(
                    KnowledgeGraphCommunityMembership.card_id.in_(desired_card_ids)
                )
            if membership_scope:
                session.execute(
                    delete(KnowledgeGraphCommunityMembership).where(
                        KnowledgeGraphCommunityMembership.adapter_name == adapter,
                        or_(*membership_scope),
                    )
                )
            if desired_memberships:
                session.execute(
                    pg_insert(KnowledgeGraphCommunityMembership).values(
                        desired_memberships
                    )
                )

            changed_relation_values: list[dict] = []
            for relation_id, relation in desired_relations_by_id.items():
                existing_relation = existing_relations_by_id.get(relation_id)
                if _community_relation_matches_row(
                    relation,
                    existing_relation,
                ):
                    unchanged_relations.append(relation_id)
                    continue
                if existing_relation is None:
                    created_relations.append(relation_id)
                else:
                    updated_relations.append(relation_id)
                changed_relation_values.append(
                    _community_relation_values(
                        relation=relation,
                        now=now,
                    )
                )

            if changed_relation_values:
                relation_statement = pg_insert(
                    KnowledgeGraphCommunityRelation
                ).values(changed_relation_values)
                relation_excluded = relation_statement.excluded
                session.execute(
                    relation_statement.on_conflict_do_update(
                        index_elements=[
                            KnowledgeGraphCommunityRelation.id
                        ],
                        set_={
                            "source_community_id": (
                                relation_excluded.source_community_id
                            ),
                            "target_community_id": (
                                relation_excluded.target_community_id
                            ),
                            "relation_kind": relation_excluded.relation_kind,
                            "supporting_edge_ids": (
                                relation_excluded.supporting_edge_ids
                            ),
                            "observed_edge_count": (
                                relation_excluded.observed_edge_count
                            ),
                            "inferred_edge_count": (
                                relation_excluded.inferred_edge_count
                            ),
                            "relation_fingerprint": (
                                relation_excluded.relation_fingerprint
                            ),
                            "status": relation_excluded.status,
                            "updated_at": now,
                        },
                    )
                )

            boundary_relations = (
                _derive_boundary_community_relations(
                    session,
                    adapter_name=adapter,
                    scoped_community_ids=set(scoped_ids),
                    representative_card_ids=set(desired_card_ids),
                )
                if rebuild_boundary_relations
                else list(desired_relations_by_id.values())
            )
            boundary_by_id = {
                item.relation_id: item for item in boundary_relations
            }
            existing_boundary = list(
                session.scalars(
                    select(KnowledgeGraphCommunityRelation).where(
                        KnowledgeGraphCommunityRelation.status == "active",
                        or_(
                            KnowledgeGraphCommunityRelation.source_community_id.in_(scoped_ids),
                            KnowledgeGraphCommunityRelation.target_community_id.in_(scoped_ids),
                        ),
                    )
                ).all()
            ) if scoped_ids else []
            obsolete_boundary_ids = sorted(
                {row.id for row in existing_boundary} - set(boundary_by_id)
            )
            if obsolete_boundary_ids:
                session.execute(
                    delete(KnowledgeGraphCommunityRelation).where(
                        KnowledgeGraphCommunityRelation.id.in_(obsolete_boundary_ids)
                    )
                )
                deleted_relations.extend(obsolete_boundary_ids)
            if boundary_relations:
                boundary_insert = pg_insert(KnowledgeGraphCommunityRelation).values(
                    [
                        _community_relation_values(relation=item, now=now)
                        for item in boundary_relations
                    ]
                )
                session.execute(
                    boundary_insert.on_conflict_do_update(
                        index_elements=[KnowledgeGraphCommunityRelation.id],
                        set_={
                            "source_community_id": boundary_insert.excluded.source_community_id,
                            "target_community_id": boundary_insert.excluded.target_community_id,
                            "relation_kind": boundary_insert.excluded.relation_kind,
                            "supporting_edge_ids": boundary_insert.excluded.supporting_edge_ids,
                            "observed_edge_count": boundary_insert.excluded.observed_edge_count,
                            "inferred_edge_count": boundary_insert.excluded.inferred_edge_count,
                            "relation_fingerprint": boundary_insert.excluded.relation_fingerprint,
                            "status": boundary_insert.excluded.status,
                            "updated_at": now,
                        },
                    )
                )

        return GraphCommunityApplyResult(
            created_community_ids=tuple(sorted(created)),
            updated_community_ids=tuple(sorted(updated)),
            unchanged_community_ids=tuple(sorted(unchanged)),
            deleted_community_ids=tuple(sorted(touched_ids - set(desired_by_id))),
            dirty_community_ids=tuple(sorted(dirty)),
            created_community_relation_ids=tuple(
                sorted(created_relations)
            ),
            updated_community_relation_ids=tuple(
                sorted(updated_relations)
            ),
            unchanged_community_relation_ids=tuple(
                sorted(unchanged_relations)
            ),
            deleted_community_relation_ids=tuple(
                sorted(deleted_relations)
            ),
        )
    def load_derivation_snapshot(
        self,
        *,
        community_id: str,
        expected_graph_fingerprint: str = "",
    ) -> CommunityDerivationSnapshot | None:
        identity = str(community_id or "").strip()
        if not identity:
            raise ValueError("community_id 不能为空")
        with get_session(self.target) as session:
            row = session.scalar(
                select(KnowledgeGraphCommunity).where(
                    KnowledgeGraphCommunity.community_id == identity,
                    KnowledgeGraphCommunity.graph_status == "active",
                )
            )
            if row is None:
                return None
            if (
                expected_graph_fingerprint
                and row.graph_fingerprint != expected_graph_fingerprint
            ):
                return None
            edge_rows = list(
                session.scalars(
                    select(KnowledgeCardRelation)
                    .where(
                        KnowledgeCardRelation.id.in_(
                            list(row.member_edge_ids or [])
                        ),
                        KnowledgeCardRelation.status == "active",
                    )
                    .order_by(KnowledgeCardRelation.id)
                ).all()
            )
            edge_by_id = {edge.id: edge for edge in edge_rows}
            missing_edges = sorted(
                set(row.member_edge_ids or []) - set(edge_by_id)
            )
            if missing_edges:
                raise ValueError(
                    f"Community 当前成员 Edge 缺失或已失效: {missing_edges}"
                )
            endpoint_ids = {
                card_id
                for edge in edge_rows
                for card_id in (edge.source_card_id, edge.target_card_id)
            }
            representative_by_card = _fact_representatives_for_cards(
                session,
                adapter_name=row.adapter_name,
                card_ids=endpoint_ids,
            )
            projected_endpoint_ids = {
                representative_by_card.get(card_id, card_id)
                for card_id in endpoint_ids
            }
            if projected_endpoint_ids != set(row.member_card_ids or []):
                raise ValueError(
                    "Community Card/Edge 当前态不一致: "
                    f"members={sorted(row.member_card_ids or [])} "
                    f"projected_endpoints={sorted(projected_endpoint_ids)}"
                )
            card_rows = list(
                session.scalars(
                    select(KnowledgeCognitiveCard).where(
                        KnowledgeCognitiveCard.cognitive_card_id.in_(
                            list(row.member_card_ids or [])
                        ),
                        KnowledgeCognitiveCard.status == "active",
                    )
                ).all()
            )
            card_by_id = {
                card.cognitive_card_id: card for card in card_rows
            }
            missing_cards = sorted(
                set(row.member_card_ids or []) - set(card_by_id)
            )
            if missing_cards:
                raise ValueError(
                    f"Community 当前成员 Card 缺失或已失效: {missing_cards}"
                )
            fact_ids = sorted(
                {
                    card.fact_id
                    for card in card_rows
                    if card.fact_id
                }
            )
            fact_card_counts = {
                str(fact_id): int(count)
                for fact_id, count in (
                    session.execute(
                        select(
                            KnowledgeCognitiveCard.fact_id,
                            func.count(
                                KnowledgeCognitiveCard.cognitive_card_id
                            ),
                        )
                        .where(
                            KnowledgeCognitiveCard.adapter_name
                            == row.adapter_name,
                            KnowledgeCognitiveCard.status == "active",
                            KnowledgeCognitiveCard.fact_id.in_(fact_ids),
                        )
                        .group_by(KnowledgeCognitiveCard.fact_id)
                    ).all()
                    if fact_ids
                    else []
                )
                if fact_id
            }
            return CommunityDerivationSnapshot(
                community_id=row.community_id,
                adapter_name=row.adapter_name,
                graph_fingerprint=row.graph_fingerprint,
                graph_version=int(row.graph_version or 0),
                member_card_ids=tuple(row.member_card_ids or []),
                member_edge_ids=tuple(row.member_edge_ids or []),
                cards=tuple(
                    StoredCommunityCard(
                        card_id=card.cognitive_card_id,
                        source_type=card.source_type or "",
                        source_id=card.source_id or "",
                        primary_chunk_id=card.primary_chunk_id or "",
                        fact_id=(
                            card.fact_id
                            or default_card_fact_id(
                                card.cognitive_card_id
                            )
                        ),
                        fact_card_count=fact_card_counts.get(
                            card.fact_id,
                            1,
                        ),
                    )
                    for card in (
                        card_by_id[card_id]
                        for card_id in row.member_card_ids or []
                    )
                ),
                edges=tuple(
                    StoredCommunityEdge(
                        edge_id=edge.id,
                        source_card_id=representative_by_card.get(
                            edge.source_card_id,
                            edge.source_card_id,
                        ),
                        target_card_id=representative_by_card.get(
                            edge.target_card_id,
                            edge.target_card_id,
                        ),
                        relation_kind=edge.relation_kind,
                        relation_type=edge.relation_type,
                        direction=edge.direction or "",
                        decision_class=edge.decision_class,
                        basis=edge.basis,
                        inference_mechanism=edge.inference_mechanism or "",
                    )
                    for edge in (edge_by_id[edge_id] for edge_id in row.member_edge_ids)
                ),
                title=row.title or "",
                fact_report=row.fact_report or "",
                fact_report_version=int(row.fact_report_version or 0),
                fact_report_generator_version=(
                    row.fact_report_generator_version or ""
                ),
                fact_report_graph_fingerprint=(
                    row.fact_report_graph_fingerprint or ""
                ),
                fact_report_status=row.fact_report_status or "missing",
                fact_referenced_card_ids=tuple(
                    row.fact_referenced_card_ids or []
                ),
                fact_referenced_edge_ids=tuple(
                    row.fact_referenced_edge_ids or []
                ),
                fact_semantic_synced_version=(
                    row.fact_semantic_synced_version or ""
                ),
                conditional_projections=tuple(
                    item
                    for item in (row.conditional_projections or [])
                    if isinstance(item, dict)
                ),
                projection_version=int(row.projection_version or 0),
                projection_generator_version=(
                    row.projection_generator_version or ""
                ),
                projection_graph_fingerprint=(
                    row.projection_graph_fingerprint or ""
                ),
                projection_fact_report_version=int(
                    row.projection_fact_report_version or 0
                ),
                projection_status=row.projection_status or "missing",
                projection_semantic_synced_version=(
                    row.projection_semantic_synced_version or ""
                ),
                projection_task_dispatched_version=int(
                    row.projection_task_dispatched_version or 0
                ),
            )

    def mark_fact_generating(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
    ) -> bool:
        return self._mark_status(
            community_id=community_id,
            graph_fingerprint=graph_fingerprint,
            values={"fact_report_status": "generating", "fact_report_error": ""},
        )

    def save_fact_report(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
        title: str,
        report_text: str,
        referenced_card_ids: list[str],
        referenced_edge_ids: list[str],
        generator_version: str,
    ) -> int | None:
        now = datetime.now(timezone.utc)
        with get_session(self.target) as session:
            row = session.scalar(
                select(KnowledgeGraphCommunity)
                .where(
                    KnowledgeGraphCommunity.community_id == community_id,
                    KnowledgeGraphCommunity.graph_status == "active",
                )
                .with_for_update()
            )
            if row is None or row.graph_fingerprint != graph_fingerprint:
                return None
            if (
                row.fact_report_graph_fingerprint == graph_fingerprint
                and row.fact_report_generator_version == generator_version
                and row.fact_report
            ):
                row.fact_report_status = "publishing"
                row.fact_report_error = ""
                return int(row.fact_report_version or 0)
            row.title = title
            row.fact_report = report_text
            row.fact_referenced_card_ids = list(referenced_card_ids)
            row.fact_referenced_edge_ids = list(referenced_edge_ids)
            row.fact_report_version = int(row.fact_report_version or 0) + 1
            row.fact_report_generator_version = generator_version
            row.fact_report_graph_fingerprint = graph_fingerprint
            row.fact_report_status = "publishing"
            row.fact_report_error = ""
            row.fact_report_generated_at = now
            row.fact_semantic_synced_version = ""
            row.conditional_projections = []
            row.projection_graph_fingerprint = ""
            row.projection_fact_report_version = 0
            row.projection_status = "missing"
            row.projection_error = ""
            row.projection_semantic_synced_version = ""
            return row.fact_report_version

    def mark_fact_semantic_ready(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
        report_version: int,
        semantic_version: str,
    ) -> bool:
        with get_session(self.target) as session:
            result = session.execute(
                update(KnowledgeGraphCommunity)
                .where(
                    KnowledgeGraphCommunity.community_id == community_id,
                    KnowledgeGraphCommunity.graph_status == "active",
                    KnowledgeGraphCommunity.graph_fingerprint
                    == graph_fingerprint,
                    KnowledgeGraphCommunity.fact_report_graph_fingerprint
                    == graph_fingerprint,
                    KnowledgeGraphCommunity.fact_report_version
                    == report_version,
                )
                .values(
                    fact_report_status="ready",
                    fact_report_error="",
                    fact_semantic_synced_version=semantic_version,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            return bool(result.rowcount)

    def mark_fact_failed(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
        error: str,
    ) -> bool:
        return self._mark_status(
            community_id=community_id,
            graph_fingerprint=graph_fingerprint,
            values={
                "fact_report_status": "failed",
                "fact_report_error": str(error or "")[:4000],
            },
        )

    def mark_projection_generating(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
        fact_report_version: int,
    ) -> bool:
        with get_session(self.target) as session:
            result = session.execute(
                update(KnowledgeGraphCommunity)
                .where(
                    KnowledgeGraphCommunity.community_id == community_id,
                    KnowledgeGraphCommunity.graph_status == "active",
                    KnowledgeGraphCommunity.graph_fingerprint
                    == graph_fingerprint,
                    KnowledgeGraphCommunity.fact_report_status == "ready",
                    KnowledgeGraphCommunity.fact_report_version
                    == fact_report_version,
                )
                .values(
                    projection_status="generating",
                    projection_error="",
                    updated_at=datetime.now(timezone.utc),
                )
            )
            return bool(result.rowcount)

    def save_projections(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
        fact_report_version: int,
        projections: list[dict],
        generator_version: str,
    ) -> int | None:
        now = datetime.now(timezone.utc)
        with get_session(self.target) as session:
            row = session.scalar(
                select(KnowledgeGraphCommunity)
                .where(
                    KnowledgeGraphCommunity.community_id == community_id,
                    KnowledgeGraphCommunity.graph_status == "active",
                )
                .with_for_update()
            )
            if (
                row is None
                or row.graph_fingerprint != graph_fingerprint
                or row.fact_report_status != "ready"
                or row.fact_report_graph_fingerprint != graph_fingerprint
                or int(row.fact_report_version or 0) != fact_report_version
            ):
                return None
            if (
                row.projection_graph_fingerprint == graph_fingerprint
                and row.projection_fact_report_version == fact_report_version
                and row.projection_generator_version == generator_version
                and row.projection_status in {"publishing", "ready", "empty"}
            ):
                return int(row.projection_version or 0)
            row.conditional_projections = list(projections)
            row.projection_version = int(row.projection_version or 0) + 1
            row.projection_generator_version = generator_version
            row.projection_graph_fingerprint = graph_fingerprint
            row.projection_fact_report_version = fact_report_version
            row.projection_status = "publishing" if projections else "empty"
            row.projection_error = ""
            row.projection_generated_at = now
            row.projection_semantic_synced_version = ""
            return row.projection_version

    def mark_projection_semantic_ready(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
        fact_report_version: int,
        projection_version: int,
        semantic_version: str,
        empty: bool,
    ) -> bool:
        with get_session(self.target) as session:
            result = session.execute(
                update(KnowledgeGraphCommunity)
                .where(
                    KnowledgeGraphCommunity.community_id == community_id,
                    KnowledgeGraphCommunity.graph_status == "active",
                    KnowledgeGraphCommunity.graph_fingerprint
                    == graph_fingerprint,
                    KnowledgeGraphCommunity.fact_report_version
                    == fact_report_version,
                    KnowledgeGraphCommunity.projection_version
                    == projection_version,
                    KnowledgeGraphCommunity.projection_graph_fingerprint
                    == graph_fingerprint,
                )
                .values(
                    projection_status="empty" if empty else "ready",
                    projection_error="",
                    projection_semantic_synced_version=semantic_version,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            return bool(result.rowcount)

    def mark_projection_failed(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
        error: str,
    ) -> bool:
        return self._mark_status(
            community_id=community_id,
            graph_fingerprint=graph_fingerprint,
            values={
                "projection_status": "failed",
                "projection_error": str(error or "")[:4000],
            },
        )

    def list_pending_report_dispatches(
        self,
        community_ids: list[str],
    ) -> list[CommunityReportDispatch]:
        identities = [
            item
            for item in dict.fromkeys(
                str(value).strip() for value in community_ids
            )
            if item
        ]
        if not identities:
            return []
        with get_session(self.target) as session:
            rows = list(
                session.scalars(
                    select(KnowledgeGraphCommunity)
                    .where(
                        KnowledgeGraphCommunity.community_id.in_(identities),
                        KnowledgeGraphCommunity.graph_status == "active",
                        KnowledgeGraphCommunity.report_task_dispatched_fingerprint
                        != KnowledgeGraphCommunity.graph_fingerprint,
                    )
                    .order_by(KnowledgeGraphCommunity.community_id)
                ).all()
            )
        return [
            CommunityReportDispatch(
                community_id=row.community_id,
                graph_fingerprint=row.graph_fingerprint,
                has_previous_report=bool(row.fact_report_version),
            )
            for row in rows
        ]

    def mark_report_task_dispatched(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
    ) -> bool:
        with get_session(self.target) as session:
            result = session.execute(
                update(KnowledgeGraphCommunity)
                .where(
                    KnowledgeGraphCommunity.community_id == community_id,
                    KnowledgeGraphCommunity.graph_fingerprint
                    == graph_fingerprint,
                    KnowledgeGraphCommunity.graph_status == "active",
                )
                .values(
                    report_task_dispatched_fingerprint=graph_fingerprint,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            return bool(result.rowcount)

    def mark_projection_task_dispatched(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
        fact_report_version: int,
    ) -> bool:
        with get_session(self.target) as session:
            result = session.execute(
                update(KnowledgeGraphCommunity)
                .where(
                    KnowledgeGraphCommunity.community_id == community_id,
                    KnowledgeGraphCommunity.graph_fingerprint
                    == graph_fingerprint,
                    KnowledgeGraphCommunity.fact_report_status == "ready",
                    KnowledgeGraphCommunity.fact_report_version
                    == fact_report_version,
                )
                .values(
                    projection_task_dispatched_version=fact_report_version,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            return bool(result.rowcount)

    def _mark_status(
        self,
        *,
        community_id: str,
        graph_fingerprint: str,
        values: dict,
    ) -> bool:
        with get_session(self.target) as session:
            result = session.execute(
                update(KnowledgeGraphCommunity)
                .where(
                    KnowledgeGraphCommunity.community_id == community_id,
                    KnowledgeGraphCommunity.graph_status == "active",
                    KnowledgeGraphCommunity.graph_fingerprint
                    == graph_fingerprint,
                )
                .values(**values, updated_at=datetime.now(timezone.utc))
            )
            return bool(result.rowcount)


def _ordered_unique(values) -> list[str]:
    return [
        item
        for item in dict.fromkeys(str(value).strip() for value in values)
        if item
    ]


def _load_communities_containing_cards(
    session,
    *,
    adapter_name: str,
    card_ids: list[str],
) -> list[KnowledgeGraphCommunity]:
    if not card_ids:
        return []
    return list(
        session.scalars(
            select(KnowledgeGraphCommunity).where(
                KnowledgeGraphCommunity.adapter_name == adapter_name,
                KnowledgeGraphCommunity.graph_status == "active",
                KnowledgeGraphCommunity.member_card_ids.has_any(
                    array(_ordered_unique(card_ids))
                ),
            )
        ).all()
    )


def _community_ids_for_cards(
    session,
    *,
    adapter_name: str,
    card_ids: set[str],
) -> set[str]:
    identities = sorted(card_ids)
    if not identities:
        return set()
    membership_ids = set(
        session.scalars(
            select(KnowledgeGraphCommunityMembership.community_id).where(
                KnowledgeGraphCommunityMembership.adapter_name == adapter_name,
                KnowledgeGraphCommunityMembership.card_id.in_(identities),
            )
        ).all()
    )
    if membership_ids:
        return membership_ids
    # Migration compatibility only: once Membership is backfilled this path is
    # not used by normal production refreshes.
    return {
        row.community_id
        for row in _load_communities_containing_cards(
            session,
            adapter_name=adapter_name,
            card_ids=identities,
        )
    }


def _load_communities_by_ids(
    session,
    *,
    adapter_name: str,
    community_ids: set[str],
) -> dict[str, KnowledgeGraphCommunity]:
    if not community_ids:
        return {}
    rows = session.scalars(
        select(KnowledgeGraphCommunity).where(
            KnowledgeGraphCommunity.adapter_name == adapter_name,
            KnowledgeGraphCommunity.graph_status == "active",
            KnowledgeGraphCommunity.community_id.in_(sorted(community_ids)),
        )
    ).all()
    return {row.community_id: row for row in rows}


def _raw_card_ids_for_representatives(
    session,
    *,
    adapter_name: str,
    representative_card_ids: set[str],
) -> list[str]:
    if not representative_card_ids:
        return []
    representatives = list(
        session.scalars(
            select(KnowledgeCognitiveCard).where(
                KnowledgeCognitiveCard.adapter_name == adapter_name,
                KnowledgeCognitiveCard.status == "active",
                KnowledgeCognitiveCard.cognitive_card_id.in_(
                    sorted(representative_card_ids)
                ),
            )
        ).all()
    )
    fact_ids = sorted({row.fact_id for row in representatives if row.fact_id})
    if not fact_ids:
        return sorted(representative_card_ids)
    return list(
        session.scalars(
            select(KnowledgeCognitiveCard.cognitive_card_id).where(
                KnowledgeCognitiveCard.adapter_name == adapter_name,
                KnowledgeCognitiveCard.status == "active",
                KnowledgeCognitiveCard.fact_id.in_(fact_ids),
            )
        ).all()
    )


def _load_active_edges_within_cards(
    session,
    *,
    card_ids: list[str],
    batch_size: int = 1_000,
) -> list[KnowledgeCardRelation]:
    identities = _ordered_unique(card_ids)
    if not identities:
        return []
    allowed = set(identities)
    edge_by_id: dict[str, KnowledgeCardRelation] = {}
    for edge in _load_active_edges_touching_cards(
        session,
        card_ids=identities,
        batch_size=batch_size,
    ):
        if edge.source_card_id in allowed and edge.target_card_id in allowed:
            edge_by_id[edge.id] = edge
    return [edge_by_id[edge_id] for edge_id in sorted(edge_by_id)]


def _derive_boundary_community_relations(
    session,
    *,
    adapter_name: str,
    scoped_community_ids: set[str],
    representative_card_ids: set[str],
) -> list[RelationGraphCommunityRelation]:
    if not scoped_community_ids or not representative_card_ids:
        return []
    raw_scope = _raw_card_ids_for_representatives(
        session,
        adapter_name=adapter_name,
        representative_card_ids=representative_card_ids,
    )
    edge_rows = _load_active_edges_touching_cards(session, card_ids=raw_scope)
    endpoint_ids = {
        card_id
        for edge in edge_rows
        for card_id in (edge.source_card_id, edge.target_card_id)
    }
    representative_by_card = _fact_representatives_for_cards(
        session,
        adapter_name=adapter_name,
        card_ids=endpoint_ids,
    )
    projected = project_edges_to_fact_representatives(
        [
            RelationGraphEdge(
                edge_id=edge.id,
                source_card_id=edge.source_card_id,
                target_card_id=edge.target_card_id,
                relation_kind=edge.relation_kind,
                decision_class=edge.decision_class,
                content_version=edge.content_version,
            )
            for edge in edge_rows
            if edge.relation_kind in RELATION_KINDS
            and edge.decision_class in {"observed", "inferred"}
            and edge.content_version
        ],
        representative_by_card_id=representative_by_card,
    )
    projected_card_ids = {
        card_id
        for edge in projected
        for card_id in (edge.source_card_id, edge.target_card_id)
    }
    memberships = session.execute(
        select(
            KnowledgeGraphCommunityMembership.card_id,
            KnowledgeGraphCommunityMembership.community_id,
        ).where(
            KnowledgeGraphCommunityMembership.adapter_name == adapter_name,
            KnowledgeGraphCommunityMembership.card_id.in_(projected_card_ids),
        )
    ).all()
    community_by_card = {card_id: community_id for card_id, community_id in memberships}
    return [
        relation
        for relation in derive_community_relations_from_membership(
            edges=projected,
            community_by_card=community_by_card,
        )
        if relation.source_community_id in scoped_community_ids
        or relation.target_community_id in scoped_community_ids
    ]


def _load_active_edges_touching_cards(
    session,
    *,
    card_ids: list[str],
    batch_size: int = 1_000,
) -> list[KnowledgeCardRelation]:
    """Load incident edges through bounded, index-friendly endpoint scans."""

    identities = _ordered_unique(card_ids)
    if not identities:
        return []
    edge_by_id: dict[str, KnowledgeCardRelation] = {}
    for offset in range(0, len(identities), batch_size):
        batch = identities[offset : offset + batch_size]
        for endpoint_column in (
            KnowledgeCardRelation.source_card_id,
            KnowledgeCardRelation.target_card_id,
        ):
            rows = session.scalars(
                select(KnowledgeCardRelation).where(
                    KnowledgeCardRelation.status == "active",
                    endpoint_column.in_(batch),
                )
            ).all()
            for row in rows:
                edge_by_id[row.id] = row
    return [edge_by_id[edge_id] for edge_id in sorted(edge_by_id)]


def _fact_representatives_for_cards(
    session,
    *,
    adapter_name: str,
    card_ids: set[str],
) -> dict[str, str]:
    """Map every requested Card to the oldest active Card in its fact group."""

    identities = sorted(
        str(card_id).strip()
        for card_id in card_ids
        if str(card_id).strip()
    )
    if not identities:
        return {}
    requested_rows = list(
        session.scalars(
            select(KnowledgeCognitiveCard).where(
                KnowledgeCognitiveCard.adapter_name == adapter_name,
                KnowledgeCognitiveCard.status == "active",
                KnowledgeCognitiveCard.cognitive_card_id.in_(identities),
            )
        ).all()
    )
    stored_fact_ids = {
        row.fact_id
        for row in requested_rows
        if row.fact_id
    }
    fact_rows = (
        list(
            session.scalars(
                select(KnowledgeCognitiveCard)
                .where(
                    KnowledgeCognitiveCard.adapter_name == adapter_name,
                    KnowledgeCognitiveCard.status == "active",
                    KnowledgeCognitiveCard.fact_id.in_(
                        sorted(stored_fact_ids)
                    ),
                )
                .order_by(
                    KnowledgeCognitiveCard.created_at.asc().nulls_last(),
                    KnowledgeCognitiveCard.cognitive_card_id.asc(),
                )
            ).all()
        )
        if stored_fact_ids
        else sorted(
            requested_rows,
            key=lambda row: row.cognitive_card_id,
        )
    )
    representative_by_fact: dict[str, str] = {}
    for row in fact_rows:
        fact_id = row.fact_id or default_card_fact_id(row.cognitive_card_id)
        representative_by_fact.setdefault(fact_id, row.cognitive_card_id)
    return {
        row.cognitive_card_id: representative_by_fact.get(
            row.fact_id or default_card_fact_id(row.cognitive_card_id),
            row.cognitive_card_id,
        )
        for row in requested_rows
    }


def _is_valid_graph_edge(
    edge: KnowledgeCardRelation,
    *,
    source: KnowledgeCognitiveCard | None,
    target: KnowledgeCognitiveCard | None,
    adapter_name: str,
) -> bool:
    if edge.status != "active":
        return False
    if edge.relation_kind not in RELATION_KINDS:
        return False
    if edge.decision_class not in {"observed", "inferred"}:
        return False
    if not edge.source_card_id or not edge.target_card_id:
        return False
    if edge.source_card_id == edge.target_card_id:
        return False
    if not edge.basis or not edge.content_version:
        return False
    if edge.decision_class == "inferred" and not edge.inference_mechanism:
        return False
    if source is None or target is None:
        return False
    if source.status != "active" or target.status != "active":
        return False
    if source.adapter_name != adapter_name or target.adapter_name != adapter_name:
        return False
    source_refs = set(edge.source_evidence_refs or [])
    target_refs = set(edge.target_evidence_refs or [])
    if not source_refs or not target_refs:
        return False
    if not source_refs.issubset(set(source.focus_evidence_refs or [])):
        return False
    if not target_refs.issubset(set(target.focus_evidence_refs or [])):
        return False
    return True


def _component_matches_row(
    component: RelationGraphCommunityComponent,
    row: KnowledgeGraphCommunity | None,
) -> bool:
    if row is None:
        return False
    return (
        row.graph_status == "active"
        and row.identity_anchor_card_id == component.identity_anchor_card_id
        and row.graph_fingerprint == component.graph_fingerprint
        and tuple(row.member_card_ids or []) == component.member_card_ids
        and tuple(row.member_edge_ids or []) == component.member_edge_ids
    )


def _community_relation_matches_row(
    relation: RelationGraphCommunityRelation,
    row: KnowledgeGraphCommunityRelation | None,
) -> bool:
    if row is None:
        return False
    return (
        row.status == "active"
        and row.source_community_id == relation.source_community_id
        and row.target_community_id == relation.target_community_id
        and row.relation_kind == relation.relation_kind
        and tuple(row.supporting_edge_ids or [])
        == relation.supporting_edge_ids
        and int(row.observed_edge_count or 0)
        == relation.observed_edge_count
        and int(row.inferred_edge_count or 0)
        == relation.inferred_edge_count
        and row.relation_fingerprint == relation.relation_fingerprint
    )


def _community_relation_values(
    *,
    relation: RelationGraphCommunityRelation,
    now: datetime,
) -> dict:
    return {
        "id": relation.relation_id,
        "source_community_id": relation.source_community_id,
        "target_community_id": relation.target_community_id,
        "relation_kind": relation.relation_kind,
        "supporting_edge_ids": list(relation.supporting_edge_ids),
        "observed_edge_count": relation.observed_edge_count,
        "inferred_edge_count": relation.inferred_edge_count,
        "relation_fingerprint": relation.relation_fingerprint,
        "status": "active",
        "updated_at": now,
    }


def _community_values(
    *,
    adapter_name: str,
    component: RelationGraphCommunityComponent,
    existing: KnowledgeGraphCommunity | None,
    now: datetime,
) -> dict:
    had_fact_report = bool(existing and existing.fact_report)
    had_projection = bool(existing and existing.projection_version > 0)
    return {
        "community_id": component.community_id,
        "adapter_name": adapter_name,
        "identity_anchor_card_id": component.identity_anchor_card_id,
        "member_card_ids": list(component.member_card_ids),
        "member_edge_ids": list(component.member_edge_ids),
        "graph_fingerprint": component.graph_fingerprint,
        "graph_version": int(existing.graph_version or 0) + 1 if existing else 1,
        "graph_status": "active",
        "title": existing.title if existing else "",
        "fact_report": existing.fact_report if existing else "",
        "fact_referenced_card_ids": (
            list(existing.fact_referenced_card_ids or []) if existing else []
        ),
        "fact_referenced_edge_ids": (
            list(existing.fact_referenced_edge_ids or []) if existing else []
        ),
        "fact_report_version": int(existing.fact_report_version or 0) if existing else 0,
        "fact_report_generator_version": (
            existing.fact_report_generator_version if existing else ""
        ),
        "fact_report_graph_fingerprint": (
            existing.fact_report_graph_fingerprint if existing else ""
        ),
        "fact_report_status": "dirty" if had_fact_report else "missing",
        "fact_report_error": "",
        "report_task_dispatched_fingerprint": (
            existing.report_task_dispatched_fingerprint if existing else ""
        ),
        "fact_semantic_synced_version": (
            existing.fact_semantic_synced_version if existing else ""
        ),
        "conditional_projections": (
            list(existing.conditional_projections or []) if existing else []
        ),
        "projection_version": int(existing.projection_version or 0) if existing else 0,
        "projection_generator_version": (
            existing.projection_generator_version if existing else ""
        ),
        "projection_graph_fingerprint": (
            existing.projection_graph_fingerprint if existing else ""
        ),
        "projection_fact_report_version": (
            int(existing.projection_fact_report_version or 0) if existing else 0
        ),
        "projection_status": "stale" if had_projection else "missing",
        "projection_error": "",
        "projection_task_dispatched_version": (
            int(existing.projection_task_dispatched_version or 0) if existing else 0
        ),
        "projection_semantic_synced_version": (
            existing.projection_semantic_synced_version if existing else ""
        ),
        "graph_changed_at": now,
        "fact_report_generated_at": (
            existing.fact_report_generated_at if existing else None
        ),
        "projection_generated_at": (
            existing.projection_generated_at if existing else None
        ),
        "updated_at": now,
    }
