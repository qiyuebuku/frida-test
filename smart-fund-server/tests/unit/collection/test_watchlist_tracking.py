from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.application.services.market_tracking_service import (
    MarketTrackingService,
    _freshness_seconds,
    _resolve_tracked_codes,
)
from src.application.services.watchlist_service import (
    WatchlistMutation,
    WatchlistItem,
    WatchlistUpdateMutation,
)
from src.domain.collection.services.fund_flow import _watchlist_collection_plan
from src.domain.collection.services.fund_flow import _fetch_watchlist_data
from src.domain.collection.watchlist_instrument import normalize_instrument


@pytest.mark.parametrize(
    ("code", "instrument_type", "name", "expected"),
    [
        ("600036", "stock", "招商银行", ("sh600036", "stock", False)),
        ("sz300750", "auto", "宁德时代", ("sz300750", "stock", False)),
        ("159915", "etf", "创业板ETF", ("159915", "fund", True)),
        ("007380", "fund", "基金", ("007380", "fund", False)),
        ("000001", "index", "上证指数", ("sh000001", "index", False)),
        ("sz399001", "auto", "深证成指", ("sz399001", "index", False)),
    ],
)
def test_normalize_instrument(
    code: str,
    instrument_type: str,
    name: str,
    expected: tuple[str, str, bool],
) -> None:
    identity = normalize_instrument(
        code=code,
        instrument_type=instrument_type,
        name=name,
    )

    assert (
        identity.code,
        identity.instrument_type,
        identity.exchange_traded,
    ) == expected


def test_ambiguous_code_requires_explicit_type() -> None:
    with pytest.raises(ValueError, match="无法自动区分"):
        normalize_instrument(
            code="000001",
            instrument_type="auto",
        )


def test_watchlist_plan_first_collection_is_full_backfill() -> None:
    plan = _watchlist_collection_plan(
        {
            "mode": "backfill",
            "newest_time": None,
            "last_run_at": None,
            "config": {"realtime_interval_seconds": 60},
        },
        today=date(2026, 7, 29),
        force=False,
    )

    assert plan is not None
    assert plan["since"] is None
    assert plan["collect_daily"] is True
    assert plan["collect_low"] is True


def test_watchlist_plan_honors_instrument_interval() -> None:
    item = {
        "mode": "incremental",
        "newest_time": "2026-07-29",
        "last_run_at": datetime.now(timezone.utc) - timedelta(seconds=30),
        "config": {"realtime_interval_seconds": 180},
    }

    assert (
        _watchlist_collection_plan(
            item,
            today=date(2026, 7, 29),
            force=False,
        )
        is None
    )
    assert (
        _watchlist_collection_plan(
            item,
            today=date(2026, 7, 29),
            force=True,
        )
        is not None
    )


def test_resolve_tracked_codes_accepts_convenient_stock_code() -> None:
    assert _resolve_tracked_codes(
        ["600036", "007380"],
        ["sh600036", "007380"],
    ) == ["sh600036", "007380"]


def test_resolve_tracked_codes_prefers_exact_fund_identity() -> None:
    assert _resolve_tracked_codes(
        ["000001"],
        ["000001", "sh000001"],
    ) == ["000001"]


def test_resolve_tracked_codes_rejects_multiple_prefixed_matches() -> None:
    with pytest.raises(ValueError, match="对应多个跟踪标的"):
        _resolve_tracked_codes(
            ["123456"],
            ["sh123456", "sz123456"],
        )


def test_realtime_dimensions_follow_priority_freshness_budget() -> None:
    assert _freshness_seconds(
        configured_interval=1800,
        expected_types={"quote", "kline"},
        priority="standard",
    ) == 1800
    assert _freshness_seconds(
        configured_interval=60,
        expected_types={"quote"},
        priority="standard",
    ) == 120
    assert _freshness_seconds(
        configured_interval=1800,
        expected_types={"nav", "kline"},
        priority="standard",
    ) == 1800
    assert _freshness_seconds(
        configured_interval=180,
        expected_types={"nav", "kline"},
        priority="standard",
    ) == 1800


