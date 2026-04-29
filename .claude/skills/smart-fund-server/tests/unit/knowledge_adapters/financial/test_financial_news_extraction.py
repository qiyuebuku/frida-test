"""Financial news extraction strategy tests."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.enums import EdgeStatus
from src.domain.knowledge.adapter import ValidationResult
from src.domain.knowledge.extraction import ExtractedEntity, TextExtractionInput, TextExtractionResult
from src.domain.knowledge.schemas import KnowledgeInput
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
from src.domain.knowledge_adapters.financial.news_extraction import (
    FinancialNewsExtractionStrategy,
    enrich_financial_text_payload,
)


@pytest.mark.asyncio
async def test_financial_news_strategy_turns_symbols_into_mentioned_entities() -> None:
    payload = {
        "source_id": "news-001",
        "title": "宁德时代加码固态电池研发",
        "published_at": "2026-04-24T09:30:00+08:00",
        "text": "宁德时代宣布加码固态电池研发。",
        "symbols": [{"exchange": "SZ", "code": "300750", "name": "宁德时代"}],
    }
    adapter = FinancialKGAdapter()
    enriched = await enrich_financial_text_payload(
        payload,
        source_id="news-001",
        source_type="news_articles",
        pipeline=adapter.text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(),
    )

    assert enriched["mentioned_entities"] == [
        {"type": "stock", "name": "宁德时代", "confidence": 0.9, "exchange": "SZ", "code": "300750"}
    ]


@pytest.mark.asyncio
async def test_financial_adapter_compiles_news_article_without_business_pre_extraction() -> None:
    record = {
        "source_type": "news_articles",
        "payload": {
            "source_id": "news-001",
            "title": "宁德时代加码固态电池研发",
            "published_at": "2026-04-24T09:30:00+08:00",
            "text": "宁德时代宣布加码固态电池研发。",
            "symbols": [{"exchange": "SZ", "code": "300750", "name": "宁德时代"}],
        },
    }

    result = await KnowledgeCompiler().compile(FinancialKGAdapter(), [record])

    assert result.failed_records == []
    assert {node.node_type for node in result.nodes} == {"event", "stock"}
    assert any(edge.relation_type == "mentions" and edge.status == EdgeStatus.ACTIVE for edge in result.edges)


@pytest.mark.asyncio
async def test_financial_adapter_can_disable_text_extraction() -> None:
    item = KnowledgeInput(
        input_type=FinancialKGAdapter.spec.sources[4].input_type,
        source_type="news_articles",
        source_id="news-001",
        observed_at=datetime.fromisoformat("2026-04-24T09:30:00+08:00"),
        adapter_name="financial",
        adapter_version="v1",
        payload={
            "source_id": "news-001",
            "title": "宁德时代加码固态电池研发",
            "published_at": "2026-04-24T09:30:00+08:00",
            "symbols": [{"exchange": "SZ", "code": "300750", "name": "宁德时代"}],
        },
        raw_text="宁德时代加码固态电池研发",
    )

    nodes = await FinancialKGAdapter(enable_text_extraction=False).extract_node_drafts(item)

    assert [node.node_type for node in nodes] == ["event"]


@pytest.mark.asyncio
async def test_financial_adapter_reuses_enriched_payload_for_nodes_and_edges() -> None:
    class CountingStrategy:
        name = "counting"
        version = "v1"

        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, item: TextExtractionInput) -> TextExtractionResult:
            del item
            self.calls += 1
            return TextExtractionResult(
                mentioned_entities=[
                    ExtractedEntity(
                        entity_type="institution",
                        canonical_name=f"机构{self.calls}",
                        confidence=0.9,
                    )
                ]
            )

        def validate_result(self, result: TextExtractionResult, item: TextExtractionInput) -> ValidationResult:
            del result, item
            return ValidationResult.success()

    strategy = CountingStrategy()
    adapter = FinancialKGAdapter(news_extraction_strategy=strategy)
    item = adapter.normalize(
        {
            "source_type": "news_articles",
            "payload": {
                "source_id": "news-001",
                "title": "测试新闻",
                "published_at": "2026-04-24T09:30:00+08:00",
                "text": "测试新闻正文。",
            },
        }
    )[0]

    nodes = await adapter.extract_node_drafts(item)
    edges = await adapter.extract_edge_drafts(item, nodes)

    assert strategy.calls == 1
    assert any(node.node_type == "institution" and node.canonical_name == "机构1" for node in nodes)
    assert any(edge.target_ref == "institution:机构1" for edge in edges)
