from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.application.services.market_observation_service import (
    CollectionSkipped,
    MarketObservationService,
    _bar_snapshots,
    _context_bootstrap_subjects,
    _etf_estimated_flow_snapshots,
    _floor_bucket,
    _government_yield_snapshots,
    _gold_ai_news_records,
    _hydrate_featured_stocks,
    _interest_rate_snapshots,
    _native_chart_snapshots,
    _require_cn_series_observation_or_catchup,
    _require_cn_stock_change_window,
    _should_bootstrap_ths_events,
    _snapshot_from_response,
    _ths_event_snapshots,
    _ths_event_sources,
    _unrecoverable_gap,
    _valuation_threshold_snapshots,
)
from src.application.services.watchlist_service import (
    DEFAULT_CONFIG,
    PRIORITY_INTERVAL_SECONDS,
    WatchlistItem,
    _is_realtime_due,
)
from src.domain.collection.services.fund_flow import (
    _expand_timeseries,
    _watchlist_collection_plan,
    normalize_northbound,
)
from src.domain.collection.watchlist_snapshot_projection import (
    project_watchlist_market_snapshots,
)


@pytest.mark.asyncio
async def test_us_sector_collection_persists_every_period_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    period_payloads = {
        f"{sector_type}_{period}": {
            "status_code": 0,
            "data": {"sector_list": [{"sector_code": subject_id}]},
        }
        for sector_type in ("industry", "concept")
        for period in ("five_day", "one_month", "three_month")
        for subject_id in [f"{sector_type}_{period}"]
    }

    class FakeTHS:
        async def get_native_us_sector_snapshot(self) -> dict:
            return {
                "status": "ok",
                "provider": "ths_app",
                "market": "us",
                "data": {"sectors": period_payloads},
                "provider_metadata": {"complete": True},
            }

    from src.infrastructure import clients

    monkeypatch.setattr(clients, "ths", FakeTHS())
    service = MarketObservationService.__new__(MarketObservationService)

    batch = await service._collect_ths_us_module(
        "sectors",
        "get_native_us_sector_snapshot",
        180,
    )

    period_snapshots = [
        row for row in batch.snapshots
        if row["data_type"] == "ths_us_sector_period"
    ]
    assert {row["subject_id"] for row in period_snapshots} == set(period_payloads)
    assert {row["subject_type"] for row in period_snapshots} == {
        "industry",
        "concept",
    }


@pytest.mark.asyncio
async def test_us_etf_collection_materializes_complete_push_tables_without_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 10, 2, 30, tzinfo=timezone.utc)

    class FakeSnapshots:
        def query_latest(self, *, subject_ids, data_types, cutoff_at=None):
            del data_types, cutoff_at
            rows = []
            if "etf_config_stream" in subject_ids:
                rows.append({
                    "subject_id": "etf_config_stream",
                    "fetched_at": now,
                    "data": {
                        "native_table": {
                            "items": [
                                {"BlockID": "D2F6", "Name": "宽基"},
                                {"BlockID": "D319SJ", "Name": "行业"},
                            ]
                        }
                    },
                })
            for block_id in ("D2F6", "D319SJ"):
                if f"etf_sector_{block_id}_stream" in subject_ids:
                    rows.append({
                        "subject_id": f"etf_sector_{block_id}_stream",
                        "fetched_at": now,
                        "data": {
                            "native_table": {
                                "dataDict": {
                                    "4": [f"ETF-{block_id}"],
                                    "34338": ["185"],
                                    "55": [block_id],
                                }
                            }
                        },
                    })
            return rows

    class FakeTHS:
        async def get_native_us_etf_sectors_snapshot(self, block_ids=None):
            raise AssertionError(f"unexpected Native repair: {block_ids}")

    from src.infrastructure import clients

    monkeypatch.setattr(clients, "ths", FakeTHS())
    service = MarketObservationService.__new__(MarketObservationService)
    service._snapshots = FakeSnapshots()

    batch = await service._collect_ths_us_etf_sectors()

    assert batch.fetched_count == 2
    assert batch.details["complete"] is True
    assert batch.details["failed_modules"] == []
    assert batch.details["push_categories"] == ["D2F6", "D319SJ"]
    assert {row["data_type"] for row in batch.snapshots} == {
        "ths_us_market_module",
        "ths_us_etf_catalog",
    }
    assert batch.snapshots[0]["data"]["etf_count"] == 2


