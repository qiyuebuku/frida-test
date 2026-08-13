from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.application.services.market_observability_service import (
    CHART_SERIES_SPECS,
    MarketObservabilityService,
    _cn_intraday_display_status,
    _merge_breadth_with_ths_indices,
)
from src.interfaces.api.routes import market_observability


NOW = datetime(2026, 7, 31, 7, 30, tzinfo=timezone.utc)


def test_ths_index_push_overlays_fresher_quote_without_losing_breadth() -> None:
    breadth = _snapshot(
        "ths_cn_market_breadth", "market", "cn:a_share:ths_breadth",
        {
            "up_count": 2353,
            "down_count": 3046,
            "flat_count": 136,
        },
    )
    pushed = _snapshot(
        "ths_cn_index_quote", "index", "cn:index:000001",
        {
            "code": "000001", "name": "上证指数", "close": 3919.51,
            "change_percent": 0.49, "source_time": NOW.isoformat(),
        },
    )

    summary = _snapshot(
        "ths_cn_market_summary", "market", "cn:a_share:ths_all_a",
        {"turnover": 1_688_700_000_000.0},
    )
    merged = _merge_breadth_with_ths_indices(
        breadth, [pushed], [summary], [breadth]
    )

    assert merged is not None
    assert merged["data"]["up_count"] == 2353
    assert merged["data"]["down_count"] == 3046
    assert merged["data"]["flat_count"] == 136
    assert merged["data"]["breadth_provider"] == "ths_native_stream"
    assert merged["data"]["indices"][0]["close"] == 3919.51
    assert merged["data"]["index_quote_provider"] == "ths_native_stream"
    assert merged["data"]["turnover"] == 1_688_700_000_000.0
    assert merged["data"]["turnover_provider"] == "ths_native_stream"
    assert merged["provider"] == "ths_native_stream"


def test_closing_quotes_merge_with_late_repeated_breadth_push() -> None:
    trade_date = date(2026, 8, 8)
    breadth = _snapshot(
        "ths_cn_market_breadth", "market", "cn:a_share:ths_breadth",
        {"up_count": 2856, "down_count": 2536, "flat_count": 143},
    )
    breadth.update({
        "trade_date": trade_date,
        "observed_at": datetime(2026, 8, 8, 8, 54, tzinfo=timezone.utc),
    })
    quote = _snapshot(
        "ths_cn_index_quote", "index", "cn:index:000001",
        {"code": "000001", "name": "上证指数", "close": 3940.04},
    )
    quote.update({
        "trade_date": trade_date,
        "observed_at": datetime(2026, 8, 8, 7, 0, tzinfo=timezone.utc),
    })
    summary = _snapshot(
        "ths_cn_market_summary", "market", "cn:a_share:ths_all_a",
        {"turnover": 2_683_400_000_000.0},
    )
    summary.update({
        "trade_date": trade_date,
        "observed_at": datetime(2026, 8, 8, 7, 0, tzinfo=timezone.utc),
    })

    merged = _merge_breadth_with_ths_indices(breadth, [quote], [summary])

    assert merged is not None
    assert merged["data"]["indices"][0]["code"] == "000001"
    assert merged["data"]["turnover"] == 2_683_400_000_000.0


