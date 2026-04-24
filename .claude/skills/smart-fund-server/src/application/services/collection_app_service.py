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
import time

from src.application.dto.collection_dto import CollectionResult
from src.infrastructure.observability import get_logger, record_collection

logger = get_logger(__name__)


async def _run(aggregator_name: str, agg_class) -> CollectionResult:
    """通用采集 use case 包装: 计时 + 上报 metrics"""
    t0 = time.time()
    try:
        result = await agg_class().tick() or {}
    finally:
        duration = time.time() - t0
    saved = result.get("total_saved", 0)
    record_collection(aggregator_name, duration, saved)
    return CollectionResult(
        aggregator=aggregator_name,
        sources_run=result.get("sources_run", 0),
        total_saved=saved,
    )


class CollectionAppService:
    """数据采集 use case 入口"""

    async def run_news_collection(self) -> CollectionResult:
        """新闻采集 use case (agg_news task)"""
        from src.domain.collection.services.news import NewsAggregator
        return await _run("news", NewsAggregator)

    async def run_fund_flow_collection(self) -> CollectionResult:
        from src.domain.collection.services.fund_flow import FundFlowAggregator
        return await _run("fund_flow", FundFlowAggregator)

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
