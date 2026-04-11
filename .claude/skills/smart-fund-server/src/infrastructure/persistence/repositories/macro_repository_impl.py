"""MacroRepository SQLAlchemy 实现"""
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.repositories.macro_repository import MacroRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import MacroIndicator

logger = logging.getLogger(__name__)


class MacroRepositoryImpl(MacroRepository):
    def upsert_batch(self, items: list[dict]) -> int:
        if not items:
            return 0
        valid = [
            it for it in items
            if it.get("indicator") and it.get("period") and it.get("value") is not None
        ]
        if not valid:
            return 0
        with get_session() as s:
            stmt = pg_insert(MacroIndicator).values(valid).on_conflict_do_nothing(
                index_elements=["indicator", "period", "source"]
            )
            result = s.execute(stmt)
            return result.rowcount or 0

    def latest_per_indicator(self) -> list[dict]:
        with get_session() as s:
            rows = s.execute(
                select(
                    MacroIndicator.indicator,
                    MacroIndicator.period,
                    MacroIndicator.value,
                    MacroIndicator.unit,
                    MacroIndicator.published_at,
                ).distinct(MacroIndicator.indicator).order_by(
                    MacroIndicator.indicator, MacroIndicator.published_at.desc()
                )
            ).all()
            return [
                {"indicator": r[0], "period": r[1], "value": r[2], "unit": r[3], "published_at": r[4]}
                for r in rows
            ]
