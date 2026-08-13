from __future__ import annotations

from datetime import UTC, date, datetime

from src.application.services.china_exchange_calendar_service import (
    ChinaExchangeCalendarService,
)


def test_calendar_marks_weekend_and_national_holiday_non_trading() -> None:
    service = ChinaExchangeCalendarService()

    weekend = service.resolve(datetime(2026, 8, 9, 6, 0, tzinfo=UTC))
    holiday = service.resolve(datetime(2026, 10, 1, 6, 0, tzinfo=UTC))

    assert weekend.market_session == "non_trading_day"
    assert weekend.trade_date == date(2026, 8, 7)
    assert holiday.market_session == "non_trading_day"
    assert holiday.trade_date < date(2026, 10, 1)


def test_calendar_resolves_intraday_phase_and_same_time_baseline() -> None:
    service = ChinaExchangeCalendarService()

    state = service.resolve(datetime(2026, 8, 10, 6, 15, tzinfo=UTC))

    assert state.is_trading_day is True
    assert state.market_session == "continuous"
    assert state.trade_date == date(2026, 8, 10)
    assert state.previous_trade_date == date(2026, 8, 7)
    assert state.next_trade_date == date(2026, 8, 11)
    assert state.comparison_cutoff_at == datetime(
        2026,
        8,
        7,
        6,
        15,
        tzinfo=UTC,
    )


def test_calendar_exposes_next_trade_date_for_futures_night_session() -> None:
    state = ChinaExchangeCalendarService().resolve(
        datetime(2026, 8, 11, 15, 32, tzinfo=UTC)
    )

    assert state.market_session == "closed"
    assert state.trade_date == date(2026, 8, 11)
    assert state.next_trade_date == date(2026, 8, 12)


def test_calendar_uses_previous_close_for_premarket() -> None:
    state = ChinaExchangeCalendarService().resolve(
        datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    )

    assert state.market_session == "pre_open"
    assert state.comparison_cutoff_at == datetime(
        2026,
        8,
        7,
        7,
        0,
        tzinfo=UTC,
    )
