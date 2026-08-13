"""Historical Shanghai/Shenzhen trading-session decisions for Agent replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import exchange_calendars


CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class ChinaSessionState:
    cutoff_at: datetime
    local_date: date
    is_trading_day: bool
    market_session: str
    trade_date: date
    previous_trade_date: date
    next_trade_date: date
    comparison_cutoff_at: datetime
    calendar_name: str = "XSHG"


class ChinaExchangeCalendarService:
    """Resolve historical sessions from the versioned XSHG calendar."""

    def __init__(self) -> None:
        self._calendar = exchange_calendars.get_calendar("XSHG")

    def resolve(self, cutoff_at: datetime) -> ChinaSessionState:
        cutoff = _aware_utc(cutoff_at)
        local = cutoff.astimezone(CHINA_TIMEZONE)
        session_label = local.date().isoformat()
        is_trading_day = bool(self._calendar.is_session(session_label))
        if is_trading_day:
            trade_date = local.date()
            previous_label = self._calendar.previous_session(session_label)
            market_session = self._session_phase(local.time().replace(tzinfo=None))
        else:
            trade_label = self._calendar.date_to_session(
                session_label,
                direction="previous",
            )
            trade_date = trade_label.date()
            previous_label = self._calendar.previous_session(trade_label)
            market_session = "non_trading_day"

        previous_trade_date = previous_label.date()
        next_label = self._calendar.next_session(
            session_label if is_trading_day else trade_label
        )
        comparison_cutoff_at = self._comparison_cutoff(
            local=local,
            market_session=market_session,
            previous_trade_date=(
                previous_trade_date if is_trading_day else trade_date
            ),
        )
        return ChinaSessionState(
            cutoff_at=cutoff,
            local_date=local.date(),
            is_trading_day=is_trading_day,
            market_session=market_session,
            trade_date=trade_date,
            previous_trade_date=previous_trade_date,
            next_trade_date=next_label.date(),
            comparison_cutoff_at=comparison_cutoff_at,
        )

    @staticmethod
    def _session_phase(local_time: time) -> str:
        if local_time < time(9, 15):
            return "pre_open"
        if local_time < time(9, 30):
            return "opening_auction"
        if local_time <= time(11, 30) or time(13, 0) <= local_time <= time(15, 0):
            return "continuous"
        if local_time < time(13, 0):
            return "midday_break"
        return "closed"

    @staticmethod
    def _comparison_cutoff(
        *,
        local: datetime,
        market_session: str,
        previous_trade_date: date,
    ) -> datetime:
        if market_session in {"non_trading_day", "pre_open", "closed"}:
            comparison_time = time(15, 0)
        elif market_session == "opening_auction":
            comparison_time = time(9, 25)
        else:
            comparison_time = local.time().replace(tzinfo=None)
            if time(11, 30) < comparison_time < time(13, 0):
                comparison_time = time(11, 30)
            comparison_time = min(comparison_time, time(15, 0))
        return datetime.combine(
            previous_trade_date,
            comparison_time,
            tzinfo=CHINA_TIMEZONE,
        ).astimezone(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cutoff_at must include a timezone")
    return value.astimezone(UTC)
