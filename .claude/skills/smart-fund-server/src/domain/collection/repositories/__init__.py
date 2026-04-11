"""采集层仓储抽象接口

DDD 风格: domain 层定义接口,infrastructure 层实现。
domain 模块只 import 这里的抽象类,不 import SQLAlchemy / psycopg2。
"""
from src.domain.collection.repositories.collection_state_repository import (
    CollectionStateRepository,
)
from src.domain.collection.repositories.macro_repository import MacroRepository
from src.domain.collection.repositories.market_cache_repository import (
    MarketCacheRepository,
)
from src.domain.collection.repositories.market_flow_repository import (
    MarketFlowRepository,
)
from src.domain.collection.repositories.news_repository import NewsRepository
from src.domain.collection.repositories.sentiment_repository import (
    SentimentRepository,
)

__all__ = [
    "NewsRepository",
    "MarketFlowRepository",
    "MarketCacheRepository",
    "SentimentRepository",
    "MacroRepository",
    "CollectionStateRepository",
]
