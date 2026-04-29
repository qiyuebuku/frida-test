"""Integration test for financial impact path retrieval."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.retrieval import HybridRetrievalRuntime, RetrievalOptions
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
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (
    KnowledgeRepositoryImpl,
)


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


def test_financial_event_to_concept_stock_fund_path_is_retrievable() -> None:
    _ensure_tables()
    _cleanup()
    repo = KnowledgeRepositoryImpl(target="test")
    result = KnowledgeCompiler(repository=repo).compile(FinancialKGAdapter(), _load_all())
    service = KnowledgeService(repository=repo)
    asyncio.run(service.rebuild_wiki("financial"))
    asyncio.run(service.rebuild_indexes("financial"))
    policy_node = next(node for node in result.nodes if node.node_type == "policy")

    hits = HybridRetrievalRuntime(repo).graph_search(
        [policy_node.node_id],
        RetrievalOptions(adapter_name="financial", graph_depth=3, graph_limit=20),
        depth=3,
        limit=20,
    )

    relation_types = {hit.title for hit in hits}
    node_refs = {node_ref for hit in hits for node_ref in hit.node_refs}
    loaded_nodes = [repo.get_node(node_id) for node_id in node_refs]
    loaded_types = {node.node_type for node in loaded_nodes if node is not None}

    assert {"affects", "belongs_to", "holds"}.issubset(relation_types)
    assert {"policy", "concept", "stock", "fund"}.issubset(loaded_types)

    _cleanup()


def _ensure_tables() -> None:
    Base.metadata.create_all(get_engine("test"), tables=KG_TABLES)


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
