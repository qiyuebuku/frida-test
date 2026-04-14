"""ORM 模型 — 与 schema/*.sql 一一对应

所有模型继承 base.Base,通过 import 触发注册到 Base.registry。
"""
from src.infrastructure.persistence.models.base import Base
from src.infrastructure.persistence.models.collection import (
    CollectionState,
    MacroIndicator,
    MarketCache,
    MarketFlow,
    News,
    Sentiment,
    WatchlistData,
)
from src.infrastructure.persistence.models.extraction import (
    Event,
    EventStream,
)
from src.infrastructure.persistence.models.trading import (
    Decision,
    FundLimit,
    IndexFund,
    IndustryIndex,
    IndustryMapping,
    PendingDecision,
    Position,
    Trade,
)
from src.infrastructure.persistence.models.reflection import (
    Lesson,
    Review,
)

__all__ = [
    "Base",
    # collection
    "News",
    "MarketFlow",
    "MarketCache",
    "Sentiment",
    "MacroIndicator",
    "CollectionState",
    "WatchlistData",
    # extraction
    "Event",
    "EventStream",
    # trading
    "PendingDecision",
    "Decision",
    "Trade",
    "Position",
    "IndustryMapping",
    "IndustryIndex",
    "IndexFund",
    "FundLimit",
    # reflection
    "Review",
    "Lesson",
]
