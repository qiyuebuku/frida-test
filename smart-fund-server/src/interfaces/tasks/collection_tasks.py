"""数据采集定时任务 — jettask-rs Thin Wrapper 调 Application Services

R3.7 重构后:
- 这个文件不再 import domain 类
- 每个 task 函数 = 调一个 application use case + 处理级联
- 全部业务逻辑下沉到 src/application/services/

jettask-rs 接入边界:
- 本文件只负责注册 TaskRouter handler 和发送 TaskMessage。
- 不在 task 层写业务逻辑,避免任务框架替换污染 application service。
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

import redis
from jettask import TaskRouter

from src.application.services import (
    CollectionAppService,
    MarketObservationService,
)
from src.application.services.collection_backfill_chain_service import (
    CollectionBackfillChainService,
)
from src.infrastructure.clients import init_clients, ths
from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL
from src.infrastructure.persistence.repositories import CollectionRunRepository
from src.infrastructure.persistence.repositories.collection_state_repository_impl import (
    CollectionStateRepositoryImpl,
)
from src.infrastructure.tasks.jettask_dispatcher import send_kg_news_ingest

logger = logging.getLogger(__name__)


# 确保客户端已初始化（Worker 进程不经过 FastAPI 生命周期）
if ths is None:
    init_clients()


# ==================== 单例应用服务 ====================
# 每个进程一份，避免每次 task 都重新构造。

_collection = CollectionAppService()
_market_observation = MarketObservationService()


router = TaskRouter()

DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
DEFAULT_RETRY_BACKOFF_MAX_SECONDS = 300


async def _run_observed_dispatch(task_name: str, operation):
    """Record scheduler-only dispatchers in the same task observability table."""
    runs = CollectionRunRepository()
    states = CollectionStateRepositoryImpl()
    started_at = datetime.now(timezone.utc)
    await asyncio.to_thread(
        states.mark_started,
        task_id=task_name,
        aggregator="internal",
        source_name=task_name,
        task_type="internal",
    )
    run_id = await asyncio.to_thread(
        runs.start,
        task_name=task_name,
        source_name="system_dispatch",
    )
    try:
        result = await operation()
        result_dict = result if isinstance(result, dict) else {}
        count = int(result_dict.get("dispatched") or result_dict.get("saved_count") or 0)
        reported_status = str(result_dict.get("status") or "success")
        state_status = (
            "partial_success" if reported_status == "partial"
            else "skipped" if reported_status == "skipped"
            else "success"
        )
        await asyncio.to_thread(
            runs.finish,
            run_id,
            status=state_status,
            fetched_count=count,
            valid_count=count,
            saved_count=0,
            details=result,
        )
        await asyncio.to_thread(
            states.mark_finished,
            task_id=task_name,
            aggregator="internal",
            source_name=task_name,
            task_type="internal",
            status=state_status,
            started_at=started_at,
            fetched_count=count,
            saved_count=int(result_dict.get("saved_count") or 0),
            details=result_dict,
        )
        return result
    except Exception as exc:
        await asyncio.to_thread(
            runs.finish,
            run_id,
            status="failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        await asyncio.to_thread(
            states.mark_finished,
            task_id=task_name,
            aggregator="internal",
            source_name=task_name,
            task_type="internal",
            status="failed",
            started_at=started_at,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


def _register_task(
    queue: str,
    *,
    max_retries: int,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    retry_backoff_max: int = DEFAULT_RETRY_BACKOFF_MAX_SECONDS,
):
    """jettask-rs 任务装饰器。

    jettask-rs 的 Python binding 使用秒级数值退避参数；这里集中声明，
    避免散落旧框架的布尔 retry_backoff 写法。
    """
    return router.task(
        queue=queue,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        retry_backoff_max=retry_backoff_max,
    )


# ==================== P0: 核心任务 ====================


@_register_task(queue="collect_collection_source", max_retries=2)
async def collect_collection_source(aggregator: str, source_name: str):
    """执行一条平铺后的数据源任务；周期和重试由 JetTask 独立控制。"""
    task_name = f"collect_{aggregator}_{source_name}"
    runs = CollectionRunRepository()
    run_id = await asyncio.to_thread(
        runs.start,
        task_name=task_name,
        source_name=source_name,
        details={"aggregator": aggregator},
    )
    started_at = time.time()
    try:
        result = await _collection.run_scheduled_collection_source(
            aggregator, source_name
        )
        if result is None:
            await asyncio.to_thread(
                runs.finish,
                run_id,
                status="skipped",
                skipped_count=1,
                details={"aggregator": aggregator, "reason": "backfill_chain_owned"},
            )
            return {"status": "skipped", "reason": "backfill_chain_owned"}
        kg_event_ids = []
        if aggregator == "news" and result.new_ids:
            kg_event_ids = await send_kg_news_ingest(result.new_ids)
        await asyncio.to_thread(
            runs.finish,
            run_id,
            status="success",
            fetched_count=int(result.fetched_count or 0),
            valid_count=int(result.valid_count or 0),
            saved_count=int(result.total_saved or 0),
            checkpoint_after=result.checkpoint_after or {},
            details={
                "aggregator": aggregator,
                "kg_event_ids": kg_event_ids,
            },
        )
        logger.info(
            "[%s] 完成，耗时 %.1fs result=%s",
            task_name,
            time.time() - started_at,
            result.to_dict(),
        )
        return result.to_dict()
    except Exception as exc:
        await asyncio.to_thread(
            runs.finish,
            run_id,
            status="failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
            details={"aggregator": aggregator},
        )
        raise


@_register_task(queue="advance_collection_backfill", max_retries=3)
async def advance_collection_backfill(aggregator: str, source_name: str):
    return await _run_observed_dispatch(
        "advance_collection_backfill",
        lambda: CollectionBackfillChainService().advance(
            aggregator=aggregator,
            source_name=source_name,
        ),
    )


@_register_task(queue="collect_watchlist_instruments", max_retries=3)
async def collect_watchlist_instruments(
    codes: list[str],
    scope: str = "bootstrap",
):
    """新增或恢复标的后立即执行首轮采集。"""

    logger.info("[collect_watchlist_instruments] 开始执行 codes=%s", codes)
    t0 = time.time()
    result = await _run_observed_dispatch(
        "collect_watchlist_instruments",
        lambda: _collection.run_watchlist_instrument_collection(
            codes,
            scope=scope,
        ),
    )
    logger.info(
        "[collect_watchlist_instruments] 完成,耗时 %.1fs result=%s",
        time.time() - t0,
        result,
    )
    return result


@_register_task(queue="scan_watchlist_instruments", max_retries=2)
async def scan_watchlist_instruments():
    """每 15 秒扫描到期标的，并按标的拆分投递采集任务。"""

    result = await _run_observed_dispatch(
        "scan_watchlist_instruments",
        _collection.scan_due_watchlist_instruments,
    )
    logger.info("[scan_watchlist_instruments] result=%s", result)
    return result


@_register_task(queue="scan_watchlist_daily", max_retries=2)
async def scan_watchlist_daily():
    result = await _run_observed_dispatch(
        "scan_watchlist_daily",
        lambda: _collection.dispatch_watchlist_scope("daily"),
    )
    logger.info("[scan_watchlist_daily] result=%s", result)
    return result


@_register_task(queue="scan_watchlist_reference", max_retries=2)
async def scan_watchlist_reference():
    result = await _run_observed_dispatch(
        "scan_watchlist_reference",
        lambda: _collection.dispatch_watchlist_scope("reference"),
    )
    logger.info("[scan_watchlist_reference] result=%s", result)
    return result


# ==================== P1: 辅助任务 ====================


@_register_task(queue="collect_market_breadth_snapshot", max_retries=2)
async def collect_market_breadth_snapshot():
    return await _market_observation.collect_market_breadth()


@_register_task(queue="collect_stock_rankings", max_retries=2)
async def collect_stock_rankings(force_boundary: bool = False):
    return await _market_observation.collect_stock_rankings(
        force_boundary=force_boundary
    )


@_register_task(queue="collect_stock_dynamic_groups", max_retries=2)
async def collect_stock_dynamic_groups(force_boundary: bool = False):
    return await _market_observation.collect_stock_dynamic_groups(
        force_boundary=force_boundary
    )


@_register_task(queue="collect_stock_change_events", max_retries=2)
async def collect_stock_change_events(force_boundary: bool = False):
    return await _market_observation.collect_stock_change_events(
        force_boundary=force_boundary
    )


@_register_task(queue="collect_ths_market_events", max_retries=2)
async def collect_ths_market_events(force_boundary: bool = False):
    """同花顺大盘、个股、板块、大笔委托异动与集合竞价。"""

    return await _market_observation.collect_ths_market_events(
        force_boundary=force_boundary
    )


@_register_task(queue="collect_ths_market_context", max_retries=2)
async def collect_ths_market_context(force_boundary: bool = False):
    """同花顺原生大盘资金与盘中情绪。"""

    return await _market_observation.collect_ths_market_context(
        force_boundary=force_boundary
    )


@_register_task(queue="collect_ths_market_profile", max_retries=2)
async def collect_ths_market_profile():
    """同花顺市场首页三张对比卡的同批次原生快照。"""
    return await _market_observation.collect_ths_market_profile()


@_register_task(queue="collect_market_boundary_snapshot", max_retries=2)
async def collect_market_boundary_snapshot():
    return await _market_observation.collect_market_breadth(
        force_boundary=True
    )


@_register_task(queue="collect_sector_market_snapshot", max_retries=2)
async def collect_sector_market_snapshot():
    return await _market_observation.collect_sector_market()


@_register_task(queue="collect_sector_fund_flow_snapshot", max_retries=2)
async def collect_sector_fund_flow_snapshot():
    return await _market_observation.collect_sector_fund_flow()


@_register_task(queue="collect_ths_sector_fragment_v2", max_retries=0)
async def collect_ths_sector_fragment_v2(
    kind: str,
    classification: str,
    metric: str | None = None,
):
    """独立采集并立即持久化一个同花顺板块数据分片。"""

    return await _market_observation.collect_ths_sector_fragment(
        kind,
        classification,
        metric,
    )


@_register_task(queue="collect_ths_sector_reference_snapshot_v2", max_retries=0)
async def collect_ths_sector_reference_snapshot_v2():
    """独立补齐到期的原生板块成分参考。"""

    return await _market_observation.collect_ths_sector_references()


@_register_task(queue="collect_ths_sector_signal_fragment_v2", max_retries=2)
async def collect_ths_sector_signal_fragment_v2(
    kind: str,
    sector_type: str | None = None,
    metric: str | None = None,
):
    """独立采集并持久化一个同花顺板块来源信号。"""

    return await _market_observation.collect_ths_sector_signal_fragment(
        kind,
        sector_type,
        metric,
    )


@_register_task(queue="collect_cross_market_snapshot", max_retries=2)
async def collect_cross_market_snapshot():
    return await _market_observation.collect_cross_market()


@_register_task(queue="collect_etf_estimated_net_inflow", max_retries=2)
async def collect_etf_estimated_net_inflow(
    force_boundary: bool = False,
):
    return await _market_observation.collect_etf_estimated_net_inflow(
        force_boundary=force_boundary
    )


@_register_task(queue="collect_ths_etf_zone", max_retries=2)
async def collect_ths_etf_zone():
    return await _market_observation.collect_ths_etf_zone()


@_register_task(queue="collect_ths_futures_zone", max_retries=2)
async def collect_ths_futures_zone():
    return await _market_observation.collect_ths_futures_zone()


@_register_task(queue="collect_ths_futures_fragment", max_retries=2)
async def collect_ths_futures_fragment(
    kind: str,
    group: str | None = None,
):
    return await _market_observation.collect_ths_futures_fragment(kind, group)


async def _collect_ths_futures_cycle_impl():
    """Collect futures fragments concurrently across native interface families."""
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    cycle_lock = redis_client.lock(
        f"{JETTASK_PREFIX}:lock:ths_futures_cycle",
        timeout=300,
        blocking_timeout=0,
        thread_local=False,
    )
    acquired = await asyncio.to_thread(cycle_lock.acquire, blocking=False)
    if not acquired:
        redis_client.close()
        return {"status": "skipped", "reason": "cycle_locked_distributed"}
    ranking_fragments = [
        ("ranking", group)
        for group in (
            "all", "night", "energy_chemical", "nonferrous", "precious",
            "ferrous", "agriculture", "financial", "shfe", "dce",
            "czce", "ine", "gfex", "cffex",
        )
    ]
    # Equal protocol/page signatures share one callback registry and stay in
    # the same sequential lane. Distinct signatures are response-identifiable
    # and can run concurrently through the broker's keyed locks.
    lanes = [
        [("indices", None)],                 # Unified 1264
        [("fund_inflow", None), ("fund_outflow", None)],  # Unified 4066
        [("market_state", None)],            # Unified 4051
        [("market_net_flow", None)],         # Unified 4067
        ranking_fragments,                    # Unified 4021
        [("hot", None)],                     # Hurricane frame 2312
    ]
    fragments = [fragment for lane in lanes for fragment in lane]
    results: dict[str, object] = {}
    try:
        async def collect_fragment(
            kind: str,
            group: str | None,
        ) -> None:
            key = kind if group is None else f"{kind}:{group}"
            for attempt in range(2):
                try:
                    result = await _market_observation.collect_ths_futures_fragment(
                        kind,
                        group,
                    )
                    results[key] = result
                    retryable_skip = (
                        isinstance(result, dict)
                        and result.get("status") == "skipped"
                        and result.get("reason") == "source_locked"
                    )
                    if not retryable_skip:
                        return
                except Exception as exc:
                    logger.exception(
                        "THS futures cycle fragment failed key=%s attempt=%s",
                        key,
                        attempt + 1,
                    )
                    results[key] = {
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                if attempt == 0:
                    await asyncio.sleep(1.5)

        async def collect_lane(
            lane: list[tuple[str, str | None]],
        ) -> None:
            for kind, group in lane:
                await collect_fragment(kind, group)
                await asyncio.sleep(0.25)

        await asyncio.gather(*(collect_lane(lane) for lane in lanes))
        final_failures = [
            (kind, group)
            for kind, group in fragments
            if not (
                isinstance(
                    results.get(kind if group is None else f"{kind}:{group}"),
                    dict,
                )
                and results[kind if group is None else f"{kind}:{group}"].get(
                    "status"
                ) in {"ok", "partial"}
            )
        ]
        if final_failures:
            # Retry only after every interface lane has drained. Some App
            # callbacks fail repeatedly while adjacent requests are active but
            # succeed immediately once the shared native transport is idle.
            await asyncio.sleep(2.0)
            await asyncio.gather(
                *(collect_fragment(kind, group) for kind, group in final_failures)
            )
    finally:
        try:
            await asyncio.to_thread(cycle_lock.release)
        except redis.exceptions.LockError:
            logger.warning("THS futures distributed cycle lock expired")
        finally:
            redis_client.close()
    completed = sum(
        isinstance(value, dict) and value.get("status") in {"ok", "partial"}
        for value in results.values()
    )
    return {
        "status": "ok" if completed == len(fragments) else "partial",
        "completed": completed,
        "total": len(fragments),
        "results": results,
    }


@_register_task(queue="collect_ths_futures_cycle", max_retries=0)
async def collect_ths_futures_cycle():
    return await _run_observed_dispatch(
        "collect_ths_futures_cycle",
        _collect_ths_futures_cycle_impl,
    )


@_register_task(queue="collect_ths_gold_zone", max_retries=2)
async def collect_ths_gold_zone():
    result = await _market_observation.collect_ths_gold_zone()
    new_ids = result.get("new_news_ids") or []
    if new_ids:
        result["kg_event_ids"] = await send_kg_news_ingest(new_ids)
    return result


@_register_task(queue="collect_ths_us_overview", max_retries=2)
async def collect_ths_us_overview():
    return await _market_observation.collect_ths_us_overview()


@_register_task(queue="collect_ths_us_sectors", max_retries=2)
async def collect_ths_us_sectors():
    return await _market_observation.collect_ths_us_sectors()


@_register_task(queue="collect_ths_us_stock_rankings", max_retries=0)
async def collect_ths_us_stock_rankings():
    return await _market_observation.collect_ths_us_stock_rankings()


@_register_task(queue="collect_ths_us_etf_sectors", max_retries=2)
async def collect_ths_us_etf_sectors():
    return await _market_observation.collect_ths_us_etf_sectors()


@_register_task(queue="collect_etf_daily_shares", max_retries=3)
async def collect_etf_daily_shares(trade_date: str | None = None):
    return await _market_observation.collect_etf_daily_shares(
        trade_date=trade_date
    )


@_register_task(queue="collect_pboc_rate_liquidity", max_retries=2)
async def collect_pboc_rate_liquidity():
    return await _market_observation.collect_pboc_rate_liquidity()


@_register_task(queue="collect_ths_index_sentiment", max_retries=2)
async def collect_ths_index_sentiment():
    return await _market_observation.collect_ths_index_sentiment()


@_register_task(queue="collect_market_daily_bars", max_retries=3)
async def collect_market_daily_bars():
    return await _market_observation.collect_market_daily_bars()


@_register_task(queue="collect_market_reference_data", max_retries=3)
async def collect_market_reference_data():
    return await _market_observation.collect_market_reference_data()


@_register_task(queue="collect_market_daily_catchup", max_retries=3)
async def collect_market_daily_catchup():
    return await _market_observation.collect_market_daily_catchup()


@_register_task(queue="collect_market_valuation", max_retries=3)
async def collect_market_valuation():
    return await _market_observation.collect_market_valuation()


@_register_task(queue="collect_bond_index", max_retries=3)
async def collect_bond_index():
    return await _market_observation.collect_bond_index()


@_register_task(queue="materialize_sentiment_signal", max_retries=1)
async def materialize_sentiment_signal(trade_date: str | None = None):
    """L2 情绪信号物化 — 写 ft_sentiment_signal 快照表"""
    logger.info("[materialize_sentiment_signal] 开始执行")
    t0 = time.time()
    result = await _run_observed_dispatch(
        "materialize_sentiment_signal",
        lambda: _collection.materialize_sentiment_signal(trade_date=trade_date),
    )
    logger.info(f"[materialize_sentiment_signal] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    return result.to_dict()