class _SnapshotRepository:
    def summarize_since(self, _since):
        return {
            "total": 120,
            "subject_count": 8,
            "latest_bucket_at": NOW,
            "by_data_type": [],
            "by_freshness": {"realtime": 100, "delayed": 20},
        }

    def list_latest(self, **_kwargs):
        return [
            _snapshot(
                "ths_cn_market_breadth",
                "market",
                "cn:a_share:ths_breadth",
                {
                    "up_count": 2000,
                    "down_count": 3000,
                    "flat_count": 100,
                    "turnover": 2_000_000_000_000,
                    "indices": [],
                },
            ),
            _snapshot(
                "sector_quote",
                "sector",
                "ths:industry:a",
                {"sector_name": "半导体", "change_pct": 3.2},
            ),
            _snapshot(
                "sector_quote",
                "sector",
                "ths:industry:b",
                {"sector_name": "银行", "change_pct": -1.1},
            ),
            _snapshot(
                "sector_flow",
                "sector",
                "sina:industry:a",
                {"sector_name": "半导体", "main_net_inflow": 10_000_000},
            ),
            _snapshot(
                "quote",
                "instrument",
                "sh600036",
                {"price": 40},
            ),
        ]

    def query_history(self, **_kwargs):
        return [
            _snapshot(
                "ths_cn_market_breadth",
                "market",
                "cn:a_share:ths_breadth",
                {
                    "up_count": 1900,
                    "down_count": 3100,
                    "flat_count": 100,
                    "turnover": 2_000_000_000_000,
                },
            )
        ]

    def query_previous_trade_date_history(self, **_kwargs):
        previous = _snapshot(
            "ths_cn_market_breadth",
            "market",
            "cn:a_share:ths_breadth",
            {
                "up_count": 2100,
                "down_count": 2900,
                "flat_count": 100,
                "turnover": 1_600_000_000_000,
            },
        )
        previous.update(
            {
                "trade_date": date(2026, 7, 30),
                "observed_at": NOW.replace(day=30),
                "fetched_at": NOW.replace(day=30),
                "bucket_at": NOW.replace(day=30),
            }
        )
        return [previous]


class _RunRepository:
    def summarize_since(self, _since):
        return {
            "total": 10,
            "by_status": {"success": 9, "failed": 1},
            "failed": 1,
            "partial_success": 0,
            "running": 0,
        }

    def list_latest(self, **_kwargs):
        return [
            {
                "task_name": "collect_market_breadth_snapshot",
                "source_name": "market_breadth",
                "status": "success",
                "started_at": NOW,
                "finished_at": NOW,
            },
            {
                "task_name": "collect_watchlist_instruments",
                "source_name": "sh600036",
                "status": "success",
                "started_at": NOW,
                "finished_at": NOW,
            },
            {
                "task_name": "collect_watchlist_instruments",
                "source_name": "960015",
                "status": "partial_success",
                "started_at": NOW,
                "finished_at": NOW,
            },
        ]


class _EtfRepository:
    def latest_summary(self):
        return {
            "trade_date": date(2026, 7, 30),
            "fund_count": 1200,
            "exchange_count": 2,
            "fetched_at": NOW,
        }


class _WatchlistService:
    def list_all(self):
        return [
            SimpleNamespace(
                code="sh600036",
                name="招商银行",
                type="stock",
                enabled=True,
                config={
                    "priority": "critical",
                    "realtime_interval_seconds": 30,
                },
                last_success_at=NOW,
                last_error="",
            )
        ]


class _CollectionRepository:
    def dashboard_context(self):
        return {
            "market_cache": {
                "market_overview": {
                    "data": {
                        "data": {
                            "capital_flow": {"totalMainFlow": 120.5},
                            "cap_comparison": {"stronger": "小盘股更强"},
                        }
                    },
                    "created_at": NOW,
                },
                "market_environment": {
                    "data": {
                        "data": {
                            "margin": {
                                "latest": {"rzye": 25000},
                                "trend": "融资余额平稳",
                            }
                        }
                    },
                    "created_at": NOW,
                },
            },
            "sentiment": {
                "limit_pool": {
                    "data": {
                        "trade_status": {"name": "交易中"},
                        "limit_up_count": {"today": {"num": 80}},
                        "limit_down_count": {"today": {"num": 5}},
                        "info": [{"code": "SH600036", "name": "招商银行"}],
                    },
                    "created_at": NOW,
                },
                "xueqiu_hot_stocks": {
                    "data": [{"code": "SH600036", "name": "招商银行"}],
                    "created_at": NOW,
                },
            },
        }

    def inventory(self):
        return [
            {
                "domain": "market_snapshot",
                "title": "市场行情快照",
                "table": "ft_market_snapshots",
                "available": True,
                "total": 120,
                "latest_at": NOW,
                "groups": [{"name": "index_quote", "count": 20}],
            },
            {
                "domain": "sentiment_signal",
                "title": "情绪派生信号",
                "table": "ft_sentiment_signal",
                "available": False,
                "total": 0,
                "latest_at": None,
                "groups": [],
                "error": "relation does not exist",
            },
        ]

    def list_records(self, **kwargs):
        return {
            "domain": kwargs["domain_key"],
            "title": "市场行情快照",
            "available": True,
            "total": 1,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
            "items": [
                {
                    "subject_id": "cn:a_share",
                    "data_type": "market_breadth",
                    "bucket_at": NOW,
                }
            ],
        }


