"""MarketCacheRepository SQLAlchemy 实现"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.repositories.market_cache_repository import (
    MarketCacheRepository,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import MarketCache
from src.infrastructure.time_utils import app_now

logger = logging.getLogger(__name__)


class MarketCacheRepositoryImpl(MarketCacheRepository):
    def upsert(
        self, data_type: str, data: dict, expires_at: datetime,
    ) -> None:
        """覆盖式 UPSERT (unique on data_type)"""
        with get_session() as s:
            stmt = pg_insert(MarketCache).values(
                data_type=data_type, data=data, expires_at=expires_at,
            ).on_conflict_do_update(
                index_elements=["data_type"],
                set_={
                    "data": data,
                    "expires_at": expires_at,
                    "created_at": app_now().replace(tzinfo=None),
                },
            )
            s.execute(stmt)

    def find_by_type(self, data_type: str) -> dict | None:
        with get_session() as s:
            row = s.scalars(
                select(MarketCache).where(MarketCache.data_type == data_type)
            ).first()
            return row.data if row else None

    def all_types(self) -> list[str]:
        with get_session() as s:
            rows = s.scalars(
                select(MarketCache.data_type).order_by(MarketCache.data_type)
            ).all()
            return list(rows)
