from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import exchange_calendars as exchange_calendars
import pandas as pd

from src.infrastructure.clients.market_contracts import MarketSession, market_error, market_result


class MarketCalendarClient:
    PROVIDER = "exchange_calendars"
    MARKET_CONFIG = {
        "cn": ("XSHG", "Asia/Shanghai"),
        "hk": ("XHKG", "Asia/Hong_Kong"),
        "us": ("XNYS", "America/New_York"),
    }

    @classmethod
    def _calendar(cls, market: str):
        try:
            calendar_name, timezone_name = cls.MARKET_CONFIG[market]
        except KeyError as exc:
            raise ValueError(f"unsupported market: {market}") from exc
        return exchange_calendars.get_calendar(calendar_name), timezone_name

    async def get_trading_calendar(
        self,
        market: str,
        start_date: date | str,
        end_date: date | str,
    ) -> dict:
        try:
            calendar, timezone_name = self._calendar(market)
            start = pd.Timestamp(start_date)
            end = pd.Timestamp(end_date)
            sessions = set(calendar.sessions_in_range(start, end).date)
            schedule = calendar.schedule.loc[str(start.date()):str(end.date())]
            rows_by_date = {
                index.date(): row
                for index, row in schedule.iterrows()
            }
            days = []
            for current in pd.date_range(start=start, end=end, freq="D"):
                current_date = current.date()
                schedule_row = rows_by_date.get(current_date)
                is_trading_day = current_date in sessions
                item = {
                    "market": market,
                    "date": current_date,
                    "is_trading_day": is_trading_day,
                    "timezone": timezone_name,
                    "open_at": None,
                    "break_start_at": None,
                    "break_end_at": None,
                    "close_at": None,
                }
                if schedule_row is not None:
                    item.update(
                        {
                            "open_at": self._local_iso(schedule_row["open"], timezone_name),
                            "break_start_at": self._local_iso(
                                schedule_row.get("break_start"), timezone_name
                            ),
                            "break_end_at": self._local_iso(
                                schedule_row.get("break_end"), timezone_name
                            ),
                            "close_at": self._local_iso(schedule_row["close"], timezone_name),
                        }
                    )
                days.append(item)
            return market_result(
                provider=self.PROVIDER,
                market=market,
                data={"count": len(days), "days": days},
                timezone_name=timezone_name,
                provider_metadata={"calendar": calendar.name},
            )
        except ValueError:
            raise
        except Exception as exc:
            return market_error(provider=self.PROVIDER, market=market, error=exc)

    async def get_market_session(
        self,
        market: str,
        at: datetime | None = None,
    ) -> dict:
        calendar, timezone_name = self._calendar(market)
        market_timezone = ZoneInfo(timezone_name)
        current = at or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=market_timezone)
        current = current.astimezone(market_timezone)
        current_date = current.date()
        session = MarketSession.CLOSED
        is_trading_day = calendar.is_session(pd.Timestamp(current_date))
        schedule_item = None

        if is_trading_day:
            schedule_item = calendar.schedule.loc[str(current_date)]
            open_at = schedule_item["open"].tz_convert(market_timezone).to_pydatetime()
            close_at = schedule_item["close"].tz_convert(market_timezone).to_pydatetime()
            break_start = self._to_local_datetime(schedule_item.get("break_start"), market_timezone)
            break_end = self._to_local_datetime(schedule_item.get("break_end"), market_timezone)
            if current < open_at:
                session = MarketSession.PRE_MARKET
            elif break_start and break_end and break_start <= current < break_end:
                session = MarketSession.LUNCH_BREAK
            elif current <= close_at:
                session = MarketSession.OPEN
            else:
                session = MarketSession.POST_MARKET

        data = {
            "market": market,
            "date": current_date,
            "is_trading_day": is_trading_day,
            "market_session": session,
            "timezone": timezone_name,
            "observed_at": current,
            "open_at": self._local_iso(
                schedule_item["open"] if schedule_item is not None else None,
                timezone_name,
            ),
            "break_start_at": self._local_iso(
                schedule_item.get("break_start") if schedule_item is not None else None,
                timezone_name,
            ),
            "break_end_at": self._local_iso(
                schedule_item.get("break_end") if schedule_item is not None else None,
                timezone_name,
            ),
            "close_at": self._local_iso(
                schedule_item["close"] if schedule_item is not None else None,
                timezone_name,
            ),
        }
        return market_result(
            provider=self.PROVIDER,
            market=market,
            data=data,
            observed_at=current.astimezone(timezone.utc),
            trade_date=current_date,
            timezone_name=timezone_name,
            market_session=session,
            provider_metadata={"calendar": calendar.name},
        )

    @staticmethod
    def _to_local_datetime(value, timezone_value: ZoneInfo) -> datetime | None:
        if value is None or pd.isna(value):
            return None
        return value.tz_convert(timezone_value).to_pydatetime()

    @classmethod
    def _local_iso(cls, value, timezone_name: str) -> str | None:
        local = cls._to_local_datetime(value, ZoneInfo(timezone_name))
        return local.isoformat() if local else None