@pytest.mark.asyncio
async def test_us_etf_collection_repairs_only_missing_push_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 10, 2, 30, tzinfo=timezone.utc)

    class FakeSnapshots:
        def query_latest(self, *, subject_ids, data_types, cutoff_at=None):
            del data_types, cutoff_at
            if "etf_config_stream" in subject_ids:
                return [{
                    "subject_id": "etf_config_stream",
                    "fetched_at": now,
                    "data": {
                        "native_table": {
                            "items": [
                                {"BlockID": "D2F6", "Name": "宽基"},
                                {"BlockID": "D319SJ", "Name": "行业"},
                            ]
                        }
                    },
                }]
            return [{
                "subject_id": "etf_sector_D2F6_stream",
                "fetched_at": now,
                "data": {
                    "native_table": {
                        "dataDict": {
                            "4": ["SPY"],
                            "34338": ["185"],
                            "55": ["SPDR"],
                        }
                    }
                },
            }]

    class FakeTHS:
        requested: set[str] | None = None

        async def get_native_us_etf_sectors_snapshot(self, block_ids=None):
            self.requested = block_ids
            return {
                "status_code": 0,
                "status": "ok",
                "data": {
                    "etf_sector_details": {
                        "D319SJ": {
                            "head": {},
                            "data": {
                                "dataDict": {
                                    "4": ["XLK"],
                                    "34338": ["185"],
                                    "55": ["Technology Select"],
                                }
                            },
                        }
                    },
                    "etf_quotes": [],
                },
                "provider_metadata": {
                    "complete": True,
                    "failed_modules": [],
                },
            }

    from src.infrastructure import clients

    fake_ths = FakeTHS()
    monkeypatch.setattr(clients, "ths", fake_ths)
    service = MarketObservationService.__new__(MarketObservationService)
    service._snapshots = FakeSnapshots()

    batch = await service._collect_ths_us_etf_sectors()

    assert fake_ths.requested == {"D319SJ"}
    assert batch.fetched_count == 2
    assert batch.details["repaired_categories"] == ["D319SJ"]
    assert batch.details["complete"] is True


def test_floor_bucket_is_stable() -> None:
    value = datetime(2026, 7, 31, 1, 2, 59, tzinfo=timezone.utc)

    assert _floor_bucket(value, 30) == datetime(
        2026,
        7,
        31,
        1,
        2,
        30,
        tzinfo=timezone.utc,
    )


def test_daily_snapshot_bucket_uses_market_trade_date_midnight() -> None:
    snapshot = _snapshot_from_response(
        response={
            "provider": "ths_native",
            "market": "cn",
            "timezone": "Asia/Shanghai",
            "trade_date": "2026-08-11",
            "fetched_at": "2026-08-10T16:30:00+00:00",
            "data": {"provider_sector_code": "881169"},
        },
        data_type="ths_sector_constituents",
        subject_type="sector",
        subject_id="ths_native:industry:881169",
        bucket_seconds=86400,
    )

    assert snapshot["trade_date"] == date(2026, 8, 11)
    assert snapshot["bucket_at"] == datetime(
        2026, 8, 10, 16, 0, tzinfo=timezone.utc
    )


def test_cn_premarket_fetch_time_snapshot_belongs_to_previous_session() -> None:
    snapshot = _snapshot_from_response(
        response={
            "provider": "ths_native",
            "market": "cn",
            "timezone": "Asia/Shanghai",
            "fetched_at": "2026-08-11T16:30:00+00:00",
            "data": {"provider_sector_code": "881169", "change_pct": -5.03},
        },
        data_type="ths_sector_hot",
        subject_type="sector",
        subject_id="ths_native:industry:881169",
        bucket_seconds=300,
    )

    assert snapshot["trade_date"] == date(2026, 8, 11)


def test_gold_ai_events_are_normalized_for_ft_news() -> None:
    rows = _gold_ai_news_records(
        {
            "status_code": 0,
            "data": [
                {
                    "news_ai_summary": "美联储隔夜逆回购使用规模下降。",
                    "news_create_time": "2026-08-05 23:58:00",
                    "news_source": "同花顺期货通",
                    "news_pc_url": "https://news.10jqka.com.cn/example.shtml",
                }
            ],
        }
    )

    assert len(rows) == 1
    assert rows[0]["source"] == "ths_gold_ai"
    assert rows[0]["title"] == "美联储隔夜逆回购使用规模下降。"
    assert rows[0]["published_at"].isoformat() == "2026-08-05T23:58:00+08:00"
    assert rows[0]["tags"] == ["黄金", "贵金属", "同花顺AI要点"]
    assert len(rows[0]["fingerprint"]) == 64
    assert len(rows[0]["dedup_key"]) == 64


