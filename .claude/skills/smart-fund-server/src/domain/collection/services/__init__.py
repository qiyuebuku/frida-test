"""数据采集服务 — 5 个 collector + 1 个 base

R3.3 重构后,这些服务从 src/domain/aggregation/ 迁移过来。
不要再 import src/domain/aggregation/,那个目录已废弃。
"""
from src.domain.collection.services.base import BaseAggregator, SourceDef
from src.domain.collection.services.fund_flow import FundFlowAggregator
from src.domain.collection.services.macro import MacroAggregator
from src.domain.collection.services.market import MarketAggregator
from src.domain.collection.services.news import NewsAggregator
from src.domain.collection.services.sentiment import SentimentAggregator

__all__ = [
    "BaseAggregator",
    "SourceDef",
    "NewsAggregator",
    "FundFlowAggregator",
    "MarketAggregator",
    "SentimentAggregator",
    "MacroAggregator",
]
