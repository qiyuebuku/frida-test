"""Tests for projecting business news into financial KG records."""

from __future__ import annotations

import pytest

from src.application.dto.knowledge_dto import (
    KnowledgeBootstrapStockNewsCommand,
    KnowledgeCompileResultDTO,
)
from src.application.services import financial_news_projection as projection
from src.application.services import knowledge_service as knowledge_service_module
from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter


def test_build_news_records_from_sources_maps_stock_mentions(monkeypatch) -> None:
    monkeypatch.setattr(projection, "_table_exists", lambda _target, _table: True)
    monkeypatch.setattr(projection, "_stock_names_by_code", lambda **_kwargs: {"300750": "宁德时代"})
    monkeypatch.setattr(
        projection,
        "_rows",
        lambda _target, _sql, _params=None: [
            {
                "id": 74342,
                "title": "宁德时代技术发布会简析",
                "content": "技术迭代驱动多维增长，补能生态加速布局。",
                "summary": "",
                "source": "eastmoney_report",
                "source_name": "东吴证券",
                "category": "company",
                "tags": ["快充"],
                "related_stocks": ["300750"],
                "published_at": "2026-04-23T00:00:00+08:00",
                "created_at": "2026-04-23T01:00:00+08:00",
            }
        ],
    )

    records = projection.build_news_records_from_sources(target="prod", codes=["300750"])

    assert records[0]["source_type"] == "news_articles"
    assert records[0]["source_id"] == "ft_news:74342"
    assert records[0]["payload"]["mentioned_entities"][0] == {
        "type": "stock",
        "exchange": "SZ",
        "code": "300750",
        "name": "宁德时代",
        "confidence": 0.7,
    }
    assert records[0]["payload"]["mentioned_entities"][1]["name"] == "快充"


@pytest.mark.asyncio
async def test_projected_news_record_compiles_without_text_extraction(monkeypatch) -> None:
    monkeypatch.setattr(projection, "_table_exists", lambda _target, _table: True)
    monkeypatch.setattr(projection, "_stock_names_by_code", lambda **_kwargs: {"300750": "宁德时代"})
    monkeypatch.setattr(
        projection,
        "_rows",
        lambda _target, _sql, _params=None: [
            {
                "id": 74342,
                "title": "宁德时代技术发布会简析",
                "content": "技术迭代驱动多维增长，补能生态加速布局。",
                "summary": "",
                "source": "eastmoney_report",
                "source_name": "东吴证券",
                "category": "company",
                "tags": [],
                "related_stocks": ["300750"],
                "published_at": "2026-04-23T00:00:00+08:00",
                "created_at": "2026-04-23T01:00:00+08:00",
            }
        ],
    )
    item = FinancialKGAdapter(enable_text_extraction=False).normalize(
        projection.build_news_records_from_sources(target="prod", codes=["300750"])[0]
    )[0]

    edges = await FinancialKGAdapter(enable_text_extraction=False).extract_edge_drafts(item, [])

    assert edges[0].relation_type == "mentions"
    assert edges[0].target_ref == "stock:SZ:300750"


@pytest.mark.asyncio
async def test_service_bootstraps_stock_news_through_compile_path(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "build_news_records_from_sources",
        lambda **_kwargs: [{"source_type": "news_articles", "payload": {"source_id": "ft_news:1"}}],
    )

    async def fake_compile(_self, command):
        assert command.adapter_name == "financial"
        assert command.concurrency == 1
        return KnowledgeCompileResultDTO(
            adapter_name="financial",
            run_id="kg_run:test",
            nodes=1,
            edges=1,
            evidence=1,
            failed_records=0,
            dry_run=command.dry_run,
        )

    monkeypatch.setattr(KnowledgeService, "compile_kg", fake_compile)

    result = await KnowledgeService().bootstrap_financial_stock_news(
        KnowledgeBootstrapStockNewsCommand(target="prod", codes=["300750"], dry_run=True)
    )

    assert result.nodes == 1
    assert result.dry_run is True