@pytest.mark.asyncio
async def test_index_collection_uses_quote_minute_and_kline(
    monkeypatch,
) -> None:
    class FakeTencent:
        async def get_stock_quote(self, codes):
            assert codes == ["sh000001"]
            return {"data": {"price": 3800}}

        async def get_minute_data(self, code):
            assert code == "sh000001"
            return {"data": {"points": [{"time": "10:00", "price": 3800}]}}

    class FakeSina:
        async def get_kline(self, code, *, scale, datalen):
            assert (code, scale, datalen) == ("sh000001", 240, 60)
            return {
                "data": {
                    "bars": [
                        {
                            "date": "2026-07-29",
                            "open": 3790,
                            "close": 3800,
                        }
                    ]
                }
            }

    monkeypatch.setattr(
        "src.infrastructure.db.checkpoint_store.list_all",
        lambda aggregator: [
            {
                "source_name": "sh000001",
                "enabled": True,
                "mode": "backfill",
                "newest_time": None,
                "last_run_at": None,
                "config": {
                    "type": "index",
                    "realtime_interval_seconds": 60,
                },
            }
        ],
    )

    batch = await _fetch_watchlist_data(
        object(),
        FakeTencent(),
        FakeSina(),
        None,
        codes=["sh000001"],
        force=True,
    )

    assert {row["data_type"] for row in batch.rows} == {
        "quote",
        "minute_data",
        "kline",
    }
    assert {row["code"] for row in batch.rows} == {"sh000001"}
    assert batch.instruments["sh000001"].outcome == "collected"


@pytest.mark.asyncio
async def test_daily_collection_treats_stale_valid_bar_as_no_new_data(
    monkeypatch,
) -> None:
    successes: list[tuple[str, str, dict, int]] = []
    failures: list[tuple[str, str, str]] = []

    class FakeSina:
        async def get_kline(self, code, *, scale, datalen):
            return {
                "status_code": 0,
                "data": {
                    "bars": [
                        {
                            "date": "2026-07-28",
                            "open": 3790,
                            "close": 3800,
                        }
                    ]
                },
            }

    monkeypatch.setattr(
        "src.infrastructure.db.checkpoint_store.list_all",
        lambda aggregator: [
            {
                "source_name": "sh000001",
                "enabled": True,
                "mode": "incremental",
                "newest_time": "2026-07-29",
                "oldest_time": "2026-01-01",
                "target_time": None,
                "backfill_status": None,
                "last_run_at": None,
                "cursor": {},
                "config": {"type": "index"},
            }
        ],
    )
    monkeypatch.setattr(
        "src.infrastructure.db.checkpoint_store.update_success",
        lambda aggregator, source, checkpoint, saved_count=0: successes.append(
            (aggregator, source, checkpoint, saved_count)
        ),
    )
    monkeypatch.setattr(
        "src.infrastructure.db.checkpoint_store.update_failure",
        lambda aggregator, source, error: failures.append(
            (aggregator, source, error)
        ),
    )

    batch = await _fetch_watchlist_data(
        object(),
        object(),
        FakeSina(),
        None,
        codes=["sh000001"],
        force=True,
        scope="daily",
    )

    result = batch.instruments["sh000001"]
    assert result.outcome == "no_new_data"
    assert result.rows == []
    assert result.successful_dimensions == {"kline"}
    assert failures == []
    assert successes[0][0:2] == ("watchlist", "sh000001")
    assert successes[0][2]["newest_time"] == "2026-07-29"
    assert successes[0][2]["cursor"]["daily_last_success_date"]


@pytest.mark.asyncio
async def test_daily_collection_keeps_real_provider_failure_visible(
    monkeypatch,
) -> None:
    failures: list[tuple[str, str, str]] = []

    class FakeSina:
        async def get_kline(self, code, *, scale, datalen):
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(
        "src.infrastructure.db.checkpoint_store.list_all",
        lambda aggregator: [
            {
                "source_name": "sh000001",
                "enabled": True,
                "mode": "incremental",
                "newest_time": "2026-07-29",
                "last_run_at": None,
                "cursor": {},
                "config": {"type": "index"},
            }
        ],
    )
    monkeypatch.setattr(
        "src.infrastructure.db.checkpoint_store.update_failure",
        lambda aggregator, source, error: failures.append(
            (aggregator, source, error)
        ),
    )

    batch = await _fetch_watchlist_data(
        object(),
        object(),
        FakeSina(),
        None,
        codes=["sh000001"],
        force=True,
        scope="daily",
    )

    result = batch.instruments["sh000001"]
    assert result.outcome == "failed"
    assert "upstream unavailable" in result.failed_dimensions["kline"]
    assert failures and failures[0][0:2] == ("watchlist", "sh000001")