def test_featured_stocks_are_hydrated_from_candidate_pool_by_code() -> None:
    result = _hydrate_featured_stocks(
        [
            {
                "rank": 1,
                "code": "000010",
                "name": None,
                "latest": None,
                "speed": None,
                "indicators": {"48": ""},
            }
        ],
        [
            {
                "rank": 3,
                "code": "000010",
                "name": "*ST美丽",
                "latest": 1.58,
                "speed": -0.63,
                "indicators": {"10": "1.58", "48": "-0.63%", "55": "*ST美丽"},
            }
        ],
    )

    assert result == [
        {
            "rank": 1,
            "code": "000010",
            "name": "*ST美丽",
            "latest": 1.58,
            "speed": -0.63,
            "indicators": {"10": "1.58", "48": "-0.63%", "55": "*ST美丽"},
        }
    ]


def test_stock_change_force_boundary_allows_post_market_validation() -> None:
    session = {
        "status": "ok",
        "data": {
            "is_trading_day": True,
            "market_session": "post_market",
        },
    }

    _require_cn_stock_change_window(session, force_boundary=True)


def test_stock_change_force_boundary_allows_closed_day_bootstrap() -> None:
    session = {
        "status": "ok",
        "data": {
            "is_trading_day": False,
            "market_session": "closed",
        },
    }

    _require_cn_stock_change_window(session, force_boundary=True)


def test_intraday_series_restarts_after_close_to_repair_missing_tail() -> None:
    session = {
        "status": "ok",
        "data": {
            "date": "2026-08-04",
            "is_trading_day": True,
            "market_session": "post_market",
            "close_at": "2026-08-04T15:00:00+08:00",
        },
    }

    catchup = _require_cn_series_observation_or_catchup(
        session,
        latest_bucket_at=datetime(
            2026,
            8,
            4,
            6,
            42,
            tzinfo=timezone.utc,
        ),
        force_boundary=False,
    )

    assert catchup is True


def test_intraday_series_skips_after_close_when_watermark_reached() -> None:
    session = {
        "status": "ok",
        "data": {
            "date": "2026-08-04",
            "is_trading_day": True,
            "market_session": "post_market",
            "close_at": "2026-08-04T15:00:00+08:00",
        },
    }

    with pytest.raises(CollectionSkipped, match="cn_market_not_open:post_market"):
        _require_cn_series_observation_or_catchup(
            session,
            latest_bucket_at=datetime(
                2026,
                8,
                4,
                7,
                0,
                tzinfo=timezone.utc,
            ),
            force_boundary=False,
        )


def test_ths_context_bootstraps_missing_history_on_closed_day_once() -> None:
    session = {
        "status": "ok",
        "data": {
            "is_trading_day": False,
            "market_session": "closed",
        },
    }

    missing = _context_bootstrap_subjects(
        session,
        latest_buckets={
            "cn:a_share:market_capital": None,
            "cn:a_share:ths_temperature": datetime(
                2026,
                7,
                31,
                7,
                tzinfo=timezone.utc,
            ),
        },
        force_boundary=False,
    )

    assert missing == ["cn:a_share:market_capital"]


def test_ths_context_skips_closed_day_after_bootstrap() -> None:
    session = {
        "status": "ok",
        "data": {
            "is_trading_day": False,
            "market_session": "closed",
        },
    }
    latest = datetime(2026, 7, 31, 7, tzinfo=timezone.utc)

    with pytest.raises(CollectionSkipped, match="cn_market_closed"):
        _context_bootstrap_subjects(
            session,
            latest_buckets={
                "cn:a_share:market_capital": latest,
                "cn:a_share:ths_temperature": latest,
            },
            force_boundary=False,
        )


@pytest.mark.parametrize(
    ("now_cn", "expected"),
    [
        (time(9, 20), ["call_auction"]),
        (time(9, 27), ["call_auction", "market_anomaly"]),
        (time(10, 0), ["market_anomaly"]),
    ],
)
def test_ths_event_sources_follow_market_phase(
    now_cn: time,
    expected: list[str],
) -> None:
    assert [item[0] for item in _ths_event_sources(now_cn)] == expected


