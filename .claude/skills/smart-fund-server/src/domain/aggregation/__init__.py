"""数据聚合层

按数据类型（非数据来源）组织，每个聚合维度对应一个定时任务和一张结果表。
"""

from src.domain.aggregation.base import BaseAggregator, SourceDef
from src.domain.aggregation.news import NewsAggregator
from src.domain.aggregation.fund_flow import FundFlowAggregator
from src.domain.aggregation.macro import MacroAggregator
from src.domain.aggregation.sentiment import SentimentAggregator
from src.domain.aggregation.market import MarketAggregator
from src.domain.aggregation.event_feedback import EventFeedbackAggregator

__all__ = [
    "BaseAggregator",
    "SourceDef",
    "NewsAggregator",
    "FundFlowAggregator",
    "MacroAggregator",
    "SentimentAggregator",
    "MarketAggregator",
    "EventFeedbackAggregator",
]