class _FakeWatchlist:
    def upsert_batch(self, _items):
        return [
            WatchlistMutation("sh600036", "created", "stock"),
            WatchlistMutation("007380", "unchanged", "fund"),
            WatchlistMutation("159915", "reactivated", "fund"),
        ]

    def update_batch(self, _updates):
        return [
            WatchlistUpdateMutation("sh600036", True, False),
            WatchlistUpdateMutation("159915", True, True),
        ]


class _FakeDataRepository:
    pass


@pytest.mark.asyncio
async def test_market_tracking_dispatches_only_new_or_reactivated(
    monkeypatch,
) -> None:
    dispatched: list[list[str]] = []

    async def resolve_fund(code: str) -> dict[str, str]:
        return {"code": code, "name": f"基金{code}"}

    async def fake_dispatch(codes):
        dispatched.append(list(codes))
        return [f"event:{code}" for code in codes]

    monkeypatch.setattr(
        "src.application.services.market_tracking_service."
        "send_watchlist_instrument_collection",
        fake_dispatch,
    )
    service = MarketTrackingService(
        watchlist_service=_FakeWatchlist(),
        data_repository=_FakeDataRepository(),
        fund_identity_resolver=resolve_fund,
    )

    result = await service.add_instruments(
        [
            {"code": "600036", "type": "stock", "reason": "银行跟踪"},
            {"code": "007380", "type": "fund", "reason": "基金跟踪"},
            {"code": "159915", "type": "etf", "reason": "ETF 跟踪"},
        ]
    )

    assert dispatched == [["sh600036", "159915"]]
    assert result["collection_event_ids"] == [
        "event:sh600036",
        "event:159915",
    ]


@pytest.mark.asyncio
async def test_market_tracking_rejects_mismatched_fund_name() -> None:
    async def resolve_fund(_code: str) -> dict[str, str]:
        return {"code": "960015", "name": "汇添富医药保健混合O"}

    service = MarketTrackingService(
        watchlist_service=_FakeWatchlist(),
        data_repository=_FakeDataRepository(),
        fund_identity_resolver=resolve_fund,
    )

    with pytest.raises(
        ValueError,
        match="960015 实际为“汇添富医药保健混合O”",
    ):
        await service.add_instruments(
            [
                {
                    "code": "960015",
                    "name": "华泰柏瑞亚洲领导企业QDII",
                    "type": "fund",
                    "reason": "QDII 跟踪",
                }
            ]
        )


@pytest.mark.asyncio
async def test_market_tracking_accepts_equivalent_fund_alias(
    monkeypatch,
) -> None:
    received: list[dict] = []

    class CapturingWatchlist:
        def upsert_batch(self, items):
            received.extend(items)
            return [WatchlistMutation("460010", "unchanged", "fund")]

    async def resolve_fund(_code: str) -> dict[str, str]:
        return {
            "code": "460010",
            "name": "华泰柏瑞亚洲领导企业混合(QDII)",
        }

    async def fake_dispatch(_codes):
        return []

    monkeypatch.setattr(
        "src.application.services.market_tracking_service."
        "send_watchlist_instrument_collection",
        fake_dispatch,
    )
    service = MarketTrackingService(
        watchlist_service=CapturingWatchlist(),
        data_repository=_FakeDataRepository(),
        fund_identity_resolver=resolve_fund,
    )

    await service.add_instruments(
        [
            {
                "code": "460010",
                "name": "华泰柏瑞亚洲领导企业QDII",
                "type": "fund",
                "reason": "QDII 跟踪",
            }
        ]
    )

    assert received[0]["name"] == "华泰柏瑞亚洲领导企业混合(QDII)"


@pytest.mark.asyncio
async def test_market_tracking_reactivation_collects_immediately(
    monkeypatch,
) -> None:
    dispatched: list[list[str]] = []

    async def fake_dispatch(codes):
        dispatched.append(list(codes))
        return ["event:159915"]

    monkeypatch.setattr(
        "src.application.services.market_tracking_service."
        "send_watchlist_instrument_collection",
        fake_dispatch,
    )
    service = MarketTrackingService(
        watchlist_service=_FakeWatchlist(),
        data_repository=_FakeDataRepository(),
    )

    result = await service.update_watchlist(
        [
            {
                "code": "sh600036",
                "realtime_interval_seconds": 180,
            },
            {"code": "159915", "enabled": True},
        ]
    )

    assert dispatched == [["159915"]]
    assert result["collection_event_ids"] == ["event:159915"]


