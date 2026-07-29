"""Application service for Agent-driven instrument tracking and market reads."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from src.application.services.watchlist_service import WatchlistService
from src.infrastructure.persistence.repositories.watchlist_data_repository_impl import (
    WatchlistDataRepositoryImpl,
)
from src.infrastructure.tasks.jettask_dispatcher import (
    send_watchlist_instrument_collection,
)


DEFAULT_SNAPSHOT_DATA_TYPES = [
    "quote",
    "realtime",
    "nav",
    "nav_technical",
    "kline",
    "minute_data",
    "performance",
    "stock_flow",
    "valuation",
    "fund_detail",
    "holding_overview",
]


class MarketTrackingService:
    def __init__(
        self,
        *,
        watchlist_service: WatchlistService | None = None,
        data_repository: WatchlistDataRepositoryImpl | None = None,
    ) -> None:
        self._watchlist = watchlist_service or WatchlistService()
        self._data = data_repository or WatchlistDataRepositoryImpl()

    async def add_instruments(
        self,
        instruments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not instruments:
            raise ValueError("instruments 不能为空")
        prepared = []
        for item in instruments:
            value = dict(item)
            value["source"] = value.get("source") or "agent"
            if value["source"] == "agent" and not str(value.get("reason") or "").strip():
                raise ValueError("Agent 添加跟踪标的时必须提供 reason")
            prepared.append(value)
        mutations = await asyncio.to_thread(self._watchlist.upsert_batch, prepared)
        collect_codes = [
            mutation.code for mutation in mutations if mutation.should_collect_now
        ]
        event_ids = await send_watchlist_instrument_collection(collect_codes)
        return {
            "operation": "market_watchlist_add",
            "items": [mutation.to_dict() for mutation in mutations],
            "collection_event_ids": event_ids,
        }

    async def list_watchlist(
        self,
        *,
        enabled_only: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        items = await asyncio.to_thread(
            self._watchlist.list_all,
            enabled_only,
        )
        bounded_limit = max(1, min(int(limit), 500))
        return {
            "operation": "market_watchlist_list",
            "total": len(items),
            "items": [item.to_dict() for item in items[:bounded_limit]],
            "truncated": len(items) > bounded_limit,
        }

    async def update_watchlist(
        self,
        updates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not updates:
            raise ValueError("updates 不能为空")
        mutations = await asyncio.to_thread(
            self._watchlist.update_batch,
            updates,
        )
        collect_codes = [
            mutation.code
            for mutation in mutations
            if mutation.should_collect_now
        ]
        event_ids = await send_watchlist_instrument_collection(collect_codes)
        return {
            "operation": "market_watchlist_update",
            "items": [mutation.to_dict() for mutation in mutations],
            "collection_event_ids": event_ids,
        }

    async def open_instruments(
        self,
        *,
        codes: list[str],
        data_types: list[str] | None = None,
    ) -> dict[str, Any]:
        requested_codes = [
            code
            for code in dict.fromkeys(str(code).strip().lower() for code in codes)
            if code
        ]
        if not requested_codes:
            raise ValueError("codes 不能为空")
        if len(requested_codes) > 8:
            raise ValueError("单次最多打开 8 个标的")
        watchlist_items = await asyncio.to_thread(
            self._watchlist.list_all,
            False,
        )
        normalized_codes = _resolve_tracked_codes(
            requested_codes,
            [item.code for item in watchlist_items],
        )
        selected_types = [
            value
            for value in dict.fromkeys(
                str(value).strip() for value in (data_types or DEFAULT_SNAPSHOT_DATA_TYPES)
            )
            if value
        ]
        items = await asyncio.to_thread(
            self._data.query_latest_by_codes,
            normalized_codes,
            selected_types,
        )
        statuses = await asyncio.gather(
            *[
                asyncio.to_thread(self._watchlist.get, code)
                for code in normalized_codes
            ]
        )
        grouped: dict[str, list[dict]] = {code: [] for code in normalized_codes}
        for item in items:
            grouped.setdefault(item["code"], []).append(item)
        return {
            "operation": "market_instrument_open",
            "instruments": [
                {
                    "code": code,
                    "tracking": status.to_dict() if status else None,
                    "latest": grouped.get(code, []),
                }
                for code, status in zip(normalized_codes, statuses)
            ],
        }

    async def instrument_history(
        self,
        *,
        code: str,
        data_type: str,
        date_start: str = "",
        date_end: str = "",
        limit: int = 120,
    ) -> dict[str, Any]:
        normalized_input_code = str(code or "").strip().lower()
        normalized_data_type = str(data_type or "").strip()
        if not normalized_input_code:
            raise ValueError("code 不能为空")
        if not normalized_data_type:
            raise ValueError("data_type 不能为空")
        start = date.fromisoformat(date_start) if date_start else None
        end = date.fromisoformat(date_end) if date_end else None
        if start and end and start > end:
            raise ValueError("date_start 不能晚于 date_end")
        watchlist_items = await asyncio.to_thread(
            self._watchlist.list_all,
            False,
        )
        normalized_code = _resolve_tracked_codes(
            [normalized_input_code],
            [item.code for item in watchlist_items],
        )[0]
        items = await asyncio.to_thread(
            self._data.query_history,
            code=normalized_code,
            data_type=normalized_data_type,
            date_start=start,
            date_end=end,
            limit=limit,
        )
        return {
            "operation": "market_instrument_history",
            "code": normalized_code,
            "data_type": normalized_data_type,
            "items": items,
            "count": len(items),
        }


def create_market_tracking_service() -> MarketTrackingService:
    return MarketTrackingService()


def _resolve_tracked_codes(
    requested_codes: list[str],
    tracked_codes: list[str],
) -> list[str]:
    """Resolve convenient six-digit input without hiding genuine ambiguity."""

    tracked = set(tracked_codes)
    resolved: list[str] = []
    for raw in requested_codes:
        if raw in tracked:
            resolved.append(raw)
            continue
        digits = raw[2:] if raw.startswith(("sh", "sz", "bj")) else raw
        matches = [
            candidate
            for candidate in tracked
            if candidate == digits
            or (
                candidate.startswith(("sh", "sz", "bj"))
                and candidate[2:] == digits
            )
        ]
        if not matches:
            resolved.append(raw)
            continue
        if len(matches) > 1:
            raise ValueError(
                f"代码 {raw} 对应多个跟踪标的，请使用规范化代码: "
                + ", ".join(sorted(matches))
            )
        resolved.append(matches[0])
    return resolved
