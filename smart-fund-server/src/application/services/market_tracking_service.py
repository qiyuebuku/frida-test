"""Application service for Agent-driven instrument tracking and market reads."""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timezone
from typing import Any

from src.application.services.watchlist_service import WatchlistService
from src.domain.collection.watchlist_instrument import is_exchange_traded_fund
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

DEFAULT_REFRESH_WAIT_SECONDS = 100.0
DEFAULT_REFRESH_POLL_SECONDS = 1.0
DEFAULT_FRESHNESS_SECONDS = 1800
REALTIME_FRESHNESS_SECONDS = 180

REALTIME_DATA_TYPES = {
    "quote",
    "realtime",
    "minute_data",
    "stock_flow",
}

CORE_DATA_TYPES_BY_INSTRUMENT = {
    "stock": {"quote", "kline"},
    "fund": {"nav"},
    "index": {"quote", "kline"},
}

SUPPORTED_DATA_TYPES_BY_INSTRUMENT = {
    "stock": {
        "stock_flow",
        "quote",
        "minute_data",
        "kline",
        "valuation",
        "guba_posts",
        "plates",
    },
    "fund": {
        "nav",
        "realtime",
        "nav_technical",
        "performance",
        "flow_trend",
        "periodic_rate",
        "profit_contribution",
        "nav_sina",
        "holdings",
        "scale",
        "holder_ratio",
        "dividend",
        "year_return",
        "max_drawdown",
        "holding_overview",
        "fund_detail",
        "style_preference",
        "asset_allocation",
        "position_detail",
        "trade_rule",
        "guba_posts",
        "manager_info",
        "hk_quote",
        "us_quote",
        "hk_us_kline",
    },
    "index": {"quote", "minute_data", "kline"},
}

EXCHANGE_TRADED_FUND_DATA_TYPES = {
    "stock_flow",
    "quote",
    "minute_data",
    "kline",
}


