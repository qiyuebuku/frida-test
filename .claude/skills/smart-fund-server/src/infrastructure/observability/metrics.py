"""Prometheus metrics 出口

应用层每个 use case 完成后调对应的 record_xxx() 上报指标。
FastAPI 用 metrics_text() 暴露 /metrics endpoint(GET 返回 prometheus text 格式)。

定义的指标:
    collection_duration_seconds (Histogram, label=aggregator)
    collection_saved_total      (Counter, label=aggregator)
    decision_count_total         (Counter, label=action)
    dry_run_trade_count_total    (Counter, label=action)
    live_trade_count_total       (Counter, label=action)
    active_event_streams         (Gauge, no label)
"""
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# 独立 registry, 不污染默认 registry (方便测试)
REGISTRY = CollectorRegistry()


# ==================== 指标定义 ====================

COLLECTION_DURATION = Histogram(
    "collection_duration_seconds",
    "数据采集 use case 的执行时长",
    labelnames=["aggregator"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)

COLLECTION_SAVED = Counter(
    "collection_saved_total",
    "数据采集累计入库行数",
    labelnames=["aggregator"],
    registry=REGISTRY,
)

DECISION_COUNT = Counter(
    "decision_count_total",
    "事件驱动决策累计数",
    labelnames=["action"],  # buy/sell/watch/hold
    registry=REGISTRY,
)

DRY_RUN_TRADE_COUNT = Counter(
    "dry_run_trade_count_total",
    "DRY_RUN 交易累计数",
    labelnames=["action"],
    registry=REGISTRY,
)

LIVE_TRADE_COUNT = Counter(
    "live_trade_count_total",
    "LIVE 真实交易累计数",
    labelnames=["action"],
    registry=REGISTRY,
)

ACTIVE_EVENT_STREAMS = Gauge(
    "active_event_streams",
    "当前活跃的事件流数(emerging/fermenting/peak)",
    registry=REGISTRY,
)


# ==================== 便捷上报 API ====================


def record_collection(aggregator: str, duration_s: float, saved: int) -> None:
    """采集 use case 完成后调"""
    COLLECTION_DURATION.labels(aggregator=aggregator).observe(duration_s)
    if saved > 0:
        COLLECTION_SAVED.labels(aggregator=aggregator).inc(saved)


def record_decision(action: str, count: int = 1) -> None:
    """决策产出时调"""
    if count > 0:
        DECISION_COUNT.labels(action=action).inc(count)


def record_trade(action: str, dry_run: bool, count: int = 1) -> None:
    """交易执行后调"""
    if count <= 0:
        return
    if dry_run:
        DRY_RUN_TRADE_COUNT.labels(action=action).inc(count)
    else:
        LIVE_TRADE_COUNT.labels(action=action).inc(count)


def set_active_streams(n: int) -> None:
    ACTIVE_EVENT_STREAMS.set(n)


# ==================== Prometheus text 输出 ====================


def metrics_text() -> tuple[bytes, str]:
    """生成 Prometheus text 格式

    Returns:
        (body bytes, content_type str)
    """
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


# ==================== 计时上下文管理器 ====================


class CollectionTimer:
    """with CollectionTimer('news') as t:
           t.saved = saved_count
       自动上报 duration 和 saved
    """
    def __init__(self, aggregator: str):
        self.aggregator = aggregator
        self.saved = 0
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self._t0
        record_collection(self.aggregator, duration, self.saved)