def test_ths_events_do_not_rewrite_existing_closed_day_snapshot() -> None:
    assert not _should_bootstrap_ths_events(
        force_boundary=False,
        has_event_snapshot=True,
    )
    assert _should_bootstrap_ths_events(
        force_boundary=False,
        has_event_snapshot=False,
    )


def test_ths_short_spirit_events_use_stable_idempotency_keys() -> None:
    response = {
        "provider": "ths_native",
        "fetched_at": "2026-07-31T07:01:00+00:00",
        "data": {
            "stock_events": [
                {
                    "dataid": "1074269404",
                    "marketcode": "17",
                    "stockcode": "600000",
                    "stockname": "浦发银行",
                    "time": "1785481200",
                    "value": "1.76%",
                }
            ],
            "sector_events": [
                {
                    "dataid": "2",
                    "marketcode": "48",
                    "stockcode": "885700",
                    "stockname": "半导体",
                    "time": "1785481199",
                    "value": "0.82%",
                }
            ],
            "large_order_events": [
                {
                    "dataid": "133990",
                    "marketcode": "17",
                    "stockcode": "600001",
                    "stockname": "邯郸钢铁",
                    "time": "1785481198",
                    "value": "600手",
                }
            ],
        },
    }

    first = _ths_event_snapshots(response)
    second = _ths_event_snapshots(response)

    assert [item["data_type"] for item in first] == [
        "ths_stock_anomaly",
        "ths_sector_anomaly",
        "ths_large_order",
    ]
    assert [item["subject_id"] for item in first] == [
        item["subject_id"] for item in second
    ]
    assert first[0]["data"]["event_type"] == "急速拉升"
    assert first[2]["data"]["event_type"] == "挂单拉升"
    assert all(item["subject_type"] == "event" for item in first)


@pytest.mark.asyncio
async def test_stock_rankings_collect_fifty_rows_for_every_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure import clients

    requested: list[tuple[str, int]] = []

    async def market_session(_market: str) -> dict:
        return {
            "status": "ok",
            "data": {
                "is_trading_day": True,
                "market_session": "open",
            },
        }

    async def stock_ranking(mode: str, count: int) -> dict:
        requested.append((mode, count))
        return {
            "status": "ok",
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": "2026-08-03T01:30:00+00:00",
            "observed_at": "2026-08-03T01:30:00+00:00",
            "provider_metadata": {
                "channel": "android_native_unified_request",
            },
            "data": {
                "sort": mode,
                "count": count,
                "stocks": [
                    {
                        "code": f"{index:06d}",
                        "name": f"stock-{index}",
                        "change_rate": index,
                        "speed": index,
                        "turnover": f"{index}亿",
                        "large_order_ratio": index,
                        "volume_ratio": index,
                        "turnover_rate": index,
                        "main_net_inflow": f"{index}万",
                        "amplitude": index,
                    }
                    for index in range(count)
                ],
            },
        }

    monkeypatch.setattr(clients.market_calendar, "get_market_session", market_session)
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(get_native_stock_ranking=stock_ranking),
    )

    batch = await MarketObservationService()._collect_stock_rankings()

    expected_modes = (
        "rise",
        "fall",
        "quick",
        "turnover",
        "large_order",
        "volume_ratio",
        "turnover_rate",
        "main_net_inflow",
        "amplitude",
    )
    assert requested == [(mode, 50) for mode in expected_modes]
    assert len(batch.snapshots) == len(expected_modes)
    assert len(batch.projections) == len(expected_modes)
    assert all(len(item["data"]["stocks"]) == 50 for item in batch.snapshots)
    assert [item[0] for item in batch.projections] == [
        f"stock_ranking:{mode}" for mode in expected_modes
    ]


