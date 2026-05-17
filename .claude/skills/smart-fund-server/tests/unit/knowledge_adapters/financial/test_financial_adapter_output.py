"""Financial adapter output tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.enums import EdgeStatus
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter


FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "knowledge" / "financial"


@pytest.mark.asyncio
async def test_financial_fixtures_compile_to_expected_nodes_edges_and_evidence() -> None:
    records = _load_all()
    expected_nodes = _load_json("expected_nodes.json")
    expected_edges = _load_json("expected_edges.json")
    adapter = FinancialKGAdapter()

    result = await KnowledgeCompiler().compile(adapter, adapter.normalize(records))

    assert result.failed_records == []
    assert expected_nodes["minimum_count"] <= len(result.nodes)
    assert set(expected_nodes["node_types"]).issubset({node.node_type for node in result.nodes})
    assert set(expected_edges["relation_types"]).issubset({edge.relation_type for edge in result.edges})
    assert all(edge.evidence_ids for edge in result.edges if edge.status == EdgeStatus.ACTIVE)
    assert any(edge.relation_type == "affects" and edge.status == EdgeStatus.CANDIDATE for edge in result.edges)
    assert result.evidence


def test_source_record_metadata_is_preserved_in_evidence_draft() -> None:
    item = FinancialKGAdapter(enable_text_extraction=False).normalize(
        {
            "source_type": "news_articles",
            "source_id": "ft_news:1",
            "observed_at": "2026-05-01T00:00:00+00:00",
            "payload": {
                "source_id": "ft_news:1",
                "published_at": "2026-05-01T00:00:00+00:00",
                "title": "测试新闻",
            },
            "raw_text": "测试新闻",
            "metadata": {
                "source_table": "ft_news",
                "source_pk": 1,
                "source_type_reason": "category_company",
                "source_type_uncertain": False,
            },
        }
    )[0]

    evidence = FinancialKGAdapter(enable_text_extraction=False).extract_evidence_drafts(item)[0]

    assert evidence.metadata["adapter"] == "financial"
    assert evidence.metadata["source_table"] == "ft_news"
    assert evidence.metadata["source_type_reason"] == "category_company"
    assert evidence.metadata["source_type_uncertain"] is False


def _load_all() -> list[dict]:
    records: list[dict] = []
    for name in [
        "stock_basics.json",
        "industry_components.json",
        "concept_components.json",
        "fund_holdings.json",
        "news_articles.json",
        "policy_news.json",
        "l1_events.json",
        "derived_signals.json",
        "feedback_records.json",
    ]:
        records.extend(_load_json(name))
    return records


def _load_json(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
