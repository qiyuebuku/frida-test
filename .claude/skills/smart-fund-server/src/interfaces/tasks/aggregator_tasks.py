"""数据聚合定时任务

每个聚合维度一个队列，Worker -c 1 串行执行，避免同表并发写入。

事件驱动级联（Phase D.1）：
    agg_event_extraction(saved>0) → agg_event_stream → trade_decision → trade_execution

每一步根据上游产出量决定是否唤醒下一步，
延迟从分钟级降到秒级。失败时下游靠定时调度兜底。
"""

import logging
import time

from jettask import TaskMessage, TaskRouter

from src.infrastructure.clients import init_clients, ths

logger = logging.getLogger(__name__)


# ==================== 事件驱动级联工具 ====================


async def _trigger_downstream(queue: str, **kwargs):
    """异步唤醒下游任务，失败不影响当前任务"""
    try:
        from src.interfaces.tasks import app
        await app.send_tasks([TaskMessage(queue=queue, kwargs=kwargs)], asyncio=True)
        logger.info(f"[cascade] → {queue} {kwargs or ''}")
    except Exception as e:
        logger.warning(f"[cascade] 唤醒 {queue} 失败: {e}")

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
    """新闻事件聚合 — 9 源串行，每源独立间隔

    级联：total_saved > 0 → 触发 agg_event_extraction
    """
    logger.info("[agg_news] 开始执行")
    t0 = time.time()
    result = await news.tick() or {}
    logger.info(f"[agg_news] 完成，耗时 {time.time()-t0:.1f}s")
    if (result.get("total_saved") or 0) > 0:
        await _trigger_downstream("agg_event_extraction")


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
    """AI 事件抽取 — 从 ft_news 读未抽取新闻，调 claude 抽取写 ft_events

    级联：saved > 0 → 触发 agg_event_stream
    """
    logger.info("[agg_event_extraction] 开始执行")
    t0 = time.time()
    result = await event_extraction.tick() or {}
    logger.info(f"[agg_event_extraction] 完成，耗时 {time.time()-t0:.1f}s")
    if (result.get("saved") or 0) > 0:
        await _trigger_downstream("agg_event_stream")


@router.task(queue="agg_event_stream", max_retries=1)
async def agg_event_stream():
    """事件流聚合 — 从 ft_events 读最近 24h 事件，按 industry 贪心聚类写 ft_event_streams

    级联：active > 0 → 触发 trade_decision
    """
    logger.info("[agg_event_stream] 开始执行")
    t0 = time.time()
    result = await event_stream.tick() or {}
    logger.info(f"[agg_event_stream] 完成，耗时 {time.time()-t0:.1f}s")
    if (result.get("active") or 0) > 0:
        await _trigger_downstream("trade_decision")


# ==================== 决策任务 ====================


from src.domain.decision import EventDrivenDecider, TradeExecutor, TradeMonitor, ReviewEngine
event_decider = EventDrivenDecider(dry_run=True)  # ⚠️ Phase C.3 真实下单前必须保持 True
trade_executor = TradeExecutor()
trade_monitor_obj = TradeMonitor()
review_engine = ReviewEngine()


@router.task(queue="trade_decision", max_retries=1)
async def trade_decision():
    """事件驱动决策 — 扫描 ft_event_streams 活跃流 → 打分 → 写 ft_pending_decisions

    级联：decisions > 0 → 触发 trade_execution
    """
    logger.info("[trade_decision] 开始执行")
    t0 = time.time()
    result = await event_decider.decide_all() or {}
    logger.info(f"[trade_decision] 完成，耗时 {time.time()-t0:.1f}s")
    if (result.get("decisions") or 0) > 0:
        await _trigger_downstream("trade_execution")


@router.task(queue="trade_execution", max_retries=1)
async def trade_execution():
    """交易执行 — 扫描 pending 决策，按 EXEC_DRY_RUN 配置执行（默认 dry_run）

    级联链路终点：trade_execution 自身不再触发下游
    （trade_monitor 走自己的定时调度，不进入级联）
    """
    logger.info("[trade_execution] 开始执行")
    t0 = time.time()
    await trade_executor.execute_pending()
    logger.info(f"[trade_execution] 完成，耗时 {time.time()-t0:.1f}s")


@router.task(queue="trade_monitor", max_retries=1)
async def trade_monitor():
    """持仓监控 — 硬止损 + 事件流衰退检测 + 浮盈加仓"""
    logger.info("[trade_monitor] 开始执行")
    t0 = time.time()
    await trade_monitor_obj.monitor_all()
    logger.info(f"[trade_monitor] 完成，耗时 {time.time()-t0:.1f}s")


@router.task(queue="review_decision", max_retries=1)
async def review_decision():
    """决策复盘 — 盘后扫描历史交易，回填 T+1/T+2 收益，更新胜率"""
    logger.info("[review_decision] 开始执行")
    t0 = time.time()
    await review_engine.review_all()
    logger.info(f"[review_decision] 完成，耗时 {time.time()-t0:.1f}s")


# ==================== P2：盘后任务 ====================


@router.task(queue="agg_event_feedback", max_retries=1)
async def agg_event_feedback():
    """事件反馈回填 — T+1/T+3 市场反应 + 衰退监控"""
    logger.info("[agg_event_feedback] 开始执行")
    t0 = time.time()
    await event_feedback.tick()
    logger.info(f"[agg_event_feedback] 完成，耗时 {time.time()-t0:.1f}s")
