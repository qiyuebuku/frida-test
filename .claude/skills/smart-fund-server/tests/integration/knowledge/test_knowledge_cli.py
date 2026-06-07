"""Integration tests for knowledge CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
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
from src.interfaces.cli.knowledge import kg
from src.application.services import knowledge_service as knowledge_service_module


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


def test_knowledge_cli_minimal_service_flow(tmp_path: Path, monkeypatch) -> None:
    _ensure_tables()
    _cleanup()
    monkeypatch.setattr(knowledge_service_module, "GraphIndexLLMReporter", lambda: _FakeGraphIndexReporter())
    runner = CliRunner()
    records_file = tmp_path / "financial_records.json"
    records_file.write_text(json.dumps({"records": _load_all()}, ensure_ascii=False), encoding="utf-8")
    try:
        health = runner.invoke(kg, ["health", "--target", "test", "--json"])
        assert health.exit_code == 0, health.output
        assert json.loads(health.output)["status"] in {"ok", "degraded"}

        compiled = runner.invoke(
            kg,
            [
                "compile",
                "--adapter",
                "financial",
                "--target",
                "test",
                "--file",
                str(records_file),
                "--json",
            ],
        )
        assert compiled.exit_code == 0, compiled.output
        compile_body = json.loads(compiled.output)
        assert compile_body["failed_records"] == 0
        assert compile_body["nodes"] > 0

        indexes = runner.invoke(
            kg,
            ["rebuild-indexes", "--adapter", "financial", "--target", "test", "--json"],
        )
        assert indexes.exit_code == 0, indexes.output
        assert json.loads(indexes.output)["graph_adjacency"] > 0

        query = runner.invoke(
            kg,
            [
                "query",
                "--adapter",
                "financial",
                "--target",
                "test",
                "--query",
                "固态电池 宁德时代",
                "--json",
            ],
        )
        assert query.exit_code == 0, query.output
        query_body = json.loads(query.output)
        assert query_body["evidence_refs"]

        quality = runner.invoke(
            kg,
            ["quality-scan", "--adapter", "financial", "--target", "test", "--json"],
        )
        assert quality.exit_code == 0, quality.output
        assert "metrics" in json.loads(quality.output)
    finally:
        _cleanup()


class _FakeGraphIndexReporter:
    async def enrich(self, *, graph_index, nodes, edges, chunks):
        return graph_index


def test_knowledge_cli_reports_file_errors() -> None:
    runner = CliRunner()

    result = runner.invoke(
        kg,
        ["compile", "--file", "/path/not-exist.json", "--target", "test"],
    )

    assert result.exit_code != 0
    assert "does not exist" in result.output


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
