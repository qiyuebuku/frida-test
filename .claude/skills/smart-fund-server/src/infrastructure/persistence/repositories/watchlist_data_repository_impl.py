"""WatchlistData Repository — ft_watchlist_data 的读写"""
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import WatchlistData

logger = logging.getLogger(__name__)


class WatchlistDataRepositoryImpl:

    def save_batch(self, items: list[dict]) -> int:
        """批量写入，(code, data_type, trade_date) 冲突则更新 data

        items: [{"code": "007380", "data_type": "nav", "trade_date": date, "data": {...}}, ...]
        """
        if not items:
            return 0
        saved = 0
        with get_session() as s:
            for item in items:
                stmt = pg_insert(WatchlistData).values(
                    code=item["code"],
                    data_type=item["data_type"],
                    trade_date=item.get("trade_date"),
                    data=item["data"],
                ).on_conflict_do_update(
                    index_elements=["code", "data_type", "trade_date"],
                    set_={"data": item["data"]},
                )
                result = s.execute(stmt)
                saved += result.rowcount or 0
        return saved

    def query_by_code(
        self,
        code: str,
        data_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """查询某标的的数据"""
        with get_session() as s:
            stmt = select(WatchlistData).where(
                WatchlistData.code == code
            )
            if data_type:
                stmt = stmt.where(WatchlistData.data_type == data_type)
            stmt = stmt.order_by(WatchlistData.trade_date.desc()).limit(limit)
            rows = s.scalars(stmt).all()
            return [
                {
                    "id": r.id,
                    "code": r.code,
                    "data_type": r.data_type,
                    "trade_date": r.trade_date,
                    "data": r.data,
                    "created_at": r.created_at,
                }
                for r in rows
            ]

    def query_latest(
        self,
        code: str,
        data_type: str,
    ) -> dict | None:
        """查询某标的某维度的最新一条数据"""
        with get_session() as s:
            row = s.scalars(
                select(WatchlistData).where(
                    WatchlistData.code == code,
                    WatchlistData.data_type == data_type,
                ).order_by(WatchlistData.trade_date.desc()).limit(1)
            ).first()
            if not row:
                return None
            return {
                "code": row.code,
                "data_type": row.data_type,
                "trade_date": row.trade_date,
                "data": row.data,
            }

    def count_by_code(self, code: str) -> dict[str, int]:
        """统计某标的各维度数据条数"""
        from sqlalchemy import func
        with get_session() as s:
            rows = s.execute(
                select(WatchlistData.data_type, func.count())
                .where(WatchlistData.code == code)
                .group_by(WatchlistData.data_type)
            ).all()
            return {r[0]: r[1] for r in rows}

    def delete_by_code(self, code: str) -> int:
        """删除某标的全部数据"""
        from sqlalchemy import delete
        with get_session() as s:
            result = s.execute(
                delete(WatchlistData).where(WatchlistData.code == code)
            )
            return result.rowcount or 0
