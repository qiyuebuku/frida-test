"""Integration tests for financial research QA context."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
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
from src.infrastructure.persistence.repositories.knowledge_repository_impl import KnowledgeRepositoryImpl


pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "knowledge" / "financial"
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


def test_research_qa_context_contains_evidence_and_consumption_split() -> None:
    _ensure_tables()
    _cleanup()
    repo = KnowledgeRepositoryImpl(target="test")
    _compile_financial(repo, _load_all())
    service = KnowledgeService(repository=repo)
    asyncio.run(service.rebuild_wiki("financial"))
    asyncio.run(service.rebuild_indexes("financial"))

    result = asyncio.run(service.build_research_context("固态电池 对 宁德时代 和 示例成长混合 的影响"))
    context = result["answer_context"]

    assert context.hits
    assert result["evidence_refs"]
    assert result["hard_score_edges"]
    assert result["explanation_edges"]
    assert context.evidence_chunks

    _cleanup()


def _ensure_tables() -> None:
    Base.metadata.create_all(get_engine("test"), tables=KG_TABLES)


def _compile_financial(repo: KnowledgeRepositoryImpl, records: list[dict]):
    adapter = FinancialKGAdapter()
    return asyncio.run(KnowledgeCompiler(repository=repo).compile(adapter, adapter.normalize(records)))


def _cleanup() -> None:
    with get_session("test") as session:
        edge_ids = select(KnowledgeEdge.edge_id).where(KnowledgeEdge.adapter_name == "financial")
        session.execute(delete(KnowledgeEdgeEvidence).where(KnowledgeEdgeEvidence.edge_id.in_(edge_ids)))
        session.execute(delete(KnowledgeGraphAdjacency).where(KnowledgeGraphAdjacency.adapter_name == "financial"))
        session.execute(delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.adapter_name == "financial"))
        session.execute(delete(KnowledgeWikiPage).where(KnowledgeWikiPage.adapter_name == "financial"))
        session.execute(delete(KnowledgeEdge).where(KnowledgeEdge.adapter_name == "financial"))
        session.execute(delete(KnowledgeEvidence).where(KnowledgeEvidence.adapter_name == "financial"))
        session.execute(delete(KnowledgeNode).where(KnowledgeNode.adapter_name == "financial"))
        session.execute(delete(KnowledgeCompilationRun).where(KnowledgeCompilationRun.adapter_name == "financial"))


def _load_all() -> list[dict]:
    records: list[dict] = []
    for name in [
        "stock_basics.json",
        "industry_components.json",
        "concept_components.json",
        "fund_holdings.json",
        "policy_news.json",
        "l1_events.json",
    ]:
        records.extend(json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")))
    return records
