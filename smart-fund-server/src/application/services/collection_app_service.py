"""数据采集应用服务

6 个 use case:
- run_news_collection
- run_fund_flow_collection
- run_market_collection
- run_sentiment_collection
- run_macro_collection
- materialize_sentiment_signal

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


class CollectionAppService:
    """数据采集 use case 入口"""

    async def run_news_collection(self) -> CollectionResult:
        """新闻采集 use case (collect_news task)"""
        from src.domain.collection.services.news import NewsAggregator
        return await _run("news", NewsAggregator)

    async def run_fund_flow_collection(self) -> CollectionResult:
        from src.domain.collection.services.fund_flow import FundFlowAggregator
        return await _run("fund_flow", FundFlowAggregator)

    async def run_watchlist_instrument_collection(
        self,
        codes: list[str],
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

        client = redis.from_url(REDIS_URL, decode_responses=True)
        lock = client.lock(
            f"{JETTASK_PREFIX}:lock:fund_flow:watchlist_data",
            timeout=600,
            blocking_timeout=90,
            thread_local=False,
        )
        acquired = await asyncio.to_thread(lock.acquire)
        if not acquired:
            client.close()
            raise RuntimeError("watchlist 采集锁等待超时")
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
                        raise RuntimeError("watchlist 采集锁续租失败")

        renewal_task = asyncio.create_task(renew_lock())
        try:
            result = await FundFlowAggregator().collect_watchlist_codes(
                normalized_codes,
                force=True,
            )
            missing = sorted(
                set(normalized_codes) - set(result.get("collected_codes") or [])
            )
            if missing:
                raise RuntimeError(
                    f"以下标的未采集到可用数据: {', '.join(missing)}"
                )
            return result
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

    async def run_sentiment_collection(self) -> CollectionResult:
        from src.domain.collection.services.sentiment import SentimentAggregator
        return await _run("sentiment", SentimentAggregator)

    async def run_macro_collection(self) -> CollectionResult:
        from src.domain.collection.services.macro import MacroAggregator
        return await _run("macro", MacroAggregator)

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
