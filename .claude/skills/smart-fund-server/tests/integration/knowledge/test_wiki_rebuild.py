"""Integration tests for generated wiki and index rebuild."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from src.application.services.knowledge_service import KnowledgeService
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


def test_wiki_and_indexes_are_rebuildable_from_fact_store() -> None:
    _ensure_tables()
    _cleanup()
    repo = KnowledgeRepositoryImpl(target="test")
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    compile_result = _compile_toy(repo, records)

    service = KnowledgeService(repository=repo)
    wiki_result = asyncio.run(service.rebuild_wiki("toy"))
    index_counts = asyncio.run(service.rebuild_indexes("toy"))

    assert len(wiki_result.pages) == 8
    assert index_counts == {"graph_adjacency": 3, "evidence_chunks": 1}
    assert repo.search_wiki_pages("toy", "Alpha", limit=10)
    assert repo.get_neighbors(compile_result.edges[0].source_node_id, adapter_name="toy")

    with get_session("test") as session:
        session.execute(delete(KnowledgeWikiPage).where(KnowledgeWikiPage.adapter_name == "toy"))

    rebuilt = asyncio.run(service.rebuild_wiki("toy"))
    assert len(rebuilt.pages) == 8
    assert len(repo.list_wiki_pages("toy")) == 8

    _cleanup()


def _ensure_tables() -> None:
    Base.metadata.create_all(get_engine("test"), tables=KG_TABLES)


def _compile_toy(repo: KnowledgeRepositoryImpl, records: list[dict]):
    adapter = ToyProjectAdapter()
    return asyncio.run(KnowledgeCompiler(repository=repo).compile(adapter, adapter.normalize(records)))


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
