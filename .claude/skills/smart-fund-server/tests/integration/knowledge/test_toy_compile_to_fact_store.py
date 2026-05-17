"""Integration test for compiler output persisted to the fact store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.toy_adapter import ToyProjectAdapter
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

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "knowledge"
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


@pytest.mark.asyncio
async def test_toy_compiler_persists_nodes_edges_evidence_and_run() -> None:
    _ensure_tables()
    _cleanup()
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    repo = KnowledgeRepositoryImpl(target="test")
    adapter = ToyProjectAdapter()

    result = await KnowledgeCompiler(repository=repo).compile(adapter, adapter.normalize(records))

    assert len(result.nodes) == 4
    assert len(result.edges) == 3
    assert len(result.evidence) == 1
    assert result.failed_records == []

    with get_session("test") as session:
        assert session.query(KnowledgeNode).filter_by(adapter_name="toy").count() == 4
        assert session.query(KnowledgeEdge).filter_by(adapter_name="toy").count() == 3
        assert session.query(KnowledgeEvidence).filter_by(adapter_name="toy").count() == 1
        assert session.query(KnowledgeEdgeEvidence).count() >= 3
        run = session.get(KnowledgeCompilationRun, result.run_id)

    assert run is not None
    assert run.status == "success"
    assert run.node_count == 4
    assert run.edge_count == 3
    assert run.evidence_count == 1

    _cleanup()


def _ensure_tables() -> None:
    Base.metadata.create_all(get_engine("test"), tables=KG_TABLES)


def _cleanup() -> None:
    with get_session("test") as session:
        toy_edge_ids = select(KnowledgeEdge.edge_id).where(KnowledgeEdge.adapter_name == "toy")
        session.execute(
            delete(KnowledgeEdgeEvidence).where(KnowledgeEdgeEvidence.edge_id.in_(toy_edge_ids))
        )
        session.execute(delete(KnowledgeGraphAdjacency).where(KnowledgeGraphAdjacency.adapter_name == "toy"))
        session.execute(delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.adapter_name == "toy"))
        session.execute(delete(KnowledgeWikiPage).where(KnowledgeWikiPage.adapter_name == "toy"))
        session.execute(delete(KnowledgeEdge).where(KnowledgeEdge.adapter_name == "toy"))
        session.execute(delete(KnowledgeEvidence).where(KnowledgeEvidence.adapter_name == "toy"))
        session.execute(delete(KnowledgeNode).where(KnowledgeNode.adapter_name == "toy"))
        session.execute(delete(KnowledgeCompilationRun).where(KnowledgeCompilationRun.adapter_name == "toy"))
