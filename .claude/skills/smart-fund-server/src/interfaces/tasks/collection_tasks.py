"""数据采集定时任务 — jettask-rs Thin Wrapper 调 Application Services

R3.7 重构后:
- 这个文件不再 import domain 类
- 每个 task 函数 = 调一个 application use case + 处理级联
- 全部业务逻辑下沉到 src/application/services/

jettask-rs 接入边界:
- 本文件只负责注册 TaskRouter handler 和发送 TaskMessage。
- 不在 task 层写业务逻辑,避免任务框架替换污染 application service。
"""
import logging
import time

from jettask import TaskRouter

from src.application.services import (
    CollectionAppService,
)
from src.infrastructure.clients import init_clients, ths
from src.infrastructure.tasks.jettask_dispatcher import send_kg_news_ingest

logger = logging.getLogger(__name__)


# 确保客户端已初始化（Worker 进程不经过 FastAPI 生命周期）
if ths is None:
    init_clients()


# ==================== 单例应用服务 ====================
# 每个进程一份，避免每次 task 都重新构造。

_collection = CollectionAppService()


router = TaskRouter()

DEFAULT_RETRY_BACKOFF_SECONDS = 2.0
DEFAULT_RETRY_BACKOFF_MAX_SECONDS = 300


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


@_register_task(queue="collect_news", max_retries=2)
async def collect_news():
    """新闻采集 — 10 源串行,每源独立间隔"""
    logger.info("[collect_news] 开始执行")
    t0 = time.time()
    result = await _collection.run_news_collection()
    kg_event_ids = await send_kg_news_ingest(result.new_ids or [])
    logger.info(f"[collect_news] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
    if kg_event_ids:
        logger.info("[collect_news] 已投递新增 ft_news ids 到 kg_news_ingest: ids=%s event_ids=%s", result.new_ids, kg_event_ids)


@_register_task(queue="collect_fund_flow", max_retries=2)
async def collect_fund_flow():
    """资金流采集 — 北向/板块/个股/龙虎榜"""
    logger.info("[collect_fund_flow] 开始执行")
    t0 = time.time()
    result = await _collection.run_fund_flow_collection()
    logger.info(f"[collect_fund_flow] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")


# ==================== P1: 辅助任务 ====================


@_register_task(queue="collect_market", max_retries=2)
async def collect_market():
    """市场数据采集 — 指数/全球/期货/外汇/板块"""
    logger.info("[collect_market] 开始执行")
    t0 = time.time()
    result = await _collection.run_market_collection()
    logger.info(f"[collect_market] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")


@_register_task(queue="collect_sentiment", max_retries=2)
async def collect_sentiment():
    """情绪舆情采集 — 股吧/雪球/涨停/热股"""
    logger.info("[collect_sentiment] 开始执行")
    t0 = time.time()
    result = await _collection.run_sentiment_collection()
    logger.info(f"[collect_sentiment] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")


@_register_task(queue="collect_macro", max_retries=2)
async def collect_macro():
    """宏观指标采集 — CPI/PMI/M2/LPR/Shibor/汇率"""
    logger.info("[collect_macro] 开始执行")
    t0 = time.time()
    result = await _collection.run_macro_collection()
    logger.info(f"[collect_macro] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")


@_register_task(queue="materialize_sentiment_signal", max_retries=1)
async def materialize_sentiment_signal(trade_date: str | None = None):
    """L2 情绪信号物化 — 写 ft_sentiment_signal 快照表"""
    logger.info("[materialize_sentiment_signal] 开始执行")
    t0 = time.time()
    result = await _collection.materialize_sentiment_signal(trade_date=trade_date)
    logger.info(f"[materialize_sentiment_signal] 完成,耗时 {time.time()-t0:.1f}s {result.to_dict()}")