class MarketTrackingService:
    def __init__(
        self,
        *,
        watchlist_service: WatchlistService | None = None,
        data_repository: WatchlistDataRepositoryImpl | None = None,
        refresh_wait_seconds: float = DEFAULT_REFRESH_WAIT_SECONDS,
        refresh_poll_seconds: float = DEFAULT_REFRESH_POLL_SECONDS,
    ) -> None:
        self._watchlist = watchlist_service or WatchlistService()
        self._data = data_repository or WatchlistDataRepositoryImpl()
        self._refresh_wait_seconds = max(0.0, float(refresh_wait_seconds))
        self._refresh_poll_seconds = max(0.001, float(refresh_poll_seconds))

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
        initial_items, initial_statuses = await self._read_snapshot(
            normalized_codes,
            selected_types,
        )
        explicit_types = bool(data_types)
        initial_grouped = _group_by_code(normalized_codes, initial_items)
        assessments = {
            code: _assess_freshness(
                status=status,
                rows=initial_grouped.get(code, []),
                selected_types=selected_types,
                explicit_types=explicit_types,
            )
            for code, status in zip(normalized_codes, initial_statuses)
        }
        refresh_codes = [
            code
            for code, status in zip(normalized_codes, initial_statuses)
            if status is not None
            and status.enabled
            and assessments[code]["is_stale"]
        ]

        refresh_started = time.monotonic()
        event_ids: list[str] = []
        completed_codes: set[str] = set()
        failed_codes: dict[str, str] = {}
        if refresh_codes:
            try:
                event_ids = await send_watchlist_instrument_collection(refresh_codes)
                completed_codes, failed_codes = await self._wait_for_refresh(
                    refresh_codes,
                    {
                        code: status
                        for code, status in zip(normalized_codes, initial_statuses)
                        if code in refresh_codes and status is not None
                    },
                )
            except Exception as exc:
                failed_codes = {
                    code: f"{type(exc).__name__}: {exc}"
                    for code in refresh_codes
                }

        refresh_waited_seconds = round(time.monotonic() - refresh_started, 3)
        observed_items = initial_items
        final_statuses = initial_statuses
        if refresh_codes:
            observed_items, final_statuses = await self._read_snapshot(
                normalized_codes,
                selected_types,
            )
        observed_grouped = _group_by_code(normalized_codes, observed_items)
        final_grouped = {
            code: (
                observed_grouped.get(code, [])
                if code in completed_codes
                else initial_grouped.get(code, [])
            )
            for code in normalized_codes
        }

        instruments = []
        event_id_by_code = dict(zip(refresh_codes, event_ids))
        for code, initial_status, final_status in zip(
            normalized_codes,
            initial_statuses,
            final_statuses,
        ):
            original_assessment = assessments[code]
            rows = final_grouped.get(code, [])
            final_assessment = _assess_freshness(
                status=final_status,
                rows=rows,
                selected_types=selected_types,
                explicit_types=explicit_types,
            )
            freshness = _build_refresh_result(
                code=code,
                initial_status=initial_status,
                original_assessment=original_assessment,
                final_assessment=final_assessment,
                refresh_requested=code in refresh_codes,
                refresh_completed=code in completed_codes,
                refresh_error=failed_codes.get(code, ""),
                event_id=event_id_by_code.get(code, ""),
                waited_seconds=refresh_waited_seconds if code in refresh_codes else 0.0,
                wait_limit_seconds=self._refresh_wait_seconds,
            )
            instruments.append(
                {
                    "code": code,
                    "tracking": final_status.to_dict() if final_status else None,
                    "freshness": freshness,
                    "latest": rows,
                }
            )
        return {
            "operation": "market_instrument_open",
            "instruments": instruments,
        }

    async def _read_snapshot(
        self,
        codes: list[str],
        data_types: list[str],
    ) -> tuple[list[dict], list[Any]]:
        items = await asyncio.to_thread(
            self._data.query_latest_by_codes,
            codes,
            data_types,
        )
        statuses = await asyncio.gather(
            *[
                asyncio.to_thread(self._watchlist.get, code)
                for code in codes
            ]
        )
        return items, list(statuses)

    async def _wait_for_refresh(
        self,
        codes: list[str],
        initial_statuses: dict[str, Any],
    ) -> tuple[set[str], dict[str, str]]:
        if self._refresh_wait_seconds <= 0:
            return set(), {}

        pending = set(codes)
        completed: set[str] = set()
        failed: dict[str, str] = {}
        deadline = time.monotonic() + self._refresh_wait_seconds
        while pending and time.monotonic() < deadline:
            pending_codes = list(pending)
            statuses = await asyncio.gather(
                *[
                    asyncio.to_thread(self._watchlist.get, code)
                    for code in pending_codes
                ]
            )
            for code, status in zip(pending_codes, statuses):
                if status is None:
                    continue
                baseline = initial_statuses[code]
                if _is_later(status.last_success_at, baseline.last_success_at):
                    completed.add(code)
                    pending.remove(code)
                    continue
                if (
                    status.last_error
                    and _is_later(status.last_run_at, baseline.last_run_at)
                ):
                    failed[code] = status.last_error
                    pending.remove(code)
            if pending:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(
                        min(self._refresh_poll_seconds, remaining)
                    )
        return completed, failed

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


