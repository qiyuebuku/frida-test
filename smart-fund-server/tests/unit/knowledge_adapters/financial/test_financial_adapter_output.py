"""Financial adapter output tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.enums import EdgeStatus
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
from src.domain.knowledge_adapters.financial.ontology import extend_financial_adapter_spec


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


@pytest.mark.asyncio
async def test_event_assertion_edges_use_extended_adapter_spec() -> None:
    spec = extend_financial_adapter_spec(
        extra_entity_types={"infrastructure_theme"},
        extra_relation_types={"constrains"},
    )
    adapter = FinancialKGAdapter(enable_text_extraction=False, adapter_spec=spec)
    records = adapter.normalize(
        {
            "source_type": "l1_events",
            "observed_at": "2026-04-24T10:05:00+08:00",
            "payload": {
                "event_id": "l1-custom",
                "event_type": "policy_constraint",
                "event_time": "2026-04-24T10:05:00+08:00",
                "title": "算力基础设施约束事件",
                "candidate_fact_package": {
                    "entities": [
                        {
                            "type": "infrastructure_theme",
                            "name": "算力基础设施",
                            "confidence": 0.9,
                        }
                    ],
                    "events": [],
                    "relations": [
                        {
                            "source": "算力基础设施约束事件",
                            "target": "算力基础设施",
                            "relation_type": "constrains",
                            "confidence": 0.8,
                        }
                    ],
                },
            },
        }
    )

    result = await KnowledgeCompiler().compile(adapter, records)

    assert result.failed_records == []
    assert any(node.node_type == "infrastructure_theme" for node in result.nodes)
    assert any(
        edge.relation_type == "constrains"
        and edge.target_node_id.startswith("kg:financial:infrastructure_theme:")
        for edge in result.edges
    )


@pytest.mark.asyncio
async def test_candidate_relation_edges_bind_only_matching_fact_signals() -> None:
    adapter = FinancialKGAdapter(enable_text_extraction=False)
    title = "并购重组政策影响产业方向"
    records = adapter.normalize(
        {
            "source_type": "news_articles",
            "source_id": "ft_news:signals",
            "observed_at": "2026-05-01T00:00:00+00:00",
            "payload": {
                "source_id": "ft_news:signals",
                "published_at": "2026-05-01T00:00:00+00:00",
                "title": title,
                "candidate_fact_package": {
                    "entities": [
                        {"type": "industry", "name": "半导体", "confidence": 0.9},
                        {"type": "industry", "name": "生物医药", "confidence": 0.9},
                    ],
                    "relations": [
                        {
                            "source": title,
                            "target": "半导体",
                            "relation_type": "affects",
                            "confidence": 0.86,
                            "evidence_spans": [
                                {
                                    "chunk_id": "kg_chunk:ev:signals:0",
                                    "text": "并购重组政策推动半导体产业链整合",
                                }
                            ],
                        },
                        {
                            "source": title,
                            "target": "生物医药",
                            "relation_type": "affects",
                            "confidence": 0.84,
                            "evidence_spans": [
                                {
                                    "chunk_id": "kg_chunk:ev:signals:1",
                                    "text": "并购重组政策推动生物医药研发平台整合",
                                }
                            ],
                        },
                    ],
                    "fact_signals": [
                        {
                            "signal_type": "impact",
                            "domain_tags": ["半导体"],
                            "impact_tags": ["产业链整合"],
                            "evidence_spans": [
                                {
                                    "chunk_id": "kg_chunk:ev:signals:0",
                                    "text": "并购重组政策推动半导体产业链整合",
                                }
                            ],
                        },
                        {
                            "signal_type": "risk",
                            "domain_tags": ["生物医药"],
                            "risk_tags": ["研发整合风险"],
                            "evidence_spans": [
                                {
                                    "chunk_id": "kg_chunk:ev:signals:1",
                                    "text": "并购重组政策推动生物医药研发平台整合",
                                }
                            ],
                        },
                    ],
                },
            },
            "raw_text": "并购重组政策推动半导体产业链整合，也推动生物医药研发平台整合。",
        }
    )

    result = await KnowledgeCompiler().compile(adapter, records)

    node_by_id = {node.node_id: node for node in result.nodes}
    edge_by_target = {
        node_by_id[edge.target_node_id].canonical_name: edge
        for edge in result.edges
        if edge.relation_type == "affects" and edge.target_node_id in node_by_id
    }
    semi_edge = edge_by_target["半导体"]
    bio_edge = edge_by_target["生物医药"]
    assert semi_edge.properties["domain_tags"] == ["半导体"]
    assert semi_edge.properties["impact_tags"] == ["产业链整合"]
    assert "risk_tags" not in semi_edge.properties
    assert bio_edge.properties["domain_tags"] == ["生物医药"]
    assert bio_edge.properties["risk_tags"] == ["研发整合风险"]
    assert "impact_tags" not in bio_edge.properties


@pytest.mark.asyncio
async def test_candidate_relation_endpoint_without_entity_is_materialized_as_node() -> None:
    adapter = FinancialKGAdapter(enable_text_extraction=False)
    title = "人工智能ETF重仓光模块龙头"
    records = adapter.normalize(
        {
            "source_type": "news_articles",
            "source_id": "ft_news:endpoint_missing",
            "observed_at": "2026-05-01T00:00:00+00:00",
            "payload": {
                "source_id": "ft_news:endpoint_missing",
                "published_at": "2026-05-01T00:00:00+00:00",
                "title": title,
                "candidate_fact_package": {
                    "entities": [
                        {
                            "type": "fund",
                            "name": "创业板人工智能ETF华宝",
                            "identifiers": {"fund_code": "159363"},
                            "confidence": 0.9,
                        }
                    ],
                    "events": [],
                    "relations": [
                        {
                            "source": "创业板人工智能ETF华宝",
                            "target": "中际旭创",
                            "relation_type": "related_to",
                            "confidence": 0.82,
                        }
                    ],
                    "fact_signals": [],
                },
            },
            "raw_text": "创业板人工智能ETF华宝重仓中际旭创。",
        }
    )

    result = await KnowledgeCompiler().compile(adapter, records)

    assert result.failed_records == []
    node_by_id = {node.node_id: node for node in result.nodes}
    target_edges = [
        edge
        for edge in result.edges
        if edge.relation_type == "related_to"
        and node_by_id.get(edge.target_node_id)
        and node_by_id[edge.target_node_id].canonical_name == "中际旭创"
    ]
    assert target_edges
    assert all(edge.evidence_ids for edge in target_edges)


@pytest.mark.asyncio
async def test_candidate_relation_endpoint_reuses_entity_alias() -> None:
    adapter = FinancialKGAdapter(enable_text_extraction=False)
    title = "光模块龙头业绩增长"
    records = adapter.normalize(
        {
            "source_type": "news_articles",
            "source_id": "ft_news:endpoint_alias",
            "observed_at": "2026-05-01T00:00:00+00:00",
            "payload": {
                "source_id": "ft_news:endpoint_alias",
                "published_at": "2026-05-01T00:00:00+00:00",
                "title": title,
                "candidate_fact_package": {
                    "entities": [
                        {
                            "type": "institution",
                            "name": "中际旭创股份有限公司",
                            "aliases": ["中际旭创"],
                            "confidence": 0.9,
                        }
                    ],
                    "events": [],
                    "relations": [
                        {
                            "source": title,
                            "target": "中际旭创",
                            "relation_type": "mentions",
                            "confidence": 0.86,
                        }
                    ],
                    "fact_signals": [],
                },
            },
            "raw_text": "中际旭创股份有限公司一季度业绩增长。",
        }
    )

    result = await KnowledgeCompiler().compile(adapter, records)

    assert result.failed_records == []
    matching_nodes = [node for node in result.nodes if node.canonical_name in {"中际旭创", "中际旭创股份有限公司"}]
    assert len(matching_nodes) == 1
    assert matching_nodes[0].canonical_name == "中际旭创股份有限公司"
    assert matching_nodes[0].node_type == "institution"
    assert any(
        edge.target_node_id == matching_nodes[0].node_id
        for edge in result.edges
        if edge.relation_type == "mentions"
    )


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
