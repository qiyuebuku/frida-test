"""SentimentSignalRepository SQLAlchemy 实现"""
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import SentimentSignal

logger = logging.getLogger(__name__)


class SentimentSignalRepositoryImpl:
    def upsert_snapshot(
        self,
        snapshot_date: date,
        market_temperature: int,
        market_level: str,
        market_trend: str | None = None,
        signals: dict | None = None,
        overheat_codes: dict | None = None,
        leading_theme: dict | None = None,
        sentiment_agg: dict | None = None,
        contributors: dict | None = None,
    ) -> None:
        """幂等 UPSERT：ON CONFLICT (snapshot_date) DO UPDATE"""
        values = {
            "snapshot_date": snapshot_date,
            "market_temperature": market_temperature,
            "market_level": market_level,
            "market_trend": market_trend,
            "signals": signals or {},
            "overheat_codes": overheat_codes,
            "leading_theme": leading_theme,
            "sentiment_agg": sentiment_agg,
            "contributors": contributors,
        }
        with get_session() as s:
            stmt = pg_insert(SentimentSignal).values(**values).on_conflict_do_update(
                index_elements=["snapshot_date"],
                set_={
                    "market_temperature": market_temperature,
                    "market_level": market_level,
                    "market_trend": market_trend,
                    "signals": values["signals"],
                    "overheat_codes": overheat_codes,
                    "leading_theme": leading_theme,
                    "sentiment_agg": sentiment_agg,
                    "contributors": contributors,
                },
            )
            s.execute(stmt)

    def get_snapshot(self, snapshot_date: date) -> dict | None:
        with get_session() as s:
            row = s.scalars(
                select(SentimentSignal).where(SentimentSignal.snapshot_date == snapshot_date)
            ).first()
            if not row:
                return None
            return {
                "snapshot_date": row.snapshot_date.isoformat(),
                "market_temperature": row.market_temperature,
                "market_level": row.market_level,
                "market_trend": row.market_trend,
                "signals": row.signals,
                "overheat_codes": row.overheat_codes,
                "leading_theme": row.leading_theme,
                "sentiment_agg": row.sentiment_agg,
                "contributors": row.contributors,
            }

    def get_range(self, start: date, end: date) -> list[dict]:
        with get_session() as s:
            rows = s.scalars(
                select(SentimentSignal)
                .where(SentimentSignal.snapshot_date >= start, SentimentSignal.snapshot_date <= end)
                .order_by(SentimentSignal.snapshot_date.desc())
            ).all()
            return [
                {
                    "snapshot_date": r.snapshot_date.isoformat(),
                    "market_temperature": r.market_temperature,
                    "market_level": r.market_level,
                    "market_trend": r.market_trend,
                    "signals": r.signals,
                    "overheat_codes": r.overheat_codes,
                    "leading_theme": r.leading_theme,
                    "sentiment_agg": r.sentiment_agg,
                }
                for r in rows
            ]
