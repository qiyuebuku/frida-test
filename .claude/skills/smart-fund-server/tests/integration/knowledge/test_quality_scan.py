"""Integration tests for quality scan and review queue."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge.enums import NodeStatus
from src.domain.knowledge.schemas import CompiledNode
from src.infrastructure.connections import get_engine, get_session
from src.infrastructure.persistence.models import Base
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCompilationRun,
    KnowledgeEdge,
    KnowledgeEdgeEvidence,
    KnowledgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeGraphAdjacency,
    KnowledgeNode,
    KnowledgeReviewItem,
    KnowledgeVersion,
    KnowledgeWikiPage,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (
    KnowledgeRepositoryImpl,
)


pytestmark = pytest.mark.integration

KG_TABLES = [
    KnowledgeNode.__table__,
    KnowledgeEvidence.__table__,
    KnowledgeEdge.__table__,
    KnowledgeEdgeEvidence.__table__,
    KnowledgeVersion.__table__,
    KnowledgeReviewItem.__table__,
    KnowledgeCompilationRun.__table__,
    KnowledgeWikiPage.__table__,
    KnowledgeGraphAdjacency.__table__,
    KnowledgeEvidenceChunk.__table__,
]


def test_quality_scan_persists_review_entries() -> None:
    _ensure_tables()
    _cleanup()
    repo = KnowledgeRepositoryImpl(target="test")
    repo.upsert_nodes(
        [
            CompiledNode(
                node_id="kg:quality:item:a",
                adapter_name="quality",
                node_type="item",
                canonical_name="Same",
                status=NodeStatus.ACTIVE,
                version="v1",
            ),
            CompiledNode(
                node_id="kg:quality:item:b",
                adapter_name="quality",
                node_type="item",
                canonical_name="Same",
                status=NodeStatus.ACTIVE,
                version="v1",
            ),
        ]
    )
    service = KnowledgeService(repository=repo)

    report = asyncio.run(service.quality_scan("quality"))
    reviews = asyncio.run(service.list_review_queue())

    assert any(issue.category == "duplicate_canonical_name" for issue in report.issues)
    assert reviews
    asyncio.run(service.apply_review_action(reviews[0].review_id, "request_more_evidence"))
    updated = repo.list_review_entries(status="request_more_evidence")
    assert any(entry.review_id == reviews[0].review_id for entry in updated)

    _cleanup()


def _ensure_tables() -> None:
    Base.metadata.create_all(get_engine("test"), tables=KG_TABLES)


def _cleanup() -> None:
    with get_session("test") as session:
        edge_ids = select(KnowledgeEdge.edge_id).where(KnowledgeEdge.adapter_name == "quality")
        session.execute(delete(KnowledgeEdgeEvidence).where(KnowledgeEdgeEvidence.edge_id.in_(edge_ids)))
        session.execute(delete(KnowledgeGraphAdjacency).where(KnowledgeGraphAdjacency.adapter_name == "quality"))
        session.execute(delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.adapter_name == "quality"))
        session.execute(delete(KnowledgeWikiPage).where(KnowledgeWikiPage.adapter_name == "quality"))
        session.execute(delete(KnowledgeReviewItem).where(KnowledgeReviewItem.object_id.like("kg:quality:%")))
        session.execute(delete(KnowledgeReviewItem).where(KnowledgeReviewItem.object_id.like("item:%")))
        session.execute(delete(KnowledgeEdge).where(KnowledgeEdge.adapter_name == "quality"))
        session.execute(delete(KnowledgeEvidence).where(KnowledgeEvidence.adapter_name == "quality"))
        session.execute(delete(KnowledgeNode).where(KnowledgeNode.adapter_name == "quality"))
        session.execute(delete(KnowledgeCompilationRun).where(KnowledgeCompilationRun.adapter_name == "quality"))
