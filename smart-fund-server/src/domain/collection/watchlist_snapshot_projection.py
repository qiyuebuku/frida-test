"""Watchlist dimensions that belong to the market snapshot fact store."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


MARKET_SNAPSHOT_DATA_TYPES = frozenset(
    {
        "quote",
        "realtime",
        "minute_data",
        "kline",
        "stock_flow",
        "hk_quote",
        "us_quote",
        "hk_us_kline",
        "global_index",
        "forex",
        "intl_futures",
        "benchmark_daily",
        "commodity_daily",
        "northbound_turnover",
        "ths_index_daily",
        "ths_sector_daily",
    }
)

HISTORICAL_MARKET_DATA_TYPES = frozenset(
    {
        "kline",
        "stock_flow",
        "hk_us_kline",
        "benchmark_daily",
        "commodity_daily",
        "northbound_turnover",
        "ths_index_daily",
        "ths_sector_daily",
    }
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def project_watchlist_market_snapshots(
    items: list[dict],
    *,
    fetched_at: datetime | None = None,
) -> list[dict]:
    """Convert market-like Watchlist rows to canonical market snapshots."""

    now = fetched_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    live_bucket = datetime.fromtimestamp(
        int(now.timestamp()) - int(now.timestamp()) % 30,
        tz=timezone.utc,
    )
    projected: dict[tuple[str, str, str, datetime], dict] = {}
    for item in items:
        data_type = str(item.get("data_type") or "").strip()
        code = str(item.get("code") or "").strip().lower()
        payload = item.get("data")
        if (
            data_type not in MARKET_SNAPSHOT_DATA_TYPES
            or not code
            or payload is None
        ):
            continue
        trade_date = _coerce_date(item.get("trade_date"), now)
        historical = data_type in HISTORICAL_MARKET_DATA_TYPES
        bucket_at = _historical_bucket(trade_date) if historical else live_bucket
        observed_at = bucket_at if historical else now
        provider = _dimension_provider(data_type, payload)
        row = {
            "data_type": data_type,
            "subject_type": "market" if code == "_market" else "instrument",
            "subject_id": code,
            "market": _instrument_market(code, data_type),
            "provider": provider,
            "trade_date": trade_date,
            "observed_at": observed_at,
            "fetched_at": now,
            "bucket_at": bucket_at,
            "freshness_status": "historical" if historical else "realtime",
            "source_latency_seconds": max(
                0.0,
                (now - observed_at).total_seconds(),
            ),
            "payload_hash": hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            "data": payload,
        }
        projected[(code, data_type, provider, bucket_at)] = row
    return list(projected.values())


def _coerce_date(value, now: datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if text:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass
    return now.astimezone(_SHANGHAI).date()


def _historical_bucket(trade_date: date) -> datetime:
    return datetime.combine(
        trade_date,
        time(hour=15),
        tzinfo=_SHANGHAI,
    ).astimezone(timezone.utc)


def _dimension_provider(data_type: str, payload) -> str:
    if isinstance(payload, dict) and payload.get("source"):
        return str(payload["source"])
    if data_type == "realtime":
        return "ths"
    if data_type in {"hk_quote", "us_quote", "hk_us_kline"}:
        return "eastmoney"
    return "tencent"


def _instrument_market(code: str, data_type: str) -> str:
    if data_type == "forex":
        return "global"
    if data_type == "intl_futures":
        return "global"
    if data_type == "global_index":
        return "global"
    if code.startswith("hk"):
        return "hk"
    if code.startswith("us"):
        return "us"
    if code.startswith(("sh", "sz", "bj")) or code[:1].isdigit():
        return "cn"
    return "unknown"
