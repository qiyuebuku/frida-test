"""Integration tests for financial adapter compilation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select

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


def test_financial_fixture_compiles_and_is_idempotent() -> None:
    _ensure_tables()
    _cleanup()
    repo = KnowledgeRepositoryImpl(target="test")
    records = _load_all()

    first = KnowledgeCompiler(repository=repo).compile(FinancialKGAdapter(), records)
    second = KnowledgeCompiler(repository=repo).compile(FinancialKGAdapter(), records)

    assert first.failed_records == []
    assert second.failed_records == []
    with get_session("test") as session:
        node_count = session.query(KnowledgeNode).filter_by(adapter_name="financial").count()
        edge_count = session.query(KnowledgeEdge).filter_by(adapter_name="financial").count()
        evidence_count = session.query(KnowledgeEvidence).filter_by(adapter_name="financial").count()

    assert node_count == len({node.node_id for node in first.nodes})
    assert edge_count == len({edge.edge_id for edge in first.edges})
    assert evidence_count == len({item.evidence_id for item in first.evidence})

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
        "derived_signals.json",
        "feedback_records.json",
    ]:
        records.extend(json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")))
    return records
