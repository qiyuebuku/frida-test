"""仓储实现 — domain 抽象接口的 SQLAlchemy 实现"""
from src.infrastructure.persistence.repositories.collection_state_repository_impl import (
    CollectionStateRepositoryImpl,
)
from src.infrastructure.persistence.repositories.fund_limit_repository_impl import (
    FundLimitRepositoryImpl,
)
from src.infrastructure.persistence.repositories.industry_mapping_repository_impl import (
    IndustryMappingRepositoryImpl,
)
from src.infrastructure.persistence.repositories.macro_repository_impl import (
    MacroRepositoryImpl,
)
from src.infrastructure.persistence.repositories.market_cache_repository_impl import (
    MarketCacheRepositoryImpl,
)
from src.infrastructure.persistence.repositories.market_flow_repository_impl import (
    MarketFlowRepositoryImpl,
)
from src.infrastructure.persistence.repositories.news_repository_impl import (
    NewsRepositoryImpl,
)
from src.infrastructure.persistence.repositories.position_repository_impl import (
    PositionRepositoryImpl,
)
from src.infrastructure.persistence.repositories.sentiment_repository_impl import (
    SentimentRepositoryImpl,
)
from src.infrastructure.persistence.repositories.sentiment_signal_repository_impl import (
    SentimentSignalRepositoryImpl,
)
from src.infrastructure.persistence.repositories.trade_repository_impl import (
    TradeRepositoryImpl,
)

__all__ = [
    # collection
    "NewsRepositoryImpl",
    "MarketFlowRepositoryImpl",
    "MarketCacheRepositoryImpl",
    "SentimentRepositoryImpl",
    "SentimentSignalRepositoryImpl",
    "MacroRepositoryImpl",
    "CollectionStateRepositoryImpl",
    # trading
    "TradeRepositoryImpl",
    "PositionRepositoryImpl",
    "IndustryMappingRepositoryImpl",
    "FundLimitRepositoryImpl",
    # reflection
]
