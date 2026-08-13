"""数据采集应用服务

采集源由 ``run_collection_source`` 逐项执行；市场兼容聚合器和派生物化
保留各自的专用入口。

每个 use case 内部调对应的 domain Aggregator (BaseAggregator 子类),
返回 CollectionResult dto。

R4: 加 prometheus metrics 上报 (collection_duration / collection_saved)
"""
import asyncio
import time

import redis

from src.application.dto.collection_dto import CollectionResult
from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL
from src.infrastructure.observability import get_logger, record_collection
from src.infrastructure.persistence.repositories.collection_state_repository_impl import (
    CollectionStateRepositoryImpl,
)

logger = get_logger(__name__)


async def _run(aggregator_name: str, agg_class) -> CollectionResult:
    """通用采集 use case 包装: 计时 + 上报 metrics"""
    t0 = time.time()
    agg = agg_class()
    try:
        result = await agg.tick() or {}
    finally:
        duration = time.time() - t0
    saved = result.get("total_saved", 0)
    record_collection(aggregator_name, duration, saved)
    new_ids = list(getattr(agg, "last_saved_ids")) if hasattr(agg, "last_saved_ids") else None
    return CollectionResult(
        aggregator=aggregator_name,
        sources_run=result.get("sources_run", 0),
        total_saved=saved,
        new_ids=new_ids,
    )


def _collection_aggregator_class(aggregator_name: str):
    """Resolve a persisted collection domain to its aggregator implementation."""
    if aggregator_name == "news":
        from src.domain.collection.services.news import NewsAggregator
        return NewsAggregator
    if aggregator_name == "fund_flow":
        from src.domain.collection.services.fund_flow import FundFlowAggregator
        return FundFlowAggregator
    if aggregator_name == "sentiment":
        from src.domain.collection.services.sentiment import SentimentAggregator
        return SentimentAggregator
    if aggregator_name == "macro":
        from src.domain.collection.services.macro import MacroAggregator
        return MacroAggregator
    raise ValueError(f"不支持的采集领域: {aggregator_name}")


