"""Application service for Agent-driven instrument tracking and market reads."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Any

from src.application.services.watchlist_service import WatchlistService
from src.application.services.market_evidence_locator import (
    MarketEvidenceIdentity,
    encode_market_evidence_locator,
    historical_analogue_evidence_locator,
    technical_state_evidence_locator,
)
from src.application.services.market_history_analysis import (
    historical_analogues,
    technical_state,
)
from src.domain.collection.watchlist_instrument import (
    is_exchange_traded_fund,
    normalize_instrument,
)
from src.domain.collection.watchlist_snapshot_projection import (
    MARKET_SNAPSHOT_DATA_TYPES,
)
from src.infrastructure import clients
from src.infrastructure.persistence.repositories import (
    InstrumentDataRepository,
    MarketSnapshotRepository,
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

# These Observatory series are persisted in ft_market_snapshots but are not
# watchlist projection types.  They still belong to the Agent's historical
# market reader; routing them through InstrumentDataRepository silently returns
# an empty result even when the requested trading date exists in PostgreSQL.
AGENT_HISTORY_SNAPSHOT_DATA_TYPES = MARKET_SNAPSHOT_DATA_TYPES | {
    "ths_sector_flow",
    "ths_sector_hot",
    "ths_sector_ranking",
}

DEFAULT_REFRESH_WAIT_SECONDS = 100.0
DEFAULT_REFRESH_POLL_SECONDS = 1.0
DEFAULT_FRESHNESS_SECONDS = 1800
REALTIME_FRESHNESS_SECONDS_BY_PRIORITY = {
    "critical": 90,
    "standard": 120,
    "low": 360,
}

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

FundIdentityResolver = Callable[[str], Awaitable[dict[str, str]]]


class MarketTrackingService:
    def __init__(
        self,
        *,
        watchlist_service: WatchlistService | None = None,
        data_repository: InstrumentDataRepository | None = None,
        snapshot_repository: MarketSnapshotRepository | None = None,
        fund_identity_resolver: FundIdentityResolver | None = None,
        refresh_wait_seconds: float = DEFAULT_REFRESH_WAIT_SECONDS,
        refresh_poll_seconds: float = DEFAULT_REFRESH_POLL_SECONDS,
    ) -> None:
        self._watchlist = watchlist_service or WatchlistService()
        self._data = data_repository or InstrumentDataRepository()
        self._snapshots = snapshot_repository or MarketSnapshotRepository()
        self._fund_identity_resolver = (
            fund_identity_resolver or _resolve_fund_identity
        )
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
            if value["source"] == "agent" and not str(
                value.get("reason") or ""
            ).strip():
                raise ValueError("Agent 添加跟踪标的时必须提供 reason")
            identity = normalize_instrument(
                code=value.get("code", ""),
                instrument_type=value.get("type", "auto"),
                name=value.get("name", ""),
            )
            if identity.instrument_type == "fund":
                official = await self._fund_identity_resolver(identity.code)
                supplied_name = identity.name
                official_name = str(official.get("name") or "").strip()
                if not official_name:
                    raise ValueError(
                        f"基金 {identity.code} 未返回可验证的官方名称"
                    )
                if supplied_name and not _fund_names_compatible(
                    supplied_name,
                    official_name,
                ):
                    raise ValueError(
                        f"基金代码与名称不一致：{identity.code} 实际为"
                        f"“{official_name}”，不是“{supplied_name}”"
                    )
                value["code"] = identity.code
                value["type"] = "fund"
                value["name"] = official_name
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
        cutoff_at: datetime | None = None,
        allow_refresh: bool = True,
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
        effective_now = cutoff_at or datetime.now(timezone.utc)
        initial_items, initial_statuses = await self._read_snapshot(
            normalized_codes,
            selected_types,
            cutoff_at=cutoff_at,
        )
        explicit_types = bool(data_types)
        initial_grouped = _group_by_code(normalized_codes, initial_items)
        assessments = {
            code: _assess_freshness(
                status=status,
                rows=initial_grouped.get(code, []),
                selected_types=selected_types,
                explicit_types=explicit_types,
                now=effective_now,
            )
            for code, status in zip(normalized_codes, initial_statuses)
        }
        refresh_codes = (
            [
                code
                for code, status in zip(normalized_codes, initial_statuses)
                if status is not None
                and status.enabled
                and assessments[code]["is_stale"]
            ]
            if allow_refresh
            else []
        )

        refresh_started = time.monotonic()
        event_ids: list[str] = []
        completed_codes: set[str] = set()
        failed_codes: dict[str, str] = {}
        if refresh_codes:
            try:
                event_ids = await send_watchlist_instrument_collection(
                    refresh_codes,
                    scope="realtime",
                )
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
                cutoff_at=cutoff_at,
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
                now=effective_now,
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
            "cutoff_at": cutoff_at,
            "realtime_refresh_requested": bool(refresh_codes),
            "instruments": instruments,
        }

    async def _read_snapshot(
        self,
        codes: list[str],
        data_types: list[str],
        *,
        cutoff_at: datetime | None,
    ) -> tuple[list[dict], list[Any]]:
        profile_types = [
            data_type
            for data_type in data_types
            if data_type not in MARKET_SNAPSHOT_DATA_TYPES
        ]
        market_types = [
            data_type
            for data_type in data_types
            if data_type in MARKET_SNAPSHOT_DATA_TYPES
        ]
        items = []
        if profile_types:
            profile_kwargs = {}
            if cutoff_at is not None:
                profile_kwargs["cutoff_at"] = cutoff_at
            items = await asyncio.to_thread(
                self._data.query_latest_by_codes,
                codes,
                profile_types,
                **profile_kwargs,
            )
        if market_types:
            snapshot_kwargs = {
                "subject_ids": codes,
                "data_types": market_types,
            }
            if cutoff_at is not None:
                snapshot_kwargs["cutoff_at"] = cutoff_at
            snapshots = await asyncio.to_thread(
                self._snapshots.query_latest,
                **snapshot_kwargs,
            )
            items = _merge_latest_snapshots(items, snapshots)
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
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        normalized_input_code = str(code or "").strip().lower()
        normalized_data_type = _history_data_type(
            normalized_input_code,
            str(data_type or "").strip(),
        )
        if not normalized_input_code:
            raise ValueError("code 不能为空")
        if not normalized_data_type:
            raise ValueError("data_type 不能为空")
        start = date.fromisoformat(date_start) if date_start else None
        end = date.fromisoformat(date_end) if date_end else None
        if cutoff_at is not None:
            cutoff_date = cutoff_at.date()
            end = min(end, cutoff_date) if end is not None else cutoff_date
        if start and end and start > end:
            raise ValueError("date_start 不能晚于 date_end")
        watchlist_items = await asyncio.to_thread(
            self._watchlist.list_all,
            False,
        )
        normalized_code = _history_subject_id(
            normalized_input_code,
            normalized_data_type,
        ) or _resolve_tracked_codes(
            [normalized_input_code],
            [item.code for item in watchlist_items],
        )[0]
        if normalized_data_type in AGENT_HISTORY_SNAPSHOT_DATA_TYPES:
            snapshots = await asyncio.to_thread(
                self._snapshots.query_history,
                subject_id=normalized_code,
                data_type=normalized_data_type,
                date_start=start,
                date_end=end,
                cutoff_at=cutoff_at,
                limit=limit,
            )
            items = [
                {
                    "id": item["id"],
                    "code": normalized_code,
                    "data_type": item["data_type"],
                    "trade_date": item["trade_date"],
                    "observed_at": item["observed_at"],
                    "fetched_at": item["fetched_at"],
                    "freshness_status": item["freshness_status"],
                    "data": item["data"],
                }
                for item in snapshots
            ]
        else:
            items = await asyncio.to_thread(
                self._data.query_history,
                code=normalized_code,
                data_type=normalized_data_type,
                date_start=start,
                date_end=end,
                cutoff_at=cutoff_at,
                limit=limit,
            )
        return {
            "operation": "market_instrument_history",
            "code": normalized_code,
            "data_type": normalized_data_type,
            "cutoff_at": cutoff_at,
            "items": items,
            "count": len(items),
            "series_semantics": _history_series_semantics(normalized_data_type),
            "window_evidence": _history_window_evidence(
                items,
                subject_id=normalized_code,
                data_type=normalized_data_type,
            ),
        }

    async def instrument_technical_state(
        self,
        *,
        code: str,
        data_type: str,
        benchmark_code: str = "",
        benchmark_data_type: str = "",
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        subject = await self.instrument_history(
            code=code, data_type=data_type, limit=120, cutoff_at=cutoff_at,
        )
        benchmark = None
        if benchmark_code:
            benchmark = await self.instrument_history(
                code=benchmark_code,
                data_type=benchmark_data_type or data_type,
                limit=120,
                cutoff_at=cutoff_at,
            )
        result = technical_state(
            _history_items_with_locators(subject),
            subject_id=subject["code"],
            benchmark_items=(
                _history_items_with_locators(benchmark) if benchmark else None
            ),
        )
        response = {
            "operation": "market_technical_state_open",
            "data_type": subject["data_type"],
            "benchmark_subject_id": benchmark["code"] if benchmark else None,
            **result,
        }
        response["analysis_evidence_locator"] = technical_state_evidence_locator(response)
        return response

    async def instrument_historical_analogues(
        self,
        *,
        code: str,
        data_type: str,
        benchmark_code: str = "",
        benchmark_data_type: str = "",
        forward_window: int = 3,
        min_samples: int = 8,
        match_distance_threshold: float = 2.5,
        search_limit: int = 500,
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        limit = max(60, min(int(search_limit), 1000))
        subject = await self.instrument_history(
            code=code, data_type=data_type, limit=limit, cutoff_at=cutoff_at,
        )
        benchmark = None
        if benchmark_code:
            benchmark = await self.instrument_history(
                code=benchmark_code,
                data_type=benchmark_data_type or data_type,
                limit=limit,
                cutoff_at=cutoff_at,
            )
        result = historical_analogues(
            _history_items_with_locators(subject),
            subject_id=subject["code"],
            benchmark_items=(
                _history_items_with_locators(benchmark) if benchmark else None
            ),
            forward_window=forward_window,
            min_samples=min_samples,
            match_distance_threshold=match_distance_threshold,
        )
        response = {
            "operation": "market_historical_analogue_open",
            "data_type": subject["data_type"],
            "benchmark_subject_id": benchmark["code"] if benchmark else None,
            **result,
        }
        response["analysis_evidence_locator"] = (
            historical_analogue_evidence_locator(response)
        )
        return response


def _history_subject_id(code: str, data_type: str) -> str | None:
    """Resolve provider-native index/sector codes to snapshot subject IDs."""

    if data_type == "ths_sector_daily":
        if code.startswith("ths:"):
            return code
        if code.startswith("886"):
            return f"ths:concept:{code}"
        if code.startswith("881"):
            return f"ths:industry:{code}"
    if data_type == "ths_index_daily":
        if code.startswith("cn:index:"):
            return code
        return f"cn:index:{code}"
    return None


def _history_data_type(code: str, data_type: str) -> str:
    """Choose the canonical persisted series for provider-native identities.

    The live index quote type is a snapshot rather than a daily history series.
    Correcting that common combination here keeps the public tool contract
    semantic instead of making the model memorize storage implementation names.
    """

    if code.startswith("cn:index:") and data_type == "ths_cn_index_quote":
        return "ths_index_daily"
    if (
        code.startswith(("ths:concept:", "ths:industry:"))
        and data_type == "ths_sector_quote"
    ):
        return "ths_sector_daily"
    return data_type


def _history_series_semantics(data_type: str) -> dict[str, str] | None:
    if data_type in {"ths_index_daily", "ths_sector_daily"}:
        return {
            "price_fields": "open/high/low/close are provider index points",
            "volume_field": "provider-native raw volume; unit is not established",
        }
    if data_type == "northbound_turnover":
        return {
            "turnover": "成交额，不表示方向性净买入或净卖出",
        }
    return None


def _history_window_evidence(
    items: list[dict[str, Any]],
    *,
    subject_id: str,
    data_type: str,
) -> dict[str, Any]:
    """Expose exact bar locators used by deterministic window statistics."""

    rows = [
        item for item in items
        if isinstance(item.get("data"), dict)
        and isinstance(item["data"].get("close"), (int, float))
        and item.get("id") is not None
    ]
    if not rows:
        return {}

    def locator(item: dict[str, Any]) -> str:
        return encode_market_evidence_locator(MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": item["id"]},
            data_type=data_type,
            subject_id=subject_id,
            fact_time=str(item.get("observed_at") or item.get("trade_date") or ""),
        ))

    result: dict[str, Any] = {
        "latest": {"trade_date": rows[0].get("trade_date"), "evidence_locator": locator(rows[0])},
    }
    for window in (5, 20, 60, 120):
        if len(rows) < window:
            continue
        sample = rows[:window]
        high = max(sample, key=lambda item: float(item["data"]["close"]))
        low = min(sample, key=lambda item: float(item["data"]["close"]))
        baseline = sample[-1]
        result[f"{window}_bars"] = {
            "baseline": {"trade_date": baseline.get("trade_date"), "evidence_locator": locator(baseline)},
            "close_high": {"trade_date": high.get("trade_date"), "evidence_locator": locator(high)},
            "close_low": {"trade_date": low.get("trade_date"), "evidence_locator": locator(low)},
        }
    return result


def _history_items_with_locators(history: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not history:
        return []
    subject_id = str(history.get("code") or "")
    data_type = str(history.get("data_type") or "")
    projected: list[dict[str, Any]] = []
    for item in history.get("items") or []:
        current = dict(item)
        if item.get("id") is not None:
            current["evidence_locator"] = encode_market_evidence_locator(
                MarketEvidenceIdentity(
                    kind="snapshot",
                    domain="market_snapshot",
                    identity={"id": item["id"]},
                    data_type=data_type,
                    subject_id=subject_id,
                    fact_time=str(item.get("observed_at") or item.get("trade_date") or ""),
                )
            )
        projected.append(current)
    return projected


def create_market_tracking_service() -> MarketTrackingService:
    return MarketTrackingService()


async def _resolve_fund_identity(code: str) -> dict[str, str]:
    client = clients.eastmoney
    if client is None:
        raise RuntimeError("基金身份校验客户端尚未初始化")
    response = await client.search_fund(code, limit=10)
    if response.get("status_code") != 0:
        raise RuntimeError(
            f"基金 {code} 身份校验失败："
            f"{response.get('status_msg') or '上游接口失败'}"
        )
    for item in response.get("data") or []:
        if str(item.get("code") or "").strip() == code:
            return {
                "code": code,
                "name": str(item.get("name") or "").strip(),
            }
    raise ValueError(f"基金代码 {code} 不存在或当前数据源无法识别")


def _fund_names_compatible(supplied: str, official: str) -> bool:
    supplied_key = _fund_name_key(supplied)
    official_key = _fund_name_key(official)
    if not supplied_key or not official_key:
        return False
    return (
        supplied_key == official_key
        or supplied_key in official_key
        or official_key in supplied_key
    )


def _fund_name_key(value: str) -> str:
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())
    compact = re.sub(
        r"(?:基金|混合|股票|债券|指数|联接|发起式?|"
        r"qdii|fof|lof|etf|型|类)",
        "",
        compact,
    )
    return re.sub(r"[a-z]$", "", compact)


def _group_by_code(
    codes: list[str],
    rows: list[dict],
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {code: [] for code in codes}
    for row in rows:
        grouped.setdefault(str(row.get("code") or ""), []).append(row)
    return grouped


def _merge_latest_snapshots(
    projected_rows: list[dict],
    snapshots: list[dict],
) -> list[dict]:
    merged = {
        (str(row.get("code") or ""), str(row.get("data_type") or "")): row
        for row in projected_rows
    }
    for snapshot in snapshots:
        code = str(snapshot.get("subject_id") or "")
        data_type = str(snapshot.get("data_type") or "")
        merged[(code, data_type)] = {
            "id": snapshot.get("id"),
            "code": code,
            "data_type": data_type,
            "trade_date": snapshot.get("trade_date"),
            "data": snapshot.get("data"),
            "observed_at": snapshot.get("observed_at"),
            "fetched_at": snapshot.get("fetched_at"),
            "updated_at": snapshot.get("fetched_at"),
            "freshness_status": snapshot.get("freshness_status"),
        }
    return sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("code") or ""),
            str(item.get("data_type") or ""),
        ),
    )


def _assess_freshness(
    *,
    status: Any,
    rows: list[dict],
    selected_types: list[str],
    explicit_types: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    if status is None:
        return {
            "is_stale": True,
            "reasons": ["instrument_not_tracked"],
            "missing_data_types": list(selected_types) if explicit_types else [],
            "unsupported_data_types": [],
            "freshness_seconds": DEFAULT_FRESHNESS_SECONDS,
        }

    priority = str(
        (status.config or {}).get("priority") or "standard"
    ).strip().lower()
    configured_interval = int(
        (status.config or {}).get("realtime_interval_seconds")
        or DEFAULT_FRESHNESS_SECONDS
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
        priority=priority,
    )
    missing_types = sorted(expected_types - available_types)
    now = now or datetime.now(timezone.utc)
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
    priority: str,
) -> int:
    threshold = max(1, int(configured_interval))
    if expected_types & REALTIME_DATA_TYPES:
        return max(
            threshold,
            REALTIME_FRESHNESS_SECONDS_BY_PRIORITY.get(priority, 120),
        )
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
