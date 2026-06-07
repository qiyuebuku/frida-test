"""KnowledgeRepository SQLAlchemy implementation."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Literal
from uuid import uuid4

from sqlalchemy import case, cast, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceStatus, EvidenceType, NodeStatus
from src.domain.knowledge.chunking import build_evidence_chunks, evidence_content_for_chunking
from src.domain.knowledge.graph_index import (
    GraphIndexCommunity,
    GraphIndexDelta,
    GraphIndexFinding,
    GraphIndexUnassignedSignal,
)
from src.domain.knowledge.quality import ReviewAction, ReviewEntry
from src.domain.knowledge.repositories import KnowledgeRepository
from src.domain.knowledge.retrieval_eval import (
    RetrievalEvalMetric,
    RetrievalEvalRun,
    RetrievalLabel,
    RetrievalTraceSnapshot,
)
from src.domain.knowledge.retrieval_profile import profile_span
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode, EvidenceChunk
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCompilationRun,
    KnowledgeEdge,
    KnowledgeEdgeEvidence,
    KnowledgeEdgeEvidenceChunk,
    KnowledgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeGraphAdjacency,
    KnowledgeGraphCommunity,
    KnowledgeGraphDelta,
    KnowledgeGraphFinding,
    KnowledgeGraphUnassignedSignal,
    KnowledgeNode,
    KnowledgeReviewItem,
    KnowledgeRetrievalEvalMetric,
    KnowledgeRetrievalEvalRun,
    KnowledgeRetrievalLabel,
    KnowledgeRetrievalTraceSnapshot,
)

Target = Literal["prod", "test"]


class KnowledgeRepositoryImpl(KnowledgeRepository):
    def __init__(self, session: Session | None = None, target: Target | None = None):
        self._session = session
        self._target = target
        self._evidence_version_columns_ready = False
        self._retrieval_quality_tables_ready = False
        self._evidence_chunk_manifest_columns_ready = False

    def ping(self) -> None:
        with profile_span("kg_repository.ping"):
            with self._session_scope() as session:
                session.execute(text("select 1"))

    def upsert_nodes(self, nodes: list[CompiledNode]) -> int:
        if not nodes:
            return 0
        with profile_span("kg_repository.upsert_nodes", nodes=len(nodes)):
            rows = [_node_values(node) for node in nodes]
            with self._session_scope() as session:
                stmt = pg_insert(KnowledgeNode).values(rows)
                excluded = stmt.excluded
                result = session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["node_id"],
                        set_={
                            "adapter_name": excluded.adapter_name,
                            "adapter_version": excluded.adapter_version,
                            "node_type": excluded.node_type,
                            "stable_key": excluded.stable_key,
                            "canonical_name": excluded.canonical_name,
                            "aliases": excluded.aliases,
                            "external_ids": excluded.external_ids,
                            "properties": excluded.properties,
                            "status": excluded.status,
                            "version": excluded.version,
                            "updated_at": datetime.now(timezone.utc),
                        },
                    )
                )
                return result.rowcount or 0

    def upsert_edges(self, edges: list[CompiledEdge]) -> int:
        if not edges:
            return 0
        with profile_span("kg_repository.upsert_edges", edges=len(edges)):
            rows = [_edge_values(edge) for edge in edges]
            with self._session_scope() as session:
                stmt = pg_insert(KnowledgeEdge).values(rows)
                excluded = stmt.excluded
                result = session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["edge_id"],
                        set_={
                            "adapter_name": excluded.adapter_name,
                            "adapter_version": excluded.adapter_version,
                            "source_node_id": excluded.source_node_id,
                            "target_node_id": excluded.target_node_id,
                            "relation_type": excluded.relation_type,
                            "properties": excluded.properties,
                            "confidence_label": excluded.confidence_label,
                            "confidence_score": excluded.confidence_score,
                            "status": excluded.status,
                            "valid_from": excluded.valid_from,
                            "valid_to": excluded.valid_to,
                            "version": excluded.version,
                            "updated_at": datetime.now(timezone.utc),
                        },
                    )
                )
                self._attach_edges_evidence_in_session(session, edges)
                return result.rowcount or 0

    def upsert_evidence(self, evidence: list[CompiledEvidence]) -> int:
        if not evidence:
            return 0
        with profile_span("kg_repository.upsert_evidence", evidence=len(evidence)):
            rows = [_evidence_values(item) for item in evidence]
            with self._session_scope() as session:
                self._ensure_evidence_version_columns(session)
                superseded = self._supersede_same_source_evidence_in_session(session, evidence)
                stmt = pg_insert(KnowledgeEvidence).values(rows)
                excluded = stmt.excluded
                result = session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["evidence_id"],
                        set_={
                            "adapter_name": excluded.adapter_name,
                            "source_type": excluded.source_type,
                            "source_id": excluded.source_id,
                            "evidence_type": excluded.evidence_type,
                            "content": excluded.content,
                            "payload": excluded.payload,
                            "span_start": excluded.span_start,
                            "span_end": excluded.span_end,
                            "version": excluded.version,
                            "status": excluded.status,
                            "source_fingerprint": excluded.source_fingerprint,
                            "superseded_by": excluded.superseded_by,
                            KnowledgeEvidence.metadata_: excluded["metadata"],
                            "updated_at": datetime.now(timezone.utc),
                        },
                    )
                )
                if superseded["edge_ids"]:
                    session.execute(
                        delete(KnowledgeGraphAdjacency).where(
                            KnowledgeGraphAdjacency.edge_id.in_(superseded["edge_ids"])
                        )
                    )
                return result.rowcount or 0

    def attach_edge_evidence(self, edge_id: str, evidence_ids: list[str]) -> int:
        with self._session_scope() as session:
            return self._attach_edge_evidence_in_session(session, edge_id, evidence_ids)

    def get_node(self, node_id: str) -> CompiledNode | None:
        with self._session_scope() as session:
            row = session.scalar(select(KnowledgeNode).where(KnowledgeNode.node_id == node_id))
            return _node_schema(row) if row else None

    def get_edge(self, edge_id: str) -> CompiledEdge | None:
        with self._session_scope() as session:
            row = session.scalar(select(KnowledgeEdge).where(KnowledgeEdge.edge_id == edge_id))
            if not row:
                return None
            evidence_ids = session.scalars(
                select(KnowledgeEdgeEvidence.evidence_id).where(
                    KnowledgeEdgeEvidence.edge_id == edge_id
                )
            ).all()
            return _edge_schema(row, list(evidence_ids))

    def get_evidence(self, evidence_id: str) -> CompiledEvidence | None:
        with self._session_scope() as session:
            self._ensure_evidence_version_columns(session)
            row = session.scalar(
                select(KnowledgeEvidence)
                .where(KnowledgeEvidence.evidence_id == evidence_id)
                .where(KnowledgeEvidence.status == EvidenceStatus.ACTIVE.value)
            )
            return _evidence_schema(row) if row else None

    def get_edge_evidence(self, edge_id: str) -> list[CompiledEvidence]:
        with self._session_scope() as session:
            self._ensure_evidence_version_columns(session)
            rows = session.scalars(
                select(KnowledgeEvidence)
                .join(
                    KnowledgeEdgeEvidence,
                    KnowledgeEvidence.evidence_id == KnowledgeEdgeEvidence.evidence_id,
                )
                .where(KnowledgeEdgeEvidence.edge_id == edge_id)
                .where(KnowledgeEvidence.status == EvidenceStatus.ACTIVE.value)
                .order_by(KnowledgeEvidence.evidence_id)
            ).all()
            return [_evidence_schema(row) for row in rows]

    def list_nodes(self, adapter_name: str) -> list[CompiledNode]:
        with profile_span("kg_repository.list_nodes", adapter=adapter_name):
            with self._session_scope() as session:
                rows = session.scalars(
                    select(KnowledgeNode)
                    .where(KnowledgeNode.adapter_name == adapter_name)
                    .order_by(KnowledgeNode.node_id)
                ).all()
                return [_node_schema(row) for row in rows]

    def list_edges(self, adapter_name: str) -> list[CompiledEdge]:
        with profile_span("kg_repository.list_edges", adapter=adapter_name):
            with self._session_scope() as session:
                rows = session.scalars(
                    select(KnowledgeEdge)
                    .where(KnowledgeEdge.adapter_name == adapter_name)
                    .order_by(KnowledgeEdge.edge_id)
                ).all()
                edge_ids = [row.edge_id for row in rows]
                evidence_by_edge: dict[str, list[str]] = {edge_id: [] for edge_id in edge_ids}
                if edge_ids:
                    with profile_span("kg_repository.list_edges.load_evidence_links", edges=len(edge_ids)):
                        links = session.execute(
                            select(KnowledgeEdgeEvidence.edge_id, KnowledgeEdgeEvidence.evidence_id)
                            .where(KnowledgeEdgeEvidence.edge_id.in_(edge_ids))
                            .order_by(KnowledgeEdgeEvidence.edge_id, KnowledgeEdgeEvidence.evidence_id)
                        ).all()
                    for edge_id, evidence_id in links:
                        evidence_by_edge.setdefault(edge_id, []).append(evidence_id)
                return [_edge_schema(row, evidence_by_edge.get(row.edge_id, [])) for row in rows]

    def count_graph_index_materials(self, adapter_name: str) -> dict[str, int]:
        with profile_span("kg_repository.count_graph_index_materials", adapter=adapter_name):
            with self._session_scope() as session:
                nodes = session.scalar(
                    select(func.count()).select_from(KnowledgeNode).where(KnowledgeNode.adapter_name == adapter_name)
                )
                edges = session.scalar(
                    select(func.count()).select_from(KnowledgeEdge).where(KnowledgeEdge.adapter_name == adapter_name)
                )
                chunks = session.scalar(
                    select(func.count())
                    .select_from(KnowledgeEvidenceChunk)
                    .where(KnowledgeEvidenceChunk.adapter_name == adapter_name)
                )
                return {"nodes": int(nodes or 0), "edges": int(edges or 0), "chunks": int(chunks or 0)}

    def list_graph_index_materials(
        self,
        adapter_name: str,
        *,
        node_ids: list[str],
        edge_ids: list[str],
        evidence_ids: list[str],
        chunk_ids: list[str],
    ) -> dict[str, list[Any]]:
        with profile_span(
            "kg_repository.list_graph_index_materials",
            adapter=adapter_name,
            node_ids=len(node_ids),
            edge_ids=len(edge_ids),
            evidence_ids=len(evidence_ids),
            chunk_ids=len(chunk_ids),
        ):
            with self._session_scope() as session:
                node_set = set(_ordered_unique(node_ids))
                edge_set = set(_ordered_unique(edge_ids))
                evidence_set = set(_ordered_unique(evidence_ids))
                chunk_set = set(_ordered_unique(chunk_ids))

                edge_stmt = select(KnowledgeEdge).where(KnowledgeEdge.adapter_name == adapter_name)
                predicates = []
                if edge_set:
                    predicates.append(KnowledgeEdge.edge_id.in_(edge_set))
                if node_set:
                    predicates.append(or_(KnowledgeEdge.source_node_id.in_(node_set), KnowledgeEdge.target_node_id.in_(node_set)))
                if predicates:
                    edge_stmt = edge_stmt.where(or_(*predicates))
                else:
                    edge_stmt = edge_stmt.where(False)
                edge_rows = list(session.scalars(edge_stmt.order_by(KnowledgeEdge.edge_id)).all())
                for row in edge_rows:
                    node_set.add(row.source_node_id)
                    node_set.add(row.target_node_id)
                    edge_set.add(row.edge_id)

                evidence_by_edge: dict[str, list[str]] = {edge_id: [] for edge_id in edge_set}
                if edge_set:
                    links = session.execute(
                        select(KnowledgeEdgeEvidence.edge_id, KnowledgeEdgeEvidence.evidence_id)
                        .where(KnowledgeEdgeEvidence.edge_id.in_(edge_set))
                        .order_by(KnowledgeEdgeEvidence.edge_id, KnowledgeEdgeEvidence.evidence_id)
                    ).all()
                    for edge_id, evidence_id in links:
                        evidence_by_edge.setdefault(edge_id, []).append(evidence_id)
                        evidence_set.add(evidence_id)

                node_rows = []
                if node_set:
                    node_rows = list(
                        session.scalars(
                            select(KnowledgeNode)
                            .where(KnowledgeNode.adapter_name == adapter_name, KnowledgeNode.node_id.in_(node_set))
                            .order_by(KnowledgeNode.node_id)
                        ).all()
                    )

                chunk_stmt = select(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.adapter_name == adapter_name)
                chunk_predicates = []
                if chunk_set:
                    chunk_predicates.append(KnowledgeEvidenceChunk.chunk_id.in_(chunk_set))
                if evidence_set:
                    chunk_predicates.append(KnowledgeEvidenceChunk.evidence_id.in_(evidence_set))
                if chunk_predicates:
                    chunk_stmt = chunk_stmt.where(or_(*chunk_predicates))
                else:
                    chunk_stmt = chunk_stmt.where(False)
                chunk_rows = list(session.scalars(chunk_stmt.order_by(KnowledgeEvidenceChunk.evidence_id, KnowledgeEvidenceChunk.chunk_id)).all())
                evidence_rows = {}
                evidence_ids_for_chunks = _ordered_unique([row.evidence_id for row in chunk_rows])
                if evidence_ids_for_chunks:
                    evidence_rows = {
                        row.evidence_id: row
                        for row in session.scalars(
                            select(KnowledgeEvidence).where(KnowledgeEvidence.evidence_id.in_(evidence_ids_for_chunks))
                        ).all()
                    }
                chunks = [
                    _chunk_schema(row, evidence_rows[row.evidence_id])
                    for row in chunk_rows
                    if row.evidence_id in evidence_rows
                ]
                return {
                    "nodes": [_node_schema(row) for row in node_rows],
                    "edges": [_edge_schema(row, evidence_by_edge.get(row.edge_id, [])) for row in edge_rows],
                    "chunks": chunks,
                }

    def list_evidence(self, adapter_name: str, *, include_inactive: bool = False) -> list[CompiledEvidence]:
        with profile_span(
            "kg_repository.list_evidence",
            adapter=adapter_name,
            include_inactive=include_inactive,
        ):
            with self._session_scope() as session:
                self._ensure_evidence_version_columns(session)
                stmt = select(KnowledgeEvidence).where(KnowledgeEvidence.adapter_name == adapter_name)
                if not include_inactive:
                    stmt = stmt.where(KnowledgeEvidence.status == EvidenceStatus.ACTIVE.value)
                rows = session.scalars(stmt.order_by(KnowledgeEvidence.evidence_id)).all()
                return [_evidence_schema(row) for row in rows]

    def cleanup_evidence_versions(self, adapter_name: str) -> dict[str, Any]:
        with profile_span("kg_repository.cleanup_evidence_versions", adapter=adapter_name):
            with self._session_scope() as session:
                self._ensure_evidence_version_columns(session)
                with profile_span("kg_repository.cleanup_evidence_versions.load_source_keys"):
                    source_keys = session.execute(
                        select(KnowledgeEvidence.source_type, KnowledgeEvidence.source_id)
                        .where(KnowledgeEvidence.adapter_name == adapter_name)
                        .group_by(KnowledgeEvidence.source_type, KnowledgeEvidence.source_id)
                    ).all()
                total_evidence_ids: set[str] = set()
                total_edge_ids: set[str] = set()
                for source_type, source_id in source_keys:
                    with profile_span(
                        "kg_repository.cleanup_evidence_versions.load_source_rows",
                        source_type=source_type,
                        source_id=source_id,
                    ):
                        rows = session.scalars(
                            select(KnowledgeEvidence)
                            .where(KnowledgeEvidence.adapter_name == adapter_name)
                            .where(KnowledgeEvidence.source_type == source_type)
                            .where(KnowledgeEvidence.source_id == source_id)
                            .order_by(
                                case((KnowledgeEvidence.status == EvidenceStatus.ACTIVE.value, 0), else_=1),
                                KnowledgeEvidence.created_at.desc().nullslast(),
                                KnowledgeEvidence.evidence_id.desc(),
                            )
                        ).all()
                    if len(rows) <= 1:
                        continue
                    keep = rows[0]
                    stale_ids = [row.evidence_id for row in rows[1:]]
                    with profile_span("kg_repository.cleanup_evidence_versions.keep_active"):
                        session.execute(
                            update(KnowledgeEvidence)
                            .where(KnowledgeEvidence.evidence_id == keep.evidence_id)
                            .values(
                                status=EvidenceStatus.ACTIVE.value,
                                superseded_by=None,
                                updated_at=datetime.now(timezone.utc),
                            )
                        )
                    summary = self._supersede_evidence_ids_in_session(
                        session,
                        stale_ids,
                        superseded_by=keep.evidence_id,
                    )
                    total_evidence_ids.update(stale_ids)
                    total_edge_ids.update(summary["edge_ids"])
                evidence_ids = sorted(total_evidence_ids)
                edge_ids = sorted(total_edge_ids)
                return {
                    "evidence": len(evidence_ids),
                    "edges": len(edge_ids),
                    "evidence_ids": evidence_ids,
                    "edge_ids": edge_ids,
                }

    def rebuild_graph_adjacency(self, adapter_name: str) -> int:
        with profile_span("kg_repository.rebuild_graph_adjacency", adapter=adapter_name):
            with self._session_scope() as session:
                with profile_span("kg_repository.rebuild_graph_adjacency.delete_old"):
                    session.execute(
                        delete(KnowledgeGraphAdjacency).where(
                            KnowledgeGraphAdjacency.adapter_name == adapter_name
                        )
                    )
                with profile_span("kg_repository.rebuild_graph_adjacency.load_edges"):
                    edges = session.scalars(
                        select(KnowledgeEdge)
                        .where(KnowledgeEdge.adapter_name == adapter_name)
                        .where(KnowledgeEdge.status == EdgeStatus.ACTIVE.value)
                    ).all()
                if not edges:
                    return 0
                rows = [
                    {
                        "adapter_name": adapter_name,
                        "source_node_id": edge.source_node_id,
                        "target_node_id": edge.target_node_id,
                        "edge_id": edge.edge_id,
                        "relation_type": edge.relation_type,
                    }
                    for edge in edges
                ]
                with profile_span("kg_repository.rebuild_graph_adjacency.insert", rows=len(rows)):
                    result = session.execute(pg_insert(KnowledgeGraphAdjacency).values(rows))
                return result.rowcount or 0

    def upsert_graph_adjacency(self, edges: list[CompiledEdge]) -> int:
        if not edges:
            return 0
        with profile_span("kg_repository.upsert_graph_adjacency", edges=len(edges)):
            edge_ids = [edge.edge_id for edge in edges]
            active_edges = [edge for edge in edges if edge.status == EdgeStatus.ACTIVE]
            if not active_edges:
                return 0
            rows = [
                {
                    "adapter_name": edge.adapter_name,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "edge_id": edge.edge_id,
                    "relation_type": edge.relation_type,
                }
                for edge in active_edges
            ]
            with self._session_scope() as session:
                with profile_span("kg_repository.upsert_graph_adjacency.delete_old", edges=len(edge_ids)):
                    session.execute(
                        delete(KnowledgeGraphAdjacency).where(
                            KnowledgeGraphAdjacency.edge_id.in_(edge_ids)
                        )
                    )
                with profile_span("kg_repository.upsert_graph_adjacency.insert", rows=len(rows)):
                    result = session.execute(pg_insert(KnowledgeGraphAdjacency).values(rows))
                return result.rowcount or 0

    def get_neighbors(self, node_id: str, adapter_name: str | None = None) -> list[str]:
        with profile_span("kg_repository.get_neighbors", adapter=adapter_name or "", node_id=node_id):
            with self._session_scope() as session:
                stmt = select(KnowledgeGraphAdjacency.target_node_id).where(
                    KnowledgeGraphAdjacency.source_node_id == node_id
                )
                if adapter_name is not None:
                    stmt = stmt.where(KnowledgeGraphAdjacency.adapter_name == adapter_name)
                return list(session.scalars(stmt.order_by(KnowledgeGraphAdjacency.target_node_id)).all())

    def rebuild_evidence_chunks(self, adapter_name: str) -> int:
        with profile_span("kg_repository.rebuild_evidence_chunks", adapter=adapter_name):
            with self._session_scope() as session:
                self._ensure_evidence_chunk_manifest_columns(session)
                with profile_span("kg_repository.rebuild_evidence_chunks.delete_old"):
                    evidence_ids_for_adapter = select(KnowledgeEvidence.evidence_id).where(
                        KnowledgeEvidence.adapter_name == adapter_name
                    )
                    session.execute(
                        delete(KnowledgeEdgeEvidenceChunk).where(
                            KnowledgeEdgeEvidenceChunk.evidence_id.in_(evidence_ids_for_adapter)
                        )
                    )
                    session.execute(
                        delete(KnowledgeEvidenceChunk).where(
                            KnowledgeEvidenceChunk.adapter_name == adapter_name
                        )
                    )
                with profile_span("kg_repository.rebuild_evidence_chunks.load_evidence"):
                    evidence_rows = session.scalars(
                        select(KnowledgeEvidence)
                        .where(KnowledgeEvidence.adapter_name == adapter_name)
                        .where(KnowledgeEvidence.status == EvidenceStatus.ACTIVE.value)
                    ).all()
                with profile_span("kg_repository.rebuild_evidence_chunks.build_rows", evidence=len(evidence_rows)):
                    rows = [
                        row_values
                        for evidence_row in evidence_rows
                        for row_values in _chunk_values(evidence_row)
                    ]
                if not rows:
                    with profile_span("kg_repository.rebuild_evidence_chunks.refresh_edge_refs"):
                        _refresh_edge_evidence_chunk_refs_for_adapter(session, adapter_name)
                    return 0
                with profile_span("kg_repository.rebuild_evidence_chunks.insert", rows=len(rows)):
                    result = _insert_evidence_chunk_rows(
                        session,
                        rows,
                    )
                with profile_span("kg_repository.rebuild_evidence_chunks.refresh_edge_refs"):
                    _refresh_edge_evidence_chunk_refs_for_adapter(session, adapter_name)
                return result.rowcount or 0

    def upsert_evidence_chunks(self, evidence: list[CompiledEvidence]) -> int:
        if not evidence:
            return 0
        with profile_span("kg_repository.upsert_evidence_chunks", evidence=len(evidence)):
            evidence_ids = [item.evidence_id for item in evidence]
            with self._session_scope() as session:
                self._ensure_evidence_chunk_manifest_columns(session)
                with profile_span("kg_repository.upsert_evidence_chunks.build_rows", evidence=len(evidence)):
                    rows = [
                        row_values
                        for item in evidence
                        for row_values in _chunk_values_from_compiled(
                            item,
                        )
                    ]
                with profile_span("kg_repository.upsert_evidence_chunks.delete_old", evidence=len(evidence_ids)):
                    session.execute(
                        delete(KnowledgeEdgeEvidenceChunk).where(
                            KnowledgeEdgeEvidenceChunk.evidence_id.in_(evidence_ids)
                        )
                    )
                    session.execute(
                        delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.evidence_id.in_(evidence_ids))
                    )
                if not rows:
                    with profile_span("kg_repository.upsert_evidence_chunks.refresh_edge_refs"):
                        _refresh_edge_evidence_chunk_refs_for_evidence(session, evidence_ids)
                    return 0
                with profile_span("kg_repository.upsert_evidence_chunks.insert", rows=len(rows)):
                    result = _insert_evidence_chunk_rows(
                        session,
                        rows,
                    )
                with profile_span("kg_repository.upsert_evidence_chunks.refresh_edge_refs"):
                    _refresh_edge_evidence_chunk_refs_for_evidence(session, evidence_ids)
                return result.rowcount or 0

    def list_evidence_chunks(self, adapter_name: str) -> list[EvidenceChunk]:
        with profile_span("kg_repository.list_evidence_chunks", adapter=adapter_name):
            with self._session_scope() as session:
                self._ensure_evidence_chunk_manifest_columns(session)
                rows = session.execute(
                    select(KnowledgeEvidenceChunk, KnowledgeEvidence)
                    .join(KnowledgeEvidence, KnowledgeEvidence.evidence_id == KnowledgeEvidenceChunk.evidence_id)
                    .where(KnowledgeEvidenceChunk.adapter_name == adapter_name)
                    .order_by(KnowledgeEvidenceChunk.evidence_id, KnowledgeEvidenceChunk.chunk_index)
                ).all()
                return [_chunk_schema(chunk, evidence) for chunk, evidence in rows]

    def replace_graph_index(
        self,
        adapter_name: str,
        *,
        communities: list[GraphIndexCommunity],
        findings: list[GraphIndexFinding],
        deltas: list[GraphIndexDelta] | None = None,
        unassigned_signals: list[GraphIndexUnassignedSignal] | None = None,
    ) -> dict[str, Any]:
        """Replace persisted Graph Index state for one adapter.

        PG keeps auditable structure and refs; readable report/finding text is
        published to Milvus by the caller.
        """

        with profile_span(
            "kg_repository.replace_graph_index",
            adapter=adapter_name,
            communities=len(communities),
            findings=len(findings),
            deltas=len(deltas or []),
            unassigned_signals=len(unassigned_signals or []),
        ):
            with self._session_scope() as session:
                old_communities = list(
                    session.scalars(
                        select(KnowledgeGraphCommunity).where(KnowledgeGraphCommunity.adapter_name == adapter_name)
                    ).all()
                )
                old_findings = list(
                    session.scalars(
                        select(KnowledgeGraphFinding).where(KnowledgeGraphFinding.adapter_name == adapter_name)
                    ).all()
                )
                old_deltas = list(
                    session.scalars(
                        select(KnowledgeGraphDelta).where(KnowledgeGraphDelta.adapter_name == adapter_name)
                    ).all()
                )
                old_community_ids = [row.community_id for row in old_communities]
                old_finding_ids = [row.finding_id for row in old_findings]
                old_delta_ids = [row.delta_id for row in old_deltas]
                session.execute(
                    delete(KnowledgeGraphDelta).where(KnowledgeGraphDelta.adapter_name == adapter_name)
                )
                session.execute(
                    delete(KnowledgeGraphFinding).where(KnowledgeGraphFinding.adapter_name == adapter_name)
                )
                session.execute(
                    delete(KnowledgeGraphCommunity).where(KnowledgeGraphCommunity.adapter_name == adapter_name)
                )
                session.execute(
                    delete(KnowledgeGraphUnassignedSignal).where(
                        KnowledgeGraphUnassignedSignal.adapter_name == adapter_name,
                        KnowledgeGraphUnassignedSignal.status == "active",
                    )
                )
                community_count = 0
                finding_count = 0
                delta_count = 0
                signal_count = 0
                if communities:
                    result = session.execute(
                        pg_insert(KnowledgeGraphCommunity).values(
                            [_graph_community_values(item) for item in communities]
                        )
                    )
                    community_count = result.rowcount or 0
                if findings:
                    result = session.execute(
                        pg_insert(KnowledgeGraphFinding).values(
                            [_graph_finding_values(item) for item in findings]
                        )
                    )
                    finding_count = result.rowcount or 0
                if deltas:
                    result = session.execute(
                        pg_insert(KnowledgeGraphDelta).values(
                            [_graph_delta_values(item) for item in deltas]
                        )
                    )
                    delta_count = result.rowcount or 0
                if unassigned_signals:
                    signal_insert = pg_insert(KnowledgeGraphUnassignedSignal)
                    result = session.execute(
                        signal_insert
                        .values([_graph_unassigned_signal_values(item) for item in unassigned_signals])
                        .on_conflict_do_update(
                            index_elements=[KnowledgeGraphUnassignedSignal.signal_id],
                            set_={
                                key: getattr(signal_insert.excluded, key)
                                for key in [
                                    "adapter_name",
                                    "projection",
                                    "title",
                                    "reason",
                                    "node_ids",
                                    "edge_ids",
                                    "evidence_ids",
                                    "chunk_ids",
                                    "topic_tags",
                                    "impact_tags",
                                    "event_type_tags",
                                    "relation_types",
                                    "support_score",
                                    "metrics",
                                    "status",
                                    "promoted_community_id",
                                    "promotion_attempts",
                                    "last_checked_at",
                                    "updated_at",
                                ]
                            },
                        )
                    )
                    signal_count = result.rowcount or 0
                new_target_ids = (
                    {item.community_id for item in communities}
                    | {item.finding_id for item in findings}
                    | {item.delta_id for item in deltas or []}
                )
                stale_target_ids = sorted(
                    target_id
                    for target_id in [*old_community_ids, *old_finding_ids, *old_delta_ids]
                    if target_id not in new_target_ids
                )
                return {
                    "communities": community_count,
                    "findings": finding_count,
                    "deltas": delta_count,
                    "unassigned_signals": signal_count,
                    "stale_target_ids": stale_target_ids,
                }

    def list_graph_communities(self, adapter_name: str) -> list[GraphIndexCommunity]:
        with profile_span("kg_repository.list_graph_communities", adapter=adapter_name):
            with self._session_scope() as session:
                rows = list(
                    session.scalars(
                        select(KnowledgeGraphCommunity)
                        .where(KnowledgeGraphCommunity.adapter_name == adapter_name)
                        .order_by(
                            KnowledgeGraphCommunity.projection,
                            KnowledgeGraphCommunity.level,
                            KnowledgeGraphCommunity.community_id,
                        )
                    ).all()
                )
                return [_graph_community_schema(row) for row in rows]

    def list_graph_findings(self, adapter_name: str) -> list[GraphIndexFinding]:
        with profile_span("kg_repository.list_graph_findings", adapter=adapter_name):
            with self._session_scope() as session:
                rows = list(
                    session.scalars(
                        select(KnowledgeGraphFinding)
                        .where(KnowledgeGraphFinding.adapter_name == adapter_name)
                        .order_by(
                            KnowledgeGraphFinding.projection,
                            KnowledgeGraphFinding.community_id,
                            KnowledgeGraphFinding.finding_id,
                        )
                    ).all()
                )
                return [_graph_finding_schema(row) for row in rows]

    def list_graph_deltas(self, adapter_name: str) -> list[GraphIndexDelta]:
        with profile_span("kg_repository.list_graph_deltas", adapter=adapter_name):
            with self._session_scope() as session:
                rows = list(
                    session.scalars(
                        select(KnowledgeGraphDelta)
                        .where(KnowledgeGraphDelta.adapter_name == adapter_name)
                        .order_by(
                            KnowledgeGraphDelta.projection,
                            KnowledgeGraphDelta.window_name,
                            KnowledgeGraphDelta.delta_id,
                        )
                    ).all()
                )
                return [_graph_delta_schema(row) for row in rows]

    def list_graph_unassigned_signals(
        self,
        adapter_name: str,
        *,
        status: str = "active",
    ) -> list[GraphIndexUnassignedSignal]:
        with profile_span("kg_repository.list_graph_unassigned_signals", adapter=adapter_name, status=status):
            with self._session_scope() as session:
                query = select(KnowledgeGraphUnassignedSignal).where(
                    KnowledgeGraphUnassignedSignal.adapter_name == adapter_name
                )
                if status:
                    query = query.where(KnowledgeGraphUnassignedSignal.status == status)
                rows = list(
                    session.scalars(
                        query.order_by(
                            KnowledgeGraphUnassignedSignal.projection,
                            KnowledgeGraphUnassignedSignal.signal_id,
                        )
                    ).all()
                )
                return [_graph_unassigned_signal_schema(row) for row in rows]

    def mark_graph_index_dirty(self, adapter_name: str, *, reason: str) -> int:
        with profile_span("kg_repository.mark_graph_index_dirty", adapter=adapter_name, reason=reason):
            with self._session_scope() as session:
                result = session.execute(
                    update(KnowledgeGraphCommunity)
                    .where(KnowledgeGraphCommunity.adapter_name == adapter_name)
                    .values(
                        status="needs_rebuild",
                        change_reason=reason[:64] or "refresh_failed",
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                return result.rowcount or 0

    def replace_graph_index_scope(
        self,
        adapter_name: str,
        *,
        remove_community_ids: list[str],
        communities: list[GraphIndexCommunity],
        findings: list[GraphIndexFinding],
        deltas: list[GraphIndexDelta] | None = None,
        unassigned_signals: list[GraphIndexUnassignedSignal] | None = None,
        promoted_signals: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Replace only one dirty community scope.

        This is intentionally scoped by community ids and does not depend on
        physical foreign keys. Findings are removed by their community_id.
        """

        remove_ids = _ordered_unique(remove_community_ids)
        with profile_span(
            "kg_repository.replace_graph_index_scope",
            adapter=adapter_name,
            remove_communities=len(remove_ids),
            communities=len(communities),
            findings=len(findings),
            deltas=len(deltas or []),
            unassigned_signals=len(unassigned_signals or []),
            promote_signals=len(promoted_signals or {}),
        ):
            with self._session_scope() as session:
                old_communities: list[KnowledgeGraphCommunity] = []
                old_findings: list[KnowledgeGraphFinding] = []
                old_deltas: list[KnowledgeGraphDelta] = []
                old_community_ids: list[str] = []
                old_finding_ids: list[str] = []
                old_delta_ids: list[str] = []
                if remove_ids:
                    old_communities = list(
                        session.scalars(
                            select(KnowledgeGraphCommunity).where(
                                KnowledgeGraphCommunity.adapter_name == adapter_name,
                                KnowledgeGraphCommunity.community_id.in_(remove_ids),
                            )
                        ).all()
                    )
                    old_findings = list(
                        session.scalars(
                            select(KnowledgeGraphFinding).where(
                                KnowledgeGraphFinding.adapter_name == adapter_name,
                                KnowledgeGraphFinding.community_id.in_(remove_ids),
                            )
                        ).all()
                    )
                    old_community_ids = [row.community_id for row in old_communities]
                    old_finding_ids = [row.finding_id for row in old_findings]
                    delta_rows = list(
                        session.scalars(
                            select(KnowledgeGraphDelta).where(
                                KnowledgeGraphDelta.adapter_name == adapter_name,
                            )
                        ).all()
                    )
                    remove_set = set(remove_ids)
                    old_delta_ids = [
                        row.delta_id
                        for row in delta_rows
                        if remove_set.intersection(str(item) for item in row.community_ids or [])
                    ]
                    old_deltas = [row for row in delta_rows if row.delta_id in set(old_delta_ids)]
                    session.execute(
                        delete(KnowledgeGraphDelta).where(
                            KnowledgeGraphDelta.adapter_name == adapter_name,
                            KnowledgeGraphDelta.delta_id.in_(old_delta_ids),
                        )
                    )
                    session.execute(
                        delete(KnowledgeGraphFinding).where(
                            KnowledgeGraphFinding.adapter_name == adapter_name,
                            KnowledgeGraphFinding.community_id.in_(remove_ids),
                        )
                    )
                    session.execute(
                        delete(KnowledgeGraphCommunity).where(
                            KnowledgeGraphCommunity.adapter_name == adapter_name,
                            KnowledgeGraphCommunity.community_id.in_(remove_ids),
                        )
                    )

                community_count = 0
                finding_count = 0
                delta_count = 0
                signal_count = 0
                promoted_count = 0
                for signal_id, community_id in (promoted_signals or {}).items():
                    result = session.execute(
                        update(KnowledgeGraphUnassignedSignal)
                        .where(
                            KnowledgeGraphUnassignedSignal.adapter_name == adapter_name,
                            KnowledgeGraphUnassignedSignal.signal_id == signal_id,
                        )
                        .values(
                            status="promoted",
                            promoted_community_id=community_id,
                            last_checked_at=datetime.now(timezone.utc),
                            promotion_attempts=KnowledgeGraphUnassignedSignal.promotion_attempts + 1,
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    promoted_count += result.rowcount or 0
                if communities:
                    community_insert = pg_insert(KnowledgeGraphCommunity)
                    result = session.execute(
                        community_insert
                        .values([_graph_community_values(item) for item in communities])
                        .on_conflict_do_update(
                            index_elements=[KnowledgeGraphCommunity.community_id],
                            set_={
                                key: getattr(community_insert.excluded, key)
                                for key in [
                                    "version_id",
                                    "adapter_name",
                                    "projection",
                                    "level",
                                    "parent_community_id",
                                    "title",
                                    "summary",
                                    "member_node_ids",
                                    "member_edge_ids",
                                    "evidence_ids",
                                    "chunk_ids",
                                    "metrics",
                                    "status",
                                    "previous_version_id",
                                    "change_reason",
                                    "lineage_id",
                                    "previous_community_ids",
                                    "updated_at",
                                ]
                            },
                        )
                    )
                    community_count = result.rowcount or 0
                if findings:
                    finding_insert = pg_insert(KnowledgeGraphFinding)
                    result = session.execute(
                        finding_insert
                        .values([_graph_finding_values(item) for item in findings])
                        .on_conflict_do_update(
                            index_elements=[KnowledgeGraphFinding.finding_id],
                            set_={
                                key: getattr(finding_insert.excluded, key)
                                for key in [
                                    "community_id",
                                    "adapter_name",
                                    "projection",
                                    "finding_type",
                                    "title",
                                    "statement",
                                    "cited_chunk_ids",
                                    "cited_evidence_ids",
                                    "supporting_edge_ids",
                                    "node_ids",
                                    "confidence",
                                    "status",
                                    "version",
                                    "payload",
                                    "updated_at",
                                ]
                            },
                        )
                    )
                    finding_count = result.rowcount or 0
                if deltas:
                    delta_insert = pg_insert(KnowledgeGraphDelta)
                    result = session.execute(
                        delta_insert
                        .values([_graph_delta_values(item) for item in deltas])
                        .on_conflict_do_update(
                            index_elements=[KnowledgeGraphDelta.delta_id],
                            set_={
                                key: getattr(delta_insert.excluded, key)
                                for key in [
                                    "adapter_name",
                                    "projection",
                                    "window_name",
                                    "started_at",
                                    "ended_at",
                                    "title",
                                    "summary",
                                    "community_ids",
                                    "finding_ids",
                                    "cited_chunk_ids",
                                    "cited_evidence_ids",
                                    "supporting_edge_ids",
                                    "node_ids",
                                    "metrics",
                                    "status",
                                    "version",
                                    "updated_at",
                                ]
                            },
                        )
                    )
                    delta_count = result.rowcount or 0
                if unassigned_signals:
                    signal_insert = pg_insert(KnowledgeGraphUnassignedSignal)
                    result = session.execute(
                        signal_insert
                        .values([_graph_unassigned_signal_values(item) for item in unassigned_signals])
                        .on_conflict_do_update(
                            index_elements=[KnowledgeGraphUnassignedSignal.signal_id],
                            set_={
                                key: getattr(signal_insert.excluded, key)
                                for key in [
                                    "adapter_name",
                                    "projection",
                                    "title",
                                    "reason",
                                    "node_ids",
                                    "edge_ids",
                                    "evidence_ids",
                                    "chunk_ids",
                                    "topic_tags",
                                    "impact_tags",
                                    "event_type_tags",
                                    "relation_types",
                                    "support_score",
                                    "metrics",
                                    "status",
                                    "promoted_community_id",
                                    "promotion_attempts",
                                    "last_checked_at",
                                    "updated_at",
                                ]
                            },
                        )
                    )
                    signal_count = result.rowcount or 0
                new_target_ids = (
                    {item.community_id for item in communities}
                    | {item.finding_id for item in findings}
                    | {item.delta_id for item in deltas or []}
                )
                stale_target_ids = sorted(
                    target_id
                    for target_id in [*old_community_ids, *old_finding_ids, *old_delta_ids]
                    if target_id not in new_target_ids
                )
                return {
                    "communities": community_count,
                    "findings": finding_count,
                    "deltas": delta_count,
                    "unassigned_signals": signal_count,
                    "promoted_unassigned_signals": promoted_count,
                    "stale_target_ids": stale_target_ids,
                    "removed_community_ids": len(old_community_ids),
                    "removed_finding_ids": len(old_finding_ids),
                    "removed_delta_ids": len(old_delta_ids),
                }

    def save_retrieval_trace_snapshot(self, snapshot: RetrievalTraceSnapshot) -> str:
        with profile_span(
            "kg_repository.save_retrieval_trace_snapshot",
            snapshot_id=snapshot.snapshot_id,
            strategy=snapshot.strategy_name,
        ):
            values = _retrieval_trace_snapshot_values(snapshot)
            with self._session_scope() as session:
                self._ensure_retrieval_quality_tables(session)
                stmt = pg_insert(KnowledgeRetrievalTraceSnapshot).values(**values)
                excluded = stmt.excluded
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["snapshot_id"],
                        set_={
                            "adapter_name": excluded.adapter_name,
                            "target": excluded.target,
                            "query": excluded.query,
                            "query_hash": excluded.query_hash,
                            "strategy_name": excluded.strategy_name,
                            "strategy_version": excluded.strategy_version,
                            "query_snapshot": excluded.query_snapshot,
                            "recall_snapshot": excluded.recall_snapshot,
                            "package_snapshot": excluded.package_snapshot,
                            "ranking_snapshot": excluded.ranking_snapshot,
                            "judge_snapshot": excluded.judge_snapshot,
                            "context_snapshot": excluded.context_snapshot,
                            "stop_snapshot": excluded.stop_snapshot,
                        },
                    )
                )
        return snapshot.snapshot_id

    def list_retrieval_trace_snapshots(
        self,
        *,
        adapter_name: str | None = None,
        target: str | None = None,
        query_hash: str | None = None,
        limit: int = 50,
    ) -> list[RetrievalTraceSnapshot]:
        with profile_span("kg_repository.list_retrieval_trace_snapshots", limit=limit):
            with self._session_scope() as session:
                self._ensure_retrieval_quality_tables(session)
                stmt = select(KnowledgeRetrievalTraceSnapshot)
                if adapter_name is not None:
                    stmt = stmt.where(KnowledgeRetrievalTraceSnapshot.adapter_name == adapter_name)
                if target is not None:
                    stmt = stmt.where(KnowledgeRetrievalTraceSnapshot.target == target)
                if query_hash is not None:
                    stmt = stmt.where(KnowledgeRetrievalTraceSnapshot.query_hash == query_hash)
                rows = session.scalars(
                    stmt.order_by(KnowledgeRetrievalTraceSnapshot.created_at.desc()).limit(max(1, limit))
                ).all()
                return [_retrieval_trace_snapshot_schema(row) for row in rows]

    def save_retrieval_label(self, label: RetrievalLabel) -> str:
        with profile_span("kg_repository.save_retrieval_label", label_id=label.label_id):
            values = _retrieval_label_values(label)
            with self._session_scope() as session:
                self._ensure_retrieval_quality_tables(session)
                stmt = pg_insert(KnowledgeRetrievalLabel).values(**values)
                excluded = stmt.excluded
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["label_id"],
                        set_={
                            "snapshot_id": excluded.snapshot_id,
                            "case_id": excluded.case_id,
                            "query": excluded.query,
                            "expected_candidates": excluded.expected_candidates,
                            "expected_answers": excluded.expected_answers,
                            "expected_evidence_refs": excluded.expected_evidence_refs,
                            "coverage_requirements": excluded.coverage_requirements,
                            "failure_stage": excluded.failure_stage,
                            "notes": excluded.notes,
                            "created_by": excluded.created_by,
                        },
                    )
                )
        return label.label_id

    def list_retrieval_labels(
        self,
        *,
        snapshot_id: str | None = None,
        case_id: str | None = None,
        limit: int = 100,
    ) -> list[RetrievalLabel]:
        with profile_span("kg_repository.list_retrieval_labels", limit=limit):
            with self._session_scope() as session:
                self._ensure_retrieval_quality_tables(session)
                stmt = select(KnowledgeRetrievalLabel)
                if snapshot_id is not None:
                    stmt = stmt.where(KnowledgeRetrievalLabel.snapshot_id == snapshot_id)
                if case_id is not None:
                    stmt = stmt.where(KnowledgeRetrievalLabel.case_id == case_id)
                rows = session.scalars(
                    stmt.order_by(KnowledgeRetrievalLabel.created_at.desc()).limit(max(1, limit))
                ).all()
                return [_retrieval_label_schema(row) for row in rows]

    def save_retrieval_eval_run(self, run: RetrievalEvalRun) -> str:
        with profile_span("kg_repository.save_retrieval_eval_run", run_id=run.run_id):
            values = _retrieval_eval_run_values(run)
            with self._session_scope() as session:
                self._ensure_retrieval_quality_tables(session)
                stmt = pg_insert(KnowledgeRetrievalEvalRun).values(**values)
                excluded = stmt.excluded
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["run_id"],
                        set_={
                            "strategy_name": excluded.strategy_name,
                            "strategy_version": excluded.strategy_version,
                            "status": excluded.status,
                            "config": excluded.config,
                            "aggregate_metrics": excluded.aggregate_metrics,
                            "finished_at": excluded.finished_at,
                        },
                    )
                )
        return run.run_id

    def finish_retrieval_eval_run(
        self,
        run_id: str,
        *,
        status: str,
        aggregate_metrics: dict[str, Any],
    ) -> None:
        with profile_span("kg_repository.finish_retrieval_eval_run", run_id=run_id):
            with self._session_scope() as session:
                self._ensure_retrieval_quality_tables(session)
                session.execute(
                    update(KnowledgeRetrievalEvalRun)
                    .where(KnowledgeRetrievalEvalRun.run_id == run_id)
                    .values(
                        status=status,
                        aggregate_metrics=aggregate_metrics,
                        finished_at=datetime.now(timezone.utc),
                    )
                )

    def upsert_retrieval_eval_metrics(self, metrics: list[RetrievalEvalMetric]) -> int:
        if not metrics:
            return 0
        with profile_span("kg_repository.upsert_retrieval_eval_metrics", metrics=len(metrics)):
            rows = [_retrieval_eval_metric_values(metric) for metric in metrics]
            with self._session_scope() as session:
                self._ensure_retrieval_quality_tables(session)
                stmt = pg_insert(KnowledgeRetrievalEvalMetric).values(rows)
                excluded = stmt.excluded
                result = session.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_kg_retrieval_eval_metrics_run_case",
                        set_={
                            "metric_id": excluded.metric_id,
                            "query": excluded.query,
                            "metrics": excluded.metrics,
                            "failure_stage": excluded.failure_stage,
                            "failure_details": excluded.failure_details,
                        },
                    )
                )
                return result.rowcount or 0

    def list_retrieval_eval_metrics(
        self,
        run_id: str,
        *,
        case_id: str | None = None,
    ) -> list[RetrievalEvalMetric]:
        with profile_span("kg_repository.list_retrieval_eval_metrics", run_id=run_id):
            with self._session_scope() as session:
                self._ensure_retrieval_quality_tables(session)
                stmt = select(KnowledgeRetrievalEvalMetric).where(
                    KnowledgeRetrievalEvalMetric.run_id == run_id
                )
                if case_id is not None:
                    stmt = stmt.where(KnowledgeRetrievalEvalMetric.case_id == case_id)
                rows = session.scalars(stmt.order_by(KnowledgeRetrievalEvalMetric.case_id)).all()
                return [_retrieval_eval_metric_schema(row) for row in rows]

    def upsert_review_entries(self, entries: list[ReviewEntry]) -> int:
        if not entries:
            return 0
        with profile_span("kg_repository.upsert_review_entries", entries=len(entries)):
            rows = [_review_entry_values(entry) for entry in entries]
            with self._session_scope() as session:
                stmt = pg_insert(KnowledgeReviewItem).values(rows)
                excluded = stmt.excluded
                result = session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["review_id"],
                        set_={
                            "object_type": excluded.object_type,
                            "object_id": excluded.object_id,
                            "severity": excluded.severity,
                            "reason": excluded.reason,
                            "status": excluded.status,
                            "payload": excluded.payload,
                            "updated_at": datetime.now(timezone.utc),
                        },
                    )
                )
                return result.rowcount or 0

    def list_review_entries(self, status: str | None = None) -> list[ReviewEntry]:
        with self._session_scope() as session:
            stmt = select(KnowledgeReviewItem)
            if status is not None:
                stmt = stmt.where(KnowledgeReviewItem.status == status)
            rows = session.scalars(stmt.order_by(KnowledgeReviewItem.review_id)).all()
            return [_review_entry_schema(row) for row in rows]

    def apply_review_action(self, review_id: str, action: ReviewAction) -> None:
        with self._session_scope() as session:
            row = session.get(KnowledgeReviewItem, review_id)
            payload = dict(row.payload or {}) if row else {}
            payload["last_action"] = action
            session.execute(
                update(KnowledgeReviewItem)
                .where(KnowledgeReviewItem.review_id == review_id)
                .values(status=str(action), payload=payload, updated_at=datetime.now(timezone.utc))
            )

    def create_compilation_run(self, run: dict[str, Any]) -> str:
        run_id = str(run.get("run_id") or f"kg_run:{uuid4()}")
        values = {
            "run_id": run_id,
            "adapter_name": run["adapter_name"],
            "adapter_version": run.get("adapter_version", ""),
            "source_batch_id": run.get("source_batch_id", ""),
            "status": run.get("status", "running"),
            "started_at": run.get("started_at") or datetime.now(timezone.utc),
            "input_count": int(run.get("input_count") or 0),
            "node_count": int(run.get("node_count") or 0),
            "edge_count": int(run.get("edge_count") or 0),
            "evidence_count": int(run.get("evidence_count") or 0),
            "failed_count": int(run.get("failed_count") or 0),
            "metadata_": run.get("metadata") or {},
        }
        with profile_span("kg_repository.create_compilation_run", run_id=run_id):
            with self._session_scope() as session:
                stmt = pg_insert(KnowledgeCompilationRun).values(**values)
                excluded = stmt.excluded
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["run_id"],
                        set_={
                            "adapter_name": excluded.adapter_name,
                            "adapter_version": excluded.adapter_version,
                            "source_batch_id": excluded.source_batch_id,
                            "status": excluded.status,
                            "started_at": excluded.started_at,
                            "input_count": excluded.input_count,
                            "node_count": excluded.node_count,
                            "edge_count": excluded.edge_count,
                            "evidence_count": excluded.evidence_count,
                            "failed_count": excluded.failed_count,
                            KnowledgeCompilationRun.metadata_: excluded["metadata"],
                        },
                    )
                )
        return run_id

    def finish_compilation_run(self, run_id: str, result: dict[str, Any]) -> None:
        values = {
            "status": result.get("status", "success"),
            "finished_at": result.get("finished_at") or datetime.now(timezone.utc),
            "input_count": int(result.get("input_count") or 0),
            "node_count": int(result.get("node_count") or 0),
            "edge_count": int(result.get("edge_count") or 0),
            "evidence_count": int(result.get("evidence_count") or 0),
            "failed_count": int(result.get("failed_count") or 0),
            "metadata_": result.get("metadata") or {},
        }
        with profile_span("kg_repository.finish_compilation_run", run_id=run_id):
            with self._session_scope() as session:
                session.execute(
                    update(KnowledgeCompilationRun)
                    .where(KnowledgeCompilationRun.run_id == run_id)
                    .values(**values)
                )

    def get_compilation_run(self, run_id: str) -> dict[str, Any] | None:
        with self._session_scope() as session:
            row = session.scalar(
                select(KnowledgeCompilationRun).where(KnowledgeCompilationRun.run_id == run_id)
            )
            return _compilation_run_schema(row) if row else None

    def list_compilation_runs(
        self,
        *,
        adapter_name: str | None = None,
        source_batch_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self._session_scope() as session:
            stmt = select(KnowledgeCompilationRun)
            if adapter_name is not None:
                stmt = stmt.where(KnowledgeCompilationRun.adapter_name == adapter_name)
            if source_batch_id is not None:
                stmt = stmt.where(KnowledgeCompilationRun.source_batch_id == source_batch_id)
            if status is not None:
                stmt = stmt.where(KnowledgeCompilationRun.status == status)
            rows = session.scalars(
                stmt.order_by(KnowledgeCompilationRun.started_at.desc()).limit(max(1, limit))
            ).all()
            return [_compilation_run_schema(row) for row in rows]

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        if self._session is not None:
            yield self._session
            self._session.flush()
            return
        with get_session(self._target) as session:
            yield session

    def _attach_edge_evidence_in_session(
        self,
        session: Session,
        edge_id: str,
        evidence_ids: list[str],
    ) -> int:
        if not evidence_ids:
            return 0
        with profile_span("kg_repository.attach_edge_evidence", evidence=len(evidence_ids)):
            rows = [{"edge_id": edge_id, "evidence_id": evidence_id} for evidence_id in evidence_ids]
            stmt = pg_insert(KnowledgeEdgeEvidence).values(rows).on_conflict_do_nothing()
            result = session.execute(stmt)
            _refresh_edge_evidence_chunk_refs_for_evidence(session, evidence_ids)
            return result.rowcount or 0

    def _attach_edges_evidence_in_session(
        self,
        session: Session,
        edges: list[CompiledEdge],
    ) -> int:
        rows = [
            {"edge_id": edge.edge_id, "evidence_id": evidence_id}
            for edge in edges
            for evidence_id in edge.evidence_ids
        ]
        if not rows:
            return 0
        with profile_span("kg_repository.attach_edges_evidence", edges=len(edges), links=len(rows)):
            stmt = pg_insert(KnowledgeEdgeEvidence).values(rows).on_conflict_do_nothing()
            result = session.execute(stmt)
            _refresh_edge_evidence_chunk_refs_for_evidence(
                session,
                _ordered_unique(row["evidence_id"] for row in rows),
            )
            return result.rowcount or 0

    def _ensure_evidence_version_columns(self, session: Session) -> None:
        if self._evidence_version_columns_ready:
            return
        with profile_span("kg_repository.ensure_evidence_version_columns"):
            existing_columns = _table_columns(session, "kg_evidence")
            required_columns = {"status", "source_fingerprint", "superseded_by", "updated_at"}
            missing_columns = required_columns - existing_columns
            if missing_columns:
                session.execute(
                    text("ALTER TABLE kg_evidence ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active'")
                )
                session.execute(text("ALTER TABLE kg_evidence ADD COLUMN IF NOT EXISTS source_fingerprint VARCHAR(128)"))
                session.execute(text("ALTER TABLE kg_evidence ADD COLUMN IF NOT EXISTS superseded_by VARCHAR(180)"))
                session.execute(text("ALTER TABLE kg_evidence ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()"))
            existing_indexes = _table_indexes(session, "kg_evidence")
            if "ix_kg_evidence_status" not in existing_indexes:
                session.execute(text("CREATE INDEX IF NOT EXISTS ix_kg_evidence_status ON kg_evidence (status)"))
            if "ix_kg_evidence_source_status" not in existing_indexes:
                session.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_kg_evidence_source_status "
                        "ON kg_evidence (adapter_name, source_type, source_id, status)"
                    )
                )
            self._evidence_version_columns_ready = True

    def _ensure_evidence_chunk_manifest_columns(self, session: Session) -> None:
        if self._evidence_chunk_manifest_columns_ready:
            return
        with profile_span("kg_repository.ensure_evidence_chunk_manifest_columns"):
            # Schema migration is owned by schema/06_knowledge.sql. Do not ALTER here:
            # integration databases may contain old tables owned by another role.
            _table_columns(session, "kg_evidence_chunks")
            self._evidence_chunk_manifest_columns_ready = True

    def _ensure_retrieval_quality_tables(self, session: Session) -> None:
        if self._retrieval_quality_tables_ready:
            return
        with profile_span("kg_repository.ensure_retrieval_quality_tables"):
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS kg_retrieval_trace_snapshots (
                        snapshot_id VARCHAR(128) PRIMARY KEY,
                        adapter_name VARCHAR(64) NOT NULL,
                        target VARCHAR(16) NOT NULL DEFAULT 'prod',
                        query TEXT NOT NULL,
                        query_hash VARCHAR(64) NOT NULL,
                        strategy_name VARCHAR(64) NOT NULL,
                        strategy_version VARCHAR(64) NOT NULL,
                        query_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                        recall_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                        package_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                        ranking_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                        judge_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                        context_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                        stop_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS kg_retrieval_labels (
                        label_id VARCHAR(128) PRIMARY KEY,
                        snapshot_id VARCHAR(128),
                        case_id VARCHAR(128),
                        query TEXT NOT NULL,
                        expected_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
                        expected_answers JSONB NOT NULL DEFAULT '[]'::jsonb,
                        expected_evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                        coverage_requirements JSONB NOT NULL DEFAULT '{}'::jsonb,
                        failure_stage VARCHAR(64),
                        notes TEXT NOT NULL DEFAULT '',
                        created_by VARCHAR(128) NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS kg_retrieval_eval_runs (
                        run_id VARCHAR(128) PRIMARY KEY,
                        strategy_name VARCHAR(64) NOT NULL,
                        strategy_version VARCHAR(64) NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        config JSONB NOT NULL DEFAULT '{}'::jsonb,
                        aggregate_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
                        started_at TIMESTAMPTZ DEFAULT NOW(),
                        finished_at TIMESTAMPTZ
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS kg_retrieval_eval_metrics (
                        metric_id VARCHAR(128) PRIMARY KEY,
                        run_id VARCHAR(128) NOT NULL,
                        case_id VARCHAR(128) NOT NULL,
                        query TEXT NOT NULL,
                        metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
                        failure_stage VARCHAR(64),
                        failure_details JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        CONSTRAINT uq_kg_retrieval_eval_metrics_run_case UNIQUE (run_id, case_id)
                    )
                    """
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_trace_snapshots_adapter "
                    "ON kg_retrieval_trace_snapshots(adapter_name, target)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_trace_snapshots_query_hash "
                    "ON kg_retrieval_trace_snapshots(query_hash)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_trace_snapshots_strategy "
                    "ON kg_retrieval_trace_snapshots(strategy_name, strategy_version)"
                )
            )
            session.execute(
                text("CREATE INDEX IF NOT EXISTS ix_kg_retrieval_labels_snapshot ON kg_retrieval_labels(snapshot_id)")
            )
            session.execute(
                text("CREATE INDEX IF NOT EXISTS ix_kg_retrieval_labels_case ON kg_retrieval_labels(case_id)")
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_eval_runs_strategy "
                    "ON kg_retrieval_eval_runs(strategy_name, strategy_version)"
                )
            )
            session.execute(
                text("CREATE INDEX IF NOT EXISTS ix_kg_retrieval_eval_runs_status ON kg_retrieval_eval_runs(status)")
            )
            session.execute(
                text("CREATE INDEX IF NOT EXISTS ix_kg_retrieval_eval_metrics_run ON kg_retrieval_eval_metrics(run_id)")
            )
            session.execute(
                text("CREATE INDEX IF NOT EXISTS ix_kg_retrieval_eval_metrics_case ON kg_retrieval_eval_metrics(case_id)")
            )
            self._retrieval_quality_tables_ready = True

    def _supersede_same_source_evidence_in_session(
        self,
        session: Session,
        evidence: list[CompiledEvidence],
    ) -> dict[str, set[str]]:
        all_evidence_ids: set[str] = set()
        all_edge_ids: set[str] = set()
        with profile_span("kg_repository.supersede_same_source_evidence", evidence=len(evidence)):
            for item in evidence:
                with profile_span(
                    "kg_repository.supersede_same_source_evidence.load_stale",
                    source_type=item.source_type,
                    source_id=item.source_id,
                ):
                    stale_ids = list(
                        session.scalars(
                            select(KnowledgeEvidence.evidence_id)
                            .where(KnowledgeEvidence.adapter_name == item.adapter_name)
                            .where(KnowledgeEvidence.source_type == item.source_type)
                            .where(KnowledgeEvidence.source_id == item.source_id)
                            .where(KnowledgeEvidence.evidence_id != item.evidence_id)
                            .where(KnowledgeEvidence.status == EvidenceStatus.ACTIVE.value)
                        ).all()
                    )
                summary = self._supersede_evidence_ids_in_session(
                    session,
                    sorted(stale_ids),
                    superseded_by=item.evidence_id,
                )
                all_evidence_ids.update(summary["evidence_ids"])
                all_edge_ids.update(summary["edge_ids"])
            return {"evidence_ids": all_evidence_ids, "edge_ids": all_edge_ids}

    def _supersede_evidence_ids_in_session(
        self,
        session: Session,
        evidence_ids: list[str],
        *,
        superseded_by: str | None,
    ) -> dict[str, set[str]]:
        if not evidence_ids:
            return {"evidence_ids": set(), "edge_ids": set()}
        with profile_span("kg_repository.supersede_evidence_ids", evidence=len(evidence_ids)):
            now = datetime.now(timezone.utc)
            edge_ids = set(
                session.scalars(
                    select(KnowledgeEdgeEvidence.edge_id).where(KnowledgeEdgeEvidence.evidence_id.in_(evidence_ids))
                ).all()
            )
            session.execute(
                update(KnowledgeEvidence)
                .where(KnowledgeEvidence.evidence_id.in_(evidence_ids))
                .values(
                    status=EvidenceStatus.SUPERSEDED.value,
                    superseded_by=superseded_by,
                    updated_at=now,
                )
            )
            if edge_ids:
                session.execute(
                    update(KnowledgeEdge)
                    .where(KnowledgeEdge.edge_id.in_(edge_ids))
                    .values(status=EdgeStatus.DEPRECATED.value, updated_at=now)
                )
                session.execute(
                    delete(KnowledgeGraphAdjacency).where(KnowledgeGraphAdjacency.edge_id.in_(edge_ids))
                )
            session.execute(
                delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.evidence_id.in_(evidence_ids))
            )
            return {"evidence_ids": set(evidence_ids), "edge_ids": edge_ids}


def _node_values(node: CompiledNode) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "adapter_name": node.adapter_name,
        "adapter_version": node.version,
        "node_type": node.node_type,
        "stable_key": node.node_id,
        "canonical_name": node.canonical_name,
        "aliases": node.aliases,
        "external_ids": node.external_ids,
        "properties": node.properties,
        "status": _enum_value(node.status),
        "version": node.version,
    }


def _edge_values(edge: CompiledEdge) -> dict[str, Any]:
    return {
        "edge_id": edge.edge_id,
        "adapter_name": edge.adapter_name,
        "adapter_version": edge.version,
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "relation_type": edge.relation_type,
        "properties": edge.properties,
        "confidence_label": _enum_value(edge.confidence_label),
        "confidence_score": edge.confidence_score,
        "status": _enum_value(edge.status),
        "valid_from": edge.valid_from,
        "valid_to": edge.valid_to,
        "version": edge.version,
    }


def _evidence_values(evidence: CompiledEvidence) -> dict[str, Any]:
    return {
        "evidence_id": evidence.evidence_id,
        "adapter_name": evidence.adapter_name,
        "source_type": evidence.source_type,
        "source_id": evidence.source_id,
        "evidence_type": _enum_value(evidence.evidence_type),
        "content": evidence.content,
        "payload": _evidence_metadata_payload(evidence.payload),
        "span_start": evidence.span_start,
        "span_end": evidence.span_end,
        "version": evidence.version,
        "status": _enum_value(evidence.status),
        "source_fingerprint": evidence.source_fingerprint,
        "superseded_by": evidence.superseded_by,
        "metadata_": evidence.metadata,
    }


_READABLE_TEXT_PAYLOAD_KEYS = {
    "body",
    "content",
    "document_text",
    "full_text",
    "html",
    "raw_text",
    "text",
}


def _evidence_metadata_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep evidence payload as source metadata; readable text lives in content."""

    return {
        key: value
        for key, value in (payload or {}).items()
        if str(key).lower() not in _READABLE_TEXT_PAYLOAD_KEYS
    }


def _retrieval_trace_snapshot_values(snapshot: RetrievalTraceSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "adapter_name": snapshot.adapter_name,
        "target": snapshot.target,
        "query": snapshot.query,
        "query_hash": snapshot.query_hash,
        "strategy_name": snapshot.strategy_name,
        "strategy_version": snapshot.strategy_version,
        "query_snapshot": snapshot.query_snapshot,
        "recall_snapshot": snapshot.recall_snapshot,
        "package_snapshot": snapshot.package_snapshot,
        "ranking_snapshot": snapshot.ranking_snapshot,
        "judge_snapshot": snapshot.judge_snapshot,
        "context_snapshot": snapshot.context_snapshot,
        "stop_snapshot": snapshot.stop_snapshot,
    }


def _retrieval_label_values(label: RetrievalLabel) -> dict[str, Any]:
    return {
        "label_id": label.label_id,
        "snapshot_id": label.snapshot_id,
        "case_id": label.case_id,
        "query": label.query,
        "expected_candidates": label.expected_candidates,
        "expected_answers": label.expected_answers,
        "expected_evidence_refs": label.expected_evidence_refs,
        "coverage_requirements": label.coverage_requirements,
        "failure_stage": label.failure_stage,
        "notes": label.notes,
        "created_by": label.created_by,
    }


def _retrieval_eval_run_values(run: RetrievalEvalRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "strategy_name": run.strategy_name,
        "strategy_version": run.strategy_version,
        "status": run.status,
        "config": run.config,
        "aggregate_metrics": run.aggregate_metrics,
        "started_at": run.started_at or datetime.now(timezone.utc),
        "finished_at": run.finished_at,
    }


def _retrieval_eval_metric_values(metric: RetrievalEvalMetric) -> dict[str, Any]:
    return {
        "metric_id": metric.metric_id,
        "run_id": metric.run_id,
        "case_id": metric.case_id,
        "query": metric.query,
        "metrics": metric.metrics,
        "failure_stage": metric.failure_stage,
        "failure_details": metric.failure_details,
    }


def _review_entry_values(entry: ReviewEntry) -> dict[str, Any]:
    return {
        "review_id": entry.review_id,
        "object_type": entry.object_type,
        "object_id": entry.object_id,
        "severity": _enum_value(entry.severity),
        "reason": entry.reason,
        "status": entry.status,
        "payload": entry.payload,
    }


def _node_schema(row: KnowledgeNode) -> CompiledNode:
    return CompiledNode(
        node_id=row.node_id,
        adapter_name=row.adapter_name,
        node_type=row.node_type,
        canonical_name=row.canonical_name,
        aliases=row.aliases or [],
        external_ids=row.external_ids or {},
        properties=row.properties or {},
        status=NodeStatus(row.status),
        version=row.version,
    )


def _edge_schema(row: KnowledgeEdge, evidence_ids: list[str]) -> CompiledEdge:
    return CompiledEdge(
        edge_id=row.edge_id,
        adapter_name=row.adapter_name,
        source_node_id=row.source_node_id,
        target_node_id=row.target_node_id,
        relation_type=row.relation_type,
        properties=row.properties or {},
        confidence_label=ConfidenceLabel(row.confidence_label),
        confidence_score=row.confidence_score,
        status=EdgeStatus(row.status),
        evidence_ids=evidence_ids,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        version=row.version,
    )


def _evidence_schema(row: KnowledgeEvidence) -> CompiledEvidence:
    return CompiledEvidence(
        evidence_id=row.evidence_id,
        adapter_name=row.adapter_name,
        evidence_type=EvidenceType(row.evidence_type),
        source_type=row.source_type,
        source_id=row.source_id,
        content=row.content,
        payload=row.payload or {},
        span_start=row.span_start,
        span_end=row.span_end,
        version=row.version,
        metadata=row.metadata_ or {},
        status=EvidenceStatus(row.status or EvidenceStatus.ACTIVE.value),
        source_fingerprint=row.source_fingerprint,
        superseded_by=row.superseded_by,
    )


def _retrieval_trace_snapshot_schema(row: KnowledgeRetrievalTraceSnapshot) -> RetrievalTraceSnapshot:
    return RetrievalTraceSnapshot(
        snapshot_id=row.snapshot_id,
        adapter_name=row.adapter_name,
        target=row.target,
        query=row.query,
        query_hash=row.query_hash,
        strategy_name=row.strategy_name,
        strategy_version=row.strategy_version,
        query_snapshot=row.query_snapshot or {},
        recall_snapshot=row.recall_snapshot or {},
        package_snapshot=row.package_snapshot or {},
        ranking_snapshot=row.ranking_snapshot or {},
        judge_snapshot=row.judge_snapshot or {},
        context_snapshot=row.context_snapshot or {},
        stop_snapshot=row.stop_snapshot or {},
        created_at=row.created_at,
    )


def _retrieval_label_schema(row: KnowledgeRetrievalLabel) -> RetrievalLabel:
    return RetrievalLabel(
        label_id=row.label_id,
        snapshot_id=row.snapshot_id,
        case_id=row.case_id,
        query=row.query,
        expected_candidates=row.expected_candidates or [],
        expected_answers=row.expected_answers or [],
        expected_evidence_refs=row.expected_evidence_refs or [],
        coverage_requirements=row.coverage_requirements or {},
        failure_stage=row.failure_stage,
        notes=row.notes or "",
        created_by=row.created_by or "",
        created_at=row.created_at,
    )


def _retrieval_eval_run_schema(row: KnowledgeRetrievalEvalRun) -> RetrievalEvalRun:
    return RetrievalEvalRun(
        run_id=row.run_id,
        strategy_name=row.strategy_name,
        strategy_version=row.strategy_version,
        status=row.status,
        config=row.config or {},
        aggregate_metrics=row.aggregate_metrics or {},
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _retrieval_eval_metric_schema(row: KnowledgeRetrievalEvalMetric) -> RetrievalEvalMetric:
    return RetrievalEvalMetric(
        metric_id=row.metric_id,
        run_id=row.run_id,
        case_id=row.case_id,
        query=row.query,
        metrics=row.metrics or {},
        failure_stage=row.failure_stage,
        failure_details=row.failure_details or {},
        created_at=row.created_at,
    )


def _review_entry_schema(row: KnowledgeReviewItem) -> ReviewEntry:
    return ReviewEntry(
        review_id=row.review_id,
        object_type=row.object_type,
        object_id=row.object_id,
        severity=row.severity,
        reason=row.reason,
        status=row.status,
        payload=row.payload or {},
    )


def _compilation_run_schema(row: KnowledgeCompilationRun) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "adapter_name": row.adapter_name,
        "adapter_version": row.adapter_version,
        "source_batch_id": row.source_batch_id,
        "status": row.status,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "input_count": row.input_count,
        "node_count": row.node_count,
        "edge_count": row.edge_count,
        "evidence_count": row.evidence_count,
        "failed_count": row.failed_count,
        "metadata": row.metadata_ or {},
    }


def _chunk_content(row: KnowledgeEvidence) -> str:
    return evidence_content_for_chunking(row.content, row.payload or {})


def _compiled_chunk_content(evidence: CompiledEvidence) -> str:
    return evidence_content_for_chunking(evidence.content, evidence.payload or {})


def _entity_search_terms(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    terms: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        for name in ("name", "code", "indicator_code", "taxonomy"):
            if item.get(name):
                terms.append(str(item[name]))
        if item.get("exchange") and item.get("code"):
            terms.append(f"{item['exchange']}:{item['code']}")
    return terms


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _chunk_values(row: KnowledgeEvidence) -> list[dict[str, Any]]:
    chunks = build_evidence_chunks(
        adapter_name=row.adapter_name,
        evidence_id=row.evidence_id,
        content=_chunk_content(row),
        payload={
            **(row.payload or {}),
            "status": row.status,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "evidence_type": row.evidence_type,
            "version": row.version,
        },
    )
    return [
        _chunk_values_from_schema(chunk)
        for chunk in chunks
    ]


def _chunk_values_from_compiled(evidence: CompiledEvidence) -> list[dict[str, Any]]:
    chunks = build_evidence_chunks(
        adapter_name=evidence.adapter_name,
        evidence_id=evidence.evidence_id,
        content=_compiled_chunk_content(evidence),
        payload={
            **(evidence.payload or {}),
            "status": evidence.status.value,
            "source_type": evidence.source_type,
            "source_id": evidence.source_id,
            "evidence_type": evidence.evidence_type.value,
            "version": evidence.version,
        },
    )
    return [
        _chunk_values_from_schema(chunk)
        for chunk in chunks
    ]


def _chunk_values_from_schema(chunk: EvidenceChunk) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "adapter_name": chunk.adapter_name,
        "evidence_id": chunk.evidence_id,
        "chunk_index": chunk.chunk_index,
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "previous_chunk_id": chunk.previous_chunk_id,
        "next_chunk_id": chunk.next_chunk_id,
        "text_hash": chunk.text_hash or "",
        "chunker_version": chunk.chunker_version or "",
    }


def _insert_evidence_chunk_rows(
    session: Session,
    rows: list[dict[str, Any]],
):
    return session.execute(pg_insert(KnowledgeEvidenceChunk).values(rows))


def _chunk_ids_by_evidence(session: Session, evidence_ids: list[str]) -> dict[str, list[str]]:
    if not evidence_ids:
        return {}
    stmt = select(KnowledgeEvidenceChunk.evidence_id, KnowledgeEvidenceChunk.chunk_id).where(
        KnowledgeEvidenceChunk.evidence_id.in_(evidence_ids)
    )
    if "chunk_index" in _table_columns(session, "kg_evidence_chunks"):
        stmt = stmt.order_by(KnowledgeEvidenceChunk.evidence_id, KnowledgeEvidenceChunk.chunk_index)
    else:
        stmt = stmt.order_by(KnowledgeEvidenceChunk.evidence_id, KnowledgeEvidenceChunk.chunk_id)
    rows = session.execute(stmt).all()
    result: dict[str, list[str]] = {}
    for evidence_id, chunk_id in rows:
        result.setdefault(evidence_id, []).append(chunk_id)
    return result


def _chunks_by_evidence(session: Session, evidence_ids: list[str]) -> dict[str, list[EvidenceChunk]]:
    if not evidence_ids:
        return {}
    result: dict[str, list[EvidenceChunk]] = {}
    rows = session.execute(
        select(KnowledgeEvidenceChunk, KnowledgeEvidence)
        .join(KnowledgeEvidence, KnowledgeEvidence.evidence_id == KnowledgeEvidenceChunk.evidence_id)
        .where(KnowledgeEvidenceChunk.evidence_id.in_(evidence_ids))
        .order_by(KnowledgeEvidenceChunk.evidence_id, KnowledgeEvidenceChunk.chunk_index)
    ).all()
    for chunk, evidence in rows:
        schema = _chunk_schema(chunk, evidence)
        result.setdefault(schema.evidence_id, []).append(schema)
    return result


def _chunk_ids_for_edge(properties: dict[str, Any], chunks: list[EvidenceChunk]) -> list[str]:
    if not chunks:
        return []
    chunk_ids = {chunk.chunk_id for chunk in chunks}
    spans = properties.get("evidence_spans")
    explicit = _explicit_chunk_ids_from_spans(spans, chunk_ids)
    if explicit:
        return explicit

    matched: list[str] = []
    if isinstance(spans, list):
        for chunk in chunks:
            if _chunk_matches_any_span(chunk, spans):
                matched.append(chunk.chunk_id)
    return _ordered_unique(matched) or [chunk.chunk_id for chunk in chunks]


def _graph_community_values(item: GraphIndexCommunity) -> dict[str, Any]:
    return {
        "community_id": item.community_id,
        "version_id": item.version_id,
        "adapter_name": item.adapter_name,
        "projection": item.projection,
        "level": item.level,
        "parent_community_id": item.parent_community_id,
        "title": item.title,
        "summary": item.summary,
        "member_node_ids": item.member_node_ids,
        "member_edge_ids": item.member_edge_ids,
        "evidence_ids": item.evidence_ids,
        "chunk_ids": item.chunk_ids,
        "metrics": item.metrics,
        "status": item.status,
        "previous_version_id": item.previous_version_id,
        "change_reason": item.change_reason,
        "lineage_id": item.lineage_id,
        "previous_community_ids": item.previous_community_ids,
        "updated_at": datetime.now(timezone.utc),
    }


def _graph_community_schema(row: KnowledgeGraphCommunity) -> GraphIndexCommunity:
    return GraphIndexCommunity(
        community_id=row.community_id,
        version_id=row.version_id,
        adapter_name=row.adapter_name,
        projection=row.projection,
        level=row.level,
        parent_community_id=row.parent_community_id or "",
        title=row.title,
        summary=row.summary,
        member_node_ids=[str(item) for item in row.member_node_ids or [] if item],
        member_edge_ids=[str(item) for item in row.member_edge_ids or [] if item],
        evidence_ids=[str(item) for item in row.evidence_ids or [] if item],
        chunk_ids=[str(item) for item in row.chunk_ids or [] if item],
        metrics=dict(row.metrics or {}),
        status=row.status,
        previous_version_id=row.previous_version_id or "",
        change_reason=row.change_reason or "build",
        lineage_id=row.lineage_id or "",
        previous_community_ids=[str(item) for item in row.previous_community_ids or [] if item],
    )


def _graph_finding_values(item: GraphIndexFinding) -> dict[str, Any]:
    return {
        "finding_id": item.finding_id,
        "community_id": item.community_id,
        "adapter_name": item.adapter_name,
        "projection": item.projection,
        "finding_type": item.finding_type,
        "title": item.title,
        "statement": item.statement,
        "cited_chunk_ids": item.cited_chunk_ids,
        "cited_evidence_ids": item.cited_evidence_ids,
        "supporting_edge_ids": item.supporting_edge_ids,
        "node_ids": item.node_ids,
        "confidence": item.confidence,
        "status": item.status,
        "version": item.version,
        "payload": item.payload,
        "updated_at": datetime.now(timezone.utc),
    }


def _graph_finding_schema(row: KnowledgeGraphFinding) -> GraphIndexFinding:
    return GraphIndexFinding(
        finding_id=row.finding_id,
        community_id=row.community_id,
        adapter_name=row.adapter_name,
        projection=row.projection,
        finding_type=row.finding_type,
        title=row.title,
        statement=row.statement,
        cited_chunk_ids=[str(item) for item in row.cited_chunk_ids or [] if item],
        cited_evidence_ids=[str(item) for item in row.cited_evidence_ids or [] if item],
        supporting_edge_ids=[str(item) for item in row.supporting_edge_ids or [] if item],
        node_ids=[str(item) for item in row.node_ids or [] if item],
        confidence=float(row.confidence or 0.0),
        status=row.status,
        version=row.version or "",
        payload=dict(row.payload or {}),
    )


def _graph_delta_values(item: GraphIndexDelta) -> dict[str, Any]:
    return {
        "delta_id": item.delta_id,
        "adapter_name": item.adapter_name,
        "projection": item.projection,
        "window_name": item.window_name,
        "started_at": item.started_at,
        "ended_at": item.ended_at,
        "title": item.title,
        "summary": item.summary,
        "community_ids": item.community_ids,
        "finding_ids": item.finding_ids,
        "cited_chunk_ids": item.cited_chunk_ids,
        "cited_evidence_ids": item.cited_evidence_ids,
        "supporting_edge_ids": item.supporting_edge_ids,
        "node_ids": item.node_ids,
        "metrics": item.metrics,
        "status": item.status,
        "version": item.version,
        "updated_at": datetime.now(timezone.utc),
    }


def _graph_delta_schema(row: KnowledgeGraphDelta) -> GraphIndexDelta:
    return GraphIndexDelta(
        delta_id=row.delta_id,
        adapter_name=row.adapter_name,
        projection=row.projection,
        window_name=row.window_name,
        started_at=row.started_at,
        ended_at=row.ended_at,
        title=row.title,
        summary=row.summary,
        community_ids=[str(item) for item in row.community_ids or [] if item],
        finding_ids=[str(item) for item in row.finding_ids or [] if item],
        cited_chunk_ids=[str(item) for item in row.cited_chunk_ids or [] if item],
        cited_evidence_ids=[str(item) for item in row.cited_evidence_ids or [] if item],
        supporting_edge_ids=[str(item) for item in row.supporting_edge_ids or [] if item],
        node_ids=[str(item) for item in row.node_ids or [] if item],
        metrics=dict(row.metrics or {}),
        status=row.status,
        version=row.version or "",
    )


def _graph_unassigned_signal_values(item: GraphIndexUnassignedSignal) -> dict[str, Any]:
    return {
        "signal_id": item.signal_id,
        "adapter_name": item.adapter_name,
        "projection": item.projection,
        "title": item.title,
        "reason": item.reason,
        "node_ids": item.node_ids,
        "edge_ids": item.edge_ids,
        "evidence_ids": item.evidence_ids,
        "chunk_ids": item.chunk_ids,
        "topic_tags": item.topic_tags,
        "impact_tags": item.impact_tags,
        "event_type_tags": item.event_type_tags,
        "relation_types": item.relation_types,
        "support_score": item.support_score,
        "metrics": item.metrics,
        "status": item.status,
        "promoted_community_id": item.promoted_community_id,
        "promotion_attempts": item.promotion_attempts,
        "last_checked_at": item.last_checked_at,
        "updated_at": datetime.now(timezone.utc),
    }


def _graph_unassigned_signal_schema(row: KnowledgeGraphUnassignedSignal) -> GraphIndexUnassignedSignal:
    return GraphIndexUnassignedSignal(
        signal_id=row.signal_id,
        adapter_name=row.adapter_name,
        projection=row.projection,
        title=row.title or "",
        reason=row.reason or "",
        node_ids=[str(item) for item in row.node_ids or [] if item],
        edge_ids=[str(item) for item in row.edge_ids or [] if item],
        evidence_ids=[str(item) for item in row.evidence_ids or [] if item],
        chunk_ids=[str(item) for item in row.chunk_ids or [] if item],
        topic_tags=[str(item) for item in row.topic_tags or [] if item],
        impact_tags=[str(item) for item in row.impact_tags or [] if item],
        event_type_tags=[str(item) for item in row.event_type_tags or [] if item],
        relation_types=[str(item) for item in row.relation_types or [] if item],
        support_score=float(row.support_score or 0.0),
        metrics=dict(row.metrics or {}),
        status=row.status or "active",
        promoted_community_id=row.promoted_community_id or "",
        promotion_attempts=int(row.promotion_attempts or 0),
        last_checked_at=row.last_checked_at,
    )


def _stable_digest(parts: list[str]) -> str:
    data = "\n".join(str(part) for part in parts)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def _explicit_chunk_ids_from_spans(spans: Any, known_chunk_ids: set[str]) -> list[str]:
    if not isinstance(spans, list):
        return []
    result: list[str] = []
    for span in spans:
        if not isinstance(span, dict):
            continue
        chunk_id = str(span.get("chunk_id") or "").strip()
        if chunk_id and chunk_id in known_chunk_ids:
            result.append(chunk_id)
    return _ordered_unique(result)


def _chunk_matches_any_span(chunk: EvidenceChunk, spans: list[Any]) -> bool:
    chunk_text = _normalize_text_for_span_match(chunk.content)
    for span in spans:
        if not isinstance(span, dict):
            continue
        span_text = _normalize_text_for_span_match(span.get("text"))
        if span_text and (span_text in chunk_text or chunk_text in span_text):
            return True
        if _span_offsets_overlap_chunk(span, chunk):
            return True
    return False


def _span_offsets_overlap_chunk(span: dict[str, Any], chunk: EvidenceChunk) -> bool:
    if chunk.start_offset is None or chunk.end_offset is None:
        return False
    start = span.get("start")
    end = span.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    return max(start, chunk.start_offset) < min(end, chunk.end_offset)


def _normalize_text_for_span_match(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _refresh_edge_evidence_chunk_refs_for_adapter(session: Session, adapter_name: str) -> None:
    evidence_ids = list(
        session.scalars(
            select(KnowledgeEvidence.evidence_id).where(KnowledgeEvidence.adapter_name == adapter_name)
        ).all()
    )
    _refresh_edge_evidence_chunk_refs_for_evidence(session, evidence_ids)


def _refresh_edge_evidence_chunk_refs_for_evidence(session: Session, evidence_ids: list[str]) -> None:
    evidence_ids = _ordered_unique(evidence_ids)
    if not evidence_ids:
        return
    chunks_by_evidence = _chunks_by_evidence(session, evidence_ids)
    link_rows = session.execute(
        select(
            KnowledgeEdgeEvidence.edge_id,
            KnowledgeEdgeEvidence.evidence_id,
            KnowledgeEdge.properties,
        )
        .join(KnowledgeEdge, KnowledgeEdge.edge_id == KnowledgeEdgeEvidence.edge_id)
        .where(KnowledgeEdgeEvidence.evidence_id.in_(evidence_ids))
    ).all()
    session.execute(
        delete(KnowledgeEdgeEvidenceChunk).where(KnowledgeEdgeEvidenceChunk.evidence_id.in_(evidence_ids))
    )
    rows = [
        {"edge_id": edge_id, "evidence_id": evidence_id, "chunk_id": chunk_id}
        for edge_id, evidence_id, properties in link_rows
        for chunk_id in _chunk_ids_for_edge(properties or {}, chunks_by_evidence.get(evidence_id, []))
    ]
    if rows:
        session.execute(pg_insert(KnowledgeEdgeEvidenceChunk).values(rows).on_conflict_do_nothing())


def _chunk_schema(row: KnowledgeEvidenceChunk, evidence: KnowledgeEvidence) -> EvidenceChunk:
    content = _slice_chunk_content(evidence, row)
    payload = {
        **(evidence.payload or {}),
        "status": evidence.status,
        "source_type": evidence.source_type,
        "source_id": evidence.source_id,
        "evidence_type": evidence.evidence_type,
        "version": evidence.version,
        "chunk_index": row.chunk_index,
        "start_offset": row.start_offset,
        "end_offset": row.end_offset,
        "previous_chunk_id": row.previous_chunk_id,
        "next_chunk_id": row.next_chunk_id,
        "chunker_version": row.chunker_version or "",
        "text_hash": row.text_hash or "",
    }
    return EvidenceChunk(
        chunk_id=row.chunk_id,
        adapter_name=row.adapter_name,
        evidence_id=row.evidence_id,
        content=content,
        chunk_index=row.chunk_index,
        start_offset=row.start_offset,
        end_offset=row.end_offset,
        previous_chunk_id=row.previous_chunk_id,
        next_chunk_id=row.next_chunk_id,
        text_hash=row.text_hash or "",
        chunker_version=row.chunker_version or "",
        payload=payload,
    )


def _slice_chunk_content(evidence: KnowledgeEvidence, chunk: KnowledgeEvidenceChunk) -> str:
    full_text = _chunk_content(evidence)
    if chunk.start_offset is None or chunk.end_offset is None:
        return full_text
    start = max(0, min(chunk.start_offset, len(full_text)))
    end = max(start, min(chunk.end_offset, len(full_text)))
    return full_text[start:end].strip()


def _lexical_terms(query: str) -> list[str]:
    terms = [term for term in re.split(r"[\s,，。；;:：|/()（）]+", query.strip()) if term]
    compact = query.strip()
    if compact and compact not in terms and len(compact) <= 40:
        terms.append(compact)
    return _ordered_unique(terms)


def _table_columns(session: Session, table_name: str) -> set[str]:
    rows = session.execute(
        text(
            """
            select column_name
            from information_schema.columns
            where table_schema = current_schema()
              and table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).scalars()
    return {str(row) for row in rows}


def _evidence_chunk_manifest_columns_available(session: Session) -> bool:
    columns = _table_columns(session, "kg_evidence_chunks")
    return {
        "chunk_index",
        "start_offset",
        "end_offset",
        "previous_chunk_id",
        "next_chunk_id",
        "text_hash",
        "chunker_version",
    }.issubset(columns)


def _table_indexes(session: Session, table_name: str) -> set[str]:
    rows = session.execute(
        text(
            """
            select indexname
            from pg_indexes
            where schemaname = current_schema()
              and tablename = :table_name
            """
        ),
        {"table_name": table_name},
    ).scalars()
    return {str(row) for row in rows}


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)