def _group_by_code(
    codes: list[str],
    rows: list[dict],
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {code: [] for code in codes}
    for row in rows:
        grouped.setdefault(str(row.get("code") or ""), []).append(row)
    return grouped


def _assess_freshness(
    *,
    status: Any,
    rows: list[dict],
    selected_types: list[str],
    explicit_types: bool,
) -> dict[str, Any]:
    if status is None:
        return {
            "is_stale": True,
            "reasons": ["instrument_not_tracked"],
            "missing_data_types": list(selected_types) if explicit_types else [],
            "unsupported_data_types": [],
            "freshness_seconds": DEFAULT_FRESHNESS_SECONDS,
        }

    configured_interval = int(
        (status.config or {}).get("interval") or DEFAULT_FRESHNESS_SECONDS
    )
    available_types = {
        str(row.get("data_type") or "")
        for row in rows
        if row.get("data_type")
    }
    supported_types = _supported_data_types(status.type, status.code)
    requested_types = set(selected_types)
    unsupported_types = sorted(
        requested_types - supported_types
        if explicit_types
        else set()
    )
    expected_types = (
        requested_types & supported_types
        if explicit_types
        else _core_data_types(status.type, status.code)
    )
    freshness_seconds = _freshness_seconds(
        configured_interval=configured_interval,
        expected_types=requested_types & supported_types,
    )
    missing_types = sorted(expected_types - available_types)
    now = datetime.now(timezone.utc)
    reasons: list[str] = []
    if not status.enabled:
        reasons.append("tracking_disabled")
    if missing_types:
        reasons.append("data_incomplete")

    last_success_at = _as_utc(status.last_success_at)
    if last_success_at is None:
        reasons.append("never_collected")
    else:
        age_seconds = max(
            0.0,
            (now - last_success_at).total_seconds(),
        )
        if age_seconds > freshness_seconds:
            reasons.append("collection_stale")

    return {
        "is_stale": bool(reasons),
        "reasons": reasons,
        "missing_data_types": missing_types,
        "unsupported_data_types": unsupported_types,
        "freshness_seconds": freshness_seconds,
    }


def _core_data_types(instrument_type: str, code: str) -> set[str]:
    core = set(
        CORE_DATA_TYPES_BY_INSTRUMENT.get(
            str(instrument_type or "").strip().lower(),
            set(),
        )
    )
    if instrument_type == "fund" and is_exchange_traded_fund(code):
        core.update({"quote", "kline"})
    return core


def _supported_data_types(instrument_type: str, code: str) -> set[str]:
    supported = set(
        SUPPORTED_DATA_TYPES_BY_INSTRUMENT.get(
            str(instrument_type or "").strip().lower(),
            set(),
        )
    )
    if instrument_type == "fund" and is_exchange_traded_fund(code):
        supported.update(EXCHANGE_TRADED_FUND_DATA_TYPES)
    return supported


def _freshness_seconds(
    *,
    configured_interval: int,
    expected_types: set[str],
) -> int:
    threshold = max(1, int(configured_interval))
    if expected_types & REALTIME_DATA_TYPES:
        return min(threshold, REALTIME_FRESHNESS_SECONDS)
    return max(threshold, DEFAULT_FRESHNESS_SECONDS)


def _build_refresh_result(
    *,
    code: str,
    initial_status: Any,
    original_assessment: dict[str, Any],
    final_assessment: dict[str, Any],
    refresh_requested: bool,
    refresh_completed: bool,
    refresh_error: str,
    event_id: str,
    waited_seconds: float,
    wait_limit_seconds: float,
) -> dict[str, Any]:
    if initial_status is None:
        status = "unavailable_untracked"
    elif not initial_status.enabled:
        status = "unavailable_disabled"
    elif (
        not refresh_requested
        and final_assessment.get("unsupported_data_types")
    ):
        status = "unsupported_data_types"
    elif not refresh_requested:
        status = "fresh"
    elif refresh_error:
        status = "refresh_failed"
    elif not refresh_completed:
        status = "refresh_timeout"
    elif final_assessment["is_stale"]:
        status = "refreshed_incomplete"
    elif final_assessment.get("unsupported_data_types"):
        status = "refreshed_with_unsupported_data_types"
    else:
        status = "refreshed"

    stale_data_returned = status in {
        "refresh_failed",
        "refresh_timeout",
        "refreshed_incomplete",
        "unavailable_disabled",
        "unavailable_untracked",
    }
    result = {
        "status": status,
        "is_stale": final_assessment["is_stale"],
        "stale_data_returned": stale_data_returned,
        "refresh_triggered": refresh_requested,
        "waited_seconds": waited_seconds,
        "freshness_seconds": final_assessment["freshness_seconds"],
        "reasons": final_assessment["reasons"],
        "missing_data_types": final_assessment["missing_data_types"],
        "unsupported_data_types": final_assessment.get(
            "unsupported_data_types",
            [],
        ),
    }
    if refresh_requested:
        result["trigger_reasons"] = original_assessment["reasons"]
    if event_id:
        result["collection_event_id"] = event_id
    if refresh_error:
        result["refresh_error"] = refresh_error
    if status == "refresh_timeout":
        result["message"] = (
            f"{code} 的即时采集在 {wait_limit_seconds:.0f} 秒内未完成，"
            "当前返回的是刷新前旧数据。"
        )
    elif status == "refresh_failed":
        result["message"] = f"{code} 即时采集失败，当前返回的是刷新前旧数据。"
    elif status == "refreshed_incomplete":
        result["message"] = (
            f"{code} 即时采集已完成，但请求的数据仍不完整；"
            "返回当前可用数据并标记为旧数据。"
        )
    elif status in {
        "unsupported_data_types",
        "refreshed_with_unsupported_data_types",
    }:
        result["message"] = (
            f"{code} 不支持请求中的部分数据维度，未将这些维度视为数据过期。"
        )
    return result


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_later(value: Any, baseline: Any) -> bool:
    current = _as_utc(value)
    previous = _as_utc(baseline)
    if current is None:
        return False
    return previous is None or current > previous


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
