from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.infrastructure.clients.market_calendar import MarketCalendarClient


@pytest.mark.asyncio
async def test_cn_calendar_distinguishes_holiday_and_trading_day() -> None:
    client = MarketCalendarClient()
    result = await client.get_trading_calendar("cn", "2026-10-01", "2026-10-09")

    days = {item["date"]: item for item in result["data"]["days"]}
    assert result["status"] == "ok"
    assert days["2026-10-01"]["is_trading_day"] is False
    assert days["2026-10-09"]["is_trading_day"] is True


@pytest.mark.asyncio
async def test_cn_market_session_identifies_lunch_break() -> None:
    client = MarketCalendarClient()
    result = await client.get_market_session(
        "cn",
        datetime(2026, 7, 30, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result["data"]["is_trading_day"] is True
    assert result["market_session"] == "lunch_break"
    assert result["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_us_market_session_uses_new_york_timezone() -> None:
    client = MarketCalendarClient()
    result = await client.get_market_session(
        "us",
        datetime(2026, 7, 30, 10, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result["market_session"] == "open"
    assert result["timezone"] == "America/New_York"
