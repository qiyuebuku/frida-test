"""MarketFlowRepository SQLAlchemy 实现"""
import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.repositories.market_flow_repository import (
    MarketFlowRepository,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import MarketFlow

logger = logging.getLogger(__name__)


class MarketFlowRepositoryImpl(MarketFlowRepository):
    def upsert_batch(self, items: list[dict]) -> int:
        """逐行 INSERT ON CONFLICT,因为 4 个 partial unique 互斥

        bulk INSERT 不能指定多个 partial unique 的 conflict_target,
        所以用 ON CONFLICT DO NOTHING(无 target),让 PG 自动匹配。
        """
        if not items:
            return 0
        # 过滤掉空 data
        valid = [
            it for it in items
            if it.get("data") and it.get("trade_date") and it.get("data_type")
        ]
        if not valid:
            return 0
        with get_session() as s:
            saved = 0
            for it in valid:
                stmt = pg_insert(MarketFlow).values(
                    data_type=it["data_type"],
                    trade_date=it["trade_date"],
                    data=it["data"],
                ).on_conflict_do_nothing()
                result = s.execute(stmt)
                saved += result.rowcount or 0
            return saved

    def max_trade_date(self, data_type: str) -> date | None:
        with get_session() as s:
            return s.scalar(
                select(func.max(MarketFlow.trade_date)).where(
                    MarketFlow.data_type == data_type
                )
            )

    def count_by_type(self) -> dict[str, int]:
        with get_session() as s:
            rows = s.execute(
                select(MarketFlow.data_type, func.count()).group_by(MarketFlow.data_type)
            ).all()
            return {dt: cnt for dt, cnt in rows}

    def get_northbound_net_flow(self, trade_date: str) -> float | None:
        """从 ft_market_flow.northbound 拿指定日期的 data.net_flow"""
        try:
            with get_session() as s:
                row = s.scalars(
                    select(MarketFlow)
                    .where(
                        MarketFlow.data_type == "northbound",
                        MarketFlow.trade_date == trade_date,
                    )
                    .order_by(MarketFlow.created_at.desc())
                    .limit(1)
                ).first()
                if row and row.data:
                    val = row.data.get("net_flow")
                    return float(val) if val is not None else None
        except Exception as e:
            logger.debug(f"get_northbound_net_flow({trade_date}) 失败: {e}")
        return None

    def get_sector_net_inflow(self, sector_name: str, trade_date: str) -> float | None:
        """从 ft_market_flow.sector_flow 拿指定日期 + 板块名的 data.net_amount"""
        try:
            with get_session() as s:
                # JSONB 路径过滤: data->>'name' = sector_name
                from sqlalchemy import text
                row = s.execute(
                    text("""
                        SELECT data->>'net_amount'
                        FROM ft_market_flow
                        WHERE data_type = 'sector_flow'
                          AND trade_date = :td
                          AND data->>'name' = :name
                        LIMIT 1
                    """),
                    {"td": trade_date, "name": sector_name},
                ).fetchone()
                if row and row[0] is not None:
                    return float(row[0])
        except Exception as e:
            logger.debug(f"get_sector_net_inflow({sector_name},{trade_date}) 失败: {e}")
        return None
