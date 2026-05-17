"""KnowledgeRepository SQLAlchemy implementation."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Literal
from uuid import uuid4

from sqlalchemy import Text, case, cast, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceStatus, EvidenceType, NodeStatus
from src.domain.knowledge.quality import ReviewAction, ReviewEntry
from src.domain.knowledge.repositories import KnowledgeRepository
from src.domain.knowledge.retrieval_document import RetrievalDocument, RetrievalDocumentVersion
from src.domain.knowledge.retrieval_eval import (
    RetrievalEvalMetric,
    RetrievalEvalRun,
    RetrievalLabel,
    RetrievalTraceSnapshot,
)
from src.domain.knowledge.retrieval_keyword import (
    lexical_query_terms,
    retrieval_document_query_score,
)
from src.domain.knowledge.retrieval_profile import profile_span
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode, EvidenceChunk
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCompilationRun,
    KnowledgeEdge,
    KnowledgeEdgeEvidence,
    KnowledgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeGraphAdjacency,
    KnowledgeNode,
    KnowledgeReviewItem,
    KnowledgeRetrievalDocument,
    KnowledgeRetrievalDocumentVersion,
    KnowledgeRetrievalEvalMetric,
    KnowledgeRetrievalEvalRun,
    KnowledgeRetrievalLabel,
    KnowledgeRetrievalTraceSnapshot,
    KnowledgeWikiPage,
)
from src.domain.knowledge.wiki import WikiPage

Target = Literal["prod", "test"]


class KnowledgeRepositoryImpl(KnowledgeRepository):
    def __init__(self, session: Session | None = None, target: Target | None = None):
        self._session = session
        self._target = target
        self._evidence_version_columns_ready = False
        self._retrieval_document_table_ready = False
        self._retrieval_quality_tables_ready = False

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

    def rebuild_wiki_pages(self, adapter_name: str, pages: list[WikiPage]) -> int:
        with profile_span("kg_repository.rebuild_wiki_pages", adapter=adapter_name, pages=len(pages)):
            with self._session_scope() as session:
                with profile_span("kg_repository.rebuild_wiki_pages.delete_old", adapter=adapter_name):
                    session.execute(delete(KnowledgeWikiPage).where(KnowledgeWikiPage.adapter_name == adapter_name))
                if not pages:
                    return 0
                with profile_span("kg_repository.rebuild_wiki_pages.build_rows", pages=len(pages)):
                    rows = [_wiki_page_values(page) for page in pages]
                with profile_span("kg_repository.rebuild_wiki_pages.insert", rows=len(rows)):
                    result = session.execute(pg_insert(KnowledgeWikiPage).values(rows))
                return result.rowcount or 0

    def upsert_wiki_pages(self, adapter_name: str, pages: list[WikiPage]) -> int:
        pages = [page for page in pages if page.adapter_name == adapter_name]
        if not pages:
            return 0
        with profile_span("kg_repository.upsert_wiki_pages", adapter=adapter_name, pages=len(pages)):
            with profile_span("kg_repository.upsert_wiki_pages.build_rows", pages=len(pages)):
                rows = [_wiki_page_values(page) for page in pages]
            with self._session_scope() as session:
                stmt = pg_insert(KnowledgeWikiPage).values(rows)
                excluded = stmt.excluded
                with profile_span("kg_repository.upsert_wiki_pages.execute", rows=len(rows)):
                    result = session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=["page_id"],
                            set_={
                                "adapter_name": excluded.adapter_name,
                                "page_type": excluded.page_type,
                                "subject_type": excluded.subject_type,
                                "subject_id": excluded.subject_id,
                                "title": excluded.title,
                                "summary": excluded.summary,
                                "content": excluded.content,
                                "source_node_ids": excluded.source_node_ids,
                                "source_edge_ids": excluded.source_edge_ids,
                                "source_evidence_ids": excluded.source_evidence_ids,
                                "version": excluded.version,
                                "updated_at": datetime.now(timezone.utc),
                            },
                        )
                    )
                return result.rowcount or 0

    def list_wiki_pages(self, adapter_name: str) -> list[WikiPage]:
        with profile_span("kg_repository.list_wiki_pages", adapter=adapter_name):
            with self._session_scope() as session:
                rows = session.scalars(
                    select(KnowledgeWikiPage)
                    .where(KnowledgeWikiPage.adapter_name == adapter_name)
                    .order_by(KnowledgeWikiPage.page_id)
                ).all()
                return [_wiki_page_schema(row) for row in rows]

    def search_wiki_pages(self, adapter_name: str, query: str, limit: int = 20) -> list[WikiPage]:
        if not query.strip():
            return []
        pattern = f"%{query.strip()}%"
        with profile_span("kg_repository.search_wiki_pages", adapter=adapter_name, limit=limit):
            with self._session_scope() as session:
                rows = session.scalars(
                    select(KnowledgeWikiPage)
                    .where(KnowledgeWikiPage.adapter_name == adapter_name)
                    .where(
                        or_(
                            KnowledgeWikiPage.title.ilike(pattern),
                            KnowledgeWikiPage.summary.ilike(pattern),
                            KnowledgeWikiPage.content.ilike(pattern),
                        )
                    )
                    .order_by(KnowledgeWikiPage.page_id)
                    .limit(limit)
                ).all()
                return [_wiki_page_schema(row) for row in rows]

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
                with profile_span("kg_repository.rebuild_evidence_chunks.delete_old"):
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
                    rows = [_chunk_values(row) for row in evidence_rows if _chunk_content(row)]
                if not rows:
                    return 0
                with profile_span("kg_repository.rebuild_evidence_chunks.insert", rows=len(rows)):
                    result = session.execute(pg_insert(KnowledgeEvidenceChunk).values(rows))
                return result.rowcount or 0

    def upsert_evidence_chunks(self, evidence: list[CompiledEvidence]) -> int:
        if not evidence:
            return 0
        with profile_span("kg_repository.upsert_evidence_chunks", evidence=len(evidence)):
            chunk_ids = [f"kg_chunk:{item.evidence_id}:0" for item in evidence]
            with profile_span("kg_repository.upsert_evidence_chunks.build_rows", evidence=len(evidence)):
                rows = [_chunk_values_from_compiled(item) for item in evidence if _compiled_chunk_content(item)]
            with self._session_scope() as session:
                with profile_span("kg_repository.upsert_evidence_chunks.delete_old", chunks=len(chunk_ids)):
                    session.execute(
                        delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.chunk_id.in_(chunk_ids))
                    )
                if not rows:
                    return 0
                with profile_span("kg_repository.upsert_evidence_chunks.insert", rows=len(rows)):
                    result = session.execute(pg_insert(KnowledgeEvidenceChunk).values(rows))
                return result.rowcount or 0

    def list_evidence_chunks(self, adapter_name: str) -> list[EvidenceChunk]:
        with profile_span("kg_repository.list_evidence_chunks", adapter=adapter_name):
            with self._session_scope() as session:
                rows = session.scalars(
                    select(KnowledgeEvidenceChunk)
                    .where(KnowledgeEvidenceChunk.adapter_name == adapter_name)
                    .order_by(KnowledgeEvidenceChunk.chunk_id)
                ).all()
                return [_chunk_schema(row) for row in rows]

    def upsert_retrieval_documents(self, documents: list[RetrievalDocument]) -> int:
        if not documents:
            return 0
        with profile_span("kg_repository.upsert_retrieval_documents", documents=len(documents)):
            rows = [_retrieval_document_values(document) for document in documents]
            with self._session_scope() as session:
                self._ensure_retrieval_document_table(session)
                stmt = pg_insert(KnowledgeRetrievalDocument).values(rows)
                excluded = stmt.excluded
                result = session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["document_id"],
                        set_={
                            "adapter_name": excluded.adapter_name,
                            "target": excluded.target,
                            "source_fact_type": excluded.source_fact_type,
                            "source_fact_id": excluded.source_fact_id,
                            "evidence_refs": excluded.evidence_refs,
                            "node_refs": excluded.node_refs,
                            "edge_refs": excluded.edge_refs,
                            "title": excluded.title,
                            "search_text": excluded.search_text,
                            "key_phrases": excluded.key_phrases,
                            "aliases": excluded.aliases,
                            "event_type": excluded.event_type,
                            "relation_intents": excluded.relation_intents,
                            "impact_direction": excluded.impact_direction,
                            "asset_classes": excluded.asset_classes,
                            "time_tags": excluded.time_tags,
                            "source_type_tags": excluded.source_type_tags,
                            "readable_relations": excluded.readable_relations,
                            "evidence_summary": excluded.evidence_summary,
                            "answer_candidate_type": excluded.answer_candidate_type,
                            "confidence": excluded.confidence,
                            "generated_by": excluded.generated_by,
                            "generation_version": excluded.generation_version,
                            KnowledgeRetrievalDocument.metadata_: excluded["metadata"],
                            "updated_at": datetime.now(timezone.utc),
                        },
                    )
                )
                return result.rowcount or 0

    def list_retrieval_documents(self, adapter_name: str, *, target: str = "prod") -> list[RetrievalDocument]:
        with profile_span("kg_repository.list_retrieval_documents", adapter=adapter_name, target=target):
            with self._session_scope() as session:
                self._ensure_retrieval_document_table(session)
                rows = session.scalars(
                    select(KnowledgeRetrievalDocument)
                    .where(KnowledgeRetrievalDocument.adapter_name == adapter_name)
                    .where(KnowledgeRetrievalDocument.target == target)
                    .order_by(KnowledgeRetrievalDocument.document_id)
                ).all()
                return [_retrieval_document_schema(row) for row in rows]

    def search_retrieval_documents(
        self,
        adapter_name: str,
        query: str,
        *,
        target: str = "prod",
        limit: int = 20,
    ) -> list[RetrievalDocument]:
        terms = lexical_query_terms(query)
        if not terms:
            return []
        with profile_span(
            "kg_repository.search_retrieval_documents",
            adapter=adapter_name,
            target=target,
            terms=len(terms),
            limit=limit,
        ):
            with self._session_scope() as session:
                self._ensure_retrieval_document_table(session)
                predicates = []
                for term in terms:
                    pattern = f"%{term}%"
                    predicates.append(KnowledgeRetrievalDocument.title.ilike(pattern))
                    predicates.append(KnowledgeRetrievalDocument.search_text.ilike(pattern))
                    predicates.append(KnowledgeRetrievalDocument.evidence_summary.ilike(pattern))
                    predicates.append(cast(KnowledgeRetrievalDocument.key_phrases, Text).ilike(pattern))
                    predicates.append(cast(KnowledgeRetrievalDocument.aliases, Text).ilike(pattern))
                    predicates.append(cast(KnowledgeRetrievalDocument.readable_relations, Text).ilike(pattern))
                candidate_limit = max(limit * 20, 200)
                rows = session.scalars(
                    select(KnowledgeRetrievalDocument)
                    .where(KnowledgeRetrievalDocument.adapter_name == adapter_name)
                    .where(KnowledgeRetrievalDocument.target == target)
                    .where(or_(*predicates))
                    .order_by(KnowledgeRetrievalDocument.document_id)
                    .limit(candidate_limit)
                ).all()
                documents = [_retrieval_document_schema(row) for row in rows]
                return sorted(
                    documents,
                    key=lambda item: (-retrieval_document_query_score(item, terms), item.document_id),
                )[:limit]

    def save_retrieval_document_version(self, version: RetrievalDocumentVersion) -> str:
        with profile_span(
            "kg_repository.save_retrieval_document_version",
            version_id=version.version_id,
            documents=len(version.changed_fact_set.get("document_ids") or []),
        ):
            values = _retrieval_document_version_values(version)
            with self._session_scope() as session:
                self._ensure_retrieval_document_table(session)
                stmt = pg_insert(KnowledgeRetrievalDocumentVersion).values(**values)
                excluded = stmt.excluded
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["version_id"],
                        set_={
                            "adapter_name": excluded.adapter_name,
                            "target": excluded.target,
                            "generation_version": excluded.generation_version,
                            "changed_fact_set": excluded.changed_fact_set,
                            "field_coverage": excluded.field_coverage,
                            "config": excluded.config,
                        },
                    )
                )
        return version.version_id

    def list_retrieval_document_versions(
        self,
        adapter_name: str,
        *,
        target: str = "prod",
        limit: int = 20,
    ) -> list[RetrievalDocumentVersion]:
        with profile_span("kg_repository.list_retrieval_document_versions", adapter=adapter_name, target=target):
            with self._session_scope() as session:
                self._ensure_retrieval_document_table(session)
                rows = session.scalars(
                    select(KnowledgeRetrievalDocumentVersion)
                    .where(KnowledgeRetrievalDocumentVersion.adapter_name == adapter_name)
                    .where(KnowledgeRetrievalDocumentVersion.target == target)
                    .order_by(KnowledgeRetrievalDocumentVersion.created_at.desc())
                    .limit(max(1, limit))
                ).all()
                return [_retrieval_document_version_schema(row) for row in rows]

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

    def _ensure_retrieval_document_table(self, session: Session) -> None:
        if self._retrieval_document_table_ready:
            return
        with profile_span("kg_repository.ensure_retrieval_document_table"):
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS kg_retrieval_documents (
                        document_id VARCHAR(260) PRIMARY KEY,
                        adapter_name VARCHAR(64) NOT NULL,
                        target VARCHAR(16) NOT NULL DEFAULT 'prod',
                        source_fact_type VARCHAR(32) NOT NULL,
                        source_fact_id VARCHAR(220) NOT NULL,
                        evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                        node_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                        edge_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                        title TEXT NOT NULL,
                        search_text TEXT NOT NULL,
                        key_phrases JSONB NOT NULL DEFAULT '[]'::jsonb,
                        aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
                        event_type VARCHAR(96),
                        relation_intents JSONB NOT NULL DEFAULT '[]'::jsonb,
                        impact_direction VARCHAR(16) NOT NULL DEFAULT 'unknown',
                        asset_classes JSONB NOT NULL DEFAULT '[]'::jsonb,
                        time_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                        source_type_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                        readable_relations JSONB NOT NULL DEFAULT '[]'::jsonb,
                        evidence_summary TEXT NOT NULL DEFAULT '',
                        answer_candidate_type VARCHAR(32) NOT NULL DEFAULT 'unknown',
                        confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        generated_by VARCHAR(16) NOT NULL DEFAULT 'rule',
                        generation_version VARCHAR(64) NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_documents_adapter_target "
                    "ON kg_retrieval_documents(adapter_name, target)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_documents_source "
                    "ON kg_retrieval_documents(source_fact_type, source_fact_id)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_documents_answer_type "
                    "ON kg_retrieval_documents(answer_candidate_type)"
                )
            )
            session.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_documents_title_trgm "
                    "ON kg_retrieval_documents USING gin (title gin_trgm_ops)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_documents_search_text_trgm "
                    "ON kg_retrieval_documents USING gin (search_text gin_trgm_ops)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_documents_evidence_summary_trgm "
                    "ON kg_retrieval_documents USING gin (evidence_summary gin_trgm_ops)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_documents_key_phrases_trgm "
                    "ON kg_retrieval_documents USING gin ((key_phrases::text) gin_trgm_ops)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_documents_aliases_trgm "
                    "ON kg_retrieval_documents USING gin ((aliases::text) gin_trgm_ops)"
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_documents_readable_relations_trgm "
                    "ON kg_retrieval_documents USING gin ((readable_relations::text) gin_trgm_ops)"
                )
            )
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS kg_retrieval_document_versions (
                        version_id VARCHAR(128) PRIMARY KEY,
                        adapter_name VARCHAR(64) NOT NULL,
                        target VARCHAR(16) NOT NULL DEFAULT 'prod',
                        generation_version VARCHAR(64) NOT NULL,
                        changed_fact_set JSONB NOT NULL DEFAULT '{}'::jsonb,
                        field_coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
                        config JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                    """
                )
            )
            session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_kg_retrieval_document_versions_adapter "
                    "ON kg_retrieval_document_versions(adapter_name, target)"
                )
            )
            self._retrieval_document_table_ready = True

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
            self._ensure_retrieval_document_table(session)
            session.execute(
                delete(KnowledgeRetrievalDocument).where(
                    KnowledgeRetrievalDocument.source_fact_type == "evidence",
                    KnowledgeRetrievalDocument.source_fact_id.in_(evidence_ids),
                )
            )
            if edge_ids:
                session.execute(
                    delete(KnowledgeRetrievalDocument).where(
                        KnowledgeRetrievalDocument.source_fact_type == "edge",
                        KnowledgeRetrievalDocument.source_fact_id.in_(edge_ids),
                    )
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
        "payload": evidence.payload,
        "span_start": evidence.span_start,
        "span_end": evidence.span_end,
        "version": evidence.version,
        "status": _enum_value(evidence.status),
        "source_fingerprint": evidence.source_fingerprint,
        "superseded_by": evidence.superseded_by,
        "metadata_": evidence.metadata,
    }