def _watchlist_item(
    *,
    code: str = "sh600036",
    last_success_at: datetime | None = None,
    last_run_at: datetime | None = None,
    last_error: str = "",
) -> WatchlistItem:
    return WatchlistItem(
        code=code,
        name="招商银行",
        type="stock",
        source="agent",
        reason="测试",
        enabled=True,
        mode="incremental",
        newest_time="2026-07-30",
        oldest_time="2026-07-01",
        backfill_status="done",
        config={
            "priority": "standard",
            "realtime_interval_seconds": 60,
            "type": "stock",
        },
        total_runs=1,
        total_saved=1,
        last_run_at=last_run_at,
        last_success_at=last_success_at,
        last_error=last_error,
    )


class _ReadableWatchlist:
    def __init__(self, item: WatchlistItem):
        self.item = item

    def list_all(self, _enabled_only=False):
        return [self.item]

    def get(self, _code):
        return self.item


class _ReadableDataRepository:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def query_latest_by_codes(self, codes, data_types):
        return [
            row
            for row in self.rows
            if row["code"] in codes and row["data_type"] in data_types
        ]


class _FakeSnapshotRepository:
    def __init__(self, source=None):
        self.source = source

    def query_latest(self, *, subject_ids, data_types):
        rows = self.source.rows if self.source is not None else []
        return [
            {
                "id": index,
                "subject_id": row["code"],
                "data_type": row["data_type"],
                "trade_date": row.get("trade_date"),
                "data": row.get("data"),
                "observed_at": row.get("updated_at"),
                "fetched_at": row.get("updated_at"),
                "freshness_status": "realtime",
            }
            for index, row in enumerate(rows, start=1)
            if row["code"] in subject_ids and row["data_type"] in data_types
        ]

    def query_history(self, **_kwargs):
        return []


@pytest.mark.asyncio
async def test_sector_flow_history_reads_market_snapshot_store_by_exact_date() -> None:
    requested = {}

    class SnapshotRepository(_FakeSnapshotRepository):
        def query_history(self, **kwargs):
            requested.update(kwargs)
            return [{
                "id": 1,
                "subject_id": kwargs["subject_id"],
                "data_type": "ths_sector_flow",
                "trade_date": date(2026, 8, 12),
                "observed_at": datetime(2026, 8, 12, 7, tzinfo=timezone.utc),
                "fetched_at": datetime(2026, 8, 12, 7, tzinfo=timezone.utc),
                "freshness_status": "historical",
                "data": {"main_net_inflow": 134.53},
            }]

    result = await MarketTrackingService(
        watchlist_service=_ReadableWatchlist(_watchlist_item(code="ths_native:concept:886033")),
        data_repository=_ReadableDataRepository([]),
        snapshot_repository=SnapshotRepository(),
    ).instrument_history(
        code="ths_native:concept:886033",
        data_type="ths_sector_flow",
        date_start="2026-08-12",
        date_end="2026-08-12",
        limit=5,
    )

    assert result["count"] == 1
    assert result["items"][0]["data"]["main_net_inflow"] == 134.53
    assert requested["data_type"] == "ths_sector_flow"
    assert requested["date_start"] == date(2026, 8, 12)
    assert requested["date_end"] == date(2026, 8, 12)


@pytest.mark.asyncio
async def test_sector_flow_history_maps_semantic_sector_identity_to_native_store() -> None:
    requested = {}

    class SnapshotRepository(_FakeSnapshotRepository):
        def query_history(self, **kwargs):
            requested.update(kwargs)
            return [{
                "id": 7,
                "subject_id": kwargs["subject_id"],
                "data_type": "ths_sector_flow",
                "trade_date": date(2026, 8, 14),
                "observed_at": datetime(2026, 8, 14, 7, tzinfo=timezone.utc),
                "fetched_at": datetime(2026, 8, 14, 7, tzinfo=timezone.utc),
                "freshness_status": "historical",
                "data": {"main_net_inflow": 111.05},
            }]

    result = await MarketTrackingService(
        watchlist_service=_ReadableWatchlist(_watchlist_item(code="ths_native:concept:886033")),
        data_repository=_ReadableDataRepository([]),
        snapshot_repository=SnapshotRepository(),
    ).instrument_history(
        code="ths:concept:886033",
        data_type="ths_sector_flow",
        date_start="2026-08-14",
        date_end="2026-08-14",
        limit=5,
    )

    assert requested["subject_id"] == "ths_native:concept:886033"
    assert result["code"] == "ths_native:concept:886033"
    assert result["items"][0]["evidence_locator"].startswith("market:v1:")


