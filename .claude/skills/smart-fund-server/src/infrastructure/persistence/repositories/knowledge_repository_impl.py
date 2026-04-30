"""KnowledgeRepository SQLAlchemy implementation."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Literal
from uuid import uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.quality import ReviewAction, ReviewEntry
from src.domain.knowledge.repositories import KnowledgeRepository
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
    KnowledgeWikiPage,
)
from src.domain.knowledge.wiki import WikiPage

Target = Literal["prod", "test"]


class KnowledgeRepositoryImpl(KnowledgeRepository):
    def __init__(self, session: Session | None = None, target: Target | None = None):
        self._session = session
        self._target = target

    def upsert_nodes(self, nodes: list[CompiledNode]) -> int:
        if not nodes:
            return 0
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
            for edge in edges:
                self._attach_edge_evidence_in_session(session, edge.edge_id, edge.evidence_ids)
            return result.rowcount or 0

    def upsert_evidence(self, evidence: list[CompiledEvidence]) -> int:
        if not evidence:
            return 0
        rows = [_evidence_values(item) for item in evidence]
        with self._session_scope() as session:
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
                        KnowledgeEvidence.metadata_: excluded["metadata"],
                    },
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
            row = session.scalar(
                select(KnowledgeEvidence).where(KnowledgeEvidence.evidence_id == evidence_id)
            )
            return _evidence_schema(row) if row else None

    def get_edge_evidence(self, edge_id: str) -> list[CompiledEvidence]:
        with self._session_scope() as session:
            rows = session.scalars(
                select(KnowledgeEvidence)
                .join(
                    KnowledgeEdgeEvidence,
                    KnowledgeEvidence.evidence_id == KnowledgeEdgeEvidence.evidence_id,
                )
                .where(KnowledgeEdgeEvidence.edge_id == edge_id)
                .order_by(KnowledgeEvidence.evidence_id)
            ).all()
            return [_evidence_schema(row) for row in rows]

    def list_nodes(self, adapter_name: str) -> list[CompiledNode]:
        with self._session_scope() as session:
            rows = session.scalars(
                select(KnowledgeNode)
                .where(KnowledgeNode.adapter_name == adapter_name)
                .order_by(KnowledgeNode.node_id)
            ).all()
            return [_node_schema(row) for row in rows]

    def list_edges(self, adapter_name: str) -> list[CompiledEdge]:
        with self._session_scope() as session:
            rows = session.scalars(
                select(KnowledgeEdge)
                .where(KnowledgeEdge.adapter_name == adapter_name)
                .order_by(KnowledgeEdge.edge_id)
            ).all()
            edge_ids = [row.edge_id for row in rows]
            evidence_by_edge: dict[str, list[str]] = {edge_id: [] for edge_id in edge_ids}
            if edge_ids:
                links = session.execute(
                    select(KnowledgeEdgeEvidence.edge_id, KnowledgeEdgeEvidence.evidence_id)
                    .where(KnowledgeEdgeEvidence.edge_id.in_(edge_ids))
                    .order_by(KnowledgeEdgeEvidence.edge_id, KnowledgeEdgeEvidence.evidence_id)
                ).all()
                for edge_id, evidence_id in links:
                    evidence_by_edge.setdefault(edge_id, []).append(evidence_id)
            return [_edge_schema(row, evidence_by_edge.get(row.edge_id, [])) for row in rows]

    def list_evidence(self, adapter_name: str) -> list[CompiledEvidence]:
        with self._session_scope() as session:
            rows = session.scalars(
                select(KnowledgeEvidence)
                .where(KnowledgeEvidence.adapter_name == adapter_name)
                .order_by(KnowledgeEvidence.evidence_id)
            ).all()
            return [_evidence_schema(row) for row in rows]

    def rebuild_wiki_pages(self, adapter_name: str, pages: list[WikiPage]) -> int:
        with self._session_scope() as session:
            session.execute(delete(KnowledgeWikiPage).where(KnowledgeWikiPage.adapter_name == adapter_name))
            if not pages:
                return 0
            rows = [_wiki_page_values(page) for page in pages]
            result = session.execute(pg_insert(KnowledgeWikiPage).values(rows))
            return result.rowcount or 0

    def upsert_wiki_pages(self, adapter_name: str, pages: list[WikiPage]) -> int:
        pages = [page for page in pages if page.adapter_name == adapter_name]
        if not pages:
            return 0
        rows = [_wiki_page_values(page) for page in pages]
        with self._session_scope() as session:
            stmt = pg_insert(KnowledgeWikiPage).values(rows)
            excluded = stmt.excluded
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
        with self._session_scope() as session:
            session.execute(
                delete(KnowledgeGraphAdjacency).where(
                    KnowledgeGraphAdjacency.adapter_name == adapter_name
                )
            )
            edges = session.scalars(
                select(KnowledgeEdge).where(KnowledgeEdge.adapter_name == adapter_name)
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
            result = session.execute(pg_insert(KnowledgeGraphAdjacency).values(rows))
            return result.rowcount or 0

    def upsert_graph_adjacency(self, edges: list[CompiledEdge]) -> int:
        if not edges:
            return 0
        edge_ids = [edge.edge_id for edge in edges]
        rows = [
            {
                "adapter_name": edge.adapter_name,
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "edge_id": edge.edge_id,
                "relation_type": edge.relation_type,
            }
            for edge in edges
        ]
        with self._session_scope() as session:
            session.execute(
                delete(KnowledgeGraphAdjacency).where(
                    KnowledgeGraphAdjacency.edge_id.in_(edge_ids)
                )
            )
            result = session.execute(pg_insert(KnowledgeGraphAdjacency).values(rows))
            return result.rowcount or 0

    def get_neighbors(self, node_id: str, adapter_name: str | None = None) -> list[str]:
        with self._session_scope() as session:
            stmt = select(KnowledgeGraphAdjacency.target_node_id).where(
                KnowledgeGraphAdjacency.source_node_id == node_id
            )
            if adapter_name is not None:
                stmt = stmt.where(KnowledgeGraphAdjacency.adapter_name == adapter_name)
            return list(session.scalars(stmt.order_by(KnowledgeGraphAdjacency.target_node_id)).all())

    def rebuild_evidence_chunks(self, adapter_name: str) -> int:
        with self._session_scope() as session:
            session.execute(
                delete(KnowledgeEvidenceChunk).where(
                    KnowledgeEvidenceChunk.adapter_name == adapter_name
                )
            )
            evidence_rows = session.scalars(
                select(KnowledgeEvidence).where(KnowledgeEvidence.adapter_name == adapter_name)
            ).all()
            rows = [_chunk_values(row) for row in evidence_rows if _chunk_content(row)]
            if not rows:
                return 0
            result = session.execute(pg_insert(KnowledgeEvidenceChunk).values(rows))
            return result.rowcount or 0

    def upsert_evidence_chunks(self, evidence: list[CompiledEvidence]) -> int:
        if not evidence:
            return 0
        chunk_ids = [f"kg_chunk:{item.evidence_id}:0" for item in evidence]
        rows = [_chunk_values_from_compiled(item) for item in evidence if _compiled_chunk_content(item)]
        with self._session_scope() as session:
            session.execute(
                delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.chunk_id.in_(chunk_ids))
            )
            if not rows:
                return 0
            result = session.execute(pg_insert(KnowledgeEvidenceChunk).values(rows))
            return result.rowcount or 0

    def list_evidence_chunks(self, adapter_name: str) -> list[EvidenceChunk]:
        with self._session_scope() as session:
            rows = session.scalars(
                select(KnowledgeEvidenceChunk)
                .where(KnowledgeEvidenceChunk.adapter_name == adapter_name)
                .order_by(KnowledgeEvidenceChunk.chunk_id)
            ).all()
            return [_chunk_schema(row) for row in rows]

    def upsert_review_entries(self, entries: list[ReviewEntry]) -> int:
        if not entries:
            return 0
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
        with self._session_scope() as session:
            session.execute(
                update(KnowledgeCompilationRun)
                .where(KnowledgeCompilationRun.run_id == run_id)
                .values(**values)
            )

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
        rows = [{"edge_id": edge_id, "evidence_id": evidence_id} for evidence_id in evidence_ids]
        stmt = pg_insert(KnowledgeEdgeEvidence).values(rows).on_conflict_do_nothing()
        result = session.execute(stmt)
        return result.rowcount or 0


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


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)