@pytest.mark.asyncio
async def test_stock_rankings_retry_only_failed_native_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure import clients

    attempts: dict[str, int] = {}

    async def market_session(_market: str) -> dict:
        return {
            "status": "ok",
            "data": {"is_trading_day": True, "market_session": "open"},
        }

    async def stock_ranking(mode: str, _count: int) -> dict:
        attempts[mode] = attempts.get(mode, 0) + 1
        if mode == "fall" and attempts[mode] == 1:
            return {"status": "error", "provider": "ths_native", "data": {}}
        metric_key = {
            "rise": "change_rate",
            "fall": "change_rate",
            "quick": "speed",
            "turnover": "turnover",
            "large_order": "large_order_ratio",
            "volume_ratio": "volume_ratio",
            "turnover_rate": "turnover_rate",
            "main_net_inflow": "main_net_inflow",
            "amplitude": "amplitude",
        }[mode]
        return {
            "status": "ok",
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": "2026-08-03T01:30:00+00:00",
            "provider_metadata": {
                "channel": "android_native_unified_request"
            },
            "data": {
                "sort": mode,
                "stocks": [
                    {
                        "code": "000001",
                        "name": "测试股票",
                        metric_key: 1,
                    }
                ],
            },
        }

    monkeypatch.setattr(clients.market_calendar, "get_market_session", market_session)
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(get_native_stock_ranking=stock_ranking),
    )

    batch = await MarketObservationService()._collect_stock_rankings()

    assert len(batch.snapshots) == 9
    assert attempts["fall"] == 2
    assert all(count == 1 for mode, count in attempts.items() if mode != "fall")
    assert batch.details == {
        "empty_modes": [],
        "recovered_modes": ["fall"],
        "recovery_fetch": False,
    }


@pytest.mark.asyncio
async def test_stock_dynamic_groups_persist_every_group_including_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.infrastructure import clients

    async def market_session(_market: str) -> dict:
        raise AssertionError("dynamic groups must not be gated by CN calendar")

    requested_counts: list[tuple[int, bool]] = []

    async def dynamic_groups(count: int, *, homepage_layout: bool = False) -> dict:
        requested_counts.append((count, homepage_layout))
        stocks = (
            [{"code": "688825", "name": "精选股票"}]
            if count == 4
            else [
                {"code": "688825", "name": "候选股票一"},
                {"code": "688826", "name": "候选股票二"},
            ]
        )
        return {
            "status": "ok",
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": "2026-08-03T01:30:00+00:00",
            "observed_at": "2026-08-03T01:30:00+00:00",
            "provider_metadata": {"channel": "android_native_hurricane"},
            "data": {
                "groups": [
                    {
                        "data_code": "rebanggegu1h",
                        "title": "同花顺热榜",
                        "stocks": stocks,
                    },
                    {
                        "data_code": "jetondiweijizhong",
                        "title": "筹码低位集中",
                        "stocks": [],
                    },
                ]
            },
        }

    monkeypatch.setattr(clients.market_calendar, "get_market_session", market_session)
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(get_native_stock_dynamic_groups=dynamic_groups),
    )

    class Snapshots:
        def list_latest(self, **_kwargs) -> list[dict]:
            return []

    batch = await MarketObservationService(
        snapshot_repository=Snapshots()
    )._collect_stock_dynamic_groups()

    assert requested_counts == [(4, True), (100, False)]
    assert [item["subject_id"] for item in batch.snapshots] == [
        "rebanggegu1h",
        "jetondiweijizhong",
    ]
    assert [item[0] for item in batch.projections] == [
        "stock_dynamic_group:rebanggegu1h",
        "stock_dynamic_group:jetondiweijizhong",
    ]
    assert batch.details == {
        "empty_groups": ["jetondiweijizhong"],
        "candidate_source": "upstream",
    }
    assert batch.snapshots[0]["data"]["stocks"] == [
        {"code": "688825", "name": "精选股票"}
    ]
    assert len(batch.snapshots[0]["data"]["candidate_stocks"]) == 2
    assert batch.snapshots[0]["data"]["candidate_pool_complete"] is True


@pytest.mark.asyncio
async def test_ths_event_task_does_not_repeat_market_request_when_stream_active(
    monkeypatch,
) -> None:
    from src.infrastructure import clients

    calls = {"auction": 0, "market": 0}

    async def market_session(_market: str) -> dict:
        return {
            "status": "ok",
            "data": {
                "date": "2026-08-04",
                "is_trading_day": True,
                "market_session": "open",
            },
        }

    async def call_auction() -> dict:
        calls["auction"] += 1
        return {
            "status": "ok",
            "provider": "ths_native",
            "market": "cn",
            "data": {"count": 0},
        }

    async def market_anomalies() -> dict:
        calls["market"] += 1
        raise AssertionError("persistent stream must own market anomalies")

    class Snapshots:
        def query_latest(self, **_kwargs) -> list[dict]:
            return [{"bucket_at": datetime.now(timezone.utc)}]

    monkeypatch.setattr(
        "src.application.services.market_observation_service."
        "_ths_native_event_stream_is_active",
        lambda: True,
    )
    monkeypatch.setattr(clients.market_calendar, "get_market_session", market_session)
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(
            get_native_call_auction=call_auction,
            get_native_market_anomalies=market_anomalies,
        ),
    )

    batch = await MarketObservationService(
        snapshot_repository=Snapshots()
    )._collect_ths_market_events(force_boundary=True)

    assert calls == {"auction": 1, "market": 0}
    assert [row["data_type"] for row in batch.snapshots] == ["call_auction"]


