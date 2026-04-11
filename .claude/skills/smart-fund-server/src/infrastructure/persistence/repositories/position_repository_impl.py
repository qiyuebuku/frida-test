"""PositionRepository SQLAlchemy 实现"""
import logging

from sqlalchemy import select, text
from sqlalchemy.sql import func

from src.domain.trading.repositories.position_repository import PositionRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.trading import Position

logger = logging.getLogger(__name__)


class PositionRepositoryImpl(PositionRepository):
    def find_live_positions(self) -> list[dict]:
        try:
            with get_session() as s:
                rows = s.scalars(
                    select(Position).where(Position.total_cost > 0)
                ).all()
                return [
                    {
                        "fund_code": r.fund_code,
                        "fund_name": r.fund_name,
                        "total_cost": float(r.total_cost or 0),
                        "shares": float(r.shares or 0),
                        "avg_cost": float(r.avg_cost or 0),
                        "current_nav": float(r.current_nav or 0),
                        "market_value": float(r.market_value or 0),
                        "profit_pct": float(r.profit_pct or 0),
                        "first_buy_date": r.first_buy_date,
                        "add_count": r.add_count or 0,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"find_live_positions 失败: {e}")
            return []

    def upsert_after_buy(
        self, fund_code: str, fund_name: str, amount: float, shares: float,
    ) -> None:
        """LIVE 模式买入后增量更新持仓"""
        try:
            with get_session() as s:
                # 用 raw SQL 因为有 NULLIF 表达式
                s.execute(
                    text("""
                        INSERT INTO ft_positions (
                            fund_code, fund_name, total_cost, shares, avg_cost,
                            first_buy_date, add_count, updated_at
                        ) VALUES (
                            :fund_code, :fund_name, :amount, :shares, :avg_cost,
                            CURRENT_DATE, 1, NOW()
                        )
                        ON CONFLICT (fund_code) DO UPDATE SET
                            total_cost = ft_positions.total_cost + EXCLUDED.total_cost,
                            shares = ft_positions.shares + EXCLUDED.shares,
                            avg_cost = (ft_positions.total_cost + EXCLUDED.total_cost) /
                                       NULLIF(ft_positions.shares + EXCLUDED.shares, 0),
                            add_count = ft_positions.add_count + 1,
                            updated_at = NOW()
                    """),
                    {
                        "fund_code": fund_code,
                        "fund_name": fund_name,
                        "amount": amount,
                        "shares": shares,
                        "avg_cost": amount / shares if shares > 0 else 0,
                    },
                )
        except Exception as e:
            logger.warning(f"upsert_after_buy 失败: {e}")