def _snapshot(data_type, subject_type, subject_id, data):
    return {
        "id": 1,
        "data_type": data_type,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "market": "cn",
        "provider": subject_id.split(":", 1)[0],
        "trade_date": date(2026, 7, 31),
        "observed_at": NOW,
        "fetched_at": NOW,
        "bucket_at": NOW,
        "freshness_status": "realtime",
        "source_latency_seconds": 0,
        "data": data,
    }


def _service() -> MarketObservabilityService:
    return MarketObservabilityService(
        snapshot_repository=_SnapshotRepository(),
        run_repository=_RunRepository(),
        etf_share_repository=_EtfRepository(),
        watchlist_service=_WatchlistService(),
        collection_repository=_CollectionRepository(),
    )


def test_dashboard_composes_market_and_collection_views() -> None:
    result = _service().dashboard(hours=24)

    assert result["summary"]["snapshots"]["total"] == 120
    assert result["summary"]["runs"]["failed"] == 1
    assert result["summary"]["watchlist_count"] == 1
    assert result["summary"]["etf_daily_shares"]["exchange_count"] == 2
    assert result["market_breadth"]["latest"]["data"]["up_count"] == 2000
    assert result["market_context"]["capital_flow"]["totalMainFlow"] == 120.5
    assert result["market_context"]["limit_up"]["today"]["num"] == 80
    assert result["market_context"]["hot_stocks"][0]["name"] == "招商银行"
    assert result["market_breadth"]["previous_same_time"] == {
        "trade_date": date(2026, 7, 30),
        "bucket_at": NOW.replace(day=30),
        "turnover": 1_600_000_000_000,
        "turnover_change": 400_000_000_000,
        "turnover_change_percent": 25.0,
        "matched_time_difference_seconds": 0.0,
        "source": "local_snapshot",
    }
    assert result["sector_quotes"][0]["sector_name"] == "半导体"
    assert result["sector_quotes"][-1]["sector_name"] == "银行"
    assert result["sector_flows"][0]["main_net_inflow"] == 10_000_000
    assert result["watchlist"][0]["latest"][0]["data"]["price"] == 40
    assert [
        item["source_name"] for item in result["collection_sources"]
    ] == ["market_breadth", "sh600036"]


def test_index_sentiment_charts_match_ths_120_session_window() -> None:
    assert CHART_SERIES_SPECS["sentiment_sh50"][2:] == (120, False)
    assert CHART_SERIES_SPECS["sentiment_growth"][2:] == (120, False)


def test_global_futures_charts_keep_a_complete_cross_midnight_session() -> None:
    assert CHART_SERIES_SPECS["futures_a50"][2:] == (1600, True)
    assert CHART_SERIES_SPECS["futures_dow"][2:] == (1600, True)
    assert CHART_SERIES_SPECS["valuation_sh"] == (
        "market_valuation_threshold",
        "cn:market:sh",
        300,
        False,
    )
    assert CHART_SERIES_SPECS["valuation_sz"] == (
        "market_valuation_threshold",
        "cn:market:sz",
        300,
        False,
    )


