"""SentimentRepository SQLAlchemy 实现"""
import logging

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.repositories.sentiment_repository import SentimentRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import Sentiment

logger = logging.getLogger(__name__)


class SentimentRepositoryImpl(SentimentRepository):
    def upsert_batch(self, items: list[dict]) -> int:
        if not items:
            return 0
        valid = [
            it for it in items
            if it.get("data") and it.get("trade_date") and it.get("data_type")
        ]
        if not valid:
            return 0
        with get_session() as s:
            saved = 0
            for it in valid:
                stmt = pg_insert(Sentiment).values(
                    data_type=it["data_type"],
                    trade_date=it["trade_date"],
                    data=it["data"],
                ).on_conflict_do_nothing()
                result = s.execute(stmt)
                saved += result.rowcount or 0
            return saved

    def count_by_type(self) -> dict[str, int]:
        with get_session() as s:
            rows = s.execute(
                select(Sentiment.data_type, func.count()).group_by(Sentiment.data_type)
            ).all()
            return {dt: cnt for dt, cnt in rows}
