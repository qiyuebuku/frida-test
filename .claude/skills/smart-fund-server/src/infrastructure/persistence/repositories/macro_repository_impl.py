"""MacroRepository SQLAlchemy 实现"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.repositories.macro_repository import MacroRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import MacroIndicator, MacroRegime

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
            stmt = pg_insert(MacroIndicator).values(valid)
            stmt = stmt.on_conflict_do_update(
                index_elements=["indicator", "period", "source"],
                set_={
                    "value": stmt.excluded.value,
                    "unit": stmt.excluded.unit,
                    "prev_value": stmt.excluded.prev_value,
                    "published_at": stmt.excluded.published_at,
                    "dim_tag": stmt.excluded.dim_tag,
                    "yoy": stmt.excluded.yoy,
                    "mom": stmt.excluded.mom,
                },
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
                    MacroIndicator.dim_tag,
                    MacroIndicator.yoy,
                    MacroIndicator.mom,
                    MacroIndicator.prev_value,
                ).distinct(MacroIndicator.indicator).order_by(
                    MacroIndicator.indicator, MacroIndicator.published_at.desc()
                )
            ).all()
            return [
                {
                    "indicator": r[0], "period": r[1], "value": r[2],
                    "unit": r[3], "published_at": r[4], "dim_tag": r[5],
                    "yoy": r[6], "mom": r[7], "prev_value": r[8],
                }
                for r in rows
            ]

    def latest_by_dim(self, dim_tag: str) -> list[dict]:
        with get_session() as s:
            rows = s.execute(
                select(
                    MacroIndicator.indicator,
                    MacroIndicator.period,
                    MacroIndicator.value,
                    MacroIndicator.unit,
                    MacroIndicator.published_at,
                    MacroIndicator.dim_tag,
                    MacroIndicator.yoy,
                    MacroIndicator.mom,
                    MacroIndicator.prev_value,
                ).where(
                    MacroIndicator.dim_tag == dim_tag
                ).distinct(MacroIndicator.indicator).order_by(
                    MacroIndicator.indicator, MacroIndicator.published_at.desc()
                )
            ).all()
            return [
                {
                    "indicator": r[0], "period": r[1], "value": r[2],
                    "unit": r[3], "published_at": r[4], "dim_tag": r[5],
                    "yoy": r[6], "mom": r[7], "prev_value": r[8],
                }
                for r in rows
            ]

    # ==================== ft_macro_regime ====================

    def upsert_regime(self, row: dict) -> None:
        with get_session() as s:
            stmt = pg_insert(MacroRegime).values([row])
            stmt = stmt.on_conflict_do_update(
                index_elements=["snapshot_date"],
                set_={
                    "computed_at": stmt.excluded.computed_at,
                    "regime": stmt.excluded.regime,
                    "overall_score": stmt.excluded.overall_score,
                    "multiplier": stmt.excluded.multiplier,
                    "liquidity_score": stmt.excluded.liquidity_score,
                    "growth_score": stmt.excluded.growth_score,
                    "inflation_score": stmt.excluded.inflation_score,
                    "external_score": stmt.excluded.external_score,
                    "policy_score": stmt.excluded.policy_score,
                    "contributors": stmt.excluded.contributors,
                },
            )
            s.execute(stmt)

    def get_current_regime(self) -> dict | None:
        with get_session() as s:
            row = s.execute(
                select(MacroRegime)
                .order_by(MacroRegime.snapshot_date.desc())
                .limit(1)
            ).scalar_one_or_none()
            if not row:
                return None
            return {
                "snapshot_date": row.snapshot_date,
                "regime": row.regime,
                "overall_score": row.overall_score,
                "multiplier": row.multiplier,
                "liquidity_score": row.liquidity_score,
                "growth_score": row.growth_score,
                "inflation_score": row.inflation_score,
                "external_score": row.external_score,
                "policy_score": row.policy_score,
                "contributors": row.contributors,
                "computed_at": row.computed_at,
            }

    def get_regime_history(self, days: int = 30) -> list[dict]:
        since = date.today() - timedelta(days=days)
        with get_session() as s:
            rows = s.scalars(
                select(MacroRegime)
                .where(MacroRegime.snapshot_date >= since)
                .order_by(MacroRegime.snapshot_date.desc())
            ).all()
            return [
                {
                    "snapshot_date": r.snapshot_date,
                    "regime": r.regime,
                    "overall_score": r.overall_score,
                    "multiplier": r.multiplier,
                    "computed_at": r.computed_at,
                }
                for r in rows
            ]