@pytest.mark.asyncio
async def test_market_open_returns_fresh_data_without_refresh(
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    watchlist = _ReadableWatchlist(
        _watchlist_item(last_success_at=now, last_run_at=now)
    )
    repository = _ReadableDataRepository(
        [
            {
                "code": "sh600036",
                "data_type": "quote",
                "trade_date": date(2026, 7, 30),
                "data": {"price": 40},
                "updated_at": now,
            }
        ]
    )

    async def fail_dispatch(_codes):
        raise AssertionError("fresh data must not trigger collection")

    monkeypatch.setattr(
        "src.application.services.market_tracking_service."
        "send_watchlist_instrument_collection",
        fail_dispatch,
    )
    result = await MarketTrackingService(
        watchlist_service=watchlist,
        data_repository=repository,
        snapshot_repository=_FakeSnapshotRepository(repository),
    ).open_instruments(codes=["600036"], data_types=["quote"])

    instrument = result["instruments"][0]
    assert instrument["freshness"]["status"] == "fresh"
    assert instrument["freshness"]["refresh_triggered"] is False
    assert instrument["freshness"]["is_stale"] is False


@pytest.mark.asyncio
async def test_market_open_does_not_refresh_unsupported_data_type(
    monkeypatch,
) -> None:
    now = datetime.now(timezone.utc)
    fund = _watchlist_item(last_success_at=now, last_run_at=now)
    fund.code = "000083"
    fund.type = "fund"
    fund.config = {
        "priority": "standard",
        "realtime_interval_seconds": 60,
        "type": "fund",
    }
    watchlist = _ReadableWatchlist(fund)

    async def fail_dispatch(_codes):
        raise AssertionError("unsupported dimensions must not trigger collection")

    monkeypatch.setattr(
        "src.application.services.market_tracking_service."
        "send_watchlist_instrument_collection",
        fail_dispatch,
    )
    result = await MarketTrackingService(
        watchlist_service=watchlist,
        data_repository=_ReadableDataRepository([]),
        snapshot_repository=_FakeSnapshotRepository(),
    ).open_instruments(codes=["000083"], data_types=["quote"])

    freshness = result["instruments"][0]["freshness"]
    assert freshness["status"] == "unsupported_data_types"
    assert freshness["unsupported_data_types"] == ["quote"]
    assert freshness["refresh_triggered"] is False
    assert freshness["is_stale"] is False


@pytest.mark.asyncio
async def test_market_open_waits_for_stale_data_refresh(
    monkeypatch,
) -> None:
    old_time = datetime.now(timezone.utc) - timedelta(hours=2)
    new_time = datetime.now(timezone.utc)
    watchlist = _ReadableWatchlist(
        _watchlist_item(last_success_at=old_time, last_run_at=old_time)
    )
    repository = _ReadableDataRepository(
        [
            {
                "code": "sh600036",
                "data_type": "quote",
                "trade_date": date(2026, 7, 29),
                "data": {"price": 39},
                "updated_at": old_time,
            }
        ]
    )

    async def refresh(codes, *, scope):
        assert codes == ["sh600036"]
        assert scope == "realtime"
        repository.rows = [
            {
                "code": "sh600036",
                "data_type": "quote",
                "trade_date": date(2026, 7, 30),
                "data": {"price": 40},
                "updated_at": new_time,
            }
        ]
        watchlist.item = _watchlist_item(
            last_success_at=new_time,
            last_run_at=new_time,
        )
        return ["event:refresh"]

    monkeypatch.setattr(
        "src.application.services.market_tracking_service."
        "send_watchlist_instrument_collection",
        refresh,
    )
    result = await MarketTrackingService(
        watchlist_service=watchlist,
        data_repository=repository,
        snapshot_repository=_FakeSnapshotRepository(repository),
        refresh_poll_seconds=0.001,
    ).open_instruments(codes=["600036"], data_types=["quote"])

    instrument = result["instruments"][0]
    assert instrument["freshness"]["status"] == "refreshed"
    assert instrument["freshness"]["refresh_triggered"] is True
    assert instrument["freshness"]["collection_event_id"] == "event:refresh"
    assert instrument["latest"][0]["data"]["price"] == 40


@pytest.mark.asyncio
async def test_market_open_refreshes_missing_supported_dimension(
    monkeypatch,
) -> None:
    initial_time = datetime.now(timezone.utc)
    refreshed_time = initial_time + timedelta(seconds=1)
    watchlist = _ReadableWatchlist(
        _watchlist_item(
            last_success_at=initial_time,
            last_run_at=initial_time,
        )
    )
    repository = _ReadableDataRepository([])

    async def refresh(_codes, *, scope):
        assert scope == "realtime"
        repository.rows = [
            {
                "code": "sh600036",
                "data_type": "quote",
                "trade_date": date(2026, 7, 30),
                "data": {"price": 40},
                "updated_at": refreshed_time,
            }
        ]
        watchlist.item = _watchlist_item(
            last_success_at=refreshed_time,
            last_run_at=refreshed_time,
        )
        return ["event:missing"]

    monkeypatch.setattr(
        "src.application.services.market_tracking_service."
        "send_watchlist_instrument_collection",
        refresh,
    )
    result = await MarketTrackingService(
        watchlist_service=watchlist,
        data_repository=repository,
        snapshot_repository=_FakeSnapshotRepository(repository),
        refresh_poll_seconds=0.001,
    ).open_instruments(codes=["600036"], data_types=["quote"])

    instrument = result["instruments"][0]
    assert instrument["freshness"]["status"] == "refreshed"
    assert instrument["freshness"]["trigger_reasons"] == ["data_incomplete"]
    assert instrument["latest"][0]["data"]["price"] == 40


@pytest.mark.asyncio
async def test_market_open_timeout_returns_pre_refresh_snapshot(
    monkeypatch,
) -> None:
    old_time = datetime.now(timezone.utc) - timedelta(hours=2)
    watchlist = _ReadableWatchlist(
        _watchlist_item(last_success_at=old_time, last_run_at=old_time)
    )
    repository = _ReadableDataRepository(
        [
            {
                "code": "sh600036",
                "data_type": "quote",
                "trade_date": date(2026, 7, 29),
                "data": {"price": 39},
                "updated_at": old_time,
            }
        ]
    )

    async def dispatch(_codes, *, scope):
        assert scope == "realtime"
        return ["event:slow"]

    monkeypatch.setattr(
        "src.application.services.market_tracking_service."
        "send_watchlist_instrument_collection",
        dispatch,
    )
    result = await MarketTrackingService(
        watchlist_service=watchlist,
        data_repository=repository,
        snapshot_repository=_FakeSnapshotRepository(repository),
        refresh_wait_seconds=0.01,
        refresh_poll_seconds=0.001,
    ).open_instruments(codes=["600036"], data_types=["quote"])

    instrument = result["instruments"][0]
    assert instrument["freshness"]["status"] == "refresh_timeout"
    assert instrument["freshness"]["is_stale"] is True
    assert instrument["freshness"]["stale_data_returned"] is True
    assert "旧数据" in instrument["freshness"]["message"]
    assert instrument["latest"][0]["data"]["price"] == 39


@pytest.mark.asyncio
async def test_market_open_failure_returns_pre_refresh_snapshot(
    monkeypatch,
) -> None:
    old_time = datetime.now(timezone.utc) - timedelta(hours=2)
    failed_time = datetime.now(timezone.utc)
    watchlist = _ReadableWatchlist(
        _watchlist_item(last_success_at=old_time, last_run_at=old_time)
    )
    repository = _ReadableDataRepository(
        [
            {
                "code": "sh600036",
                "data_type": "quote",
                "trade_date": date(2026, 7, 29),
                "data": {"price": 39},
                "updated_at": old_time,
            }
        ]
    )

    async def dispatch(_codes, *, scope):
        assert scope == "realtime"
        watchlist.item = _watchlist_item(
            last_success_at=old_time,
            last_run_at=failed_time,
            last_error="upstream unavailable",
        )
        return ["event:failed"]

    monkeypatch.setattr(
        "src.application.services.market_tracking_service."
        "send_watchlist_instrument_collection",
        dispatch,
    )
    result = await MarketTrackingService(
        watchlist_service=watchlist,
        data_repository=repository,
        snapshot_repository=_FakeSnapshotRepository(repository),
        refresh_poll_seconds=0.001,
    ).open_instruments(codes=["600036"], data_types=["quote"])

    instrument = result["instruments"][0]
    assert instrument["freshness"]["status"] == "refresh_failed"
    assert instrument["freshness"]["refresh_error"] == "upstream unavailable"
    assert instrument["latest"][0]["data"]["price"] == 39
