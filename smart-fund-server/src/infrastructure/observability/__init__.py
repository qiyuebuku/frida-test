"""可观测性 — 统一日志 / 指标 / 链路追踪

R4 引入。统一各处的 logger 配置 + Prometheus metrics 出口。
"""
from src.infrastructure.observability.logging import configure_logging, get_logger
from src.infrastructure.observability.metrics import (
    COLLECTION_DURATION,
    COLLECTION_SAVED,
    DECISION_COUNT,
    DRY_RUN_TRADE_COUNT,
    LIVE_TRADE_COUNT,
    record_collection,
    record_decision,
    record_trade,
    metrics_text,
)

__all__ = [
    "configure_logging",
    "get_logger",
    "COLLECTION_DURATION",
    "COLLECTION_SAVED",
    "DECISION_COUNT",
    "DRY_RUN_TRADE_COUNT",
    "LIVE_TRADE_COUNT",
    "record_collection",
    "record_decision",
    "record_trade",
    "metrics_text",
]
