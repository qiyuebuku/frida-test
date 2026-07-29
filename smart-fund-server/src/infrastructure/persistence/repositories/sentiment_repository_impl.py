"""SentimentRepository SQLAlchemy 实现"""
import logging
from datetime import date, timedelta

from sqlalchemy import func, select, text
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

    def get_sentiment_trend(self, industry: str, days: int = 3) -> str:
        """计算指定行业的情绪趋势（v1: ILIKE 模糊匹配，粗糙但可用）

        Returns: "rising" / "stable" / "falling" / "unknown"
        """
        try:
            with get_session() as s:
                since = date.today() - timedelta(days=days)
                pattern = f"%{industry}%"
                rows = s.execute(
                    text("""
                        SELECT trade_date, count(*) AS cnt
                        FROM ft_sentiment
                        WHERE trade_date >= :since
                          AND data::text ILIKE :pat
                        GROUP BY trade_date
                        ORDER BY trade_date
                    """),
                    {"since": since.isoformat(), "pat": pattern},
                ).fetchall()

                if len(rows) < 2:
                    return "unknown"

                # 简单线性回归斜率
                n = len(rows)
                xs = list(range(n))
                ys = [r[1] for r in rows]
                x_mean = sum(xs) / n
                y_mean = sum(ys) / n
                numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
                denominator = sum((x - x_mean) ** 2 for x in xs)
                if denominator == 0:
                    return "stable"
                slope = numerator / denominator

                if slope > 0.5:
                    return "rising"
                elif slope < -0.5:
                    return "falling"
                return "stable"
        except Exception as e:
            logger.debug(f"get_sentiment_trend({industry}) 失败: {e}")
            return "unknown"
