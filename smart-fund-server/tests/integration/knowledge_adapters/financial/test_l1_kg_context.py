"""Integration tests for L1 consuming and writing KG context."""

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
    KnowledgeEdgeEvidenceChunk,
    KnowledgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeGraphAdjacency,
    KnowledgeNode,
    KnowledgeReviewItem,
    KnowledgeVersion,
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
    KnowledgeEdgeEvidenceChunk.__table__,
    KnowledgeVersion.__table__,
    KnowledgeReviewItem.__table__,
    KnowledgeCompilationRun.__table__,
    KnowledgeGraphAdjacency.__table__,
    KnowledgeEvidenceChunk.__table__,
]


def test_l1_text_resolves_entities_and_expands_candidates() -> None:
    _ensure_tables()
    _cleanup()
    repo = _bootstrap()
    service = KnowledgeService(repository=repo)

    resolved = asyncio.run(service.resolve_financial_entities("固态电池政策支持宁德时代"))
    expanded = asyncio.run(service.expand_financial_candidates(resolved["candidates"], depth=2))

    assert any(item["node_type"] == "concept" for item in resolved["candidates"])
    assert any(item["node_type"] == "stock" for item in resolved["candidates"])
    assert expanded["candidate_node_ids"]

    _cleanup()


def test_l1_event_record_writes_back_through_financial_adapter() -> None:
    _ensure_tables()
    _cleanup()
    repo = _bootstrap()
    service = KnowledgeService(repository=repo)
    event_record = {
        "event_id": "l1-kg-002",
        "event_type": "policy_support",
        "event_time": "2026-04-24T12:00:00+08:00",
        "title": "固态电池新增政策支持",
        "mentioned_entities": [
            {"type": "concept", "taxonomy": "theme", "name": "固态电池"},
        ],
        "affected_entities": [
            {
                "type": "stock",
                "exchange": "SZ",
                "code": "300750",
                "name": "宁德时代",
                "direction": "positive",
                "confidence": 0.66,
            }
        ],
    }

    result = asyncio.run(service.write_l1_event_to_kg(event_record))

    assert result.failed_records == []
    assert any(node.node_type == "event" and node.canonical_name == "固态电池新增政策支持" for node in result.nodes)
    assert any(edge.relation_type == "affects" for edge in result.edges)
    assert all(edge.evidence_ids for edge in result.edges)

    _cleanup()


def _bootstrap() -> KnowledgeRepositoryImpl:
    repo = KnowledgeRepositoryImpl(target="test")
    _compile_financial(repo, _load_all())
    service = KnowledgeService(repository=repo)
    asyncio.run(service.rebuild_indexes("financial"))
    return repo


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
        session.execute(

            delete(KnowledgeEdgeEvidenceChunk).where(

                KnowledgeEdgeEvidenceChunk.evidence_id.in_(

                    select(KnowledgeEvidence.evidence_id).where(KnowledgeEvidence.adapter_name == "financial")

                )

            )

        )

        session.execute(delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.adapter_name == "financial"))
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
