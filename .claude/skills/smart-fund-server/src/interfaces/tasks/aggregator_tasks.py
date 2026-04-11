"""数据聚合定时任务

每个聚合维度一个队列，Worker -c 1 串行执行，避免同表并发写入。
"""

import logging
import time

from jettask import TaskRouter

from src.infrastructure.clients import init_clients, ths

logger = logging.getLogger(__name__)

# 确保客户端已初始化（Worker 进程不经过 FastAPI lifespan）
if ths is None:
    init_clients()

from src.domain.aggregation import (
    NewsAggregator, FundFlowAggregator, MacroAggregator,
    SentimentAggregator, MarketAggregator, EventFeedbackAggregator,
    EventExtractionAggregator, EventStreamAggregator,
)

router = TaskRouter()

news = NewsAggregator()
fund_flow = FundFlowAggregator()
macro = MacroAggregator()
sentiment = SentimentAggregator()
market = MarketAggregator()
event_feedback = EventFeedbackAggregator()
event_extraction = EventExtractionAggregator()
event_stream = EventStreamAggregator()


# ==================== P0：核心任务 ====================


@router.task(queue="agg_news", max_retries=2, retry_backoff=True)
async def agg_news():
    """新闻事件聚合 — 9 源串行，每源独立间隔"""
    logger.info("[agg_news] 开始执行")
    t0 = time.time()
    await news.tick()
    logger.info(f"[agg_news] 完成，耗时 {time.time()-t0:.1f}s")


@router.task(queue="agg_fund_flow", max_retries=2, retry_backoff=True)
async def agg_fund_flow():
    """资金流聚合 — 北向/板块/个股主力/龙虎榜"""
    logger.info("[agg_fund_flow] 开始执行")
    t0 = time.time()
    await fund_flow.tick()
    logger.info(f"[agg_fund_flow] 完成，耗时 {time.time()-t0:.1f}s")


# ==================== P1：辅助任务 ====================


@router.task(queue="agg_market", max_retries=2, retry_backoff=True)
async def agg_market():
    """市场数据聚合 — 指数/全球/期货/外汇/板块"""
    logger.info("[agg_market] 开始执行")
    t0 = time.time()
    await market.tick()
    logger.info(f"[agg_market] 完成，耗时 {time.time()-t0:.1f}s")


@router.task(queue="agg_sentiment", max_retries=2, retry_backoff=True)
async def agg_sentiment():
    """情绪舆情聚合 — 股吧/雪球/涨停/热股"""
    logger.info("[agg_sentiment] 开始执行")
    t0 = time.time()
    await sentiment.tick()
    logger.info(f"[agg_sentiment] 完成，耗时 {time.time()-t0:.1f}s")


@router.task(queue="agg_macro", max_retries=2, retry_backoff=True)
async def agg_macro():
    """宏观指标聚合 — CPI/PMI/M2/LPR/Shibor/汇率"""
    logger.info("[agg_macro] 开始执行")
    t0 = time.time()
    await macro.tick()
    logger.info(f"[agg_macro] 完成，耗时 {time.time()-t0:.1f}s")


# ==================== AI 处理任务 ====================


@router.task(queue="agg_event_extraction", max_retries=1)
async def agg_event_extraction():
    """AI 事件抽取 — 从 ft_news 读未抽取新闻，调 claude 抽取写 ft_events"""
    logger.info("[agg_event_extraction] 开始执行")
    t0 = time.time()
    await event_extraction.tick()
    logger.info(f"[agg_event_extraction] 完成，耗时 {time.time()-t0:.1f}s")


@router.task(queue="agg_event_stream", max_retries=1)
async def agg_event_stream():
    """事件流聚合 — 从 ft_events 读最近 24h 事件，按 industry 贪心聚类写 ft_event_streams"""
    logger.info("[agg_event_stream] 开始执行")
    t0 = time.time()
    await event_stream.tick()
    logger.info(f"[agg_event_stream] 完成，耗时 {time.time()-t0:.1f}s")


# ==================== P2：盘后任务 ====================


@router.task(queue="agg_event_feedback", max_retries=1)
async def agg_event_feedback():
    """事件反馈回填 — T+1/T+3 市场反应 + 衰退监控"""
    logger.info("[agg_event_feedback] 开始执行")
    t0 = time.time()
    await event_feedback.tick()
    logger.info(f"[agg_event_feedback] 完成，耗时 {time.time()-t0:.1f}s")