class CollectionAppService:
    """数据采集 use case 入口"""

    async def run_collection_source(
        self,
        aggregator_name: str,
        source_name: str,
        *,
        state_override: dict | None = None,
        persist_checkpoint: bool = True,
    ) -> CollectionResult:
        """运行一个 JetTask 调度项对应的单一数据源。"""
        started_at = time.time()
        aggregator = _collection_aggregator_class(aggregator_name)()
        result = await aggregator.run_source(
            source_name,
            state_override=state_override,
            persist_checkpoint=persist_checkpoint,
        )
        duration = time.time() - started_at
        saved = int(result.get("saved_count") or 0)
        metric_name = f"{aggregator_name}.{source_name}"
        record_collection(metric_name, duration, saved)
        new_ids = (
            list(getattr(aggregator, "last_saved_ids"))
            if hasattr(aggregator, "last_saved_ids")
            else None
        )
        return CollectionResult(
            aggregator=aggregator_name,
            sources_run=1,
            total_saved=saved,
            new_ids=new_ids,
            source_name=source_name,
            fetched_count=int(result.get("fetched_count") or 0),
            valid_count=int(result.get("valid_count") or 0),
            checkpoint_before=result.get("checkpoint_before") or {},
            checkpoint_after=result.get("checkpoint_after") or {},
        )

    async def run_scheduled_collection_source(
        self,
        aggregator_name: str,
        source_name: str,
    ) -> CollectionResult | None:
        """Run incremental Schedule work; active backfills are owned by the chain."""
        state = await asyncio.to_thread(
            CollectionStateRepositoryImpl().get,
            aggregator_name,
            source_name,
        )
        if state and state.get("mode") == "backfill":
            # Historical replay owns the persisted cursor, but it must not
            # suspend the live feed for days or weeks. Fetch the current edge
            # with an incremental view and update execution freshness only;
            # the backfill chain keeps its original cursor in the same row.
            live_state = {
                **state,
                "mode": "incremental",
                "target_time": None,
                "cursor": None,
                "backfill_status": state.get("backfill_status"),
                "last_success_at": None,
            }
            return await self.run_collection_source(
                aggregator_name,
                source_name,
                state_override=live_state,
                persist_checkpoint=False,
            )
        return await self.run_collection_source(aggregator_name, source_name)

    async def run_watchlist_instrument_collection(
        self,
        codes: list[str],
        *,
        scope: str = "bootstrap",
    ) -> dict:
        """Immediately collect selected watchlist instruments."""

        from src.domain.collection.services.fund_flow import FundFlowAggregator

        normalized_codes = [
            code
            for code in dict.fromkeys(str(code).strip().lower() for code in codes)
            if code
        ]
        if not normalized_codes:
            return {
                "requested_codes": [],
                "collected_codes": [],
                "rows": 0,
                "saved": 0,
            }

        results = await asyncio.gather(
            *[
                self._collect_watchlist_code(
                    code,
                    FundFlowAggregator,
                    scope,
                )
                for code in normalized_codes
            ],
            return_exceptions=True,
        )
        collected_codes: list[str] = []
        no_new_data_codes: list[str] = []
        partial_codes: list[str] = []
        rows = 0
        saved = 0
        skipped_codes: list[str] = []
        errors: dict[str, str] = {}
        for code, result in zip(normalized_codes, results):
            if isinstance(result, Exception):
                errors[code] = f"{type(result).__name__}: {result}"
                continue
            if result.get("status") == "locked":
                skipped_codes.append(code)
                continue
            collected_codes.extend(result.get("collected_codes") or [])
            no_new_data_codes.extend(
                result.get("no_new_data_codes") or []
            )
            if str((result.get("outcomes") or {}).get(code, "")).startswith(
                "partial"
            ):
                partial_codes.append(code)
            rows += int(result.get("rows") or 0)
            saved += int(result.get("saved") or 0)
        if errors:
            raise RuntimeError(
                "watchlist 标的采集失败: "
                + "; ".join(f"{code}={error}" for code, error in errors.items())
            )
        return {
            "requested_codes": normalized_codes,
            "collected_codes": sorted(set(collected_codes)),
            "no_new_data_codes": sorted(set(no_new_data_codes)),
            "partial_codes": sorted(set(partial_codes)),
            "skipped_locked_codes": skipped_codes,
            "rows": rows,
            "saved": saved,
        }

    async def scan_due_watchlist_instruments(self) -> dict:
        """Dispatch one independent task for every due watchlist instrument."""

        from src.application.services.watchlist_service import WatchlistService
        from src.infrastructure.tasks.jettask_dispatcher import (
            send_watchlist_instrument_collection,
        )

        due_items = await asyncio.to_thread(
            WatchlistService().list_due_realtime
        )
        if not due_items:
            return {"due": 0, "dispatched": 0, "event_ids": []}
        from src.infrastructure import clients

        market_names = {
            _watchlist_market_name(item.code) for item in due_items
        }
        session_results = await asyncio.gather(
            *[
                clients.market_calendar.get_market_session(market)
                for market in sorted(market_names)
            ],
            return_exceptions=True,
        )
        open_markets = {
            market
            for market, result in zip(sorted(market_names), session_results)
            if isinstance(result, dict)
            and result.get("status") == "ok"
            and (result.get("data") or {}).get("market_session") == "open"
        }
        due_items = [
            item
            for item in due_items
            if _watchlist_market_name(item.code) in open_markets
        ]
        if not due_items:
            return {
                "due": 0,
                "dispatched": 0,
                "event_ids": [],
                "reason": "tracked_markets_closed",
            }
        client = redis.from_url(REDIS_URL, decode_responses=True)
        leased_codes: list[str] = []
        try:
            for item in due_items:
                interval = max(
                    30,
                    int(
                        (item.config or {}).get(
                            "realtime_interval_seconds",
                            60,
                        )
                    ),
                )
                acquired = await asyncio.to_thread(
                    client.set,
                    f"{JETTASK_PREFIX}:dispatch:watchlist:{item.code}",
                    "1",
                    nx=True,
                    ex=max(30, interval),
                )
                if acquired:
                    leased_codes.append(item.code)
            event_ids = await send_watchlist_instrument_collection(
                leased_codes,
                scope="realtime",
            )
            return {
                "due": len(due_items),
                "dispatched": len(leased_codes),
                "codes": leased_codes,
                "event_ids": event_ids,
            }
        except Exception:
            if leased_codes:
                await asyncio.to_thread(
                    client.delete,
                    *[
                        f"{JETTASK_PREFIX}:dispatch:watchlist:{code}"
                        for code in leased_codes
                    ],
                )
            raise
        finally:
            client.close()

    async def dispatch_watchlist_scope(self, scope: str) -> dict:
        """Dispatch daily or reference collection for every enabled item."""

        if scope not in {"daily", "reference"}:
            raise ValueError("scope 必须是 daily 或 reference")
        from src.application.services.watchlist_service import WatchlistService
        from src.infrastructure.tasks.jettask_dispatcher import (
            send_watchlist_instrument_collection,
        )

        items = await asyncio.to_thread(
            WatchlistService().list_all,
            True,
        )
        codes = [item.code for item in items]
        event_ids = await send_watchlist_instrument_collection(
            codes,
            scope=scope,
        )
        return {
            "scope": scope,
            "dispatched": len(codes),
            "codes": codes,
            "event_ids": event_ids,
        }

    @staticmethod
    async def _collect_watchlist_code(
        code: str,
        aggregator_class,
        scope: str,
    ) -> dict:
        from src.infrastructure.persistence.repositories import (
            CollectionRunRepository,
        )

        runs = CollectionRunRepository()
        run_id = await asyncio.to_thread(
            runs.start,
            task_name="collect_watchlist_instruments",
            source_name=code,
            details={"scope": scope},
        )
        client = redis.from_url(REDIS_URL, decode_responses=True)
        lock = client.lock(
            f"{JETTASK_PREFIX}:lock:watchlist:{code}",
            timeout=600,
            blocking_timeout=0,
            thread_local=False,
        )
        acquired = await asyncio.to_thread(lock.acquire, blocking=False)
        if not acquired:
            client.close()
            await asyncio.to_thread(
                runs.finish,
                run_id,
                status="skipped",
                skipped_count=1,
                details={"scope": scope, "reason": "instrument_locked"},
            )
            return {"status": "locked", "code": code}

        stop_renewal = asyncio.Event()

        async def renew_lock() -> None:
            while True:
                try:
                    await asyncio.wait_for(stop_renewal.wait(), timeout=30)
                    return
                except TimeoutError:
                    extended = await asyncio.to_thread(
                        lock.extend,
                        600,
                        replace_ttl=True,
                    )
                    if not extended:
                        raise RuntimeError(
                            f"watchlist {code} 采集锁续租失败"
                        )

        renewal_task = asyncio.create_task(renew_lock())
        try:
            result = await aggregator_class().collect_watchlist_codes(
                [code],
                force=True,
                scope=scope,
            )
            outcome = str(
                (result.get("outcomes") or {}).get(code) or "failed"
            )
            if outcome == "failed":
                diagnostic = (result.get("diagnostics") or {}).get(code) or {}
                failures = diagnostic.get("failed_dimensions") or {}
                message = "; ".join(
                    f"{dimension}={error}"
                    for dimension, error in sorted(failures.items())
                )
                raise RuntimeError(
                    f"{code} 所有采集维度失败"
                    + (f": {message}" if message else "")
                )
            run_status = (
                "partial_success"
                if outcome.startswith("partial")
                else "success"
            )
            await asyncio.to_thread(
                runs.finish,
                run_id,
                status=run_status,
                fetched_count=int(result.get("rows") or 0),
                valid_count=int(result.get("rows") or 0),
                saved_count=int(result.get("saved") or 0),
                details={
                    "scope": scope,
                    "outcome": outcome,
                    "diagnostics": (
                        (result.get("diagnostics") or {}).get(code) or {}
                    ),
                },
            )
            return result
        except Exception as exc:
            await asyncio.to_thread(
                runs.finish,
                run_id,
                status="failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
                details={"scope": scope},
            )
            raise
        finally:
            stop_renewal.set()
            await renewal_task
            try:
                await asyncio.to_thread(lock.release)
            finally:
                client.close()
    async def run_market_collection(self) -> CollectionResult:
        from src.domain.collection.services.market import MarketAggregator
        return await _run("market", MarketAggregator)

    async def materialize_sentiment_signal(self, trade_date: str | None = None) -> CollectionResult:
        """物化 L2 情绪信号到 ft_sentiment_signal 快照表

        盘后定时调用（cron 30 15 * * 1-5），也可手动指定日期回填。
        """
        from datetime import date as date_type
        from src.domain.collection.services.sentiment import SentimentAggregator

        t0 = time.time()
        agg = SentimentAggregator()
        target = date_type.fromisoformat(trade_date) if trade_date else None
        result = await agg.materialize_snapshot(target)
        duration = time.time() - t0
        record_collection("sentiment_signal", duration, 1)
        return CollectionResult(
            aggregator="sentiment_signal",
            sources_run=1,
            total_saved=1,
        )


def _watchlist_market_name(code: str) -> str:
    normalized = str(code or "").strip().lower()
    if normalized.startswith("hk"):
        return "hk"
    if normalized.startswith("us"):
        return "us"
    return "cn"
