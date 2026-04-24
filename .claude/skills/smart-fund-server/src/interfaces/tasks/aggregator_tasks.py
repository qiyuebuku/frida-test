"""数据聚合定时任务 — Thin Wrapper 调 Application Services

R3.7 重构后:
- 这个文件不再 import domain 类
- 每个 task 函数 = 调一个 application use case + 处理级联
- 全部业务逻辑下沉到 src/application/services/

事件驱动级联 (Phase D.1):
    agg_news (saved>0) → agg_event_extraction → agg_event_stream → trade_decision → trade_execution
    每一步根据上游产出量决定是否唤醒下一步,延迟从分钟级降到秒级。
    失败时下游靠定时调度兜底。
"""
import logging
import time

from jettask import TaskMessage, TaskRouter

from src.application.services import (
    CollectionAppService,
    ExtractionAppService,
    ReflectionAppService,
    TradingAppService,
)
from src.infrastructure.clients import init_clients, ths

logger = logging.getLogger(__name__)


# 确保客户端已初始化(Worker 进程不经过 FastAPI lifespan)
if ths is None:
    init_clients()


# ==================== 事件驱动级联工具 ====================


async def _trigger_downstream(queue: str, **kwargs):
    """异步唤醒下游任务,失败不影响当前任务

    jettask-rs API: app.send([TaskMessage]) 是 async
    """
    try:
        from src.interfaces.tasks import app
        await app.send([TaskMessage(queue=queue, kwargs=kwargs)])
        logger.info(f"[cascade] → {queue} {kwargs or ''}")
    except Exception as e:
        logger.warning(f"[cascade] 唤醒 {queue} 失败: {e}")


# ==================== 单例 app service ====================
# 每个进程一份,避免每次 task 都 new

_collection = CollectionAppService()
_extraction = ExtractionAppService()
_trading = TradingAppService()
_reflection = ReflectionAppService()


router = TaskRouter()


# ==================== P0: 核心任务 ====================