def test_dashboard_prefers_provider_intraday_turnover_comparison() -> None:
    repository = _SnapshotRepository()
    rows = repository.list_latest()
    rows[0]["data"]["previous_same_time_turnover"] = {
        "previous_trade_date": "2026-07-30",
        "comparison_time": "14:12",
        "previous_turnover": 1_500_000_000_000,
    }
    repository.list_latest = lambda **_kwargs: rows
    service = MarketObservabilityService(
        snapshot_repository=repository,
        run_repository=_RunRepository(),
        etf_share_repository=_EtfRepository(),
        watchlist_service=_WatchlistService(),
    )

    result = service.dashboard(hours=24)

    assert result["market_breadth"]["previous_same_time"] == {
        "trade_date": "2026-07-30",
        "comparison_time": "14:12",
        "turnover": 1_500_000_000_000,
        "turnover_change": 500_000_000_000,
        "turnover_change_percent": pytest.approx(100 / 3),
        "source": "provider_intraday",
    }


def test_cn_intraday_display_status_marks_final_snapshot_closed() -> None:
    snapshot = _snapshot(
        "market_breadth",
        "market",
        "cn:a_share",
        {},
    )
    snapshot["observed_at"] = datetime(
        2026,
        7,
        31,
        7,
        5,
        tzinfo=timezone.utc,
    )

    assert _cn_intraday_display_status(
        snapshot,
        now=datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc),
    ) == {"status": "closed", "label": "已收盘"}


def test_cn_intraday_display_status_keeps_stale_intraday_visible() -> None:
    snapshot = _snapshot(
        "market_breadth",
        "market",
        "cn:a_share",
        {},
    )
    snapshot["observed_at"] = datetime(
        2026,
        7,
        31,
        6,
        45,
        tzinfo=timezone.utc,
    )

    assert (
        _cn_intraday_display_status(
            snapshot,
            now=datetime(2026, 7, 31, 6, 55, tzinfo=timezone.utc),
        )
        is None
    )


def test_dashboard_api_and_page_redirect(monkeypatch) -> None:
    monkeypatch.setattr(
        market_observability,
        "MarketObservabilityService",
        lambda: _service(),
    )
    app = FastAPI()
    app.include_router(market_observability.router)
    client = TestClient(app)

    response = client.get("/api/market-observability/dashboard?hours=6")
    assert response.status_code == 200
    assert response.json()["window_hours"] == 6

    redirect = client.get("/market-dashboard", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"].endswith(
        "/static/market_observability_dashboard.html"
    )


def test_inventory_reports_all_domains_and_unavailable_tables() -> None:
    result = _service().inventory()

    assert result["domain_count"] == 2
    assert result["available_count"] == 1
    assert result["total_records"] == 120
    assert result["domains"][1]["error"] == "relation does not exist"


def test_collection_records_forwards_pagination_and_filters() -> None:
    result = _service().collection_records(
        domain="market_snapshot",
        group="index_quote",
        query="cn:a_share",
        limit=25,
        offset=50,
    )

    assert result["total"] == 1
    assert result["limit"] == 25
    assert result["offset"] == 50
    assert result["items"][0]["data_type"] == "market_breadth"


def test_inventory_and_records_api(monkeypatch) -> None:
    monkeypatch.setattr(
        market_observability,
        "MarketObservabilityService",
        lambda: _service(),
    )
    app = FastAPI()
    app.include_router(market_observability.router)
    client = TestClient(app)

    inventory = client.get("/api/market-observability/inventory")
    assert inventory.status_code == 200
    assert inventory.json()["available_count"] == 1

    records = client.get(
        "/api/market-observability/records",
        params={
            "domain": "market_snapshot",
            "group": "index_quote",
            "query": "cn:a_share",
            "limit": 25,
            "offset": 50,
        },
    )
    assert records.status_code == 200
    assert records.json()["items"][0]["subject_id"] == "cn:a_share"
