from datetime import datetime, timezone

from src.infrastructure.clients.market_contracts import (
    MarketDataStatus,
    market_error,
    market_result,
)


def test_market_result_distinguishes_empty_from_error() -> None:
    empty = market_result(provider="sina", market="cn", data=[])
    counted_empty = market_result(
        provider="sina",
        market="cn",
        data={"count": 0, "items": []},
    )
    error = market_error(
        provider="sina",
        market="cn",
        error=RuntimeError("connection reset"),
    )

    assert empty["status"] == "empty"
    assert counted_empty["status"] == "empty"
    assert empty["status_code"] == 0
    assert error["status"] == "upstream_error"
    assert error["status_code"] == -1
    assert error["provider_metadata"]["error_type"] == "RuntimeError"


def test_market_result_serializes_dates_and_nan() -> None:
    result = market_result(
        provider="test",
        market="cn",
        data={"value": float("nan")},
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        trade_date="2026-07-30",
        status=MarketDataStatus.OK,
    )

    assert result["data"]["value"] is None
    assert result["trade_date"] == "2026-07-30"
    assert result["observed_at"] == "2026-07-30T00:00:00Z"
