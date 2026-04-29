"""Tests for bootstrapping financial stock nodes from source tables."""

from __future__ import annotations

import pytest

from src.application.dto.knowledge_dto import KnowledgeBootstrapStocksCommand
from src.application.services import financial_stock_bootstrap as bootstrap
from src.application.services import knowledge_service as knowledge_service_module
from src.application.services.knowledge_service import KnowledgeService


def test_stock_from_common_business_payloads() -> None:
    assert bootstrap.stock_from_any({"code": "SZ300750", "name": "宁德时代"}) == {
        "code": "300750",
        "exchange": "SZ",
        "name": "宁德时代",
    }
    assert bootstrap.stock_from_any({"thsStockCode": "300750.SZ", "secName": "宁德时代"}) == {
        "code": "300750",
        "exchange": "SZ",
        "name": "宁德时代",
    }
    assert bootstrap.stock_from_any("300750") == {
        "code": "300750",
        "exchange": "SZ",
        "name": "300750",
    }


def test_build_stock_basics_records_from_sources_dedupes_and_prefers_names(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "_table_exists", lambda _target, _table: True)

    def fake_rows(_target, sql, _params=None):
        if "from ft_news" in sql:
            return [
                {
                    "id": 1,
                    "title": "宁德时代 300750 技术发布会",
                    "related_stocks": ["300750"],
                    "created_at": "2026-04-28T10:00:00+08:00",
                }
            ]
        if "from ft_sentiment" in sql:
            return [
                {
                    "id": 2,
                    "data_type": "xueqiu_hot_stocks",
                    "data": {"stocks": [{"code": "SZ300750", "name": "宁德时代"}]},
                    "created_at": "2026-04-28T11:00:00+08:00",
                }
            ]
        if "from ft_watchlist_data" in sql:
            return [
                {
                    "id": 3,
                    "data_type": "profit_contribution",
                    "data": [{"thsStockCode": "300750.SZ", "secName": "宁德时代"}],
                    "created_at": "2026-04-28T12:00:00+08:00",
                }
            ]
        return []

    monkeypatch.setattr(bootstrap, "_rows", fake_rows)

    assert bootstrap.build_stock_basics_records_from_sources(
        target="prod",
        codes=["300750"],
    ) == [
        {
            "source_type": "stock_basics",
            "observed_at": "2026-04-28T11:00:00+08:00",
            "payload": {
                "code": "300750",
                "exchange": "SZ",
                "name": "宁德时代",
                "status": "active",
            },
        }
    ]


@pytest.mark.asyncio
async def test_service_bootstraps_stocks_through_existing_compile_path(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "build_stock_basics_records_from_sources",
        lambda **_kwargs: [
            {
                "source_type": "stock_basics",
                "observed_at": "1970-01-01T00:00:00+00:00",
                "payload": {
                    "code": "300750",
                    "exchange": "SZ",
                    "name": "宁德时代",
                    "status": "active",
                },
            }
        ],
    )

    result = await KnowledgeService().bootstrap_financial_stock_entities(
        KnowledgeBootstrapStocksCommand(target="prod", codes=["300750"], dry_run=True)
    )

    assert result.dry_run is True
    assert result.nodes == 1
    assert result.failed_records == 0
