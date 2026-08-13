from __future__ import annotations

import importlib
from datetime import date, datetime, timezone

import pytest

from src.application.services.market_observation_service import (
    _dated_series_snapshots,
    _stock_change_snapshot,
    _with_etf_daily_share_change,
)
from src.infrastructure.clients.chinabond import ChinaBondClient
from src.infrastructure.clients.eastmoney import EastmoneyClient
from src.infrastructure.clients.market_valuation import MarketValuationClient


def test_market_valuation_normalizes_pe_and_pb_histories() -> None:
    pe = MarketValuationClient._normalize_pe_rows(
        {
            "data": [
                {"date": "2026-07-30", "pe": 14.2, "close": 3800},
                {"date": "invalid"},
            ]
        }
    )
    pb = MarketValuationClient._normalize_pb_rows(
        {
            "data": [
                {
                    "date": "2026-07-30",
                    "pb": 1.4,
                    "addPb": 1.2,
                    "middlePb": 1.1,
                    "close": 3800,
                }
            ]
        }
    )

    assert pe == [{"date": "2026-07-30", "pe": 14.2, "close": 3800.0}]
    assert pb[0]["pb"] == 1.4
    assert pb[0]["weighted_pb"] == 1.2
    assert pb[0]["median_pb"] == 1.1


def test_chinabond_normalizes_timestamp_map() -> None:
    rows = ChinaBondClient._normalize_rows(
        {"CFZS_00": {"1785369600000": 123.45}},
        indicator="wealth",
        indicator_code="CFZS",
        indicator_name="财富指数",
    )

    assert rows == [
        {
            "date": "2026-07-30",
            "value": 123.45,
            "indicator": "wealth",
            "indicator_code": "CFZS",
            "indicator_name": "财富指数",
        }
    ]


def test_stock_change_identity_uses_event_time_and_type() -> None:
    fetched_at = datetime(2026, 7, 31, 1, 25, 2, tzinfo=timezone.utc)
    row = _stock_change_snapshot(
        {
            "time": "09:25:01",
            "code": "600000",
            "market": 1,
            "name": "浦发银行",
            "typeCode": 8207,
            "typeName": "竞价上涨",
            "price": 10.0,
        },
        trade_date=date(2026, 7, 31),
        fetched_at=fetched_at,
    )

    assert row["subject_id"].startswith(
        "cn:stock_change:2026-07-31:09:25:01:1:600000:8207:"
    )
    assert row["bucket_at"] == datetime(
        2026,
        7,
        31,
        1,
        25,
        1,
        tzinfo=timezone.utc,
    )
    assert row["freshness_status"] == "realtime"


def test_dated_series_keeps_latest_date_for_refresh() -> None:
    rows = _dated_series_snapshots(
        response={
            "provider": "legulegu",
            "market": "cn",
            "fetched_at": "2026-07-31T08:00:00+00:00",
        },
        rows=[
            {"date": "2026-07-29", "pe": 14.0},
            {"date": "2026-07-30", "pe": 14.2},
            {"date": "2026-07-31", "pe": 14.3},
        ],
        data_type="market_pe",
        subject_type="market",
        subject_id="cn:market:sh",
        latest_date=date(2026, 7, 30),
    )

    assert [row["trade_date"] for row in rows] == [
        date(2026, 7, 30),
        date(2026, 7, 31),
    ]
    assert {row["provider"] for row in rows} == {"legulegu"}


@pytest.mark.asyncio
async def test_market_valuation_rejects_unknown_market() -> None:
    client = MarketValuationClient()
    try:
        with pytest.raises(ValueError):
            await client.get_market_valuation_history("hk")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stock_change_network_failure_is_not_reported_as_empty(
    monkeypatch,
) -> None:
    class FailingHttpClient:
        async def get(self, *_args, **_kwargs):
            raise RuntimeError("network down")

    async def no_wait(_seconds: float) -> None:
        return None

    client = EastmoneyClient()
    original_client = client._client
    client._client = FailingHttpClient()
    await original_client.aclose()
    eastmoney_module = importlib.import_module(
        "src.infrastructure.clients.eastmoney"
    )
    monkeypatch.setattr(eastmoney_module.asyncio, "sleep", no_wait)

    result = await client.get_stock_changes("all", 50)

    assert result["status_code"] == -1
    assert result["data"] is None


def test_etf_daily_share_change_is_not_labeled_realtime() -> None:
    item = _with_etf_daily_share_change(
        {
            "exchange": "sse",
            "code": "510300",
            "shares": 1100,
        },
        previous_date=date(2026, 7, 30),
        previous_shares=1000,
    )

    assert item["confirmed_share_change"] == 100
    assert item["confirmed_share_change_rate"] == 10
    assert item["flow_confirmation_frequency"] == "daily"
    assert item["realtime_net_subscription_available"] is False