def test_snapshot_without_provider_time_uses_fetch_time_status() -> None:
    row = _snapshot_from_response(
        response={
            "status": "ok",
            "provider": "sina",
            "market": "cn",
            "fetched_at": "2026-07-31T08:00:00+00:00",
            "observed_at": "2026-07-31T08:00:00+00:00",
            "data": {"count": 1},
        },
        data_type="sector_quote",
        subject_type="sector",
        subject_id="sina:industry:test",
        bucket_seconds=60,
    )

    assert row["observed_at"] is None
    assert row["fetched_at"] == datetime(
        2026,
        7,
        31,
        8,
        tzinfo=timezone.utc,
    )
    assert row["freshness_status"] == "fetch_time"


def test_etf_estimated_flow_expands_minutes_and_updates_latest_point() -> None:
    response = {
        "status": "ok",
        "provider": "ths",
        "market": "cn",
        "fetched_at": "2026-07-31T07:01:10+00:00",
        "data": {
            "coverage_market": "szse_etf",
            "methodology": "（申购量-赎回量）*IOPV",
            "total_net_inflow_yuan": -300.0,
            "trend": [
                {"timestamp": 1785481140, "net_inflow_yuan": -100.0},
                {"timestamp": 1785481200, "net_inflow_yuan": -200.0},
            ],
            "top_inflow": {
                "code": "159845",
                "name": "华夏中证1000ETF",
                "net_inflow_yuan": 100.0,
            },
            "ranking_scope": "ths_cooperative_etf_pool",
            "ranking_fund_count": 456,
        },
    }

    rows = _etf_estimated_flow_snapshots(
        response=response,
        latest_bucket_at=datetime.fromtimestamp(
            1785481140,
            tz=timezone.utc,
        ),
    )

    assert len(rows) == 2
    assert rows[-1]["data"]["net_inflow_yuan"] == -300.0
    assert rows[-1]["data"]["top_inflow"]["code"] == "159845"
    assert rows[0]["data"].get("top_inflow") is None
    assert rows[-1]["data"]["is_official_subscription"] is False


def test_native_chart_expands_points_and_only_replays_latest_bucket() -> None:
    response = {
        "status": "ok",
        "provider": "ths_native",
        "market": "cn",
        "fetched_at": "2026-07-31T02:00:00+00:00",
        "data": {
            "indicator": "market_temperature",
            "indicator_key": "sjdp_temperature_hs",
            "name": "市场情绪",
            "summary": {"tips": "测试"},
            "points": [
                {"time": "202607310930", "temperature": 60.0},
                {"time": "202607310931", "temperature": 71.0},
            ],
        },
    }

    rows = _native_chart_snapshots(
        response=response,
        data_type="market_sentiment",
        subject_type="market",
        subject_id="cn:a_share:ths_temperature",
        latest_bucket_at=datetime(
            2026,
            7,
            31,
            1,
            31,
            tzinfo=timezone.utc,
        ),
    )

    assert len(rows) == 1
    assert rows[0]["data"]["temperature"] == 71.0
    assert rows[0]["data"]["summary"] == {"tips": "测试"}


@pytest.mark.parametrize(
    ("subject_id", "evening_time", "morning_time"),
    [
        (
            "global:futures:ftse_a50",
            "202607311646",
            "202608010515",
        ),
        (
            "global:futures:dow_jones",
            "202607311700",
            "202608010500",
        ),
    ],
)
def test_native_futures_points_share_cross_midnight_trade_date(
    subject_id: str,
    evening_time: str,
    morning_time: str,
) -> None:
    response = {
        "status": "ok",
        "provider": "ths_native",
        "market": "global",
        "fetched_at": "2026-08-01T06:00:00+00:00",
        "data": {
            "indicator": "test_futures",
            "points": [
                {"time": evening_time, "value": 1.0},
                {"time": morning_time, "value": 2.0},
            ],
        },
    }

    rows = _native_chart_snapshots(
        response=response,
        data_type="futures_intraday",
        subject_type="index",
        subject_id=subject_id,
        latest_bucket_at=None,
    )

    assert [row["trade_date"].isoformat() for row in rows] == [
        "2026-08-01",
        "2026-08-01",
    ]


