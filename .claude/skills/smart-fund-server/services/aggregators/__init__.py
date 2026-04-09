"""数据聚合层

按数据类型（非数据来源）组织，每个聚合维度对应一个定时任务和一张结果表。
"""

from services.aggregators.base import BaseAggregator, SourceDef
from services.aggregators.news import NewsAggregator
from services.aggregators.fund_flow import FundFlowAggregator
from services.aggregators.macro import MacroAggregator
from services.aggregators.sentiment import SentimentAggregator
from services.aggregators.market import MarketAggregator
from services.aggregators.event_feedback import EventFeedbackAggregator

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
