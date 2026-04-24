"""RuleThresholdRepository SQLAlchemy 实现"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.extraction.repositories.rule_threshold_repository import RuleThresholdRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.extraction import RuleThreshold

logger = logging.getLogger(__name__)


class RuleThresholdRepositoryImpl(RuleThresholdRepository):

    def get_threshold(self, rule_name: str) -> dict | None:
        with get_session() as s:
            row = s.scalar(
                select(RuleThreshold).where(RuleThreshold.rule_name == rule_name).limit(1)
            )
            if not row:
                return None
            return self._to_dict(row)

    def get_thresholds_by_source(self, data_source: str) -> list[dict]:
        with get_session() as s:
            rows = s.scalars(
                select(RuleThreshold).where(RuleThreshold.data_source == data_source)
            ).all()
            return [self._to_dict(r) for r in rows]

    def upsert_threshold(self, data: dict) -> bool:
        try:
            with get_session() as s:
                stmt = pg_insert(RuleThreshold).values(**data).on_conflict_do_update(
                    index_elements=["rule_name"],
                    set_={
                        "percentile_95": data.get("percentile_95"),
                        "percentile_99": data.get("percentile_99"),
                        "sigma_value": data.get("sigma_value"),
                        "threshold_config": data.get("threshold_config", {}),
                        "window_days": data.get("window_days", 90),
                        "last_computed_at": func.now(),
                        "updated_at": func.now(),
                    },
                )
                s.execute(stmt)
                return True
        except Exception as e:
            logger.warning(f"upsert_threshold({data.get('rule_name')}) 失败: {e}")
            return False

    def get_all_stale(self, max_age_hours: int = 25) -> list[dict]:
        with get_session() as s:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
            rows = s.scalars(
                select(RuleThreshold).where(
                    (RuleThreshold.last_computed_at.is_(None))
                    | (RuleThreshold.last_computed_at < cutoff)
                )
            ).all()
            return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: RuleThreshold) -> dict:
        return {
            "id": row.id,
            "rule_name": row.rule_name,
            "data_source": row.data_source,
            "metric_name": row.metric_name,
            "window_days": row.window_days,
            "percentile_95": row.percentile_95,
            "percentile_99": row.percentile_99,
            "sigma_value": row.sigma_value,
            "threshold_config": row.threshold_config or {},
            "last_computed_at": row.last_computed_at,
        }
