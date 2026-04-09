"""数据聚合定时任务

每个聚合维度一个队列，Worker -c 1 串行执行，避免同表并发写入。
"""

from jettask import TaskRouter

from src.domain.aggregation import (
    NewsAggregator, FundFlowAggregator, MacroAggregator,
    SentimentAggregator, MarketAggregator, EventFeedbackAggregator,
)

router = TaskRouter()

news = NewsAggregator()
fund_flow = FundFlowAggregator()
macro = MacroAggregator()
sentiment = SentimentAggregator()
market = MarketAggregator()
event_feedback = EventFeedbackAggregator()


# ==================== P0：核心任务 ====================


@router.task(queue="agg_news", max_retries=2, retry_backoff=True)
async def agg_news():
    """新闻事件聚合 — 9 源串行，每源独立间隔"""
    await news.tick()


@router.task(queue="agg_fund_flow", max_retries=2, retry_backoff=True)
async def agg_fund_flow():
    """资金流聚合 — 北向/板块/个股主力/龙虎榜"""
    await fund_flow.tick()


# ==================== P1：辅助任务 ====================


@router.task(queue="agg_market", max_retries=2, retry_backoff=True)
async def agg_market():
    """市场数据聚合 — 指数/全球/期货/外汇/板块"""
    await market.tick()


@router.task(queue="agg_sentiment", max_retries=2, retry_backoff=True)
async def agg_sentiment():
    """情绪舆情聚合 — 股吧/雪球/涨停/热股"""
    await sentiment.tick()


@router.task(queue="agg_macro", max_retries=2, retry_backoff=True)
async def agg_macro():
    """宏观指标聚合 — CPI/PMI/M2/LPR/Shibor/汇率"""
    await macro.tick()


# ==================== P2：盘后任务 ====================


@router.task(queue="agg_event_feedback", max_retries=1)
async def agg_event_feedback():
    """事件反馈回填 — T+1/T+3 市场反应 + 衰退监控"""
    await event_feedback.tick()
