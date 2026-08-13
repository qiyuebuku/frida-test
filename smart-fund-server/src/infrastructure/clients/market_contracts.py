from __future__ import annotations

import math
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MarketDataStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    UPSTREAM_ERROR = "upstream_error"
    PARSE_ERROR = "parse_error"


class MarketSession(StrEnum):
    PRE_MARKET = "pre_market"
    OPEN = "open"
    LUNCH_BREAK = "lunch_break"
    POST_MARKET = "post_market"
    CLOSED = "closed"


class TradingStatus(StrEnum):
    TRADING = "trading"
    HALTED = "halted"
    CLOSED = "closed"
    NO_VALID_QUOTE = "no_valid_quote"
    UNKNOWN = "unknown"


class MarketDataResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    status_code: int
    status: MarketDataStatus
    provider: str
    market: str
    observed_at: datetime
    fetched_at: datetime
    source_time: str | None = None
    trade_date: date | None = None
    timezone: str | None = None
    market_session: MarketSession | None = None
    data: Any = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None


def clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):
        try:
            return clean_value(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): clean_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_value(item) for item in value]
    return value


def market_result(
    *,
    provider: str,
    market: str,
    data: Any,
    status: MarketDataStatus | None = None,
    observed_at: datetime | None = None,
    source_time: str | None = None,
    trade_date: date | str | None = None,
    timezone_name: str | None = None,
    market_session: MarketSession | None = None,
    provider_metadata: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc)
    cleaned = clean_value(data)
    is_empty = cleaned in (None, [], {}) or (
        isinstance(cleaned, dict) and cleaned.get("count") == 0
    )
    resolved_status = status or (
        MarketDataStatus.EMPTY if is_empty else MarketDataStatus.OK
    )
    if isinstance(trade_date, str):
        trade_date = date.fromisoformat(trade_date[:10])
    response = MarketDataResponse(
        status_code=0 if resolved_status in (MarketDataStatus.OK, MarketDataStatus.EMPTY) else -1,
        status=resolved_status,
        provider=provider,
        market=market,
        observed_at=observed_at or fetched_at,
        fetched_at=fetched_at,
        source_time=source_time,
        trade_date=trade_date,
        timezone=timezone_name,
        market_session=market_session,
        data=cleaned,
        provider_metadata=clean_value(provider_metadata or {}),
        message=message,
    )
    return response.model_dump(mode="json", exclude_none=True)


def market_error(
    *,
    provider: str,
    market: str,
    error: Exception | str,
    status: MarketDataStatus = MarketDataStatus.UPSTREAM_ERROR,
    provider_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(provider_metadata or {})
    metadata.setdefault(
        "error_type",
        type(error).__name__ if isinstance(error, Exception) else "upstream_error",
    )
    return market_result(
        provider=provider,
        market=market,
        data=None,
        status=status,
        provider_metadata=metadata,
        message=str(error),
    )
