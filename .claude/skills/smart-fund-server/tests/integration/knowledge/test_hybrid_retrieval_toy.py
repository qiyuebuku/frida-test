"""Integration tests for toy hybrid retrieval."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.retrieval import HybridRetrievalRuntime, RetrievalOptions
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


def test_hybrid_retrieval_toy_minimum_path() -> None:
    _ensure_tables()
    _cleanup()
    repo = KnowledgeRepositoryImpl(target="test")
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    compile_result = KnowledgeCompiler(repository=repo).compile(ToyProjectAdapter(), records)
    service = KnowledgeService(repository=repo)
    asyncio.run(service.rebuild_wiki("toy"))
    asyncio.run(service.rebuild_indexes("toy"))

    runtime = HybridRetrievalRuntime(repo)
    options = RetrievalOptions(adapter_name="toy", max_chars=2000)
    keyword_hits = runtime.keyword_search("Alpha", options)
    wiki_hits = runtime.wiki_search("Alpha", options)
    graph_hits = runtime.graph_search([keyword_hits[0].node_refs[0]], options, depth=2)
    chunk_hits = runtime.chunk_read([compile_result.evidence[0].evidence_id], options)
    context = runtime.build_answer_context("Alpha", options)

    assert keyword_hits
    assert wiki_hits
    assert graph_hits
    assert chunk_hits[0].evidence_refs == [compile_result.evidence[0].evidence_id]
    assert context.hits
    assert context.matched_nodes
    assert context.matched_edges
    assert context.wiki_pages
    assert context.evidence_chunks
    assert context.budget_usage.used_chars <= context.budget_usage.max_chars

    _cleanup()


def test_knowledge_service_build_answer_context() -> None:
    _ensure_tables()
    _cleanup()
    repo = KnowledgeRepositoryImpl(target="test")
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    KnowledgeCompiler(repository=repo).compile(ToyProjectAdapter(), records)
    service = KnowledgeService(repository=repo)
    asyncio.run(service.rebuild_wiki("toy"))
    asyncio.run(service.rebuild_indexes("toy"))

    context = asyncio.run(
        service.build_answer_context("Design Alpha", RetrievalOptions(adapter_name="toy"))
    )

    assert context.query == "Design Alpha"
    assert context.hits

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