def _wiki_page_values(page: WikiPage) -> dict[str, Any]:
    return {
        "page_id": page.page_id,
        "adapter_name": page.adapter_name,
        "page_type": page.page_type,
        "subject_type": page.subject_type,
        "subject_id": page.subject_id,
        "title": page.title,
        "summary": page.summary,
        "content": page.content,
        "source_node_ids": page.source_node_ids,
        "source_edge_ids": page.source_edge_ids,
        "source_evidence_ids": page.source_evidence_ids,
        "version": page.version,
    }


def _retrieval_document_values(document: RetrievalDocument) -> dict[str, Any]:
    return {
        "document_id": document.document_id,
        "adapter_name": document.adapter_name,
        "target": document.target,
        "source_fact_type": document.source_fact_type,
        "source_fact_id": document.source_fact_id,
        "evidence_refs": document.evidence_refs,
        "node_refs": document.node_refs,
        "edge_refs": document.edge_refs,
        "title": document.title,
        "search_text": document.search_text,
        "key_phrases": document.key_phrases,
        "aliases": document.aliases,
        "event_type": document.event_type,
        "relation_intents": document.relation_intents,
        "impact_direction": document.impact_direction,
        "asset_classes": document.asset_classes,
        "time_tags": document.time_tags,
        "source_type_tags": document.source_type_tags,
        "readable_relations": document.readable_relations,
        "evidence_summary": document.evidence_summary,
        "answer_candidate_type": document.answer_candidate_type,
        "confidence": document.confidence,
        "generated_by": document.generated_by,
        "generation_version": document.generation_version,
        "metadata_": document.metadata,
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


def _retrieval_document_version_values(version: RetrievalDocumentVersion) -> dict[str, Any]:
    return {
        "version_id": version.version_id,
        "adapter_name": version.adapter_name,
        "target": version.target,
        "generation_version": version.generation_version,
        "changed_fact_set": version.changed_fact_set,
        "field_coverage": version.field_coverage,
        "config": version.config,
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


def _wiki_page_schema(row: KnowledgeWikiPage) -> WikiPage:
    return WikiPage(
        page_id=row.page_id,
        adapter_name=row.adapter_name,
        page_type=row.page_type,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        title=row.title,
        summary=row.summary,
        content=row.content,
        source_node_ids=row.source_node_ids or [],
        source_edge_ids=row.source_edge_ids or [],
        source_evidence_ids=row.source_evidence_ids or [],
        version=row.version,
    )


def _retrieval_document_schema(row: KnowledgeRetrievalDocument) -> RetrievalDocument:
    return RetrievalDocument(
        document_id=row.document_id,
        adapter_name=row.adapter_name,
        target=row.target,
        source_fact_type=row.source_fact_type,
        source_fact_id=row.source_fact_id,
        evidence_refs=row.evidence_refs or [],
        node_refs=row.node_refs or [],
        edge_refs=row.edge_refs or [],
        title=row.title,
        search_text=row.search_text,
        key_phrases=row.key_phrases or [],
        aliases=row.aliases or [],
        event_type=row.event_type,
        relation_intents=row.relation_intents or [],
        impact_direction=row.impact_direction or "unknown",
        asset_classes=row.asset_classes or [],
        time_tags=row.time_tags or [],
        source_type_tags=row.source_type_tags or [],
        readable_relations=row.readable_relations or [],
        evidence_summary=row.evidence_summary or "",
        answer_candidate_type=row.answer_candidate_type or "unknown",
        confidence=float(row.confidence or 0.0),
        generated_by=row.generated_by or "rule",
        generation_version=row.generation_version,
        metadata=row.metadata_ or {},
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


def _retrieval_document_version_schema(row: KnowledgeRetrievalDocumentVersion) -> RetrievalDocumentVersion:
    return RetrievalDocumentVersion(
        version_id=row.version_id,
        adapter_name=row.adapter_name,
        target=row.target,
        generation_version=row.generation_version,
        changed_fact_set=row.changed_fact_set or {},
        field_coverage=row.field_coverage or {},
        config=row.config or {},
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
    payload = row.payload or {}
    parts: list[str] = []
    if isinstance(payload, dict):
        parts.extend(
            str(payload.get(name) or "")
            for name in ("title", "source_name", "signal_type")
            if payload.get(name)
        )
        parts.extend(_entity_search_terms(payload.get("mentioned_entities")))
        parts.extend(_entity_search_terms(payload.get("affected_entities")))
        parts.extend(_entity_search_terms([payload.get("target_ref")]))
    if row.content and row.content.strip():
        parts.append(row.content)
    elif payload:
        parts.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return "\n".join(_ordered_unique(part.strip() for part in parts if part and part.strip()))


def _compiled_chunk_content(evidence: CompiledEvidence) -> str:
    payload = evidence.payload or {}
    parts: list[str] = []
    if isinstance(payload, dict):
        parts.extend(
            str(payload.get(name) or "")
            for name in ("title", "source_name", "signal_type")
            if payload.get(name)
        )
        parts.extend(_entity_search_terms(payload.get("mentioned_entities")))
        parts.extend(_entity_search_terms(payload.get("affected_entities")))
        parts.extend(_entity_search_terms([payload.get("target_ref")]))
    if evidence.content and evidence.content.strip():
        parts.append(evidence.content)
    elif payload:
        parts.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return "\n".join(_ordered_unique(part.strip() for part in parts if part and part.strip()))


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


def _chunk_values(row: KnowledgeEvidence) -> dict[str, Any]:
    return {
        "chunk_id": f"kg_chunk:{row.evidence_id}:0",
        "adapter_name": row.adapter_name,
        "evidence_id": row.evidence_id,
        "content": _chunk_content(row),
        "payload": row.payload or {},
    }


def _chunk_values_from_compiled(evidence: CompiledEvidence) -> dict[str, Any]:
    return {
        "chunk_id": f"kg_chunk:{evidence.evidence_id}:0",
        "adapter_name": evidence.adapter_name,
        "evidence_id": evidence.evidence_id,
        "content": _compiled_chunk_content(evidence),
        "payload": evidence.payload or {},
    }


def _chunk_schema(row: KnowledgeEvidenceChunk) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=row.chunk_id,
        adapter_name=row.adapter_name,
        evidence_id=row.evidence_id,
        content=row.content,
        payload=row.payload or {},
    )


def _lexical_terms(query: str) -> list[str]:
    terms = [term for term in re.split(r"[\s,，。；;:：|/()（）]+", query.strip()) if term]
    compact = query.strip()
    if compact and compact not in terms and len(compact) <= 40:
        terms.append(compact)
    return _ordered_unique(terms)


def _retrieval_document_match_score(document: RetrievalDocument, terms: list[str]) -> float:
    score = 0.0
    title = document.title.lower()
    search_text = document.search_text.lower()
    phrase_text = "\n".join([*document.key_phrases, *document.aliases, *document.readable_relations]).lower()
    for term in terms:
        lowered = term.lower()
        if lowered in title:
            score += 5.0
        if lowered in phrase_text:
            score += 3.0
        if lowered in search_text:
            score += 1.0
    if document.answer_candidate_type == "answer":
        score += 0.5
    elif document.answer_candidate_type == "support":
        score += 0.25
    return score


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
