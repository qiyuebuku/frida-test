"""数据采集应用服务

5 个 use case:
- run_news_collection
- run_fund_flow_collection
- run_market_collection
- run_sentiment_collection
- run_macro_collection

每个 use case 内部调对应的 domain Aggregator (BaseAggregator 子类),
返回 CollectionResult dto。
"""
import logging

from src.application.dto.collection_dto import CollectionResult

logger = logging.getLogger(__name__)


class CollectionAppService:
    """数据采集 use case 入口"""

    async def run_news_collection(self) -> CollectionResult:
        """新闻采集 use case (agg_news task)"""
        from src.domain.collection.services.news import NewsAggregator
        result = await NewsAggregator().tick() or {}
        return CollectionResult(
            aggregator="news",
            sources_run=result.get("sources_run", 0),
            total_saved=result.get("total_saved", 0),
        )

    async def run_fund_flow_collection(self) -> CollectionResult:
        from src.domain.collection.services.fund_flow import FundFlowAggregator
        result = await FundFlowAggregator().tick() or {}
        return CollectionResult(
            aggregator="fund_flow",
            sources_run=result.get("sources_run", 0),
            total_saved=result.get("total_saved", 0),
        )

    async def run_market_collection(self) -> CollectionResult:
        from src.domain.collection.services.market import MarketAggregator
        result = await MarketAggregator().tick() or {}
        return CollectionResult(
            aggregator="market",
            sources_run=result.get("sources_run", 0),
            total_saved=result.get("total_saved", 0),
        )

    async def run_sentiment_collection(self) -> CollectionResult:
        from src.domain.collection.services.sentiment import SentimentAggregator
        result = await SentimentAggregator().tick() or {}
        return CollectionResult(
            aggregator="sentiment",
            sources_run=result.get("sources_run", 0),
            total_saved=result.get("total_saved", 0),
        )

    async def run_macro_collection(self) -> CollectionResult:
        from src.domain.collection.services.macro import MacroAggregator
        result = await MacroAggregator().tick() or {}
        return CollectionResult(
            aggregator="macro",
            sources_run=result.get("sources_run", 0),
            total_saved=result.get("total_saved", 0),
        )
