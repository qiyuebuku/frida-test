"""Integration tests for L2 storyline context from KG paths."""

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
from src.infrastructure.persistence.repositories.knowledge_repository_impl import KnowledgeRepositoryImpl


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


def test_l2_can_group_events_by_shared_concept_and_graph_path() -> None:
    _ensure_tables()
    _cleanup()
    repo = KnowledgeRepositoryImpl(target="test")
    compile_result = _compile_financial(repo, _load_all())
    service = KnowledgeService(repository=repo)
    asyncio.run(service.rebuild_indexes("financial"))
    event_nodes = [node for node in compile_result.nodes if node.node_type in {"event", "policy"}]

    paths = []
    for node in event_nodes:
        result = asyncio.run(service.find_financial_paths([node.node_id], max_depth=2, limit=10))
        paths.extend(result["paths"])

    shared_concept_paths = [
        path
        for path in paths
        if any(item["node_type"] == "concept" and item["canonical_name"] == "固态电池" for item in path["path"])
    ]

    assert len(event_nodes) >= 2
    assert shared_concept_paths
    assert any(path["evidence_refs"] for path in shared_concept_paths)

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
