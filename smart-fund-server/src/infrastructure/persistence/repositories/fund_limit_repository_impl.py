"""FundLimitRepository SQLAlchemy 实现"""
import logging

from sqlalchemy import select

from src.domain.trading.repositories.fund_limit_repository import FundLimitRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.trading import FundLimit

logger = logging.getLogger(__name__)


class FundLimitRepositoryImpl(FundLimitRepository):
    def get(self, fund_code: str) -> dict | None:
        try:
            with get_session() as s:
                row = s.scalars(
                    select(FundLimit).where(FundLimit.fund_code == fund_code)
                ).first()
                if not row:
                    return None
                return {
                    "min_buy": float(row.min_buy or 0),
                    "max_buy": float(row.max_buy or 0),
                    "daily_limit": float(row.daily_limit or 0),
                    "is_suspended": bool(row.is_suspended),
                }
        except Exception:
            return None