def test_valuation_thresholds_are_separate_from_current_market_pe_pb() -> None:
    response = {
        "status": "ok",
        "provider": "ths_public",
        "market": "cn",
        "data": {
            "points": [
                {
                    "date": "20260731",
                    "szzz": 3832.26,
                    "szzz_risk_pe": 16.2,
                    "szzz_chance_pe": 13.8,
                    "szzz_risk_pb": 1.6,
                    "szzz_chance_pb": 1.4,
                    "szcz": 13285.8,
                    "szcz_risk_pe": 31.0,
                    "szcz_chance_pe": 24.0,
                    "szcz_risk_pb": 3.4,
                    "szcz_chance_pb": 2.7,
                    "cyb": 3244.62,
                    "cyb_risk_pe": 60.0,
                    "cyb_chance_pe": 40.0,
                    "cyb_risk_pb": 7.0,
                    "cyb_chance_pb": 4.5,
                }
            ]
        },
    }

    rows = _valuation_threshold_snapshots(
        response=response,
        latest_dates={},
    )

    assert len(rows) == 3
    assert {row["data_type"] for row in rows} == {
        "market_valuation_threshold"
    }
    assert rows[0]["data"]["risk_pe"] == 16.2
    assert rows[0]["data"]["is_current_market_pe_pb"] is False


def test_daily_bar_response_expands_to_independent_trade_dates() -> None:
    response = {
        "status": "ok",
        "provider": "sina",
        "market": "cn",
        "fetched_at": "2026-07-31T08:00:00+00:00",
        "data": {
            "symbol": "sh000300",
            "interval": "1d",
            "bars": [
                {"date": "2026-07-29", "close": 4000},
                {"date": "2026-07-30", "close": 4010},
            ],
        },
    }

    rows = _bar_snapshots(
        response=response,
        data_type="benchmark_daily",
        subject_type="index",
        subject_id="cn:sh000300",
    )

    assert [row["trade_date"] for row in rows] == [
        date(2026, 7, 29),
        date(2026, 7, 30),
    ]
    assert len({row["bucket_at"] for row in rows}) == 2


def test_ths_sector_daily_bar_keeps_native_identity() -> None:
    response = {
        "status": "ok",
        "provider": "eastmoney",
        "market": "cn",
        "fetched_at": "2026-08-10T08:00:00+00:00",
        "data": {
            "interval": "1d",
            "klines": [{"date": "2026-08-08", "close": 1234.5}],
        },
    }

    rows = _bar_snapshots(
        response=response,
        data_type="ths_sector_daily",
        subject_type="sector",
        subject_id="ths:concept:886033",
        bars_key="klines",
        common_payload={
            "provider_sector_code": "886033",
            "sector_name": "共封装光学(CPO)",
            "sector_type": "concept",
        },
    )

    assert rows[0]["subject_id"] == "ths:concept:886033"
    assert rows[0]["data"]["provider_sector_code"] == "886033"


def test_northbound_deal_amount_is_not_labeled_as_net_flow() -> None:
    rows = normalize_northbound(
        {
            "result": {
                "data": [
                    {
                        "TRADE_DATE": "2026-08-08 00:00:00",
                        "MUTUAL_TYPE": "005",
                        "DEAL_AMT": 123456.0,
                    }
                ]
            }
        }
    )

    assert rows[0]["data"]["turnover"] == 123456.0
    assert rows[0]["data"]["net_flow"] is None
    assert rows[0]["data"]["directional_flow_available"] is False


def test_rate_responses_keep_independent_rate_identities() -> None:
    interest_rows = _interest_rate_snapshots(
        {
            "provider": "pboc",
            "market": "cn",
            "fetched_at": "2026-07-31T08:00:00+00:00",
            "data": {
                "shibor": {
                    "on": [{"date": "2026-07-30", "value": 1.2}],
                    "1w": [{"date": "2026-07-30", "value": 1.3}],
                },
                "lpr": [{"date": "2026-07-20", "lpr_1y": 3.0}],
            },
        }
    )
    yield_rows = _government_yield_snapshots(
        {
            "provider": "chinamoney",
            "market": "global",
            "fetched_at": "2026-07-31T08:00:00+00:00",
            "data": {
                "yields": [
                    {
                        "date": "2026-07-29",
                        "market": "cn",
                        "tenor": "10y",
                        "yield": 2.0,
                    },
                    {
                        "date": "2026-07-30",
                        "market": "cn",
                        "tenor": "10y",
                        "yield": 2.1,
                    },
                ]
            },
        }
    )

    assert {row["subject_id"] for row in interest_rows} == {
        "cn:shibor:on",
        "cn:shibor:1w",
        "cn:lpr",
    }
    assert yield_rows[0]["data"]["yield"] == 2.1


