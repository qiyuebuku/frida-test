"""Scheduled market observations and official ETF daily facts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import redis

from src.infrastructure.clients.market_contracts import (
    MarketDataStatus,
    market_result,
)
from src.infrastructure.clients.ths_native_stream import (
    THS_NATIVE_EVENT_STREAM_HEALTH_KEY,
    THS_NATIVE_STREAM_HEALTH_KEY,
)
from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL
from src.infrastructure.db import checkpoint_store
from src.infrastructure.observability import get_logger
from src.infrastructure.persistence.repositories import (
    CollectionRunRepository,
    EtfDailyShareRepository,
    MarketSnapshotRepository,
    NewsRepositoryImpl,
)
from src.application.services.china_exchange_calendar_service import (
    ChinaExchangeCalendarService,
)


logger = get_logger(__name__)
CN_TIMEZONE = ZoneInfo("Asia/Shanghai")
REALTIME_TYPES = frozenset(
    {"market_breadth", "sector_quote", "sector_flow", "index_quote",
     "futures_quote", "forex_quote", "stock_change",
     "etf_estimated_net_inflow", "market_anomaly", "call_auction",
     "market_capital", "market_sentiment", "futures_intraday",
     "forex_intraday", "northbound_capital", "stock_ranking",
     "ths_stock_anomaly", "ths_sector_anomaly", "ths_large_order",
     "ths_market_profile",
     "ths_sector_hot", "ths_sector_ranking", "ths_sector_flow",
     "ths_etf_zone", "ths_etf_hot_ranking", "ths_etf_ranking_universe",
     "ths_futures_zone", "ths_futures_module", "ths_gold_zone",
     "ths_gold_module"}
    | {"ths_us_market_zone", "ths_us_market_module"}
)

THS_EVENT_STREAMS = (
    ("stock_events", "ths_stock_anomaly", "instrument", "stock"),
    ("sector_events", "ths_sector_anomaly", "sector", "sector"),
    ("large_order_events", "ths_large_order", "instrument", "large_order"),
)
THS_EVENT_LABELS = {
    "592572": "特大主动买",
    "592574": "特大主动卖",
    "1074269404": "急速拉升",
    "1074269405": "猛烈打压",
    "133990": "挂单拉升",
    "133991": "挂单打压",
}

THS_CN_INDEX_DAILY_INSTRUMENTS = (
    ("1A0001", "16", "000001", "上证指数"),
    ("399001", "32", "399001", "深证成指"),
    ("399006", "32", "399006", "创业板指"),
    ("899050", "144", "899050", "北证50"),
    ("1B0680", "16", "000680", "科创综指"),
    ("1B0688", "16", "000688", "科创50"),
    ("1B0510", "16", "000510", "中证A500"),
    ("1B0300", "16", "000300", "沪深300"),
    ("1B0852", "16", "000852", "中证1000"),
    ("1B0016", "16", "000016", "上证50"),
    ("1B0905", "16", "000905", "中证500"),
    ("399330", "32", "399330", "深证100"),
    ("1B0698", "16", "000698", "科创100"),
    ("883957", "48", "399303", "国证2000"),
)


def _ths_native_stream_is_active() -> bool:
    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        return bool(client.exists(THS_NATIVE_STREAM_HEALTH_KEY))
    except Exception:
        return False
    finally:
        client.close()


def _ths_native_event_stream_is_active() -> bool:
    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        return bool(client.exists(THS_NATIVE_EVENT_STREAM_HEALTH_KEY))
    except Exception:
        return False
    finally:
        client.close()


class CollectionSkipped(RuntimeError):
    pass


def _should_bootstrap_ths_events(
    *,
    force_boundary: bool,
    has_event_snapshot: bool,
) -> bool:
    """Bootstrap missing event data without rewriting a closed-day snapshot."""

    return force_boundary or not has_event_snapshot


@dataclass
class ObservationBatch:
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    etf_daily_shares: list[dict[str, Any]] = field(default_factory=list)
    news_items: list[dict[str, Any]] = field(default_factory=list)
    projections: list[tuple[str, dict[str, Any], int]] = field(
        default_factory=list
    )
    fetched_count: int = 0
    skipped_count: int = 0
    status: str = "success"
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def valid_count(self) -> int:
        return len(self.snapshots) + len(self.etf_daily_shares) + len(self.news_items)


class MarketObservationService:
    """Public market observation use cases.

    Every method is independently schedulable and uses its own source lock.
    """

    data_domain = "market_observation"
    SOURCE_CONFIGS = {
        "market_breadth": {
            "interval": 30,
            "lock_timeout_seconds": 120,
            "default_mode": "incremental",
        },
        "stock_rankings": {
            "interval": 120,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
        "stock_dynamic_groups": {
            "interval": 180,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
        "stock_changes": {
            "interval": 30,
            "lock_timeout_seconds": 120,
            "default_mode": "incremental",
        },
        "ths_market_events": {
            "interval": 30,
            "lock_timeout_seconds": 180,
            "default_mode": "incremental",
        },
        "ths_market_context": {
            "interval": 60,
            "lock_timeout_seconds": 180,
            "default_mode": "incremental",
        },
        "sector_market": {
            "interval": 60,
            "lock_timeout_seconds": 180,
            "default_mode": "incremental",
        },
        "sector_fund_flow": {
            "interval": 60,
            "lock_timeout_seconds": 180,
            "default_mode": "incremental",
        },
        "ths_sector_core": {
            # A full snapshot performs 25 serialized VM bridge calls and takes
            # about four minutes. The cadence must stay above that runtime or
            # a single-worker deployment will accumulate stale queued jobs.
            "interval": 300,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
        "ths_sector_signals": {
            "interval": 300,
            "lock_timeout_seconds": 900,
            "default_mode": "incremental",
        },
        "ths_sector_references": {
            "interval": 300,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
        "cross_market": {
            "interval": 60,
            "lock_timeout_seconds": 180,
            "default_mode": "incremental",
        },
        "etf_estimated_flow": {
            "interval": 60,
            "lock_timeout_seconds": 180,
            "default_mode": "incremental",
        },
        "ths_etf_zone": {
            "interval": 60,
            "lock_timeout_seconds": 180,
            "default_mode": "incremental",
        },
        "ths_futures_zone": {
            "interval": 120,
            "lock_timeout_seconds": 300,
            "default_mode": "incremental",
        },
        "ths_gold_zone": {
            "interval": 120,
            "lock_timeout_seconds": 300,
            "default_mode": "incremental",
        },
        "ths_us_overview": {
            "interval": 60,
            "lock_timeout_seconds": 90,
            "default_mode": "incremental",
        },
        "ths_us_sectors": {
            "interval": 120,
            "lock_timeout_seconds": 90,
            "default_mode": "incremental",
        },
        "ths_us_stock_rankings": {
            "interval": 60,
            "lock_timeout_seconds": 120,
            "default_mode": "incremental",
        },
        "ths_us_etf_sectors": {
            "interval": 120,
            "lock_timeout_seconds": 120,
            "default_mode": "incremental",
        },
        "pboc_rate_liquidity": {
            "interval": 3600,
            "lock_timeout_seconds": 300,
            "default_mode": "incremental",
        },
        "ths_index_sentiment": {
            "interval": 900,
            "lock_timeout_seconds": 180,
            "default_mode": "incremental",
        },
        "market_daily_bars": {
            "interval": 86400,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
        "market_reference_data": {
            "interval": 86400,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
        "market_daily_catchup": {
            "interval": 86400,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
        "etf_daily_shares": {
            "interval": 86400,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
        "market_valuation": {
            "interval": 86400,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
        "bond_index": {
            "interval": 86400,
            "lock_timeout_seconds": 600,
            "default_mode": "incremental",
        },
    }

    def __init__(
        self,
        *,
        snapshot_repository: MarketSnapshotRepository | None = None,
        etf_share_repository: EtfDailyShareRepository | None = None,
        run_repository: CollectionRunRepository | None = None,
        news_repository: NewsRepositoryImpl | None = None,
    ) -> None:
        self._snapshots = snapshot_repository or MarketSnapshotRepository()
        self._etf_shares = etf_share_repository or EtfDailyShareRepository()
        self._runs = run_repository or CollectionRunRepository()
        self._news = news_repository or NewsRepositoryImpl()

    @classmethod
    def init_state(cls) -> None:
        for source_name, config in cls.SOURCE_CONFIGS.items():
            checkpoint_store.ensure_initialized(
                cls.data_domain,
                source_name,
                {
                    "mode": config.get("default_mode", "incremental"),
                    "target_time": None,
                    "newest_time": None,
                    "oldest_time": None,
                    "backfill_status": None,
                    "cursor": None,
                },
                config,
            )

    async def collect_market_breadth(
        self,
        *,
        force_boundary: bool = False,
    ) -> dict[str, Any]:
        return await self._execute(
            task_name=(
                "collect_market_boundary_snapshot"
                if force_boundary
                else "collect_market_breadth_snapshot"
            ),
            source_name="market_breadth",
            bucket_seconds=30,
            collector=lambda: self._collect_market_breadth(
                force_boundary=force_boundary
            ),
        )

    async def collect_stock_change_events(
        self,
        *,
        force_boundary: bool = False,
    ) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_stock_change_events",
            source_name="stock_changes",
            bucket_seconds=1,
            collector=lambda: self._collect_stock_change_events(
                force_boundary=force_boundary
            ),
        )

    async def collect_stock_rankings(
        self,
        *,
        force_boundary: bool = False,
    ) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_stock_rankings",
            source_name="stock_rankings",
            bucket_seconds=30,
            collector=lambda: self._collect_stock_rankings(
                force_boundary=force_boundary
            ),
        )

    async def collect_stock_dynamic_groups(
        self,
        *,
        force_boundary: bool = False,
    ) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_stock_dynamic_groups",
            source_name="stock_dynamic_groups",
            bucket_seconds=60,
            collector=lambda: self._collect_stock_dynamic_groups(
                force_boundary=force_boundary
            ),
        )

    async def collect_ths_market_events(
        self,
        *,
        force_boundary: bool = False,
    ) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_market_events",
            source_name="ths_market_events",
            bucket_seconds=30,
            collector=lambda: self._collect_ths_market_events(
                force_boundary=force_boundary
            ),
        )

    async def collect_ths_market_context(
        self,
        *,
        force_boundary: bool = False,
    ) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_market_context",
            source_name="ths_market_context",
            bucket_seconds=60,
            collector=lambda: self._collect_ths_market_context(
                force_boundary=force_boundary
            ),
        )

    async def collect_ths_market_profile(self) -> dict[str, Any]:
        """Persist the three THS market-home comparison cards as one snapshot."""
        return await self._execute(
            task_name="collect_ths_market_profile",
            source_name="ths_market_profile",
            bucket_seconds=60,
            collector=self._collect_ths_market_profile,
        )

    async def collect_sector_market(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_sector_market_snapshot",
            source_name="sector_market",
            bucket_seconds=60,
            collector=self._collect_sector_market,
        )

    async def collect_sector_fund_flow(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_sector_fund_flow_snapshot",
            source_name="sector_fund_flow",
            bucket_seconds=60,
            collector=self._collect_sector_fund_flow,
        )

    async def collect_ths_sector_fragment(
        self,
        kind: str,
        classification: str,
        metric: str | None = None,
    ) -> dict[str, Any]:
        fragment_key = "_".join(
            item for item in (kind, classification, metric) if item
        )
        bucket_seconds = 120 if kind == "ranking" else 60
        return await self._execute(
            task_name=f"collect_ths_sector_fragment_{fragment_key}",
            source_name=f"ths_sector_{fragment_key}",
            bucket_seconds=bucket_seconds,
            collector=lambda: self._collect_ths_sector_fragment(
                kind,
                classification,
                metric,
            ),
        )

    async def collect_ths_sector_signal_fragment(
        self,
        kind: str,
        sector_type: str | None = None,
        metric: str | None = None,
    ) -> dict[str, Any]:
        fragment_key = "_".join(
            item for item in (kind, sector_type, metric) if item
        )
        return await self._execute(
            task_name=f"collect_ths_sector_signal_{fragment_key}",
            source_name=f"ths_sector_signal_{fragment_key}",
            bucket_seconds=300,
            collector=lambda: self._collect_ths_sector_signal_fragment(
                kind,
                sector_type,
                metric,
            ),
        )

    async def collect_ths_sector_references(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_sector_reference_snapshot_v2",
            source_name="ths_sector_references",
            bucket_seconds=86400,
            collector=self._collect_ths_sector_references,
        )

    async def collect_cross_market(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_cross_market_snapshot",
            source_name="cross_market",
            bucket_seconds=60,
            collector=self._collect_cross_market,
        )

    async def collect_etf_estimated_net_inflow(
        self,
        *,
        force_boundary: bool = False,
    ) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_etf_estimated_net_inflow",
            source_name="etf_estimated_flow",
            bucket_seconds=60,
            collector=lambda: self._collect_etf_estimated_net_inflow(
                force_boundary=force_boundary
            ),
        )

    async def collect_ths_etf_zone(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_etf_zone",
            source_name="ths_etf_zone",
            bucket_seconds=60,
            collector=self._collect_ths_etf_zone,
        )

    async def collect_ths_futures_zone(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_futures_zone",
            source_name="ths_futures_zone",
            bucket_seconds=60,
            collector=self._collect_ths_futures_zone,
        )

    async def collect_ths_futures_fragment(
        self,
        kind: str,
        group: str | None = None,
    ) -> dict[str, Any]:
        fragment_id = f"{kind}:{group}" if group else kind
        return await self._execute(
            task_name="collect_ths_futures_fragment",
            source_name=f"ths_futures_fragment:{fragment_id}",
            bucket_seconds=60,
            collector=lambda: self._collect_ths_futures_fragment(kind, group),
        )

    async def collect_ths_gold_zone(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_gold_zone",
            source_name="ths_gold_zone",
            bucket_seconds=60,
            collector=self._collect_ths_gold_zone,
        )

    async def collect_ths_us_market_zone(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_us_market_zone",
            source_name="ths_us_market_zone",
            bucket_seconds=60,
            collector=self._collect_ths_us_market_zone,
        )

    async def collect_ths_us_overview(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_us_overview", source_name="ths_us_overview",
            bucket_seconds=30,
            collector=lambda: self._collect_ths_us_module(
                "overview", "get_native_us_overview_snapshot", 90
            ),
        )

    async def collect_ths_us_sectors(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_us_sectors", source_name="ths_us_sectors",
            bucket_seconds=60,
            collector=lambda: self._collect_ths_us_module(
                "sectors", "get_native_us_sector_snapshot", 180
            ),
        )

    async def collect_ths_us_stock_rankings(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_us_stock_rankings",
            source_name="ths_us_stock_rankings", bucket_seconds=60,
            collector=self._collect_ths_us_stock_rankings,
        )

    async def collect_ths_us_etf_sectors(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_us_etf_sectors",
            source_name="ths_us_etf_sectors", bucket_seconds=60,
            collector=self._collect_ths_us_etf_sectors,
        )

    async def collect_etf_daily_shares(
        self,
        *,
        trade_date: str | None = None,
    ) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_etf_daily_shares",
            source_name="etf_daily_shares",
            bucket_seconds=86400,
            collector=lambda: self._collect_etf_daily_shares(trade_date),
        )

    async def collect_pboc_rate_liquidity(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_pboc_rate_liquidity",
            source_name="pboc_rate_liquidity",
            bucket_seconds=3600,
            collector=self._collect_pboc_rate_liquidity,
        )

    async def collect_ths_index_sentiment(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_ths_index_sentiment",
            source_name="ths_index_sentiment",
            bucket_seconds=900,
            collector=self._collect_ths_index_sentiment,
        )

    async def collect_market_daily_bars(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_market_daily_bars",
            source_name="market_daily_bars",
            bucket_seconds=86400,
            collector=self._collect_market_daily_bars,
        )

    async def collect_market_reference_data(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_market_reference_data",
            source_name="market_reference_data",
            bucket_seconds=86400,
            collector=self._collect_market_reference_data,
        )

    async def collect_market_daily_catchup(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_market_daily_catchup",
            source_name="market_daily_catchup",
            bucket_seconds=86400,
            collector=self._collect_market_daily_catchup,
        )

    async def collect_market_valuation(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_market_valuation",
            source_name="market_valuation",
            bucket_seconds=86400,
            collector=self._collect_market_valuation,
        )

    async def collect_bond_index(self) -> dict[str, Any]:
        return await self._execute(
            task_name="collect_bond_index",
            source_name="bond_index",
            bucket_seconds=86400,
            collector=self._collect_bond_index,
        )

    async def _execute(
        self,
        *,
        task_name: str,
        source_name: str,
        bucket_seconds: int,
        collector: Callable[[], Awaitable[ObservationBatch]],
    ) -> dict[str, Any]:
        self.init_state()
        state = checkpoint_store.get(self.data_domain, source_name) or {}
        run_id = self._runs.start(
            task_name=task_name,
            source_name=source_name,
            checkpoint_before=_checkpoint_view(state),
        )
        checkpoint_store.mark_started(
            task_id=task_name,
            aggregator=self.data_domain,
            source_name=source_name,
            task_type="callback" if source_name.startswith("ths_") else "pull",
        )
        if state.get("enabled") is False:
            self._runs.finish(run_id, status="skipped", skipped_count=1)
            return {"status": "skipped", "reason": "source_disabled"}

        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        lock_timeout = max(
            60,
            int(
                self.SOURCE_CONFIGS.get(source_name, {}).get(
                    "lock_timeout_seconds",
                    300,
                )
            ),
        )
        lock = redis_client.lock(
            f"{JETTASK_PREFIX}:lock:{self.data_domain}:{source_name}",
            timeout=lock_timeout,
            blocking_timeout=0,
            thread_local=False,
        )
        acquired = await asyncio.to_thread(lock.acquire, blocking=False)
        if not acquired:
            redis_client.close()
            self._runs.finish(run_id, status="skipped", skipped_count=1)
            return {"status": "skipped", "reason": "source_locked"}

        stop_renewal = asyncio.Event()

        async def renew_lock() -> None:
            renewal_interval = max(15, min(60, lock_timeout // 3))
            while True:
                try:
                    await asyncio.wait_for(
                        stop_renewal.wait(),
                        timeout=renewal_interval,
                    )
                    return
                except TimeoutError:
                    extended = await asyncio.to_thread(
                        lock.extend,
                        lock_timeout,
                        replace_ttl=True,
                    )
                    if not extended:
                        logger.error(
                            "市场采集锁续租失败 source=%s",
                            source_name,
                        )
                        return

        renewal_task = asyncio.create_task(renew_lock())
        try:
            batch = await collector()
            gap = _unrecoverable_gap(
                source_name=source_name,
                state=state,
                interval_seconds=int(
                    self.SOURCE_CONFIGS.get(source_name, {}).get(
                        "interval",
                        bucket_seconds,
                    )
                ),
            )
            if gap:
                batch.details["unrecoverable_gap"] = gap
            saved = 0
            if batch.snapshots:
                saved += await asyncio.to_thread(
                    self._snapshots.upsert_batch,
                    batch.snapshots,
                )
            if batch.etf_daily_shares:
                saved += await asyncio.to_thread(
                    self._etf_shares.upsert_batch,
                    batch.etf_daily_shares,
                )
            new_news_ids: list[int] = []
            if batch.news_items:
                new_news_ids = await asyncio.to_thread(
                    self._news.upsert_batch_returning_ids,
                    batch.news_items,
                )
                saved += len(new_news_ids)
                batch.details["new_news_ids"] = new_news_ids
                batch.details["news_item_count"] = len(batch.news_items)
            newest = _newest_business_time(batch)
            checkpoint = {
                "mode": "incremental",
                "target_time": state.get("target_time"),
                "newest_time": newest,
                "oldest_time": state.get("oldest_time") or newest,
                "backfill_status": state.get("backfill_status"),
                "cursor": None,
            }
            checkpoint_store.update_success(
                self.data_domain,
                source_name,
                checkpoint,
                saved_count=saved,
                task_id=task_name,
                task_type="callback" if source_name.startswith("ths_") else "pull",
            )
            source_times = [
                item["observed_at"]
                for item in batch.snapshots
                if item.get("observed_at") is not None
            ]
            self._runs.finish(
                run_id,
                status=batch.status,
                fetched_count=batch.fetched_count,
                valid_count=batch.valid_count,
                saved_count=saved,
                skipped_count=batch.skipped_count,
                source_time_min=min(source_times) if source_times else None,
                source_time_max=max(source_times) if source_times else None,
                checkpoint_after=checkpoint,
                details=batch.details,
            )
            return {
                "status": batch.status,
                "source_name": source_name,
                "fetched_count": batch.fetched_count,
                "valid_count": batch.valid_count,
                "saved_count": saved,
                **batch.details,
            }
        except CollectionSkipped as exc:
            checkpoint_store.update_success(
                self.data_domain,
                source_name,
                None,
                saved_count=0,
                task_id=task_name,
                task_type="callback" if source_name.startswith("ths_") else "pull",
            )
            self._runs.finish(
                run_id,
                status="skipped",
                skipped_count=1,
                details={"reason": str(exc)},
            )
            return {"status": "skipped", "reason": str(exc)}
        except Exception as exc:
            checkpoint_store.update_failure(
                self.data_domain,
                source_name,
                f"{type(exc).__name__}: {exc}",
                task_id=task_name,
                task_type="callback" if source_name.startswith("ths_") else "pull",
            )
            self._runs.finish(
                run_id,
                status="failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise
        finally:
            stop_renewal.set()
            await renewal_task
            try:
                await asyncio.to_thread(lock.release)
            except redis.exceptions.LockError:
                logger.warning(
                    "市场采集锁已失效或不再属于当前任务 source=%s",
                    source_name,
                )
            finally:
                redis_client.close()

    async def _collect_market_breadth(
        self,
        *,
        force_boundary: bool,
    ) -> ObservationBatch:
        from src.infrastructure import clients

        session = await clients.market_calendar.get_market_session("cn")
        _require_cn_observation_window(session, force_boundary=force_boundary)
        response, turnover_comparison = await asyncio.gather(
            clients.eastmoney.get_market_breadth(),
            clients.eastmoney.get_market_intraday_turnover_comparison(),
        )
        _require_ok(response, "market breadth")
        comparison_status = turnover_comparison.get("status")
        if comparison_status == MarketDataStatus.OK.value:
            response["data"]["previous_same_time_turnover"] = (
                turnover_comparison["data"]
            )
        snapshot = _snapshot_from_response(
            response=response,
            data_type="market_breadth",
            subject_type="market",
            subject_id="cn:a_share",
            bucket_seconds=30,
        )
        return ObservationBatch(
            snapshots=[snapshot],
            projections=[("market_breadth", response, 90)],
            fetched_count=1,
            details={
                "market_session": (
                    (session.get("data") or {}).get("market_session")
                ),
                "force_boundary": force_boundary,
                "turnover_comparison_status": comparison_status,
                "turnover_comparison_message": turnover_comparison.get(
                    "message"
                ),
            },
        )

    async def _collect_stock_rankings(
        self,
        *,
        force_boundary: bool = False,
    ) -> ObservationBatch:
        from src.infrastructure import clients

        session = await clients.market_calendar.get_market_session("cn")
        modes = (
            "rise",
            "fall",
            "quick",
            "turnover",
            "large_order",
            "volume_ratio",
            "turnover_rate",
            "main_net_inflow",
            "amplitude",
        )
        latest_rankings = [
            await asyncio.to_thread(
                self._latest_bucket_at,
                "stock_ranking",
                mode,
            )
            for mode in modes
        ]
        available_latest = [item for item in latest_rankings if item is not None]
        latest_complete_ranking = min(available_latest) if available_latest else None
        recovery_fetch = _require_cn_series_observation_or_catchup(
            session,
            latest_bucket_at=latest_complete_ranking,
            force_boundary=force_boundary,
        )
        metric_keys = {
            "rise": "change_rate",
            "fall": "change_rate",
            "quick": "speed",
            "turnover": "turnover",
            "large_order": "large_order_ratio",
            "volume_ratio": "volume_ratio",
            "turnover_rate": "turnover_rate",
            "main_net_inflow": "main_net_inflow",
            "amplitude": "amplitude",
        }

        def is_valid(mode: str, response: dict[str, Any]) -> bool:
            stocks = (response.get("data") or {}).get("stocks") or []
            metadata = response.get("provider_metadata") or {}
            metric_key = metric_keys[mode]
            return (
                response.get("status") == "ok"
                and response.get("provider") == "ths_native"
                and metadata.get("channel")
                == "android_native_unified_request"
                and any(
                    row.get("code")
                    and row.get("name")
                    and row.get(metric_key) is not None
                    for row in stocks
                )
            )

        responses = []
        recovered_modes: list[str] = []
        for mode in modes:
            response = await clients.ths.get_native_stock_ranking(mode, 50)
            if not is_valid(mode, response):
                await asyncio.sleep(1.0)
                retry = await clients.ths.get_native_stock_ranking(mode, 50)
                if is_valid(mode, retry):
                    recovered_modes.append(mode)
                response = retry
            responses.append(response)
        snapshots: list[dict[str, Any]] = []
        projections: list[tuple[str, dict[str, Any], int]] = []
        empty_modes: list[str] = []
        for mode, response in zip(modes, responses, strict=True):
            stocks = (response.get("data") or {}).get("stocks") or []
            if not is_valid(mode, response):
                empty_modes.append(mode)
                continue
            snapshots.append(
                _snapshot_from_response(
                    response=response,
                    data_type="stock_ranking",
                    subject_type="ranking",
                    subject_id=mode,
                    bucket_seconds=30,
                )
            )
            projections.append((f"stock_ranking:{mode}", response, 604800))
        if not snapshots:
            raise CollectionSkipped("native stock rankings returned no rows")
        return ObservationBatch(
            snapshots=snapshots,
            projections=projections,
            fetched_count=len(responses),
            skipped_count=len(empty_modes),
            status="partial" if empty_modes else "success",
            details={
                "empty_modes": empty_modes,
                "recovered_modes": recovered_modes,
                "recovery_fetch": recovery_fetch,
            },
        )

    async def _collect_stock_dynamic_groups(
        self,
        *,
        force_boundary: bool = False,
    ) -> ObservationBatch:
        from src.infrastructure import clients

        # The App refreshes these rankings outside the A-share trading window,
        # including post-close and weekend recomputation.  The App response is
        # therefore the freshness authority; the local exchange calendar must
        # not suppress collection.
        featured_response = await clients.ths.get_native_stock_dynamic_groups(
            4,
            homepage_layout=True,
        )
        for label, response in (("featured", featured_response),):
            _require_ok(response, f"THS {label} stock dynamic groups")
            metadata = response.get("provider_metadata") or {}
            if (
                response.get("provider") != "ths_native"
                or metadata.get("channel") != "android_native_hurricane"
            ):
                raise RuntimeError(
                    f"THS {label} stock dynamic group response is not native"
                )

        featured_groups = (featured_response.get("data") or {}).get("groups") or []
        cached_candidates = _recent_stock_dynamic_candidate_groups(
            self._snapshots.list_latest(
                data_types=["stock_dynamic_group"],
                subject_type="ranking",
                limit=100,
            ),
            now=datetime.now(timezone.utc),
        )
        featured_codes = {
            str(group.get("data_code") or group.get("key") or "").strip()
            for group in featured_groups
            if isinstance(group, dict)
        }
        use_candidate_cache = bool(featured_codes) and featured_codes.issubset(
            cached_candidates
        )
        if use_candidate_cache:
            candidate_groups = list(cached_candidates.values())
        else:
            candidate_response = await clients.ths.get_native_stock_dynamic_groups(100)
            _require_ok(candidate_response, "THS candidate stock dynamic groups")
            candidate_metadata = candidate_response.get("provider_metadata") or {}
            if (
                candidate_response.get("provider") != "ths_native"
                or candidate_metadata.get("channel") != "android_native_hurricane"
            ):
                raise RuntimeError(
                    "THS candidate stock dynamic group response is not native"
                )
            candidate_groups = (
                (candidate_response.get("data") or {}).get("groups") or []
            )
        candidates_by_code = {
            str(group.get("data_code") or group.get("key") or "").strip(): group
            for group in candidate_groups
            if isinstance(group, dict)
        }
        groups: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        for featured_group in featured_groups:
            if not isinstance(featured_group, dict):
                continue
            data_code = str(
                featured_group.get("data_code")
                or featured_group.get("key")
                or ""
            ).strip()
            if not data_code:
                continue
            candidate_group = candidates_by_code.get(data_code) or {}
            featured_stocks = list(featured_group.get("stocks") or [])
            candidate_stocks = list(
                candidate_group.get("candidate_stocks")
                or candidate_group.get("stocks")
                or []
            )
            featured_stocks = _hydrate_featured_stocks(
                featured_stocks,
                candidate_stocks,
            )
            groups.append(
                {
                    **candidate_group,
                    **featured_group,
                    "stocks": featured_stocks,
                    "featured_stocks": featured_stocks,
                    "candidate_stocks": candidate_stocks,
                    "featured_count": len(featured_stocks),
                    "candidate_count": len(candidate_stocks),
                    "candidate_pool_complete": True,
                }
            )
            seen_codes.add(data_code)
        for data_code, candidate_group in candidates_by_code.items():
            if not data_code or data_code in seen_codes:
                continue
            candidate_stocks = list(
                candidate_group.get("candidate_stocks")
                or candidate_group.get("stocks")
                or []
            )
            groups.append(
                {
                    **candidate_group,
                    "stocks": [],
                    "featured_stocks": [],
                    "candidate_stocks": candidate_stocks,
                    "featured_count": 0,
                    "candidate_count": len(candidate_stocks),
                    "candidate_pool_complete": True,
                }
            )
        if not groups:
            raise CollectionSkipped("native stock dynamic groups returned no groups")

        snapshots: list[dict[str, Any]] = []
        projections: list[tuple[str, dict[str, Any], int]] = []
        empty_groups: list[str] = []
        for group in groups:
            data_code = str(
                group.get("data_code") or group.get("key") or ""
            ).strip()
            if not data_code:
                continue
            group_response = {**featured_response, "data": group}
            snapshots.append(
                _snapshot_from_response(
                    response=group_response,
                    data_type="stock_dynamic_group",
                    subject_type="ranking",
                    subject_id=data_code,
                    bucket_seconds=60,
                )
            )
            projections.append(
                (f"stock_dynamic_group:{data_code}", group_response, 604800)
            )
            if not group.get("featured_stocks") and not group.get("candidate_stocks"):
                empty_groups.append(data_code)
        if not snapshots:
            raise CollectionSkipped(
                "native stock dynamic groups contained no valid identities"
            )
        return ObservationBatch(
            snapshots=snapshots,
            projections=projections,
            fetched_count=len(groups),
            status="success",
            details={
                "empty_groups": empty_groups,
                "candidate_source": "cache" if use_candidate_cache else "upstream",
            },
        )

    async def _collect_stock_change_events(
        self,
        *,
        force_boundary: bool = False,
    ) -> ObservationBatch:
        from src.infrastructure import clients

        session = await clients.market_calendar.get_market_session("cn")
        _require_cn_stock_change_window(
            session,
            force_boundary=force_boundary,
        )
        raw = await clients.eastmoney.get_stock_changes("all", 5000)
        if raw.get("status_code") != 0:
            raise RuntimeError(
                raw.get("msg") or "stock change source failed"
            )
        changes = ((raw.get("data") or {}).get("changes") or [])
        now = datetime.now(timezone.utc)
        trade_date = now.astimezone(CN_TIMEZONE).date()
        snapshots = [
            _stock_change_snapshot(
                item,
                trade_date=trade_date,
                fetched_at=now,
            )
            for item in changes
            if item.get("code") and item.get("time")
        ]
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=len(changes),
            details={
                "market_session": (
                    (session.get("data") or {}).get("market_session")
                ),
                "event_count": len(snapshots),
                "source_total": (raw.get("data") or {}).get("total"),
                "force_boundary": force_boundary,
            },
        )

    async def _collect_ths_market_events(
        self,
        *,
        force_boundary: bool,
    ) -> ObservationBatch:
        from src.infrastructure import clients

        session = await clients.market_calendar.get_market_session("cn")
        has_event_snapshot = all(
            self._latest_bucket_at(data_type, subject_id) is not None
            for data_type, subject_id in (
                ("market_anomaly", "cn:a_share:ths_anomaly"),
                ("call_auction", "cn:a_share:call_auction"),
            )
        )
        session_data = session.get("data") or {}
        bootstrap = _should_bootstrap_ths_events(
            force_boundary=force_boundary,
            has_event_snapshot=has_event_snapshot,
        )
        _require_cn_stock_change_window(
            session,
            force_boundary=bootstrap,
        )
        now_cn = datetime.now(timezone.utc).astimezone(CN_TIMEZONE).time()
        event_stream_active = await asyncio.to_thread(
            _ths_native_event_stream_is_active
        )
        definitions = []
        event_sources = (
            [
                ("call_auction", "cn:a_share:call_auction"),
                ("market_anomaly", "cn:a_share:ths_anomaly"),
            ]
            if bootstrap
            else _ths_event_sources(now_cn)
        )
        trade_date = _parse_date(session_data.get("date"))
        if not session_data.get("is_trading_day"):
            trade_date = await _latest_cn_trading_date(
                clients.market_calendar
            )
        for data_type, subject_id in event_sources:
            if data_type == "call_auction":
                call = clients.ths.get_native_call_auction
            elif event_stream_active:
                continue
            else:
                call = clients.ths.get_native_market_anomalies
            definitions.append((data_type, subject_id, call))

        if not definitions:
            raise CollectionSkipped("ths_native_event_stream_owned")

        responses = []
        for _data_type, _subject_id, call in definitions:
            try:
                responses.append(await call())
            except Exception as exc:
                responses.append(exc)

        snapshots: list[dict[str, Any]] = []
        errors: list[str] = []
        for (data_type, subject_id, _call), response in zip(
            definitions,
            responses,
        ):
            if isinstance(response, Exception):
                errors.append(f"{data_type}:{type(response).__name__}")
                continue
            if response.get("status") not in {
                MarketDataStatus.OK.value,
                MarketDataStatus.EMPTY.value,
            }:
                errors.append(f"{data_type}:{response.get('status')}")
                continue
            response = {
                **response,
                "trade_date": response.get("trade_date") or trade_date,
                "data": {
                    **(response.get("data") or {}),
                    "trade_date": str(
                        response.get("trade_date") or trade_date or ""
                    ) or None,
                },
            }
            if data_type == "market_anomaly":
                event_snapshots = _ths_event_snapshots(response)
                snapshots.extend(event_snapshots)
                response_data = response.get("data") or {}
                response = {
                    **response,
                    "data": {
                        "count": response_data.get("count") or 0,
                        "trade_date": response_data.get("trade_date"),
                        "market_events": response_data.get("market_events") or [],
                        "curve": response_data.get("curve") or [],
                        "axis": response_data.get("axis") or {},
                        "stock_events": (
                            response_data.get("stock_events") or []
                        )[-2:],
                        "sector_events": (
                            response_data.get("sector_events") or []
                        )[-2:],
                        "large_order_events": (
                            response_data.get("large_order_events") or []
                        )[-3:],
                        "event_counts": {
                            key: len(response_data.get(key) or [])
                            for key, *_rest in THS_EVENT_STREAMS
                        },
                    },
                }
            snapshots.append(
                _snapshot_from_response(
                    response=response,
                    data_type=data_type,
                    subject_type="market",
                    subject_id=subject_id,
                    bucket_seconds=30,
                )
            )
        if not snapshots:
            raise RuntimeError(f"all THS event sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=sum(
                int(((item.get("data") or {}).get("count") or 0))
                for item in responses
                if isinstance(item, dict)
            ),
            status="partial_success" if errors else "success",
            details={
                "errors": errors,
                "force_boundary": force_boundary,
                "bootstrap": bootstrap,
                "event_snapshot_count": sum(
                    1
                    for item in snapshots
                    if item.get("data_type") in {
                        "ths_stock_anomaly",
                        "ths_sector_anomaly",
                        "ths_large_order",
                    }
                ),
                "market_session": (
                    (session.get("data") or {}).get("market_session")
                ),
            },
        )

    async def _collect_ths_market_profile(self) -> ObservationBatch:
        from src.infrastructure import clients

        profile_response, limit_response = await asyncio.gather(
            clients.ths.get_native_market_profile(),
            clients.ths.get_native_limit_comparison(),
            return_exceptions=True,
        )
        errors: list[str] = []
        profile: dict[str, Any] = {}
        limit_data: dict[str, Any] = {}
        if isinstance(profile_response, Exception):
            errors.append(f"profile:{type(profile_response).__name__}")
        elif profile_response.get("status") != MarketDataStatus.OK.value:
            errors.append(f"profile:{profile_response.get('status')}")
        else:
            profile = dict(profile_response.get("data") or {})
        if isinstance(limit_response, Exception):
            errors.append(f"limit:{type(limit_response).__name__}")
        elif limit_response.get("status") != MarketDataStatus.OK.value:
            errors.append(f"limit:{limit_response.get('status')}")
        else:
            limit_data = dict(limit_response.get("data") or {})
        if not profile and not limit_data:
            raise RuntimeError(f"THS market profile failed: {errors}")

        source = (
            profile_response
            if isinstance(profile_response, dict) and profile
            else limit_response
        )
        combined_response = {
            "market": "cn",
            "provider": "ths_native",
            "fetched_at": source.get("fetched_at"),
            "observed_at": source.get("observed_at"),
            "source_time": source.get("source_time"),
            "trade_date": source.get("trade_date"),
            "provider_metadata": {
                "channel": "android_native_market_profile",
            },
            "data": {
                "limit_up_count": limit_data.get("limit_up"),
                "limit_down_count": limit_data.get("limit_down"),
                "yesterday_limit": profile.get("yesterday_limit") or {},
                "cap_comparison": profile.get("cap_comparison") or {},
            },
        }
        return ObservationBatch(
            snapshots=[_snapshot_from_response(
                response=combined_response,
                data_type="ths_market_profile",
                subject_type="market",
                subject_id="cn:a_share:ths_market_profile",
                bucket_seconds=60,
            )],
            fetched_count=1,
            status="partial_success" if errors else "success",
            details={"errors": errors},
        )

    async def _collect_ths_market_context(
        self,
        *,
        force_boundary: bool,
    ) -> ObservationBatch:
        from src.infrastructure import clients

        session = await clients.market_calendar.get_market_session("cn")
        definitions = (
            (
                "market_capital",
                "market_capital",
                "cn:a_share:market_capital",
            ),
            (
                "market_temperature",
                "market_sentiment",
                "cn:a_share:ths_temperature",
            ),
        )
        latest_buckets = {
            subject_id: await asyncio.to_thread(
                self._latest_bucket_at,
                data_type,
                subject_id,
            )
            for _indicator, data_type, subject_id in definitions
        }
        native_skip_reason: str | None = None
        bootstrap_subjects: list[str] = []
        if await asyncio.to_thread(_ths_native_stream_is_active):
            native_skip_reason = "persistent_native_stream_active"
        else:
            try:
                bootstrap_subjects = _context_bootstrap_subjects(
                    session,
                    latest_buckets=latest_buckets,
                    force_boundary=force_boundary,
                )
            except CollectionSkipped as exc:
                native_skip_reason = str(exc)

        snapshots: list[dict[str, Any]] = []
        errors: list[str] = []
        if native_skip_reason is None:
            for indicator, data_type, subject_id in definitions:
                response = await clients.ths.get_native_realtime_indicator(
                    indicator
                )
                if response.get("status") != MarketDataStatus.OK.value:
                    errors.append(f"{indicator}:{response.get('status')}")
                    continue
                snapshots.extend(
                    _native_chart_snapshots(
                        response=response,
                        data_type=data_type,
                        subject_type="market",
                        subject_id=subject_id,
                        latest_bucket_at=latest_buckets[subject_id],
                    )
                )

            northbound_current = (
                await clients.ths.get_native_realtime_indicator(
                    "northbound_capital"
                )
            )
            if northbound_current.get("status") == MarketDataStatus.OK.value:
                current_payload = northbound_current.get("data") or {}
                current_points = current_payload.get("points") or []
                if current_points:
                    current_response = {
                        **northbound_current,
                        "data": {
                            **current_payload,
                            "points": current_points[-1:],
                        },
                    }
                    current_subject_id = "cn:northbound:ths"
                    current_data_type = "northbound_capital_current"
                    current_latest = await asyncio.to_thread(
                        self._latest_bucket_at,
                        current_data_type,
                        current_subject_id,
                    )
                    snapshots.extend(
                        _native_chart_snapshots(
                            response=current_response,
                            data_type=current_data_type,
                            subject_type="market",
                            subject_id=current_subject_id,
                            latest_bucket_at=current_latest,
                        )
                    )
            else:
                errors.append(
                    "northbound_capital:"
                    f"{northbound_current.get('status')}"
                )

        history_subject_id = "cn:northbound:turnover:ths"
        history_data_type = "northbound_turnover"
        history_latest = await asyncio.to_thread(
            self._latest_bucket_at,
            history_data_type,
            history_subject_id,
        )
        now_cn = datetime.now(timezone.utc).astimezone(CN_TIMEZONE)
        session_data = session.get("data") or {}
        should_refresh_history = (
            history_latest is None
            or force_boundary
            or (
                bool(session_data.get("is_trading_day"))
                and history_latest.astimezone(CN_TIMEZONE).date()
                < now_cn.date()
            )
        )
        if should_refresh_history:
            northbound_history = (
                await clients.ths.get_northbound_turnover_history()
            )
            if northbound_history.get("status") == MarketDataStatus.OK.value:
                snapshots.extend(
                    _native_chart_snapshots(
                        response=northbound_history,
                        data_type=history_data_type,
                        subject_type="market",
                        subject_id=history_subject_id,
                        latest_bucket_at=history_latest,
                    )
                )
            else:
                errors.append(
                    "northbound_turnover:"
                    f"{northbound_history.get('status')}"
                )
        if not snapshots and native_skip_reason:
            raise CollectionSkipped(native_skip_reason)
        if not snapshots and errors:
            raise RuntimeError(f"all THS context sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=len(snapshots),
            status="partial_success" if errors else "success",
            details={
                "errors": errors,
                "force_boundary": force_boundary,
                "market_session": (
                    (session.get("data") or {}).get("market_session")
                ),
                "bootstrap_subjects": bootstrap_subjects,
                "native_skip_reason": native_skip_reason,
            },
        )

    async def _collect_sector_market(self) -> ObservationBatch:
        from src.infrastructure import clients

        session = await clients.market_calendar.get_market_session("cn")
        _require_open_session(session)
        responses = await asyncio.gather(
            clients.ths.get_sector_snapshot("industry"),
            clients.sina.get_sector_ranking("concept", count=500),
            return_exceptions=True,
        )
        snapshots: list[dict[str, Any]] = []
        projections: list[tuple[str, dict[str, Any], int]] = []
        errors: list[str] = []
        for sector_type, response in zip(("industry", "concept"), responses):
            if isinstance(response, Exception):
                errors.append(f"{sector_type}:{type(response).__name__}")
                continue
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(f"{sector_type}:{response.get('status')}")
                continue
            sectors = ((response.get("data") or {}).get("sectors") or [])
            for sector in sectors:
                snapshots.append(
                    _sector_snapshot(
                        response=response,
                        sector=sector,
                        sector_type=sector_type,
                        data_type="sector_quote",
                        bucket_seconds=60,
                    )
                )
            projections.append(
                (f"sector_market_{sector_type}", response, 120)
            )
        if not snapshots:
            raise RuntimeError(f"all sector market sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            projections=projections,
            fetched_count=sum(
                ((item.get("data") or {}).get("count") or 0)
                for item in responses
                if isinstance(item, dict)
            ),
            status="partial_success" if errors else "success",
            details={"errors": errors},
        )

    async def _collect_sector_fund_flow(self) -> ObservationBatch:
        from src.infrastructure import clients

        session = await clients.market_calendar.get_market_session("cn")
        _require_open_session(session)
        responses = await asyncio.gather(
            clients.sina.get_sector_money_flow("industry", count=500),
            clients.sina.get_sector_money_flow("concept", count=500),
            return_exceptions=True,
        )
        snapshots: list[dict[str, Any]] = []
        projections: list[tuple[str, dict[str, Any], int]] = []
        errors: list[str] = []
        for sector_type, response in zip(("industry", "concept"), responses):
            if isinstance(response, Exception):
                errors.append(f"{sector_type}:{type(response).__name__}")
                continue
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(f"{sector_type}:{response.get('status')}")
                continue
            sectors = ((response.get("data") or {}).get("sectors") or [])
            for rank, sector in enumerate(sectors, start=1):
                payload = {**sector, "rank": rank}
                snapshots.append(
                    _sector_snapshot(
                        response=response,
                        sector=payload,
                        sector_type=sector_type,
                        data_type="sector_flow",
                        bucket_seconds=60,
                    )
                )
            projections.append(
                (f"sector_fund_flow_{sector_type}", response, 120)
            )
        if not snapshots:
            raise RuntimeError(f"all sector fund-flow sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            projections=projections,
            fetched_count=sum(
                ((item.get("data") or {}).get("count") or 0)
                for item in responses
                if isinstance(item, dict)
            ),
            status="partial_success" if errors else "success",
            details={"errors": errors},
        )

    async def _collect_ths_sector_fragment(
        self,
        kind: str,
        classification: str,
        metric: str | None,
    ) -> ObservationBatch:
        """Collect and persist one independently scheduled THS sector slice."""

        from src.infrastructure import clients

        if kind == "hot":
            latest_hot_rows = await asyncio.to_thread(
                self._snapshots.list_latest,
                data_types=["ths_sector_hot"],
                limit=10000,
            )
            latest_fetched_at = max(
                (
                    row.get("fetched_at")
                    for row in latest_hot_rows
                    if (row.get("data") or {}).get("sector_type")
                    == classification
                    and isinstance(row.get("fetched_at"), datetime)
                ),
                default=None,
            )
            now = datetime.now(timezone.utc)
            interval_seconds = _hot_sector_poll_interval_seconds(now)
            if (
                latest_fetched_at is not None
                and (now - latest_fetched_at.astimezone(timezone.utc)).total_seconds()
                < interval_seconds
            ):
                raise CollectionSkipped(
                    f"ths_hot_poll_not_due:{classification}:"
                    f"{interval_seconds}s"
                )
        else:
            session = await clients.market_calendar.get_market_session("cn")
            if kind == "flow":
                latest_flow_rows = await asyncio.to_thread(
                    self._snapshots.list_latest,
                    data_types=["ths_sector_flow"],
                    limit=10000,
                )
                latest_bucket_at = max(
                    (
                        row.get("bucket_at")
                        for row in latest_flow_rows
                        if (row.get("data") or {}).get("sector_type")
                        == classification
                        and row.get("bucket_at") is not None
                    ),
                    default=None,
                )
                _require_cn_series_observation_or_catchup(
                    session,
                    latest_bucket_at=latest_bucket_at,
                    force_boundary=False,
                )
            else:
                _require_open_session(session)
        if kind == "table":
            if classification not in {
                "all",
                "industry",
                "concept",
                "style",
                "region",
            }:
                raise ValueError("invalid THS sector-table classification")
            if metric is not None:
                raise ValueError("THS sector-table fragment does not accept metric")
            response = await clients.ths.get_native_sector_ranking_bundle(
                classification,
                50,
            )
            if response.get("status") != MarketDataStatus.OK.value:
                raise RuntimeError(
                    f"THS sector table failed: {classification}:"
                    f"{response.get('status')}"
                )
            rankings = ((response.get("data") or {}).get("rankings") or {})
            snapshots = []
            projections = []
            for ranking_metric in ("change", "speed", "volume_ratio"):
                sectors = rankings.get(ranking_metric) or []
                metric_response = {
                    **response,
                    "data": {
                        "classification": classification,
                        "metric": ranking_metric,
                        "count": len(sectors),
                        "sectors": sectors,
                    },
                }
                snapshots.extend(
                    _sector_snapshot(
                        response=metric_response,
                        sector=sector,
                        sector_type=classification,
                        data_type="ths_sector_ranking",
                        bucket_seconds=60,
                        identity_suffix=f"{classification}_{ranking_metric}",
                    )
                    for sector in sectors
                )
                projections.append(
                    (
                        f"ths_sector_ranking_{classification}_{ranking_metric}",
                        metric_response,
                        120,
                    )
                )
            if not snapshots:
                raise RuntimeError(
                    f"THS sector table is empty: {classification}"
                )
            return ObservationBatch(
                snapshots=snapshots,
                projections=projections,
                fetched_count=len(snapshots),
                status="success",
                details={
                    "source": "ths_app_vm",
                    "kind": kind,
                    "classification": classification,
                    "source_row_count": (response.get("data") or {}).get(
                        "source_row_count"
                    ),
                    "derived_metrics": ["change", "speed", "volume_ratio"],
                },
            )
        if kind == "hot":
            if classification not in {"concept", "industry", "index"}:
                raise ValueError("invalid THS hot-board classification")
            response = await clients.ths.get_native_hot_boards(classification, 50)
            data_type = "ths_sector_hot"
            identity_suffix = None
            projection_name = f"ths_sector_hot_{classification}"
        elif kind == "ranking":
            if classification not in {
                "all",
                "industry",
                "concept",
                "style",
                "region",
            }:
                raise ValueError("invalid THS sector-ranking classification")
            if metric not in {
                "change",
                "speed",
                "volume_ratio",
                "limit_up_count",
            }:
                raise ValueError("invalid THS sector-ranking metric")
            response = await clients.ths.get_native_sector_ranking(
                metric,
                50,
                classification,
            )
            data_type = "ths_sector_ranking"
            identity_suffix = f"{classification}_{metric}"
            projection_name = f"ths_sector_ranking_{classification}_{metric}"
        elif kind == "flow":
            if classification not in {"industry", "concept", "region"}:
                raise ValueError("invalid THS sector-flow classification")
            if metric is not None:
                raise ValueError("THS sector-flow fragment does not accept metric")
            response = await clients.ths.get_native_sector_fund_flow(
                classification,
                500,
            )
            data_type = "ths_sector_flow"
            identity_suffix = None
            projection_name = f"ths_sector_flow_{classification}"
        else:
            raise ValueError(
                "THS sector fragment kind must be table, hot, ranking or flow"
            )

        if response.get("status") != MarketDataStatus.OK.value:
            raise RuntimeError(
                f"THS sector fragment failed: {kind}:{classification}:{metric}:"
                f"{response.get('status')}"
            )
        sectors = ((response.get("data") or {}).get("sectors") or [])
        snapshots = [
            _sector_snapshot(
                response=response,
                sector=sector,
                sector_type=classification,
                data_type=data_type,
                bucket_seconds=60 if kind != "ranking" else 120,
                identity_suffix=identity_suffix,
            )
            for sector in sectors
        ]
        if not snapshots:
            raise RuntimeError(
                f"THS sector fragment is empty: {kind}:{classification}:{metric}"
            )
        return ObservationBatch(
            snapshots=snapshots,
            projections=[
                (
                    projection_name,
                    response,
                    120 if kind != "ranking" else 240,
                )
            ],
            fetched_count=len(snapshots),
            status="success",
            details={
                "source": "ths_app_vm",
                "kind": kind,
                "classification": classification,
                "metric": metric,
            },
        )

    async def _collect_ths_sector_references(self) -> ObservationBatch:
        """Incrementally persist native constituents for active THS boards."""

        from src.infrastructure import clients

        metadata_rows: list[dict[str, Any]] = []
        metadata_errors: list[str] = []
        metadata_results = await asyncio.gather(
            clients.ths.get_native_hot_boards("concept", 50),
            clients.ths.get_native_hot_boards("industry", 50),
            clients.ths.get_native_hot_boards("index", 50),
            return_exceptions=True,
        )
        for sector_type, result in zip(
            ("concept", "industry", "index"),
            metadata_results,
            strict=True,
        ):
            if isinstance(result, Exception):
                metadata_errors.append(
                    f"hot_{sector_type}:{type(result).__name__}"
                )
                continue
            if result.get("status") != MarketDataStatus.OK.value:
                metadata_errors.append(
                    f"hot_{sector_type}:{result.get('status')}"
                )
                continue
            for sector in ((result.get("data") or {}).get("sectors") or []):
                metadata_rows.append(
                    {
                        **sector,
                        "sector_type": sector_type,
                    }
                )

        source_rows = await asyncio.to_thread(
            self._snapshots.list_latest,
            data_types=[
                "ths_sector_hot",
                "ths_sector_ranking",
                "ths_sector_flow",
                "ths_sector_rotation",
            ],
            subject_type="sector",
            limit=10000,
        )
        existing_rows = await asyncio.to_thread(
            self._snapshots.list_latest,
            data_types=["ths_sector_constituents"],
            subject_type="sector",
            limit=10000,
        )
        existing_by_code = {
            str((row.get("data") or {}).get("provider_sector_code")): row
            for row in existing_rows
            if (row.get("data") or {}).get("provider_sector_code")
        }
        session = ChinaExchangeCalendarService().resolve(datetime.now(timezone.utc))
        quote_trade_date = (
            session.previous_trade_date
            if session.market_session == "pre_open"
            else session.trade_date
        )
        type_priority = {
            "concept": 0,
            "industry": 1,
            "index": 2,
            "style": 3,
            "region": 4,
            "all": 5,
        }
        candidates: dict[str, dict[str, Any]] = {}
        # Select the exchange calendar's effective quote day.  A legacy bug
        # stamped pre-open snapshots with the local calendar day; choosing the
        # maximum persisted date would let those future-dated rows shadow every
        # correctly repaired snapshot indefinitely.
        candidate_rows = [
            (row.get("data") or {}, row.get("bucket_at"))
            for row in source_rows
            if _parse_date(row.get("trade_date")) == quote_trade_date
        ]
        candidate_rows.extend((row, None) for row in metadata_rows)
        for data, bucket_at in candidate_rows:
            code = str(data.get("provider_sector_code") or "").strip()
            if not code:
                continue
            sector_type = str(data.get("sector_type") or "all")
            # THS style/all rows such as 883404 (情绪指数), 883420
            # (减持新规) and 883421 (同花顺全A) are statistical boards,
            # not constituent-query blocks. Sending them to sif-constituent-
            # stock deterministically returns native callback error code 20.
            if sector_type not in {"concept", "industry", "index", "region"}:
                continue
            candidate = {
                **data,
                "provider_sector_code": code,
                "sector_type": sector_type,
                "bucket_at": bucket_at,
            }
            current = candidates.get(code)
            if current is None:
                candidates[code] = candidate
                continue
            preferred = (
                candidate
                if type_priority.get(candidate["sector_type"], 99)
                < type_priority.get(str(current.get("sector_type")), 99)
                else current
            )
            merged = {**preferred}
            for source in (current, candidate):
                for key in (
                    "sector_name",
                    "market_code",
                    "heat_rank",
                    "heat_score",
                    "representative_etf_code",
                    "representative_etf_name",
                ):
                    if source.get(key) is not None:
                        merged[key] = source[key]
            candidates[code] = merged

        def refresh_priority(item: dict[str, Any]) -> int:
            existing = existing_by_code.get(item["provider_sector_code"])
            if (
                existing is None
                or existing.get("trade_date") != quote_trade_date
                or not _snapshot_bucket_matches_trade_date(existing)
            ):
                # Incorrect trade dates contaminate cross-sectional breadth
                # comparisons, so repair these rows before enriching otherwise
                # usable current rows with optional ETF metadata.
                return 0
            existing_data = existing.get("data") or {}
            metadata_missing = bool(
                item.get("representative_etf_code")
                and not existing_data.get("representative_etf_code")
            )
            return 1 if metadata_missing else 2

        def needs_refresh(item: dict[str, Any]) -> bool:
            return refresh_priority(item) < 2

        def due_key(item: dict[str, Any]) -> tuple[int, int, datetime]:
            existing = existing_by_code.get(item["provider_sector_code"])
            existing_time = (
                existing.get("bucket_at")
                if existing and existing.get("bucket_at") is not None
                else datetime.min.replace(tzinfo=timezone.utc)
            )
            heat_rank = item.get("heat_rank")
            return (
                refresh_priority(item),
                int(heat_rank) if heat_rank is not None else 10000,
                existing_time,
            )

        all_due = [
            item
            for item in sorted(candidates.values(), key=due_key)
            if needs_refresh(item)
        ]
        due = all_due[:3]
        if not due:
            raise CollectionSkipped("all active THS sector references are current")

        snapshots: list[dict[str, Any]] = []
        errors: list[str] = []
        responses = await asyncio.gather(
            *(
                clients.ths.get_native_sector_constituents(
                    item["provider_sector_code"],
                    market_code=str(item.get("market_code") or "48"),
                    count=1000,
                )
                for item in due
            ),
            return_exceptions=True,
        )
        for item, response in zip(due, responses, strict=True):
            code = item["provider_sector_code"]
            if isinstance(response, Exception):
                errors.append(f"{code}:exception:{type(response).__name__}")
                continue
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(
                    f"{code}:{response.get('status')}:{response.get('message')}"
                )
                continue
            enriched = {
                **response,
                "data": {
                    **(response.get("data") or {}),
                    "sector_name": item.get("sector_name"),
                    "sector_type": item.get("sector_type"),
                    "representative_etf_code": item.get(
                        "representative_etf_code"
                    ),
                    "representative_etf_name": item.get(
                        "representative_etf_name"
                    ),
                },
            }
            snapshots.append(
                _snapshot_from_response(
                    response=enriched,
                    data_type="ths_sector_constituents",
                    subject_type="sector",
                    subject_id=(
                        f"ths_native:{item.get('sector_type') or 'all'}:{code}"
                    ),
                    bucket_seconds=86400,
                    trade_date_override=quote_trade_date,
                )
            )
        if not snapshots:
            raise RuntimeError(f"all THS sector reference sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=len(snapshots),
            status="partial_success" if errors else "success",
            details={
                "errors": errors,
                "metadata_errors": metadata_errors,
                "requested_sector_codes": [item["provider_sector_code"] for item in due],
                "remaining_due_count": max(0, len(all_due) - len(snapshots)),
                "source": "ths_app_vm",
            },
        )

    async def _collect_ths_sector_signal_fragment(
        self,
        kind: str,
        sector_type: str | None,
        metric: str | None,
    ) -> ObservationBatch:
        """Collect one THS-derived signal without coupling sibling sources."""

        from src.infrastructure import clients

        name = kind
        if kind == "rotation":
            if sector_type not in {"industry", "concept"}:
                raise ValueError("invalid THS rotation sector_type")
            if metric not in {
                "change",
                "five_day_change",
                "rise_rate",
                "limit_up_count",
                "main_net_inflow",
            }:
                raise ValueError("invalid THS rotation metric")
            name = f"rotation_{sector_type}_{metric}"
            response = await clients.ths.get_native_sector_rotation(
                sector_type=sector_type,
                metric=metric,
                day_count=60,
                sector_count=10,
            )
        elif kind == "industry_opportunity":
            if sector_type is not None or metric is not None:
                raise ValueError("industry_opportunity does not accept selectors")
            response = await clients.ths.get_native_industry_opportunities()
        elif kind == "prosperity":
            if sector_type is not None or metric is not None:
                raise ValueError("prosperity does not accept selectors")
            name = "sector_prosperity"
            response = await clients.ths.get_native_sector_prosperity(50)
        elif kind == "commodity_linkage":
            if sector_type is not None or metric is not None:
                raise ValueError("commodity_linkage does not accept selectors")
            name = "sector_commodity_linkage"
            response = await clients.ths.get_native_sector_commodity_linkage(500)
        else:
            raise ValueError("unsupported THS sector signal kind")

        if response.get("status") != MarketDataStatus.OK.value:
            raise RuntimeError(f"THS sector signal failed: {name}:{response.get('status')}")
        payload = response.get("data") or {}
        snapshots: list[dict[str, Any]] = []
        if kind == "rotation":
            for period in payload.get("periods") or []:
                period_date = period.get("date")
                parsed_period_date = _parse_date(period_date)
                period_bucket = (
                    datetime.combine(
                        parsed_period_date,
                        time(15, 0),
                        tzinfo=CN_TIMEZONE,
                    ).astimezone(timezone.utc)
                    if parsed_period_date
                    else None
                )
                for rank, block in enumerate(period.get("block_list") or [], start=1):
                    snapshots.append(
                        _sector_snapshot(
                            response=response,
                            sector={
                                "provider_sector_code": block.get("code"),
                                "sector_name": block.get("name"),
                                "sector_type": sector_type,
                                "metric": metric,
                                "rank": rank,
                                "source_date": period_date,
                                "source_signal": block.get("info") or {},
                            },
                            sector_type=str(sector_type),
                            data_type="ths_sector_rotation",
                            bucket_seconds=300,
                            identity_suffix=str(metric),
                            trade_date_override=parsed_period_date,
                            bucket_at_override=period_bucket,
                        )
                    )
        elif kind == "industry_opportunity":
            for category in ("hotspot", "lowLevel", "revival"):
                for rank, item in enumerate(payload.get(category) or [], start=1):
                    snapshots.append(
                        _sector_snapshot(
                            response=response,
                            sector={
                                "provider_sector_code": item.get("code"),
                                "sector_name": item.get("securityName"),
                                "sector_type": "industry",
                                "opportunity_category": category,
                                "rank": rank,
                                "indicator": item.get("indic") or {},
                                "interval_data": item.get("intervalData") or {},
                            },
                            sector_type="industry",
                            data_type="ths_industry_opportunity",
                            bucket_seconds=300,
                            identity_suffix=category,
                        )
                    )
        elif kind == "prosperity":
            snapshots = [
                _sector_snapshot(
                    response=response,
                    sector=item,
                    sector_type="prosperity_theme",
                    data_type="ths_sector_prosperity",
                    bucket_seconds=300,
                )
                for item in payload.get("items") or []
            ]
        else:
            for item in payload.get("items") or []:
                linkage_type = str(item.get("linkage_type") or "unknown")
                identity_payload = json.dumps(
                    {
                        "code": item.get("provider_sector_code"),
                        "name": item.get("sector_name"),
                        "mapping": item.get("related_asset_mapping"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                identity = hashlib.sha1(identity_payload.encode("utf-8")).hexdigest()[:16]
                item["identity_version"] = 2
                snapshots.append(
                    _snapshot_from_response(
                        response={**response, "data": item},
                        data_type="ths_sector_commodity_linkage",
                        subject_type="commodity",
                        subject_id=f"ths_composite:commodity:v2:{linkage_type}:{identity}",
                        bucket_seconds=300,
                    )
                )
        if not snapshots:
            raise RuntimeError(f"THS sector signal is empty: {name}")
        return ObservationBatch(
            snapshots=snapshots,
            projections=[(f"ths_{name}", response, 600)],
            fetched_count=len(snapshots),
            status="success",
            details={"source": "ths_app_vm", "fragment": name},
        )

    async def _collect_cross_market(self) -> ObservationBatch:
        from src.infrastructure import clients

        calls = [
            ("global_index", "index_quote", clients.sina.get_global_index()),
            ("domestic_futures", "futures_quote", clients.sina.get_futures()),
            ("international_futures", "futures_quote", clients.tencent.get_intl_futures()),
            ("forex", "forex_quote", clients.sina.get_forex()),
            ("bond_futures", "futures_quote", clients.sina.get_bond_futures()),
        ]
        results = await asyncio.gather(
            *[item[2] for item in calls],
            return_exceptions=True,
        )
        snapshots: list[dict[str, Any]] = []
        projections: list[tuple[str, dict[str, Any], int]] = []
        errors: list[str] = []
        fetched_count = 0
        for (name, data_type, _call), raw in zip(calls, results):
            if isinstance(raw, Exception):
                errors.append(f"{name}:{type(raw).__name__}")
                continue
            response = _coerce_market_response(
                raw,
                provider="tencent" if name == "international_futures" else "sina",
                market="global",
            )
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(f"{name}:{response.get('status')}")
                continue
            fetched_count += _response_count(response)
            snapshots.append(
                _snapshot_from_response(
                    response=response,
                    data_type=data_type,
                    subject_type="market",
                    subject_id=f"{response['provider']}:{name}",
                    bucket_seconds=60,
                )
            )
            projections.append((f"cross_market_{name}", response, 180))

        native_definitions = (
            (
                "ftse_a50",
                "futures_intraday",
                "global:futures:ftse_a50",
            ),
            (
                "dow_futures",
                "futures_intraday",
                "global:futures:dow_jones",
            ),
            (
                "usd_cny",
                "forex_intraday",
                "cn:forex:usd_cny:ths",
            ),
        )
        if not await asyncio.to_thread(_ths_native_stream_is_active):
            for indicator, data_type, subject_id in native_definitions:
                response = await clients.ths.get_native_realtime_indicator(
                    indicator
                )
                if response.get("status") != MarketDataStatus.OK.value:
                    errors.append(f"ths_{indicator}:{response.get('status')}")
                    continue
                latest_bucket_at = await asyncio.to_thread(
                    self._latest_bucket_at,
                    data_type,
                    subject_id,
                )
                new_rows = _native_chart_snapshots(
                    response=response,
                    data_type=data_type,
                    subject_type=(
                        "currency" if data_type == "forex_intraday" else "index"
                    ),
                    subject_id=subject_id,
                    latest_bucket_at=latest_bucket_at,
                )
                snapshots.extend(new_rows)
                fetched_count += len(new_rows)
        if not snapshots:
            raise RuntimeError(f"all cross-market sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            projections=projections,
            fetched_count=fetched_count,
            status="partial_success" if errors else "success",
            details={"errors": errors},
        )

    async def _collect_etf_estimated_net_inflow(
        self,
        *,
        force_boundary: bool,
    ) -> ObservationBatch:
        from src.infrastructure import clients

        session = await clients.market_calendar.get_market_session("cn")
        latest_rows = await asyncio.to_thread(
            self._snapshots.list_latest,
            data_types=["etf_estimated_net_inflow"],
            subject_type="etf_market",
            limit=10,
        )
        latest_bucket_at = max(
            (
                row["bucket_at"]
                for row in latest_rows
                if row.get("bucket_at") is not None
            ),
            default=None,
        )
        catchup = _require_cn_series_observation_or_catchup(
            session,
            latest_bucket_at=latest_bucket_at,
            force_boundary=force_boundary,
        )
        response = await clients.ths.get_etf_estimated_net_inflow()
        _require_ok(response, "ETF estimated net inflow")
        snapshots = _etf_estimated_flow_snapshots(
            response=response,
            latest_bucket_at=latest_bucket_at,
        )
        payload = response.get("data") or {}
        return ObservationBatch(
            snapshots=snapshots,
            projections=[
                ("etf_estimated_net_inflow", response, 120)
            ],
            fetched_count=len(payload.get("trend") or []),
            details={
                "market_session": (
                    (session.get("data") or {}).get("market_session")
                ),
                "force_boundary": force_boundary,
                "catchup": catchup,
                "latest_bucket_before": (
                    latest_bucket_at.isoformat() if latest_bucket_at else None
                ),
                "new_minute_count": len(snapshots),
                "coverage_market": payload.get("coverage_market"),
                "value_type": payload.get("value_type"),
                "is_official_subscription": False,
                "top_inflow": payload.get("top_inflow"),
            },
        )

    async def _collect_ths_etf_zone(self) -> ObservationBatch:
        """ETF 专区全天采集；盘后热度和海外行情仍会变化，禁止交易时段跳过。"""
        from src.infrastructure import clients

        response = await clients.ths.get_native_etf_zone_snapshot()
        _require_ok(response, "THS ETF zone")
        payload = response.get("data") or {}
        snapshots = [
            _snapshot_from_response(
                response=response,
                data_type="ths_etf_zone",
                subject_type="etf_market",
                subject_id="etf_zone",
                bucket_seconds=60,
            ),
            _snapshot_from_response(
                response={**response, "data": {
                    "rank_definitions": payload.get("rank_definitions"),
                    "track_tree": payload.get("track_tree"),
                    "etf_universe": payload.get("etf_universe"),
                    "etf_quotes": payload.get("etf_quotes"),
                    "etf_count": payload.get("etf_count"),
                }},
                data_type="ths_etf_ranking_universe",
                subject_type="etf_market",
                subject_id="all",
                bucket_seconds=60,
            ),
            _snapshot_from_response(
                response={**response, "data": payload.get("us_cross_border_etf") or {}},
                data_type="ths_etf_cross_border",
                subject_type="etf_market",
                subject_id="us_home",
                bucket_seconds=60,
            ),
        ]
        for category, ranking in (payload.get("hot_rankings") or {}).items():
            snapshots.append(
                _snapshot_from_response(
                    response={**response, "data": ranking},
                    data_type="ths_etf_hot_ranking",
                    subject_type="ranking",
                    subject_id=str(category),
                    bucket_seconds=60,
                )
            )
        return ObservationBatch(
            snapshots=snapshots,
            projections=[("ths_etf_zone", response, 120)],
            fetched_count=int(payload.get("etf_count") or 0),
            details={
                "etf_count": payload.get("etf_count"),
                "hot_categories": list((payload.get("hot_rankings") or {}).keys()),
                "runs_outside_trading_hours": True,
            },
        )

    async def _collect_ths_futures_zone(self) -> ObservationBatch:
        """期货含日盘和夜盘，按自然时间全天采集，不套用 A 股时段。"""
        from src.infrastructure import clients

        response = await clients.ths.get_native_futures_zone_snapshot()
        _require_ok(response, "THS futures zone")
        payload = response.get("data") or {}
        snapshots = [
            _snapshot_from_response(
                response=response, data_type="ths_futures_zone",
                subject_type="futures_market", subject_id="futures_zone",
                bucket_seconds=60,
            )
        ]
        modules = {}
        if "market_state" in payload or "market_net_flow" in payload:
            modules["market"] = {
                "market_state": payload.get("market_state"),
                "market_net_flow": payload.get("market_net_flow"),
            }
        for subject_id, payload_key in (
            ("hot", "hot_continuous_contracts"),
            ("indices", "futures_indices"),
            ("fund_flow", "commodity_fund_flow"),
            ("rankings", "main_contract_rankings"),
        ):
            if payload_key in payload:
                modules[subject_id] = payload[payload_key]
        for subject_id, data in modules.items():
            snapshots.append(_snapshot_from_response(
                response={**response, "data": data or {}},
                data_type="ths_futures_module", subject_type="futures_market",
                subject_id=subject_id, bucket_seconds=60,
            ))
        return ObservationBatch(
            snapshots=snapshots,
            projections=[("ths_futures_zone", response, 120)],
            fetched_count=sum(
                len(((item or {}).get("dataDict") or {}).get("4") or [])
                for item in (payload.get("main_contract_rankings") or {}).values()
            ),
            details={
                "module_count": len(modules),
                "complete": bool((response.get("provider_metadata") or {}).get("complete")),
                "completed_modules": (response.get("provider_metadata") or {}).get("completed_modules") or {},
                "module_errors": (response.get("provider_metadata") or {}).get("errors") or {},
                "runs_outside_a_share_hours": True,
            },
        )

    async def _collect_ths_futures_fragment(
        self,
        kind: str,
        group: str | None,
    ) -> ObservationBatch:
        from src.infrastructure import clients

        response = await clients.ths.get_native_futures_fragment(kind, group)
        _require_ok(response, f"THS futures fragment {kind}/{group or '-'}")
        payload = response.get("data") or {}
        native_table = payload.get("native_table")
        subject_id = f"{kind}:{group}" if group else kind
        snapshot_response = {
            **response,
            "data": {
                "kind": kind,
                "group": group,
                "native_table": native_table,
            },
        }
        data_dict = (
            native_table.get("dataDict")
            if isinstance(native_table, dict)
            else None
        ) or {}
        row_count = max(
            (len(values) for values in data_dict.values() if isinstance(values, list)),
            default=(
                len(native_table.get("rows") or [])
                if isinstance(native_table, dict)
                else 0
            ),
        )
        return ObservationBatch(
            snapshots=[_snapshot_from_response(
                response=snapshot_response,
                data_type="ths_futures_module",
                subject_type="futures_market",
                subject_id=subject_id,
                bucket_seconds=60,
            )],
            projections=[],
            fetched_count=row_count,
            details={
                "kind": kind,
                "group": group,
                "row_count": row_count,
                "atomic_fragment": True,
            },
        )

    async def _collect_ths_gold_zone(self) -> ObservationBatch:
        """黄金横跨境内、国际和线下市场，全天采集且不套用 A 股时段。"""
        from src.infrastructure import clients

        response = await clients.ths.get_native_gold_zone_snapshot()
        _require_ok(response, "THS gold zone")
        payload = response.get("data") or {}
        snapshots = [
            _snapshot_from_response(
                response=response,
                data_type="ths_gold_zone",
                subject_type="gold_market",
                subject_id="gold_zone",
                bucket_seconds=60,
            )
        ]
        modules = {
            "quotes": {
                key: payload.get(key)
                for key in (
                    "domestic_gold_kline", "international_gold_kline",
                    "gold_etf_flow", "gold_market_quotes", "jewelry_quotes",
                )
            },
            "capital": {
                "domestic": payload.get("domestic_capital"),
                "international": payload.get("international_capital"),
            },
            "opportunities": {
                key: payload.get(key)
                for key in (
                    "gold_etf_rank", "gold_fund_rank",
                    "stock_recommendations", "etf_recommendations",
                    "fund_recommendations", "futures_recommendations",
                    "gold_recommend_head",
                )
            },
            "offline_price": {
                key: payload.get(key)
                for key in (
                    "jewelry_prices", "gold_bar_prices",
                    "bank_gold_prices", "recycle_gold_prices",
                )
            },
            "reserve": {
                "tabs": payload.get("gold_reserve_tabs"),
                "year_up": payload.get("gold_reserve_year_up"),
                "month_up": payload.get("gold_reserve_month_up"),
                "month_down": payload.get("gold_reserve_month_down"),
                "curves": payload.get("gold_reserve_curves"),
            },
            "analytics": {
                key: payload.get(key)
                for key in (
                    "history_spread", "seasonality_statistics",
                    "seasonality_monthly_change", "gold_stock_correlation",
                    "gold_silver_correlation", "silver_spot_kline",
                    "gold_future_kline", "silver_future_kline",
                    "ssh_gold_stock_kline",
                    "gold_spot_intraday_kline", "gold_futures_intraday_kline",
                    "gold_spot_overseas_intraday_kline", "gold_fx_intraday_kline",
                    "gold_silver_spot_intraday_kline",
                    "gold_ratio_gold_intraday_kline",
                    "gold_ratio_silver_intraday_kline",
                    "gold_silver_futures_intraday_kline",
                    "gold_silver_ratio_threshold",
                    "gold_silver_ratio_products",
                    "domestic_silver_kline", "brent_kline",
                )
            },
            "content": {
                key: payload.get(key)
                for key in (
                    "future_news", "gold_ai_summary_list", "gold_ai_summary_count",
                    "gold_ai_system_time", "market_banner", "gold_cards", "grid_config",
                    "investment_links", "explanation",
                )
            },
        }
        for subject_id, data in modules.items():
            snapshots.append(
                _snapshot_from_response(
                    response={**response, "data": data or {}},
                    data_type="ths_gold_module",
                    subject_type="gold_market",
                    subject_id=subject_id,
                    bucket_seconds=60,
                )
            )
        return ObservationBatch(
            snapshots=snapshots,
            news_items=_gold_ai_news_records(payload.get("gold_ai_summary_list")),
            projections=[("ths_gold_zone", response, 180)],
            fetched_count=len(payload) - 1,
            details={
                "module_count": len(modules),
                "runs_outside_a_share_hours": True,
            },
        )

    async def _collect_ths_us_module(
        self,
        module: str,
        client_method: str,
        projection_ttl: int,
    ) -> ObservationBatch:
        """Persist one US page module immediately, independent of slow siblings."""
        from src.infrastructure import clients

        response = await getattr(clients.ths, client_method)()
        _require_ok(response, f"THS US market {module}")
        payload = response.get("data") or {}
        metadata = response.get("provider_metadata") or {}
        if module in {"stock_rankings", "etf_sectors"} and not metadata.get(
            "complete"
        ):
            raise RuntimeError(
                f"THS US {module} incomplete: "
                f"{metadata.get('failed_modules') or []}"
            )
        snapshot = _snapshot_from_response(
            response=response,
            data_type="ths_us_market_module",
            subject_type="us_market",
            subject_id=module,
            bucket_seconds=30 if module == "overview" else 60,
        )
        snapshots = [snapshot]
        if module == "sectors":
            sector_payloads = payload.get("sectors") or {}
            for sector_type in ("industry", "concept"):
                for period in ("five_day", "one_month", "three_month"):
                    subject_id = f"{sector_type}_{period}"
                    period_payload = sector_payloads.get(subject_id)
                    if not isinstance(period_payload, dict):
                        continue
                    snapshots.append(
                        _snapshot_from_response(
                            response={**response, "data": period_payload},
                            data_type="ths_us_sector_period",
                            subject_type=sector_type,
                            subject_id=subject_id,
                            bucket_seconds=120,
                        )
                    )
        if module == "etf_sectors":
            snapshots.append(
                _snapshot_from_response(
                    response=response,
                    data_type="ths_us_etf_catalog",
                    subject_type="us_etf_market",
                    subject_id="all_categories",
                    bucket_seconds=60,
                )
            )
        return ObservationBatch(
            snapshots=snapshots,
            projections=[(f"ths_us_market_{module}", response, projection_ttl)],
            fetched_count=sum(
                len(value) for value in payload.values() if isinstance(value, list)
            ),
            status="success" if metadata.get("complete") else "partial_success",
            details={
                "module": module,
                "complete": bool(metadata.get("complete")),
                "failed_modules": metadata.get("failed_modules") or [],
                "timezone": "America/New_York",
                "runs_outside_a_share_hours": True,
            },
        )

    async def _collect_ths_us_stock_rankings(self) -> ObservationBatch:
        """Materialize the seven rankings from push frames, repairing only gaps.

        The App uses the same Unified callback interface for the live ranking
        subscriptions and one-shot requests. Re-requesting all seven tabs every
        two minutes creates avoidable callback contention (most visibly on
        ``djg``). The stream is authoritative; the scheduled job now only
        requests tabs for which no pushed frame has ever been persisted.
        """
        from src.infrastructure import clients

        tab_names = {
            "all": "全部",
            "us24hremen": "24H最热",
            "zhonggaigu": "中概股",
            "djg": "低价股",
            "redianmeigu": "热点美股",
            "ssxg": "上市新股",
            "redianetf": "热点ETF",
        }
        all_subjects = {
            "ranking_all_pre_market_stream",
            "ranking_all_regular_stream",
            "ranking_all_after_hours_stream",
        }
        subject_to_tab = {
            **{subject: "all" for subject in all_subjects},
            **{
                f"ranking_{tab_id}_stream": tab_id
                for tab_id in tab_names
                if tab_id != "all"
            },
        }
        rows = await asyncio.to_thread(
            self._snapshots.query_latest,
            subject_ids=list(subject_to_tab),
            data_types=["ths_us_market_module"],
        )
        rankings: dict[str, dict[str, Any]] = {}
        ranking_times: dict[str, datetime] = {}
        for row in rows:
            tab_id = subject_to_tab.get(str(row.get("subject_id") or ""))
            data = row.get("data") or {}
            native_table = data.get("native_table")
            fetched_at = row.get("fetched_at")
            if tab_id is None or not isinstance(native_table, dict):
                continue
            if tab_id in ranking_times and fetched_at <= ranking_times[tab_id]:
                continue
            rankings[tab_id] = {"head": {}, "data": native_table}
            ranking_times[tab_id] = fetched_at

        missing = set(tab_names).difference(rankings)
        labels = None
        if missing:
            repair = await clients.ths.get_native_us_stock_rankings_snapshot(missing)
            _require_ok(repair, "THS US stock rankings repair")
            repair_data = repair.get("data") or {}
            rankings.update(repair_data.get("stock_rankings") or {})
            labels = repair_data.get("stock_ranking_labels")
            repair_metadata = repair.get("provider_metadata") or {}
            failed = list(repair_metadata.get("failed_modules") or [])
        else:
            # Labels are a cheap public HTTP payload and do not touch the App
            # callback channel. Keep them current even when all ranking tables
            # came from the live stream.
            label_response = await clients.ths.get_native_us_stock_rankings_snapshot(set())
            labels = (label_response.get("data") or {}).get("stock_ranking_labels")
            failed = []

        fetched_at = datetime.now(timezone.utc)
        response = {
            "provider": "ths_native_stream",
            "market": "us",
            "fetched_at": fetched_at,
            "source_time": fetched_at.isoformat(),
            "trade_date": fetched_at.astimezone(ZoneInfo("America/New_York")).date(),
            "timezone": "America/New_York",
            "provider_metadata": {
                "source_component": "US market home/ranking push materialization",
                "complete": not failed and set(rankings) == set(tab_names),
                "failed_modules": failed,
                "push_tabs": sorted(set(tab_names).difference(missing)),
                "repaired_tabs": sorted(missing.difference(failed)),
            },
            "data": {
                "stock_ranking_labels": labels,
                "tabs": [
                    {"id": tab_id, "name": name}
                    for tab_id, name in tab_names.items()
                ],
                "stock_rankings": rankings,
            },
        }
        metadata = response["provider_metadata"]
        if not metadata["complete"]:
            raise RuntimeError(f"THS US stock_rankings incomplete: {failed}")
        snapshot = _snapshot_from_response(
            response=response,
            data_type="ths_us_market_module",
            subject_type="us_market",
            subject_id="stock_rankings",
            bucket_seconds=60,
        )
        return ObservationBatch(
            snapshots=[snapshot],
            projections=[("ths_us_market_stock_rankings", response, 240)],
            fetched_count=len(rankings),
            details=dict(metadata),
        )

    async def _collect_ths_us_etf_sectors(self) -> ObservationBatch:
        """Materialize ETF categories from push frames and repair true gaps.

        Protocol 1360 uses the same App callback lane as other US ranking
        tables. Re-requesting all nine categories every ten minutes creates
        avoidable callback contention and random -131 timeouts. The live
        stream and the last complete catalog are authoritative membership
        sources; Native request/response is used only for a category that has
        never been persisted.
        """
        from src.infrastructure import clients

        seed_rows = await asyncio.to_thread(
            self._snapshots.query_latest,
            subject_ids=["etf_config_stream", "all_categories"],
            data_types=["ths_us_market_module", "ths_us_etf_catalog"],
        )
        config_table: dict[str, Any] = {}
        prior_payload: dict[str, Any] = {}
        for row in seed_rows:
            data = row.get("data") or {}
            if row.get("subject_id") == "etf_config_stream":
                candidate = data.get("native_table")
                if isinstance(candidate, dict):
                    config_table = candidate
            elif row.get("subject_id") == "all_categories":
                prior_payload = data

        prior_config = prior_payload.get("etf_sector_config") or {}
        if not config_table:
            candidate = prior_config.get("data")
            if isinstance(candidate, dict):
                config_table = candidate
        categories = config_table.get("items") or (
            (config_table.get("data") or {}).get("items") or []
        )
        category_ids = {
            str(item["BlockID"])
            for item in categories
            if isinstance(item, dict) and item.get("BlockID")
        }

        prior_details = prior_payload.get("etf_sector_details") or {}
        details: dict[str, dict[str, Any]] = {
            block_id: value
            for block_id, value in prior_details.items()
            if block_id in category_ids and isinstance(value, dict)
        }
        push_ids: set[str] = set()
        if category_ids:
            subject_to_block = {
                f"etf_sector_{block_id}_stream": block_id
                for block_id in category_ids
            }
            rows = await asyncio.to_thread(
                self._snapshots.query_latest,
                subject_ids=list(subject_to_block),
                data_types=["ths_us_market_module"],
            )
            for row in rows:
                block_id = subject_to_block.get(str(row.get("subject_id") or ""))
                native_table = (row.get("data") or {}).get("native_table")
                if block_id is None or not isinstance(native_table, dict):
                    continue
                columns = native_table.get("dataDict") or {}
                if not isinstance(columns, dict) or not any(
                    isinstance(values, list) and values
                    for values in columns.values()
                ):
                    continue
                details[block_id] = {"head": {}, "data": native_table}
                push_ids.add(block_id)

        missing = category_ids.difference(details)
        repaired_ids: set[str] = set()
        repair_quotes: list[dict[str, Any]] = []
        if not category_ids or missing:
            repair = await clients.ths.get_native_us_etf_sectors_snapshot(
                missing if category_ids else None
            )
            _require_ok(repair, "THS US ETF sector repair")
            repair_data = repair.get("data") or {}
            if not category_ids:
                repair_config = repair_data.get("etf_sector_config") or {}
                candidate = repair_config.get("data")
                if isinstance(candidate, dict):
                    config_table = candidate
                categories = config_table.get("items") or []
                category_ids = {
                    str(item["BlockID"])
                    for item in categories
                    if isinstance(item, dict) and item.get("BlockID")
                }
            repair_details = repair_data.get("etf_sector_details") or {}
            for block_id, value in repair_details.items():
                if block_id in category_ids and isinstance(value, dict):
                    details[block_id] = value
                    repaired_ids.add(block_id)
            repair_quotes = [
                value for value in repair_data.get("etf_quotes") or []
                if isinstance(value, dict)
            ]

        failed = sorted(category_ids.difference(details))
        member_keys: set[tuple[str, str]] = set()
        for detail in details.values():
            columns = (detail.get("data") or {}).get("dataDict") or {}
            codes = columns.get("4") or []
            markets = columns.get("34338") or columns.get("36103") or []
            for index, code in enumerate(codes):
                if code and index < len(markets) and markets[index] not in (None, ""):
                    member_keys.add((str(markets[index]), str(code)))

        quote_payloads = [
            value for value in prior_payload.get("etf_quotes") or []
            if isinstance(value, dict)
        ]
        quote_payloads.extend(repair_quotes)
        complete = bool(category_ids) and not failed
        fetched_at = datetime.now(timezone.utc)
        response = market_result(
            provider="ths_native_stream",
            market="us",
            data={
                "etf_sector_config": {"head": {}, "data": config_table},
                "etf_sector_details": details,
                "etf_quotes": quote_payloads,
                "etf_count": len(member_keys),
            },
            source_time=fetched_at.isoformat(),
            trade_date=fetched_at.astimezone(ZoneInfo("America/New_York")).date(),
            timezone_name="America/New_York",
            provider_metadata={
                "source_component": "US market home/ETF push materialization",
                "module": "etf_sectors",
                "complete": complete,
                "failed_modules": failed,
                "push_categories": sorted(push_ids),
                "repaired_categories": sorted(repaired_ids),
                "runs_outside_a_share_hours": True,
                "includes_pre_and_after_market": True,
            },
        )
        if not complete:
            raise RuntimeError(f"THS US etf_sectors incomplete: {failed}")

        snapshots = [
            _snapshot_from_response(
                response=response,
                data_type="ths_us_market_module",
                subject_type="us_market",
                subject_id="etf_sectors",
                bucket_seconds=60,
            ),
            _snapshot_from_response(
                response=response,
                data_type="ths_us_etf_catalog",
                subject_type="us_etf_market",
                subject_id="all_categories",
                bucket_seconds=60,
            ),
        ]
        return ObservationBatch(
            snapshots=snapshots,
            projections=[("ths_us_market_etf_sectors", response, 360)],
            fetched_count=len(details),
            details=dict(response.get("provider_metadata") or {}),
        )

    async def _collect_ths_us_market_zone(self) -> ObservationBatch:
        """美股页覆盖盘前、盘中和盘后，全天采集且使用纽约交易日。"""
        from src.infrastructure import clients

        response = await clients.ths.get_native_us_market_zone_snapshot()
        _require_ok(response, "THS US market zone")
        payload = response.get("data") or {}
        snapshots = [
            _snapshot_from_response(
                response=response,
                data_type="ths_us_market_zone",
                subject_type="us_market",
                subject_id="us_market_home",
                bucket_seconds=60,
            )
        ]
        modules = {
            "breadth": {
                "today": payload.get("breadth_today"),
                "month": payload.get("breadth_month"),
            },
            "indices": payload.get("indices"),
            "sectors": payload.get("sectors"),
            "etf_sectors": {
                "config": payload.get("etf_sector_config"),
                "details": payload.get("etf_sector_details"),
            },
            "stock_ranking_labels": payload.get("stock_ranking_labels"),
            "stock_rankings": payload.get("stock_rankings"),
        }
        for subject_id, data in modules.items():
            snapshots.append(
                _snapshot_from_response(
                    response={**response, "data": data or {}},
                    data_type="ths_us_market_module",
                    subject_type="us_market",
                    subject_id=subject_id,
                    bucket_seconds=60,
                )
            )
        return ObservationBatch(
            snapshots=snapshots,
            projections=[("ths_us_market_zone", response, 180)],
            fetched_count=sum(
                len(value) for value in payload.values() if isinstance(value, list)
            ),
            details={
                "module_count": len(modules),
                "timezone": "America/New_York",
                "runs_outside_a_share_hours": True,
            },
        )

    async def _collect_etf_daily_shares(
        self,
        requested_trade_date: str | None,
    ) -> ObservationBatch:
        from src.infrastructure import clients

        target_date = (
            date.fromisoformat(requested_trade_date)
            if requested_trade_date
            else await _latest_completed_cn_trade_date()
        )
        response = await clients.exchange_fund.get_etf_daily_shares(
            target_date.isoformat()
        )
        _require_ok(response, "ETF daily shares")
        metadata = response.get("provider_metadata") or {}
        if metadata.get("complete") is not True:
            raise RuntimeError("ETF daily shares response is incomplete")
        fetched_at = _parse_datetime(response.get("fetched_at")) or datetime.now(
            timezone.utc
        )
        items = ((response.get("data") or {}).get("items") or [])
        previous_date, previous_shares = await asyncio.to_thread(
            self._etf_shares.previous_shares,
            before_date=target_date,
        )
        enriched_items = [
            _with_etf_daily_share_change(
                item,
                previous_date=previous_date,
                previous_shares=previous_shares.get(
                    (str(item["exchange"]), str(item["code"]))
                ),
            )
            for item in items
        ]
        rows = [
            {
                "exchange": item["exchange"],
                "code": item["code"],
                "name": item.get("name") or "",
                "trade_date": date.fromisoformat(item["date"]),
                "shares": item["shares"],
                "share_unit": item.get("share_unit") or "share",
                "provider": response.get("provider") or "cn_exchanges",
                "observed_at": None,
                "fetched_at": fetched_at,
                "data": item,
            }
            for item in enriched_items
        ]
        if not rows:
            raise RuntimeError(
                f"ETF daily shares returned no rows for {target_date}"
            )
        return ObservationBatch(
            etf_daily_shares=rows,
            fetched_count=len(items),
            details={
                "trade_date": target_date.isoformat(),
                "exchange_counts": (
                    (response.get("data") or {}).get("exchange_counts") or {}
                ),
                "previous_trade_date": (
                    previous_date.isoformat() if previous_date else None
                ),
                "daily_share_change_available": previous_date is not None,
                "realtime_net_subscription_available": False,
            },
        )

    async def _collect_pboc_rate_liquidity(self) -> ObservationBatch:
        from src.infrastructure import clients

        responses = await asyncio.gather(
            clients.pboc.get_interest_rates(60),
            clients.pboc.get_government_bond_yields(
                (date.today() - timedelta(days=180)).strftime("%Y%m%d"),
                180,
            ),
            return_exceptions=True,
        )
        snapshots: list[dict[str, Any]] = []
        errors: list[str] = []
        interest_response, yield_response = responses
        if isinstance(interest_response, Exception):
            errors.append(f"interest_rates:{type(interest_response).__name__}")
        elif interest_response.get("status") == MarketDataStatus.OK.value:
            snapshots.extend(_interest_rate_snapshots(interest_response))
        else:
            errors.append(f"interest_rates:{interest_response.get('status')}")

        if isinstance(yield_response, Exception):
            errors.append(
                f"government_bond_yields:{type(yield_response).__name__}"
            )
        elif yield_response.get("status") == MarketDataStatus.OK.value:
            snapshots.extend(_government_yield_snapshots(yield_response))
        else:
            errors.append(
                f"government_bond_yields:{yield_response.get('status')}"
            )

        if not snapshots:
            raise RuntimeError(f"all PBOC rate sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=len(snapshots),
            status="partial_success" if errors else "success",
            details={"errors": errors},
        )

    async def _collect_ths_index_sentiment(self) -> ObservationBatch:
        from src.infrastructure import clients

        snapshots: list[dict[str, Any]] = []
        errors: list[str] = []
        sentiment_responses = await asyncio.gather(
            clients.ths.get_index_sentiment_history("sh50"),
            clients.ths.get_index_sentiment_history("growth"),
            return_exceptions=True,
        )
        for index, subject_id, response in zip(
            ("sh50", "growth"),
            ("cn:index:sh50", "cn:index:growth"),
            sentiment_responses,
        ):
            if isinstance(response, Exception):
                errors.append(
                    f"index_sentiment_{index}:{type(response).__name__}"
                )
                continue
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(
                    f"index_sentiment_{index}:{response.get('status')}"
                )
                continue
            latest_dates = await asyncio.to_thread(
                self._snapshots.latest_trade_dates,
                data_type="index_sentiment",
                subject_ids=[subject_id],
            )
            payload = response.get("data") or {}
            snapshots.extend(
                _dated_series_snapshots(
                    response=response,
                    rows=payload.get("items") or [],
                    data_type="index_sentiment",
                    subject_type="index",
                    subject_id=subject_id,
                    latest_date=latest_dates.get(subject_id),
                    common_payload={
                        "index": index,
                        "index_code": payload.get("index_code"),
                        "index_name": payload.get("index_name"),
                        "methodology_available": False,
                    },
                )
            )
        if not snapshots:
            raise RuntimeError(f"all THS index sentiment sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=len(snapshots),
            status="partial_success" if errors else "success",
            details={"errors": errors},
        )

    async def _collect_market_daily_bars(self) -> ObservationBatch:
        from src.infrastructure import clients

        cn_index_calls = [
            (
                "ths_index_daily",
                "index",
                f"cn:index:{canonical_code}",
                clients.ths.get_native_security_daily_bars(
                    native_code,
                    market_code,
                    name=name,
                    # Unified 1234/2312 returns a stale historical page when
                    # klinecount=500 for sector/index identities.  300 is the
                    # largest production-verified window that still ends at
                    # the newest trading day; it also exceeds the Agent's
                    # 120-bar research requirement.
                    count=300,
                ),
            )
            for native_code, market_code, canonical_code, name
            in THS_CN_INDEX_DAILY_INSTRUMENTS
        ]
        fixed_calls = [
            *cn_index_calls,
            (
                "benchmark_daily",
                "index",
                "hk:hsi",
                clients.sina.get_benchmark_kline("hk", "HSI", 60),
            ),
            (
                "benchmark_daily",
                "index",
                "us:dji",
                clients.sina.get_benchmark_kline("us", ".DJI", 60),
            ),
            (
                "commodity_daily",
                "commodity",
                "cn:au0",
                clients.sina.get_commodity_kline(
                    "AU0",
                    start_date=(date.today() - timedelta(days=180)).strftime(
                        "%Y%m%d"
                    ),
                    limit=120,
                ),
            ),
            (
                "commodity_daily",
                "commodity",
                "global:gc",
                clients.sina.get_commodity_kline(
                    "GC",
                    international=True,
                    limit=120,
                ),
            ),
        ]
        fixed_results = await asyncio.gather(
            *[item[3] for item in fixed_calls],
            return_exceptions=True,
        )
        snapshots: list[dict[str, Any]] = []
        errors: list[str] = []
        for (data_type, subject_type, subject_id, _call), response in zip(
            fixed_calls,
            fixed_results,
        ):
            if isinstance(response, Exception):
                errors.append(f"{subject_id}:{type(response).__name__}")
                continue
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(f"{subject_id}:{response.get('status')}")
                continue
            snapshots.extend(
                _bar_snapshots(
                    response=response,
                    data_type=data_type,
                    subject_type=subject_type,
                    subject_id=subject_id,
                )
            )

        ths_sector_rows = await asyncio.to_thread(
            self._snapshots.list_latest,
            data_types=[
                "ths_sector_hot",
                "ths_sector_ranking",
                "ths_sector_flow",
            ],
            limit=10000,
        )
        sectors_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
        for row in ths_sector_rows:
            data = row.get("data") or {}
            sector_kind = str(data.get("sector_type") or "").strip()
            provider_code = str(
                data.get("provider_sector_code") or ""
            ).strip()
            sector_name = str(
                data.get("name") or data.get("sector_name") or ""
            ).strip()
            if (
                sector_kind in {"industry", "concept"}
                and provider_code
                and sector_name
            ):
                sectors_by_identity[(sector_kind, provider_code)] = {
                    "sector_type": sector_kind,
                    "provider_sector_code": provider_code,
                    "sector_name": sector_name,
                    "market_code": str(data.get("market_code") or "48"),
                }
        sectors = list(sectors_by_identity.values())

        semaphore = asyncio.Semaphore(8)

        async def fetch_sector_bars(sector: dict[str, Any]):
            async with semaphore:
                return await clients.ths.get_native_security_daily_bars(
                    str(sector["provider_sector_code"]),
                    str(sector["market_code"]),
                    name=str(sector["sector_name"]),
                    count=300,
                )

        sector_results = await asyncio.gather(
            *[fetch_sector_bars(sector) for sector in sectors],
            return_exceptions=True,
        )
        for sector, response in zip(sectors, sector_results):
            code = str(sector.get("provider_sector_code") or "")
            if isinstance(response, Exception):
                errors.append(f"sector:{code}:{type(response).__name__}")
                continue
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(f"sector:{code}:{response.get('status')}")
                continue
            snapshots.extend(
                _bar_snapshots(
                    response=response,
                    data_type="ths_sector_daily",
                    subject_type="sector",
                    subject_id=f"ths:{sector['sector_type']}:{code}",
                    common_payload={
                        "provider_sector_code": code,
                        "market_code": sector["market_code"],
                        "sector_name": sector["sector_name"],
                        "sector_type": sector["sector_type"],
                        "classification": "ths_native",
                    },
                )
            )

        try:
            etf_response = await clients.sina.get_etf_catalog()
        except Exception as exc:
            etf_response = None
            errors.append(f"etf_catalog:{type(exc).__name__}")
        if (
            isinstance(etf_response, dict)
            and etf_response.get("status") == MarketDataStatus.OK.value
        ):
            snapshots.extend(_etf_daily_quote_snapshots(etf_response))
        elif etf_response is not None:
            errors.append(f"etf_catalog:{etf_response.get('status')}")
        if not snapshots:
            raise RuntimeError(f"all daily market sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=len(snapshots),
            status="partial_success" if errors else "success",
            details={
                "errors": errors[:100],
                "error_count": len(errors),
                "sector_count": len(sectors),
            },
        )

    async def _collect_market_reference_data(self) -> ObservationBatch:
        from src.infrastructure import clients

        reference_results = await asyncio.gather(
            clients.sina.get_sector_ranking("industry", count=500),
            clients.sina.get_sector_ranking("concept", count=500),
            clients.sina.get_etf_catalog(),
            return_exceptions=True,
        )
        industry, concept, etfs = reference_results
        snapshots: list[dict[str, Any]] = []
        errors: list[str] = []
        sectors: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for sector_type, response in (
            ("industry", industry),
            ("concept", concept),
        ):
            if isinstance(response, Exception):
                errors.append(f"{sector_type}:{type(response).__name__}")
                continue
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(f"{sector_type}:{response.get('status')}")
                continue
            for sector in ((response.get("data") or {}).get("sectors") or []):
                code = str(sector.get("provider_sector_code") or "")
                if not code:
                    continue
                sectors.append((sector_type, sector, response))
                snapshots.append(
                    _sector_snapshot(
                        response=response,
                        sector=sector,
                        sector_type=sector_type,
                        data_type="sector_reference",
                        bucket_seconds=86400,
                    )
                )

        semaphore = asyncio.Semaphore(8)

        async def fetch_constituents(
            sector_type: str,
            sector: dict[str, Any],
        ):
            async with semaphore:
                return await clients.sina.get_sector_constituents(
                    str(sector["provider_sector_code"]),
                    sector_type=sector_type,
                    page_size=500,
                )

        constituent_results = await asyncio.gather(
            *[
                fetch_constituents(sector_type, sector)
                for sector_type, sector, _response in sectors
            ],
            return_exceptions=True,
        )
        for (sector_type, sector, _response), response in zip(
            sectors,
            constituent_results,
        ):
            code = str(sector["provider_sector_code"])
            if isinstance(response, Exception):
                errors.append(f"constituents:{code}:{type(response).__name__}")
                continue
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(f"constituents:{code}:{response.get('status')}")
                continue
            snapshots.append(
                _snapshot_from_response(
                    response=response,
                    data_type="sector_constituents",
                    subject_type="sector",
                    subject_id=f"sina:{sector_type}:{code}",
                    bucket_seconds=86400,
                )
            )

        if isinstance(etfs, Exception):
            errors.append(f"etf_catalog:{type(etfs).__name__}")
        elif etfs.get("status") == MarketDataStatus.OK.value:
            for item in ((etfs.get("data") or {}).get("etfs") or []):
                symbol = str(item.get("symbol") or item.get("code") or "")
                if not symbol:
                    continue
                snapshots.append(
                    _snapshot_from_response(
                        response={**etfs, "data": item},
                        data_type="etf_reference",
                        subject_type="instrument",
                        subject_id=symbol.lower(),
                        bucket_seconds=86400,
                    )
                )
        else:
            errors.append(f"etf_catalog:{etfs.get('status')}")
        if not snapshots:
            raise RuntimeError(f"all reference sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=len(snapshots),
            status="partial_success" if errors else "success",
            details={
                "errors": errors[:100],
                "error_count": len(errors),
                "sector_count": len(sectors),
            },
        )

    async def _collect_market_daily_catchup(self) -> ObservationBatch:
        bars = await self._collect_market_daily_bars()
        target_dates = await _recent_completed_cn_trade_dates(10)
        complete_dates = await asyncio.to_thread(
            self._etf_shares.list_complete_dates,
            target_dates,
        )
        missing_dates = [
            item for item in target_dates if item not in complete_dates
        ]
        for missing_date in missing_dates:
            etf_batch = await self._collect_etf_daily_shares(
                missing_date.isoformat()
            )
            bars.etf_daily_shares.extend(etf_batch.etf_daily_shares)
            bars.fetched_count += etf_batch.fetched_count
        bars.details["etf_catchup_dates"] = [
            item.isoformat() for item in missing_dates
        ]
        return bars

    async def _collect_market_valuation(self) -> ObservationBatch:
        from src.infrastructure import clients

        responses = await asyncio.gather(
            clients.market_valuation.get_market_valuation_history("sh"),
            clients.market_valuation.get_market_valuation_history("sz"),
            clients.ths.get_market_valuation_thresholds(),
            return_exceptions=True,
        )
        snapshots: list[dict[str, Any]] = []
        errors: list[str] = []
        subject_ids = ["cn:market:sh", "cn:market:sz"]
        latest_pe = await asyncio.to_thread(
            self._snapshots.latest_trade_dates,
            data_type="market_pe",
            subject_ids=subject_ids,
        )
        latest_pb = await asyncio.to_thread(
            self._snapshots.latest_trade_dates,
            data_type="market_pb",
            subject_ids=subject_ids,
        )
        for market_code, subject_id, response in zip(
            ("sh", "sz"),
            subject_ids,
            responses[:2],
        ):
            if isinstance(response, Exception):
                errors.append(
                    f"{market_code}:{type(response).__name__}"
                )
                continue
            if response.get("status") != MarketDataStatus.OK.value:
                errors.append(
                    f"{market_code}:{response.get('status')}"
                )
                continue
            payload = response.get("data") or {}
            snapshots.extend(
                _dated_series_snapshots(
                    response=response,
                    rows=payload.get("pe") or [],
                    data_type="market_pe",
                    subject_type="market",
                    subject_id=subject_id,
                    latest_date=latest_pe.get(subject_id),
                    common_payload={
                        "market_code": market_code,
                        "market_name": payload.get("market_name"),
                        "valuation_source": "third_party",
                    },
                )
            )
        threshold_response = responses[2]
        if isinstance(threshold_response, Exception):
            errors.append(
                "ths_thresholds:"
                f"{type(threshold_response).__name__}"
            )
        elif (
            threshold_response.get("status")
            != MarketDataStatus.OK.value
        ):
            errors.append(
                "ths_thresholds:"
                f"{threshold_response.get('status')}"
            )
        else:
            threshold_subjects = {
                "sh": "cn:market:sh",
                "sz": "cn:market:sz",
                "cyb": "cn:index:cyb",
            }
            latest_thresholds = await asyncio.to_thread(
                self._snapshots.latest_trade_dates,
                data_type="market_valuation_threshold",
                subject_ids=list(threshold_subjects.values()),
            )
            snapshots.extend(
                _valuation_threshold_snapshots(
                    response=threshold_response,
                    latest_dates=latest_thresholds,
                )
            )
            snapshots.extend(
                _dated_series_snapshots(
                    response=response,
                    rows=payload.get("pb") or [],
                    data_type="market_pb",
                    subject_type="market",
                    subject_id=subject_id,
                    latest_date=latest_pb.get(subject_id),
                    common_payload={
                        "market_code": market_code,
                        "market_name": payload.get("market_name"),
                        "valuation_source": "third_party",
                    },
                )
            )
        if not snapshots and errors:
            raise RuntimeError(
                f"all market valuation sources failed: {errors}"
            )
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=len(snapshots),
            status="partial_success" if errors else "success",
            details={"errors": errors},
        )

    async def _collect_bond_index(self) -> ObservationBatch:
        from src.infrastructure import clients

        responses = await asyncio.gather(
            clients.chinabond.get_new_composite_index_history(),
            clients.ths.get_native_bond_market_history("long"),
            clients.ths.get_native_bond_market_history("short"),
            clients.ths.get_native_bond_market_history("benchmark"),
            return_exceptions=True,
        )
        response = responses[0]
        errors: list[str] = []
        if isinstance(response, Exception):
            errors.append(f"chinabond:{type(response).__name__}")
            series = {}
        elif response.get("status") != MarketDataStatus.OK.value:
            errors.append(f"chinabond:{response.get('status')}")
            series = {}
        else:
            series = ((response.get("data") or {}).get("series") or {})
        subject_ids = {
            indicator: f"cn:chinabond:new_composite:{indicator}"
            for indicator in series
        }
        latest_dates = await asyncio.to_thread(
            self._snapshots.latest_trade_dates,
            data_type="bond_index",
            subject_ids=list(subject_ids.values()),
        )
        snapshots: list[dict[str, Any]] = []
        for indicator, rows in series.items():
            subject_id = subject_ids[indicator]
            snapshots.extend(
                _dated_series_snapshots(
                    response=response,
                    rows=rows,
                    data_type="bond_index",
                    subject_type="index",
                    subject_id=subject_id,
                    latest_date=latest_dates.get(subject_id),
                    common_payload={
                        "index_id": (
                            (response.get("data") or {}).get("index_id")
                        ),
                        "index_name": (
                            (response.get("data") or {}).get("index_name")
                        ),
                    },
                )
            )
        bond_subjects = {
            "long": "cn:bond_futures:T9999",
            "short": "cn:bond_futures:TS9999",
            "benchmark": "cn:index:ths_all_a",
        }
        latest_bond_dates = await asyncio.to_thread(
            self._snapshots.latest_trade_dates,
            data_type="bond_market_price",
            subject_ids=list(bond_subjects.values()),
        )
        for tenor, native_response in zip(
            ("long", "short", "benchmark"),
            responses[1:],
        ):
            if isinstance(native_response, Exception):
                errors.append(
                    f"ths_bond_{tenor}:"
                    f"{type(native_response).__name__}"
                )
                continue
            if (
                native_response.get("status")
                != MarketDataStatus.OK.value
            ):
                errors.append(
                    f"ths_bond_{tenor}:"
                    f"{native_response.get('status')}"
                )
                continue
            payload = native_response.get("data") or {}
            subject_id = bond_subjects[tenor]
            snapshots.extend(
                _dated_series_snapshots(
                    response=native_response,
                    rows=payload.get("items") or [],
                    data_type="bond_market_price",
                    subject_type=(
                        "index" if tenor == "benchmark" else "futures"
                    ),
                    subject_id=subject_id,
                    latest_date=latest_bond_dates.get(subject_id),
                    common_payload={
                        "tenor": tenor,
                        "code": payload.get("code"),
                        "name": payload.get("name"),
                        "instrument_type": (
                            "broad_market_benchmark"
                            if tenor == "benchmark"
                            else "continuous_bond_futures"
                        ),
                    },
                )
            )
        if not snapshots:
            raise RuntimeError(f"all bond sources failed: {errors}")
        return ObservationBatch(
            snapshots=snapshots,
            fetched_count=len(snapshots),
            status="partial_success" if errors else "success",
            details={
                "series_count": len(series),
                "official_source": True,
                "errors": errors,
            },
        )

    def _latest_bucket_at(
        self,
        data_type: str,
        subject_id: str,
    ) -> datetime | None:
        rows = self._snapshots.query_latest(
            subject_ids=[subject_id],
            data_types=[data_type],
        )
        return rows[0].get("bucket_at") if rows else None

def _require_ok(response: dict[str, Any], label: str) -> None:
    if response.get("status") != MarketDataStatus.OK.value:
        raise RuntimeError(
            f"{label} failed: status={response.get('status')} "
            f"message={response.get('message') or ''}"
        )


def _require_open_session(session_response: dict[str, Any]) -> None:
    _require_ok(session_response, "market session")
    data = session_response.get("data") or {}
    if not data.get("is_trading_day") or data.get("market_session") != "open":
        raise CollectionSkipped(
            f"cn_market_not_open:{data.get('market_session') or 'closed'}"
        )


def _hot_sector_poll_interval_seconds(now: datetime) -> int:
    """Adaptive cadence for THS attention signals, which move off-session."""

    local = now.astimezone(CN_TIMEZONE)
    minute = local.hour * 60 + local.minute
    is_weekday = local.weekday() < 5
    if is_weekday and (
        9 * 60 + 30 <= minute < 11 * 60 + 30
        or 13 * 60 <= minute < 15 * 60
    ):
        return 60
    if 60 <= minute < 7 * 60:
        return 1800
    return 300


def _require_cn_observation_window(
    session_response: dict[str, Any],
    *,
    force_boundary: bool,
) -> None:
    _require_ok(session_response, "market session")
    data = session_response.get("data") or {}
    if not data.get("is_trading_day"):
        raise CollectionSkipped("cn_market_closed")
    if force_boundary:
        return
    if data.get("market_session") != "open":
        raise CollectionSkipped(
            f"cn_market_not_open:{data.get('market_session') or 'closed'}"
        )


def _require_cn_series_observation_or_catchup(
    session_response: dict[str, Any],
    *,
    latest_bucket_at: datetime | None,
    force_boundary: bool,
) -> bool:
    """Allow a closed-phase fetch when today's intraday series is incomplete.

    Returning ``True`` means this execution is a recovery fetch.  The caller
    must request the provider's complete intraday series and persist only rows
    at or after ``latest_bucket_at``.  This check intentionally happens before
    the ordinary market-session skip so a process restart cannot strand a
    trading-day series at its pre-restart watermark.
    """

    _require_ok(session_response, "market session")
    data = session_response.get("data") or {}
    if force_boundary:
        return data.get("market_session") != "open"
    if not data.get("is_trading_day"):
        raise CollectionSkipped("cn_market_closed")
    market_session = str(data.get("market_session") or "closed")
    if market_session == "open":
        return False
    if market_session == "pre_market":
        raise CollectionSkipped("cn_market_not_open:pre_market")

    cutoff_value = (
        data.get("break_start_at")
        if market_session == "lunch_break"
        else data.get("close_at")
    )
    expected_cutoff = _parse_datetime(cutoff_value)
    session_date = _parse_date(data.get("date"))
    latest_date = (
        latest_bucket_at.astimezone(CN_TIMEZONE).date()
        if latest_bucket_at is not None
        else None
    )
    if (
        expected_cutoff is not None
        and latest_bucket_at is not None
        and latest_date == session_date
        and latest_bucket_at >= expected_cutoff
    ):
        raise CollectionSkipped(f"cn_market_not_open:{market_session}")
    return True


def _context_bootstrap_subjects(
    session_response: dict[str, Any],
    *,
    latest_buckets: dict[str, datetime | None],
    force_boundary: bool,
) -> list[str]:
    """Allow one historical bootstrap when deployment starts off-session."""

    try:
        _require_cn_observation_window(
            session_response,
            force_boundary=force_boundary,
        )
    except CollectionSkipped:
        missing_subjects = [
            subject_id
            for subject_id, latest_bucket_at in latest_buckets.items()
            if latest_bucket_at is None
        ]
        if not missing_subjects:
            raise
        return missing_subjects
    return []


def _ths_event_sources(now_cn: time) -> list[tuple[str, str]]:
    """Select only the native event streams relevant to the market phase."""

    sources: list[tuple[str, str]] = []
    if now_cn <= time(9, 30):
        sources.append(("call_auction", "cn:a_share:call_auction"))
    if now_cn >= time(9, 25):
        sources.append(("market_anomaly", "cn:a_share:ths_anomaly"))
    return sources


def _require_cn_stock_change_window(
    session_response: dict[str, Any],
    *,
    force_boundary: bool = False,
) -> None:
    _require_ok(session_response, "market session")
    data = session_response.get("data") or {}
    if force_boundary:
        return
    if not data.get("is_trading_day"):
        raise CollectionSkipped("cn_market_closed")
    now_cn = datetime.now(timezone.utc).astimezone(CN_TIMEZONE).time()
    if not time(9, 15) <= now_cn <= time(15, 5):
        raise CollectionSkipped("cn_stock_change_window_closed")


async def _latest_cn_trading_date(calendar_client) -> date | None:
    today = datetime.now(timezone.utc).astimezone(CN_TIMEZONE).date()
    response = await calendar_client.get_trading_calendar(
        "cn",
        today - timedelta(days=14),
        today,
    )
    if response.get("status") != MarketDataStatus.OK.value:
        return None
    dates = [
        _parse_date(item.get("date"))
        for item in ((response.get("data") or {}).get("days") or [])
        if item.get("is_trading_day")
    ]
    return max((item for item in dates if item is not None), default=None)


def _sector_snapshot(
    *,
    response: dict[str, Any],
    sector: dict[str, Any],
    sector_type: str,
    data_type: str,
    bucket_seconds: int,
    identity_suffix: str | None = None,
    trade_date_override: date | None = None,
    bucket_at_override: datetime | None = None,
) -> dict[str, Any]:
    provider = str(response.get("provider") or "unknown")
    code = str(sector.get("provider_sector_code") or "").strip()
    name = str(sector.get("sector_name") or sector.get("name") or "").strip()
    if not code and not name:
        raise ValueError("sector identity is empty")
    identity = code or _normalized_name(name)
    data = {
        **sector,
        "sector_type": sector_type,
        "provider_sector_code": code or None,
        "sector_name": name,
        "mapping_status": (
            "provider_identity" if code else "mapping_pending"
        ),
    }
    subject_id = f"{provider}:{sector_type}:{identity}"
    if identity_suffix:
        subject_id = f"{subject_id}:{identity_suffix}"
    return _snapshot_from_response(
        response={**response, "data": data},
        data_type=data_type,
        subject_type="sector",
        subject_id=subject_id,
        bucket_seconds=bucket_seconds,
        trade_date_override=trade_date_override,
        bucket_at_override=bucket_at_override,
    )


def _snapshot_from_response(
    *,
    response: dict[str, Any],
    data_type: str,
    subject_type: str,
    subject_id: str,
    bucket_seconds: int,
    trade_date_override: date | None = None,
    bucket_at_override: datetime | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    fetched_at = _parse_datetime(response.get("fetched_at")) or now
    source_time = response.get("source_time")
    observed_at = (
        _parse_datetime(response.get("observed_at"))
        if source_time
        else None
    )
    metadata = response.get("provider_metadata") or {}
    freshness = str(metadata.get("freshness") or "").lower()
    latency: float | None = None
    if observed_at is not None:
        latency = max(0.0, (fetched_at - observed_at).total_seconds())
    if freshness == "delayed" or (
        latency is not None and latency > 180
    ):
        freshness_status = "delayed"
    elif observed_at is None:
        # Some successful quote/ranking endpoints do not expose an upstream
        # timestamp. Keep observed_at empty, but distinguish a known fetch
        # time from a completely unknown data timestamp.
        freshness_status = "fetch_time"
    else:
        freshness_status = "realtime"
    payload = response.get("data") or {}
    resolved_trade_date = trade_date_override or _parse_date(
        response.get("trade_date")
    )
    if (
        resolved_trade_date is None
        and str(response.get("market") or "").lower() == "cn"
        and subject_type in {"market", "index", "stock", "sector", "fund", "etf"}
    ):
        session = ChinaExchangeCalendarService().resolve(fetched_at)
        resolved_trade_date = (
            session.previous_trade_date
            if session.market_session == "pre_open"
            else session.trade_date
        )
    if resolved_trade_date is None:
        resolved_trade_date = fetched_at.astimezone(CN_TIMEZONE).date()
    bucket_at = bucket_at_override
    if bucket_at is None and bucket_seconds >= 86400:
        timezone_name = str(response.get("timezone") or "Asia/Shanghai")
        try:
            market_timezone = ZoneInfo(timezone_name)
        except (KeyError, ValueError):
            market_timezone = CN_TIMEZONE
        bucket_at = datetime.combine(
            resolved_trade_date,
            time.min,
            tzinfo=market_timezone,
        ).astimezone(timezone.utc)
    if bucket_at is None:
        bucket_at = _floor_bucket(now, bucket_seconds)
    return {
        "data_type": data_type,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "market": str(response.get("market") or "unknown"),
        "provider": str(response.get("provider") or "unknown"),
        "trade_date": resolved_trade_date,
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "bucket_at": bucket_at,
        "freshness_status": freshness_status,
        "source_latency_seconds": latency,
        "payload_hash": _payload_hash(payload),
        "data": payload,
    }


def _native_chart_snapshots(
    *,
    response: dict[str, Any],
    data_type: str,
    subject_type: str,
    subject_id: str,
    latest_bucket_at: datetime | None,
) -> list[dict[str, Any]]:
    payload = response.get("data") or {}
    points = payload.get("points") or []
    fetched_at = (
        _parse_datetime(response.get("fetched_at"))
        or datetime.now(timezone.utc)
    )
    provider = str(response.get("provider") or "ths_native")
    result: list[dict[str, Any]] = []
    latest_index = len(points) - 1
    for position, point in enumerate(points):
        raw_time = point.get("time") or point.get("date")
        observed_at = _parse_ths_chart_time(raw_time)
        if observed_at is None:
            continue
        if latest_bucket_at is not None and observed_at < latest_bucket_at:
            continue
        point_payload = {
            **point,
            "indicator": payload.get("indicator"),
            "indicator_key": payload.get("indicator_key"),
            "name": payload.get("name"),
        }
        if position == latest_index:
            point_payload["summary"] = payload.get("summary") or {}
        latency = max(0.0, (fetched_at - observed_at).total_seconds())
        result.append(
            {
                "data_type": data_type,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "market": str(response.get("market") or "cn"),
                "provider": provider,
                "trade_date": _ths_chart_trade_date(
                    observed_at,
                    data_type=data_type,
                    subject_id=subject_id,
                ),
                "observed_at": observed_at,
                "fetched_at": fetched_at,
                "bucket_at": observed_at,
                "freshness_status": (
                    "realtime" if latency <= 180 else "delayed"
                ),
                "source_latency_seconds": latency,
                "payload_hash": _payload_hash(point_payload),
                "data": point_payload,
            }
        )
    return result


def _ths_chart_trade_date(
    observed_at: datetime,
    *,
    data_type: str,
    subject_id: str,
) -> date:
    """Map cross-midnight futures points to one trading session."""

    local_value = observed_at.astimezone(CN_TIMEZONE)
    trade_date = local_value.date()
    if data_type != "futures_intraday":
        return trade_date
    rollover_time = {
        "global:futures:ftse_a50": time(16, 45),
        "global:futures:dow_jones": time(17, 0),
    }.get(subject_id)
    if rollover_time is not None and local_value.time() >= rollover_time:
        return trade_date + timedelta(days=1)
    return trade_date


def _parse_ths_chart_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\D", "", str(value))
    formats = {12: "%Y%m%d%H%M", 8: "%Y%m%d"}
    date_format = formats.get(len(text))
    if date_format is None:
        return None
    try:
        local_value = datetime.strptime(text, date_format).replace(
            tzinfo=CN_TIMEZONE
        )
    except ValueError:
        return None
    return local_value.astimezone(timezone.utc)


def _etf_estimated_flow_snapshots(
    *,
    response: dict[str, Any],
    latest_bucket_at: datetime | None,
) -> list[dict[str, Any]]:
    payload = response.get("data") or {}
    trend = payload.get("trend") or []
    if not trend:
        return []
    fetched_at = (
        _parse_datetime(response.get("fetched_at"))
        or datetime.now(timezone.utc)
    )
    provider = str(response.get("provider") or "ths")
    latest_timestamp = max(
        int(item["timestamp"])
        for item in trend
        if item.get("timestamp") is not None
    )
    benchmark_by_timestamp = {
        int(item["timestamp"]): item.get("index_value")
        for item in (payload.get("benchmark_trend") or [])
        if item.get("timestamp") is not None
    }
    rows: list[dict[str, Any]] = []
    for item in trend:
        raw_timestamp = item.get("timestamp")
        if raw_timestamp is None:
            continue
        observed_at = datetime.fromtimestamp(
            int(raw_timestamp),
            tz=timezone.utc,
        )
        if (
            latest_bucket_at is not None
            and observed_at < latest_bucket_at
        ):
            continue
        is_latest = int(raw_timestamp) == latest_timestamp
        net_inflow = item.get("net_inflow_yuan")
        if is_latest:
            net_inflow = payload.get(
                "total_net_inflow_yuan",
                net_inflow,
            )
        point_payload = {
            "net_inflow_yuan": net_inflow,
            "benchmark_index_value": benchmark_by_timestamp.get(int(raw_timestamp)),
            "benchmark": payload.get("benchmark"),
            "value_type": "estimated",
            "coverage_market": payload.get("coverage_market"),
            "methodology": payload.get("methodology"),
            "is_official_subscription": False,
        }
        if is_latest:
            point_payload.update(
                {
                    "top_inflow": payload.get("top_inflow"),
                    "ranking_scope": payload.get("ranking_scope"),
                    "ranking_fund_count": payload.get(
                        "ranking_fund_count"
                    ),
                    "benchmark_trend": payload.get("benchmark_trend") or [],
                }
            )
        latency = max(
            0.0,
            (fetched_at - observed_at).total_seconds(),
        )
        rows.append(
            {
                "data_type": "etf_estimated_net_inflow",
                "subject_type": "etf_market",
                "subject_id": "cn:etf:szse:estimated_net_inflow",
                "market": "cn",
                "provider": provider,
                "trade_date": observed_at.astimezone(
                    CN_TIMEZONE
                ).date(),
                "observed_at": observed_at,
                "fetched_at": fetched_at,
                "bucket_at": observed_at,
                "freshness_status": (
                    "realtime" if latency <= 180 else "delayed"
                ),
                "source_latency_seconds": latency,
                "payload_hash": _payload_hash(point_payload),
                "data": point_payload,
            }
        )
    return rows


def _stock_change_snapshot(
    item: dict[str, Any],
    *,
    trade_date: date,
    fetched_at: datetime,
) -> dict[str, Any]:
    local_time = datetime.strptime(
        f"{trade_date.isoformat()} {item['time']}",
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=CN_TIMEZONE)
    observed_at = local_time.astimezone(timezone.utc)
    market_value = item.get("market")
    market_code = "" if market_value is None else str(market_value)
    code = str(item["code"]).strip()
    type_value = item.get("typeCode")
    type_code = "unknown" if type_value is None else str(type_value)
    event_fingerprint = _payload_hash(item)[:12]
    subject_id = (
        f"cn:stock_change:{trade_date.isoformat()}:{item['time']}:"
        f"{market_code}:{code}:{type_code}:{event_fingerprint}"
    )
    latency = max(0.0, (fetched_at - observed_at).total_seconds())
    payload = {
        **item,
        "event_id": subject_id,
        "event_time": local_time.isoformat(),
    }
    return {
        "data_type": "stock_change",
        "subject_type": "event",
        "subject_id": subject_id,
        "market": "cn",
        "provider": "eastmoney",
        "trade_date": trade_date,
        "observed_at": observed_at,
        "fetched_at": fetched_at,
        "bucket_at": observed_at,
        "freshness_status": (
            "realtime" if latency <= 180 else "delayed"
        ),
        "source_latency_seconds": latency,
        "payload_hash": _payload_hash(payload),
        "data": payload,
    }


def _ths_event_snapshots(
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    """Persist the Short Spirit buffers as idempotent event facts."""

    fetched_at = _parse_datetime(response.get("fetched_at")) or datetime.now(
        timezone.utc
    )
    provider = str(response.get("provider") or "ths_native")
    response_data = response.get("data") or {}
    result: list[dict[str, Any]] = []
    for source_key, data_type, entity_type, stream in THS_EVENT_STREAMS:
        for raw_item in response_data.get(source_key) or []:
            if not isinstance(raw_item, dict):
                continue
            try:
                observed_at = datetime.fromtimestamp(
                    int(raw_item.get("time")),
                    tz=timezone.utc,
                )
            except (TypeError, ValueError, OSError):
                continue
            event_type_id = str(raw_item.get("dataid") or "unknown")
            market_code = str(raw_item.get("marketcode") or "unknown")
            entity_code = str(raw_item.get("stockcode") or "unknown")
            event_fingerprint = _payload_hash(
                {"stream": stream, **raw_item}
            )[:16]
            trade_date = observed_at.astimezone(CN_TIMEZONE).date()
            event_id = (
                f"cn:ths_event:{trade_date.isoformat()}:{stream}:"
                f"{market_code}:{entity_code}:{event_type_id}:"
                f"{event_fingerprint}"
            )
            local_event_time = observed_at.astimezone(CN_TIMEZONE)
            payload = {
                **raw_item,
                "event_id": event_id,
                "stream": stream,
                "entity_type": entity_type,
                "event_type_id": event_type_id,
                "event_type": THS_EVENT_LABELS.get(event_type_id),
                "event_time": local_event_time.isoformat(),
            }
            latency = max(0.0, (fetched_at - observed_at).total_seconds())
            result.append(
                {
                    "data_type": data_type,
                    "subject_type": "event",
                    "subject_id": event_id,
                    "market": "cn",
                    "provider": provider,
                    "trade_date": trade_date,
                    "observed_at": observed_at,
                    "fetched_at": fetched_at,
                    "bucket_at": observed_at,
                    "freshness_status": (
                        "realtime" if latency <= 180 else "delayed"
                    ),
                    "source_latency_seconds": latency,
                    "payload_hash": _payload_hash(payload),
                    "data": payload,
                }
            )
    return result


def _dated_series_snapshots(
    *,
    response: dict[str, Any],
    rows: list[dict[str, Any]],
    data_type: str,
    subject_type: str,
    subject_id: str,
    latest_date: date | None,
    common_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        observed_date = _parse_date(row.get("date"))
        if observed_date is None:
            continue
        if latest_date is not None and observed_date < latest_date:
            continue
        result.append(
            _dated_snapshot(
                response=response,
                payload={**(common_payload or {}), **row},
                data_type=data_type,
                subject_type=subject_type,
                subject_id=subject_id,
                observed_date=observed_date,
            )
        )
    return result


def _valuation_threshold_snapshots(
    *,
    response: dict[str, Any],
    latest_dates: dict[str, date],
) -> list[dict[str, Any]]:
    payload = response.get("data") or {}
    points = payload.get("points") or []
    definitions = {
        "sh": {
            "subject_id": "cn:market:sh",
            "market_name": "上证指数",
            "price_key": "szzz",
            "prefix": "szzz",
        },
        "sz": {
            "subject_id": "cn:market:sz",
            "market_name": "深证成指",
            "price_key": "szcz",
            "prefix": "szcz",
        },
        "cyb": {
            "subject_id": "cn:index:cyb",
            "market_name": "创业板指",
            "price_key": "cyb",
            "prefix": "cyb",
        },
    }
    result: list[dict[str, Any]] = []
    for market_code, definition in definitions.items():
        prefix = definition["prefix"]
        rows = [
            {
                "date": point.get("date"),
                "market_code": market_code,
                "market_name": definition["market_name"],
                "index_price": point.get(definition["price_key"]),
                "risk_pe": point.get(f"{prefix}_risk_pe"),
                "chance_pe": point.get(f"{prefix}_chance_pe"),
                "risk_pb": point.get(f"{prefix}_risk_pb"),
                "chance_pb": point.get(f"{prefix}_chance_pb"),
                "value_semantics": "risk_and_opportunity_thresholds",
                "is_current_market_pe_pb": False,
            }
            for point in points
        ]
        subject_id = str(definition["subject_id"])
        result.extend(
            _dated_series_snapshots(
                response=response,
                rows=rows,
                data_type="market_valuation_threshold",
                subject_type="market",
                subject_id=subject_id,
                latest_date=latest_dates.get(subject_id),
            )
        )
    return result


def _with_etf_daily_share_change(
    item: dict[str, Any],
    *,
    previous_date: date | None,
    previous_shares: Any,
) -> dict[str, Any]:
    current = float(item["shares"])
    if previous_date is None or previous_shares is None:
        return {
            **item,
            "previous_trade_date": None,
            "previous_shares": None,
            "confirmed_share_change": None,
            "confirmed_share_change_rate": None,
            "flow_confirmation_frequency": "daily",
            "realtime_net_subscription_available": False,
        }
    previous = float(previous_shares)
    change = current - previous
    return {
        **item,
        "previous_trade_date": previous_date.isoformat(),
        "previous_shares": previous,
        "confirmed_share_change": change,
        "confirmed_share_change_rate": (
            change / previous * 100 if previous else None
        ),
        "flow_confirmation_frequency": "daily",
        "realtime_net_subscription_available": False,
    }


def _coerce_market_response(
    raw: dict[str, Any],
    *,
    provider: str,
    market: str,
) -> dict[str, Any]:
    if "status" in raw and "fetched_at" in raw:
        return raw
    now = datetime.now(timezone.utc)
    data = raw.get("data") if isinstance(raw, dict) else None
    return {
        "status_code": raw.get("status_code", 0),
        "status": (
            MarketDataStatus.OK.value
            if data not in (None, {}, [])
            else MarketDataStatus.EMPTY.value
        ),
        "provider": provider,
        "market": market,
        "observed_at": now.isoformat(),
        "fetched_at": now.isoformat(),
        "data": data,
        "provider_metadata": {},
    }


def _interest_rate_snapshots(
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    data = response.get("data") or {}
    rows: list[tuple[str, dict[str, Any]]] = []
    for tenor, values in (data.get("shibor") or {}).items():
        if values:
            rows.append((f"cn:shibor:{str(tenor).lower()}", values[0]))
    for item in (data.get("lpr") or [])[:1]:
        rows.append(("cn:lpr", item))
    return [
        _dated_snapshot(
            response=response,
            payload=payload,
            data_type="interest_rate",
            subject_type="rate",
            subject_id=subject_id,
            observed_date=_parse_date(payload.get("date")),
        )
        for subject_id, payload in rows
        if _parse_date(payload.get("date"))
    ]


def _government_yield_snapshots(
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for item in ((response.get("data") or {}).get("yields") or []):
        market = str(item.get("market") or "")
        tenor = str(item.get("tenor") or "")
        observed_date = _parse_date(item.get("date"))
        if not market or not tenor or observed_date is None:
            continue
        key = (market, tenor)
        current_date = _parse_date((latest.get(key) or {}).get("date"))
        if current_date is None or observed_date >= current_date:
            latest[key] = item
    return [
        _dated_snapshot(
            response=response,
            payload=payload,
            data_type="government_bond_yield",
            subject_type="rate",
            subject_id=f"{market}:government_bond:{tenor}",
            observed_date=_parse_date(payload.get("date")),
        )
        for (market, tenor), payload in latest.items()
    ]


def _bar_snapshots(
    *,
    response: dict[str, Any],
    data_type: str,
    subject_type: str,
    subject_id: str,
    bars_key: str = "bars",
    common_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    payload = response.get("data") or {}
    bars = payload.get(bars_key) or []
    result: list[dict[str, Any]] = []
    for bar in bars:
        observed_date = _parse_date(bar.get("date"))
        if observed_date is None:
            continue
        result.append(
            _dated_snapshot(
                response=response,
                payload={
                    **bar,
                    **(common_payload or {}),
                    "interval": payload.get("interval") or "1d",
                    "name": payload.get("name"),
                    "symbol": payload.get("symbol")
                    or payload.get("code"),
                },
                data_type=data_type,
                subject_type=subject_type,
                subject_id=subject_id,
                observed_date=observed_date,
            )
        )
    return result


def _etf_daily_quote_snapshots(
    response: dict[str, Any],
) -> list[dict[str, Any]]:
    observed_date = (
        _parse_date(response.get("trade_date"))
        or datetime.now(timezone.utc).astimezone(CN_TIMEZONE).date()
    )
    result: list[dict[str, Any]] = []
    for item in ((response.get("data") or {}).get("etfs") or []):
        symbol = str(item.get("symbol") or item.get("code") or "").lower()
        if not symbol:
            continue
        result.append(
            _dated_snapshot(
                response=response,
                payload=item,
                data_type="etf_daily_quote",
                subject_type="instrument",
                subject_id=symbol,
                observed_date=observed_date,
            )
        )
    return result


def _dated_snapshot(
    *,
    response: dict[str, Any],
    payload: dict[str, Any],
    data_type: str,
    subject_type: str,
    subject_id: str,
    observed_date: date | None,
) -> dict[str, Any]:
    if observed_date is None:
        raise ValueError("observed_date is required")
    fetched_at = _parse_datetime(response.get("fetched_at")) or datetime.now(
        timezone.utc
    )
    bucket_at = datetime.combine(
        observed_date,
        time.min,
        tzinfo=timezone.utc,
    )
    return {
        "data_type": data_type,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "market": str(response.get("market") or "unknown"),
        "provider": str(response.get("provider") or "unknown"),
        "trade_date": observed_date,
        "observed_at": None,
        "fetched_at": fetched_at,
        "bucket_at": bucket_at,
        "freshness_status": "historical",
        "source_latency_seconds": None,
        "payload_hash": _payload_hash(payload),
        "data": payload,
    }


def _response_count(response: dict[str, Any]) -> int:
    data = response.get("data") or {}
    if isinstance(data, dict):
        try:
            return int(data.get("count") or 1)
        except (TypeError, ValueError):
            return 1
    return len(data) if isinstance(data, list) else 1


async def _latest_completed_cn_trade_date() -> date:
    from src.infrastructure import clients

    now_cn = datetime.now(timezone.utc).astimezone(CN_TIMEZONE)
    start = now_cn.date() - timedelta(days=10)
    response = await clients.market_calendar.get_trading_calendar(
        "cn",
        start,
        now_cn.date(),
    )
    _require_ok(response, "CN trading calendar")
    trading_days = [
        date.fromisoformat(item["date"])
        for item in ((response.get("data") or {}).get("days") or [])
        if item.get("is_trading_day")
    ]
    if now_cn.time() < time(18, 0):
        trading_days = [item for item in trading_days if item < now_cn.date()]
    if not trading_days:
        raise RuntimeError("no completed CN trading day found")
    return max(trading_days)


async def _recent_completed_cn_trade_dates(limit: int) -> list[date]:
    from src.infrastructure import clients

    now_cn = datetime.now(timezone.utc).astimezone(CN_TIMEZONE)
    response = await clients.market_calendar.get_trading_calendar(
        "cn",
        now_cn.date() - timedelta(days=max(20, limit * 3)),
        now_cn.date(),
    )
    _require_ok(response, "CN trading calendar")
    dates = [
        date.fromisoformat(item["date"])
        for item in ((response.get("data") or {}).get("days") or [])
        if item.get("is_trading_day")
    ]
    if now_cn.time() < time(18, 0):
        dates = [item for item in dates if item < now_cn.date()]
    return sorted(dates, reverse=True)[: max(1, limit)]


def _checkpoint_view(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: state.get(key)
        for key in (
            "mode",
            "target_time",
            "newest_time",
            "oldest_time",
            "backfill_status",
            "cursor",
        )
    }


def _unrecoverable_gap(
    *,
    source_name: str,
    state: dict[str, Any],
    interval_seconds: int,
) -> dict[str, Any] | None:
    if source_name not in {
        "market_breadth",
        "sector_market",
        "sector_fund_flow",
        "cross_market",
    }:
        return None
    last_success = _parse_datetime(state.get("last_success_at"))
    if last_success is None:
        return None
    now = datetime.now(timezone.utc)
    gap_seconds = (now - last_success).total_seconds()
    if gap_seconds <= max(60, interval_seconds * 2):
        return None
    return {
        "started_at": last_success.isoformat(),
        "ended_at": now.isoformat(),
        "gap_seconds": round(gap_seconds, 3),
        "reason": "current_snapshot_source_cannot_backfill",
    }


def _recent_stock_dynamic_candidate_groups(
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    max_age_seconds: int = 1800,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        fetched_at = _parse_datetime(
            row.get("fetched_at") or row.get("bucket_at")
        )
        if fetched_at is None or (now - fetched_at).total_seconds() > max_age_seconds:
            continue
        data = dict(row.get("data") or {})
        if (
            data.get("candidate_pool_complete") is not True
            or not isinstance(data.get("candidate_stocks"), list)
        ):
            continue
        data_code = str(
            data.get("data_code") or row.get("subject_id") or ""
        ).strip()
        if data_code:
            groups[data_code] = data
    return groups


def _hydrate_featured_stocks(
    featured_stocks: list[dict[str, Any]],
    candidate_stocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates_by_code = {
        str(item.get("code") or "").strip(): item
        for item in candidate_stocks
        if isinstance(item, dict) and item.get("code")
    }
    hydrated: list[dict[str, Any]] = []
    for item in featured_stocks:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        candidate = dict(candidates_by_code.get(code) or {})
        merged = {**candidate, **item}
        for key in ("name", "latest", "change_rate", "speed"):
            if merged.get(key) in {None, ""} and candidate.get(key) not in {
                None,
                "",
            }:
                merged[key] = candidate[key]
        candidate_indicators = dict(candidate.get("indicators") or {})
        featured_indicators = {
            key: value
            for key, value in dict(item.get("indicators") or {}).items()
            if value not in {None, ""}
        }
        if candidate_indicators or featured_indicators:
            merged["indicators"] = {
                **candidate_indicators,
                **featured_indicators,
            }
        hydrated.append(merged)
    return hydrated


def _gold_ai_news_records(source: Any) -> list[dict[str, Any]]:
    """把同花顺黄金AI事件规范化为 ft_news 行，原始响应仍保留在快照中。"""

    current = source
    for _ in range(4):
        if not isinstance(current, dict) or "data" not in current:
            break
        current = current["data"]
    if not isinstance(current, list):
        return []

    records: list[dict[str, Any]] = []
    for item in current:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("news_ai_summary") or "").strip()
        published_text = str(item.get("news_create_time") or "").strip()
        if not summary or not published_text:
            continue
        try:
            published_at = datetime.strptime(
                published_text,
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=CN_TIMEZONE)
        except ValueError:
            continue
        url = str(item.get("news_pc_url") or item.get("news_url") or "").strip()
        source_name = str(item.get("news_source") or "同花顺").strip()
        identity = url or f"{published_text}:{summary}"
        fingerprint = hashlib.sha256(
            f"ths_gold_ai:{identity}".encode("utf-8")
        ).hexdigest()
        normalized_title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", summary.lower())
        dedup_key = hashlib.sha256(
            f"{published_at.date().isoformat()}:{normalized_title}".encode("utf-8")
        ).hexdigest()
        records.append(
            {
                "title": summary,
                "summary": summary,
                "content": "",
                "source": "ths_gold_ai",
                "source_name": source_name,
                "source_reliability": 0.7,
                "category": "commodity",
                "url": url,
                "tags": ["黄金", "贵金属", "同花顺AI要点"],
                "related_stocks": [],
                "published_at": published_at,
                "fingerprint": fingerprint,
                "news_kind": "news",
                "dedup_key": dedup_key,
                "content_fingerprint": None,
            }
        )
    return records


def _newest_business_time(batch: ObservationBatch) -> str | None:
    values = [
        item["bucket_at"].isoformat()
        for item in batch.snapshots
        if item.get("bucket_at")
    ]
    values.extend(
        item["trade_date"].isoformat()
        for item in batch.etf_daily_shares
        if item.get("trade_date")
    )
    return max(values) if values else None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _floor_bucket(value: datetime, seconds: int) -> datetime:
    timestamp = int(value.timestamp())
    return datetime.fromtimestamp(
        timestamp - timestamp % max(1, seconds),
        tz=timezone.utc,
    )


def _snapshot_bucket_matches_trade_date(snapshot: dict[str, Any]) -> bool:
    """Detect legacy daily buckets floored at UTC instead of market midnight."""

    trade_date = _parse_date(snapshot.get("trade_date"))
    bucket_at = _parse_datetime(snapshot.get("bucket_at"))
    if trade_date is None or bucket_at is None:
        return False
    return bucket_at.astimezone(CN_TIMEZONE).date() == trade_date


def _payload_hash(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalized_name(value: str) -> str:
    return "".join(value.lower().split())
