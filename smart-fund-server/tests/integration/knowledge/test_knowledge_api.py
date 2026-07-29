"""Integration tests for knowledge HTTP API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.infrastructure.connections import get_engine, get_session
from src.infrastructure.persistence.models import Base
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCompilationRun,
    KnowledgeEdge,
    KnowledgeEdgeEvidence,
    KnowledgeEdgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeEvidenceChunk,
    KnowledgeGraphAdjacency,
    KnowledgeGraphCommunity,
    KnowledgeGraphDelta,
    KnowledgeGraphFinding,
    KnowledgeNode,
    KnowledgeReviewItem,
    KnowledgeVersion,
)
from src.application.services import knowledge_service as knowledge_service_module
from src.interfaces.api.routes.knowledge import router as knowledge_router


pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "knowledge" / "financial"
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
    KnowledgeGraphFinding.__table__,
    KnowledgeGraphDelta.__table__,
    KnowledgeGraphCommunity.__table__,
]


def test_knowledge_api_minimal_service_flow(monkeypatch) -> None:
    _ensure_tables()
    _cleanup()
    monkeypatch.setattr(knowledge_service_module, "GraphIndexLLMReporter", lambda: _FakeGraphIndexReporter())
    client = _client()
    try:
        health = client.get("/api/kg/health")
        assert health.status_code == 200
        assert "financial" in health.json()["adapters"]

        compile_resp = client.post(
            "/api/kg/compile",
            json={"adapter_name": "financial", "target": "test", "records": _load_all()},
        )
        assert compile_resp.status_code == 200
        compile_body = compile_resp.json()
        assert compile_body["failed_records"] == 0
        assert compile_body["nodes"] > 0
        assert compile_body["edges"] > 0
        assert compile_body["evidence"] > 0

        index_resp = client.post(
            "/api/kg/rebuild-indexes",
            json={"adapter_name": "financial", "target": "test"},
        )
        assert index_resp.status_code == 200
        assert index_resp.json()["graph_adjacency"] > 0
        assert index_resp.json()["evidence_chunks"] > 0

        context_resp = client.post(
            "/api/kg/research-context",
            json={
                "adapter_name": "financial",
                "target": "test",
                "query": "固态电池 宁德时代",
            },
        )
        assert context_resp.status_code == 200
        context = context_resp.json()
        assert context["hits"]
        assert context["matched_nodes"]
        assert context["matched_edges"]
        assert context["evidence_refs"]

        quality_resp = client.post(
            "/api/kg/quality-scan",
            json={"adapter_name": "financial", "target": "test"},
        )
        assert quality_resp.status_code == 200
        assert "metrics" in quality_resp.json()

        reviews_resp = client.get("/api/kg/reviews?status=open&target=test")
        assert reviews_resp.status_code == 200
        assert "total" in reviews_resp.json()
    finally:
        _cleanup()


class _FakeGraphIndexReporter:
    async def enrich(self, *, graph_index, nodes, edges, chunks):
        return graph_index


def test_knowledge_api_rejects_unknown_adapter() -> None:
    client = _client()

    response = client.post(
        "/api/kg/rebuild-indexes",
        json={"adapter_name": "missing", "target": "test"},
    )

    assert response.status_code == 400
    assert "adapter_name 不支持" in response.json()["detail"]


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(knowledge_router)
    return TestClient(app)


def _ensure_tables() -> None:
    Base.metadata.create_all(get_engine("test"), tables=KG_TABLES)


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
        session.execute(delete(KnowledgeReviewItem).where(KnowledgeReviewItem.object_id.like("kg:%")))
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
        "feedback_records.json",
    ]:
        records.extend(json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")))
    return records