@router.task(queue="agg_news", max_retries=2, retry_backoff=True)
async def agg_news():
    """新闻采集 — 9 源串行,每源独立间隔

    级联: total_saved > 0 → 触发 agg_event_extraction
    """
    logger.info("[agg_news] 开始执行")
    t0 = time.time()
    result = await _collection.run_news_collection()
    logger.info(f"[agg_news] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.total_saved > 0:
        await _trigger_downstream("agg_event_extraction")
        await _trigger_downstream("l1a_classify_news")


@router.task(queue="agg_fund_flow", max_retries=2, retry_backoff=True)
async def agg_fund_flow():
    """资金流采集 — 北向/板块/个股/龙虎榜

    级联: total_saved > 0 → 触发 l1b_detect_fund_flow
    """
    logger.info("[agg_fund_flow] 开始执行")
    t0 = time.time()
    result = await _collection.run_fund_flow_collection()
    logger.info(f"[agg_fund_flow] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.total_saved > 0:
        await _trigger_downstream("l1b_detect_fund_flow")


# ==================== P1: 辅助任务 ====================


@router.task(queue="agg_market", max_retries=2, retry_backoff=True)
async def agg_market():
    """市场数据采集 — 指数/全球/期货/外汇/板块

    级联: total_saved > 0 → 触发 l1b_detect_market
    """
    logger.info("[agg_market] 开始执行")
    t0 = time.time()
    result = await _collection.run_market_collection()
    logger.info(f"[agg_market] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.total_saved > 0:
        await _trigger_downstream("l1b_detect_market")


@router.task(queue="agg_sentiment", max_retries=2, retry_backoff=True)
async def agg_sentiment():
    """情绪舆情采集 — 股吧/雪球/涨停/热股

    级联: total_saved > 0 → 触发 l1b_detect_sentiment
    """
    logger.info("[agg_sentiment] 开始执行")
    t0 = time.time()
    result = await _collection.run_sentiment_collection()
    logger.info(f"[agg_sentiment] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.total_saved > 0:
        await _trigger_downstream("l1b_detect_sentiment")


@router.task(queue="agg_macro", max_retries=2, retry_backoff=True)
async def agg_macro():
    """宏观指标采集 — CPI/PMI/M2/LPR/Shibor/汇率

    级联: total_saved > 0 → 触发 l1b_detect_macro
    """
    logger.info("[agg_macro] 开始执行")
    t0 = time.time()
    result = await _collection.run_macro_collection()
    logger.info(f"[agg_macro] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.total_saved > 0:
        await _trigger_downstream("l1b_detect_macro")


# ==================== AI 处理任务 ====================


@router.task(queue="agg_event_extraction", max_retries=1)
async def agg_event_extraction():
    """AI 事件抽取 — 从 ft_news 读未抽取新闻,调 claude 抽取写 ft_events

    级联: saved > 0 → 触发 agg_event_stream
    """
    logger.info("[agg_event_extraction] 开始执行")
    t0 = time.time()
    result = await _extraction.extract_events_from_news()
    logger.info(f"[agg_event_extraction] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.saved > 0:
        await _trigger_downstream("agg_event_stream")


# ==================== L1b 数值事件检测 ====================


@router.task(queue="l1b_detect_fund_flow", max_retries=2, retry_backoff=True)
async def l1b_detect_fund_flow():
    """L1b 资金流事件检测"""
    logger.info("[l1b_detect_fund_flow] 开始执行")
    t0 = time.time()
    result = await _extraction.run_l1b_fund_flow()
    logger.info(f"[l1b_detect_fund_flow] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.saved > 0:
        await _trigger_downstream("agg_event_stream")


@router.task(queue="l1b_detect_macro", max_retries=2, retry_backoff=True)
async def l1b_detect_macro():
    """L1b 宏观指标事件检测"""
    logger.info("[l1b_detect_macro] 开始执行")
    t0 = time.time()
    result = await _extraction.run_l1b_macro()
    logger.info(f"[l1b_detect_macro] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.saved > 0:
        await _trigger_downstream("agg_event_stream")


@router.task(queue="l1b_detect_sentiment", max_retries=2, retry_backoff=True)
async def l1b_detect_sentiment():
    """L1b 情绪事件检测"""
    logger.info("[l1b_detect_sentiment] 开始执行")
    t0 = time.time()
    result = await _extraction.run_l1b_sentiment()
    logger.info(f"[l1b_detect_sentiment] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.saved > 0:
        await _trigger_downstream("agg_event_stream")


@router.task(queue="l1b_detect_market", max_retries=2, retry_backoff=True)
async def l1b_detect_market():
    """L1b 市场快照事件检测"""
    logger.info("[l1b_detect_market] 开始执行")
    t0 = time.time()
    result = await _extraction.run_l1b_market()
    logger.info(f"[l1b_detect_market] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.saved > 0:
        await _trigger_downstream("agg_event_stream")


@router.task(queue="l1_refresh_thresholds", max_retries=1)
async def l1_refresh_thresholds():
    """L1 阈值刷新 — 每日从历史数据滚动计算"""
    logger.info("[l1_refresh_thresholds] 开始执行")
    t0 = time.time()
    result = await _extraction.refresh_l1_thresholds()
    logger.info(f"[l1_refresh_thresholds] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")


# ==================== L1a AI 文本事件抽取 ====================


@router.task(queue="l1a_classify_news", max_retries=2, retry_backoff=True)
async def l1a_classify_news():
    """L1a 新闻分类 — 关键词预分流 + LLM 分类，推入 Redis 桶

    级联: classified > 0 → 触发 l1a_extract_bucket
    """
    logger.info("[l1a_classify_news] 开始执行")
    t0 = time.time()
    result = await _extraction.run_l1a_classify()
    logger.info(f"[l1a_classify_news] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.classified > 0:
        await _trigger_downstream("l1a_extract_bucket")


@router.task(queue="l1a_extract_bucket", max_retries=2, retry_backoff=True)
async def l1a_extract_bucket():
    """L1a 桶批量抽取 — 从 Redis 桶取出同类新闻，调 Planner API 抽取事件

    级联: saved > 0 → 触发 agg_event_stream
    """
    logger.info("[l1a_extract_bucket] 开始执行")
    t0 = time.time()
    result = await _extraction.run_l1a_extract()
    logger.info(f"[l1a_extract_bucket] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.saved > 0:
        await _trigger_downstream("agg_event_stream")


@router.task(queue="agg_event_stream", max_retries=1)
async def agg_event_stream():
    """事件流聚合 — 按 industry 贪心聚类近 24h 事件

    级联: active > 0 → 触发 trade_decision
    """
    logger.info("[agg_event_stream] 开始执行")
    t0 = time.time()
    result = await _extraction.aggregate_event_streams()
    logger.info(f"[agg_event_stream] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.active > 0:
        await _trigger_downstream("trade_decision")


# ==================== 决策任务 ====================


@router.task(queue="trade_decision", max_retries=1)
async def trade_decision():
    """事件驱动决策 — 扫描活跃事件流 → 打分 → 写 ft_pending_decisions

    级联: decisions > 0 → 触发 trade_execution
    """
    logger.info("[trade_decision] 开始执行")
    t0 = time.time()
    result = await _trading.score_event_streams()
    logger.info(f"[trade_decision] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if result.decisions > 0:
        await _trigger_downstream("trade_execution")


@router.task(queue="trade_execution", max_retries=1)
async def trade_execution():
    """交易执行 — 扫描 pending 决策,默认 dry_run

    ⚠️ 真实下单需要 EXEC_DRY_RUN=false + LIVE_FUND_WHITELIST + 单笔/单日上限
    级联终点: trade_execution 不再触发下游
    """
    logger.info("[trade_execution] 开始执行")
    t0 = time.time()
    result = await _trading.execute_pending_decisions()
    logger.info(f"[trade_execution] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")


@router.task(queue="trade_monitor", max_retries=1)
async def trade_monitor():
    """持仓监控 — 硬止损 + 事件流衰退检测 + 浮盈加仓"""
    logger.info("[trade_monitor] 开始执行")
    t0 = time.time()
    result = await _trading.monitor_positions()
    logger.info(f"[trade_monitor] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")


@router.task(queue="review_decision", max_retries=1)
async def review_decision():
    """决策复盘 — 盘后扫描历史交易,回填 T+1/T+2 收益,更新胜率"""
    logger.info("[review_decision] 开始执行")
    t0 = time.time()
    result = await _reflection.run_review()
    logger.info(f"[review_decision] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")


# ==================== P2: 盘后任务 ====================


@router.task(queue="agg_event_feedback", max_retries=1)
async def agg_event_feedback():
    """事件反馈回填 — T+1/T+3 市场反应"""
    logger.info("[agg_event_feedback] 开始执行")
    t0 = time.time()
    result = await _extraction.backfill_market_reaction()
    logger.info(f"[agg_event_feedback] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")


@router.task(queue="materialize_sentiment_signal", max_retries=1)
async def materialize_sentiment_signal(trade_date: str | None = None):
    """L2 情绪信号物化 — 写 ft_sentiment_signal 快照表"""
    logger.info("[materialize_sentiment_signal] 开始执行")
    t0 = time.time()
    result = await _collection.materialize_sentiment_signal(trade_date=trade_date)
    logger.info(f"[materialize_sentiment_signal] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
