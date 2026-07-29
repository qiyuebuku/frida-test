"""TradeRepository SQLAlchemy 实现"""
import json
import logging
from datetime import date

from sqlalchemy import func, select

from src.domain.trading.repositories.trade_repository import TradeRepository
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.trading import Trade
from src.infrastructure.time_utils import app_today

logger = logging.getLogger(__name__)


class TradeRepositoryImpl(TradeRepository):
    def insert(
        self,
        decision_id: int,
        fund_code: str,
        fund_name: str,
        action: str,
        amount: float,
        shares: float | None,
        order_no: str,
        reason: str,
        api_response: dict | None,
        dry_run: bool,
    ) -> int | None:
        try:
            with get_session() as s:
                row = Trade(
                    fund_code=fund_code,
                    fund_name=fund_name,
                    action=action,
                    amount=amount,
                    shares=shares,
                    order_no=order_no,
                    reason=reason,
                    api_response=api_response or {},
                    dry_run=dry_run,
                    decision_id=decision_id,
                )
                s.add(row)
                s.flush()
                return row.id
        except Exception as e:
            logger.warning(f"trade insert 失败: {e}")
            return None

    def paper_positions(self) -> list[dict]:
        """从 dry_run trades 聚合 paper position"""
        try:
            with get_session() as s:
                from sqlalchemy import text
                rows = s.execute(text("""
                    SELECT
                        fund_code,
                        MAX(fund_name) AS fund_name,
                        SUM(CASE WHEN action='buy' THEN amount ELSE -amount END) AS total_cost,
                        SUM(CASE WHEN action='buy' THEN COALESCE(shares,0) ELSE -COALESCE(shares,0) END) AS shares,
                        MIN(trade_date) AS first_buy_date,
                        COUNT(*) FILTER (WHERE action='buy') AS buy_count
                    FROM ft_trades
                    WHERE dry_run = TRUE
                    GROUP BY fund_code
                    HAVING SUM(CASE WHEN action='buy' THEN amount ELSE -amount END) > 0
                """)).fetchall()
                return [
                    {
                        "fund_code": r[0],
                        "fund_name": r[1],
                        "total_cost": float(r[2]) if r[2] else 0,
                        "shares": float(r[3]) if r[3] else 0,
                        "first_buy_date": r[4],
                        "buy_count": r[5],
                        "profit_pct": 0.0,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"paper_positions 失败: {e}")
            return []

    def today_live_buy_total(self) -> float:
        try:
            with get_session() as s:
                total = s.scalar(
                    select(func.coalesce(func.sum(Trade.amount), 0))
                    .where(
                        Trade.trade_date == app_today(),
                        Trade.action == "buy",
                        Trade.dry_run.is_(False),
                    )
                )
                return float(total) if total else 0.0
        except Exception:
            return 0.0