def test_watchlist_scopes_do_not_mix_frequencies() -> None:
    item = {
        "mode": "incremental",
        "newest_time": "2026-07-30",
        "last_run_at": None,
        "cursor": {},
        "config": {"realtime_interval_seconds": 60},
    }

    realtime = _watchlist_collection_plan(
        item,
        today=date(2026, 7, 31),
        force=True,
        scope="realtime",
    )
    daily = _watchlist_collection_plan(
        item,
        today=date(2026, 7, 31),
        force=True,
        scope="daily",
    )
    reference = _watchlist_collection_plan(
        item,
        today=date(2026, 7, 31),
        force=True,
        scope="reference",
    )

    assert realtime["collect_realtime"] is True
    assert realtime["collect_daily"] is False
    assert realtime["collect_low"] is False
    assert daily["collect_realtime"] is False
    assert daily["collect_daily"] is True
    assert reference["collect_low"] is True


def test_daily_refresh_keeps_same_day_kline_for_upsert() -> None:
    data = {
        "symbol": "sh600036",
        "bars": [
            {"date": "2026-07-30", "close": 39.5},
            {"date": "2026-07-31", "close": 40.0},
        ],
    }

    incremental = _expand_timeseries(
        "sh600036",
        "kline",
        data,
        date(2026, 7, 31),
        "2026-07-31",
    )
    daily_refresh = _expand_timeseries(
        "sh600036",
        "kline",
        data,
        date(2026, 7, 31),
        "2026-07-31",
        include_since=True,
    )

    assert incremental == []
    assert [row["trade_date"] for row in daily_refresh] == ["2026-07-31"]


def test_historical_snapshot_keeps_each_trade_date() -> None:
    rows = project_watchlist_market_snapshots(
        [
            {
                "code": "sh600036",
                "data_type": "stock_flow",
                "trade_date": "2026-07-29",
                "data": {"value": 1},
            },
            {
                "code": "sh600036",
                "data_type": "stock_flow",
                "trade_date": "2026-07-30",
                "data": {"value": 2},
            },
        ]
    )

    assert len(rows) == 2
    assert [row["data"]["value"] for row in rows] == [1, 2]
    assert rows[0]["bucket_at"] != rows[1]["bucket_at"]


def test_watchlist_priority_controls_due_time() -> None:
    now = datetime.now(timezone.utc)
    item = WatchlistItem(
        code="sh600036",
        name="招商银行",
        type="stock",
        source="agent",
        reason="test",
        enabled=True,
        mode="incremental",
        newest_time=None,
        oldest_time=None,
        backfill_status=None,
        config={
            "priority": "critical",
            "realtime_interval_seconds": 30,
        },
        total_runs=0,
        total_saved=0,
        last_run_at=now - timedelta(seconds=31),
        last_success_at=None,
        last_error="",
    )

    assert _is_realtime_due(item, now) is True


def test_watchlist_frequency_policy_is_not_conservative() -> None:
    assert DEFAULT_CONFIG["realtime_interval_seconds"] == 60
    assert PRIORITY_INTERVAL_SECONDS == {
        "critical": 30,
        "standard": 60,
        "low": 180,
    }


def test_realtime_gap_is_recorded_but_daily_sources_are_not() -> None:
    state = {
        "last_success_at": datetime.now(timezone.utc) - timedelta(minutes=10)
    }

    assert _unrecoverable_gap(
        source_name="market_breadth",
        state=state,
        interval_seconds=30,
    ) is not None
    assert _unrecoverable_gap(
        source_name="market_daily_bars",
        state=state,
        interval_seconds=86400,
    ) is None


def test_daily_schedule_interval_does_not_become_lock_ttl() -> None:
    config = MarketObservationService.SOURCE_CONFIGS[
        "market_daily_catchup"
    ]

    assert config["interval"] == 86400
    assert config["lock_timeout_seconds"] == 600
