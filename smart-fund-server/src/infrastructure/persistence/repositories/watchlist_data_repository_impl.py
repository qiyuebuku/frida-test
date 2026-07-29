"""WatchlistData Repository — ft_watchlist_data 的读写"""
import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import WatchlistData

logger = logging.getLogger(__name__)


class WatchlistDataRepositoryImpl:

    CHUNK_SIZE = 1000

    def save_batch(self, items: list[dict]) -> int:
        """批量写入，(code, data_type, trade_date) 冲突则更新 data

        使用 PostgreSQL multi-row VALUES + ON CONFLICT DO UPDATE (EXCLUDED.data)
        一条 SQL 插入 CHUNK_SIZE 条，大幅减少 round-trip 次数。

        items: [{"code": "007380", "data_type": "nav", "trade_date": date, "data": {...}}, ...]
        """
        if not items:
            return 0

        saved = 0
        with get_session() as s:
            for i in range(0, len(items), self.CHUNK_SIZE):
                chunk = items[i:i + self.CHUNK_SIZE]
                values = [
                    {
                        "code": it["code"],
                        "data_type": it["data_type"],
                        "trade_date": it.get("trade_date"),
                        "data": it["data"],
                    }
                    for it in chunk
                ]
                stmt = pg_insert(WatchlistData).values(values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["code", "data_type", "trade_date"],
                    set_={
                        "data": stmt.excluded.data,
                        "updated_at": func.now(),
                    },
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
                    "updated_at": r.updated_at,
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
                "updated_at": row.updated_at,
            }

    def query_latest_by_codes(
        self,
        codes: list[str],
        data_types: list[str] | None = None,
    ) -> list[dict]:
        """Return the latest row for every (code, data_type)."""

        normalized_codes = [
            code
            for code in dict.fromkeys(str(code).strip().lower() for code in codes)
            if code
        ]
        if not normalized_codes:
            return []
        with get_session() as session:
            ranked = (
                select(
                    WatchlistData.id.label("id"),
                    func.row_number()
                    .over(
                        partition_by=(
                            WatchlistData.code,
                            WatchlistData.data_type,
                        ),
                        order_by=(
                            WatchlistData.trade_date.desc().nullslast(),
                            WatchlistData.updated_at.desc().nullslast(),
                            WatchlistData.id.desc(),
                        ),
                    )
                    .label("rank"),
                )
                .where(WatchlistData.code.in_(normalized_codes))
            )
            if data_types:
                ranked = ranked.where(WatchlistData.data_type.in_(data_types))
            ranked_subquery = ranked.subquery()
            rows = session.scalars(
                select(WatchlistData)
                .join(ranked_subquery, ranked_subquery.c.id == WatchlistData.id)
                .where(ranked_subquery.c.rank == 1)
                .order_by(WatchlistData.code, WatchlistData.data_type)
            ).all()
            return [_serialize(row) for row in rows]

    def query_history(
        self,
        *,
        code: str,
        data_type: str,
        date_start: date | None = None,
        date_end: date | None = None,
        limit: int = 120,
    ) -> list[dict]:
        with get_session() as session:
            stmt = select(WatchlistData).where(
                WatchlistData.code == str(code).strip().lower(),
                WatchlistData.data_type == str(data_type).strip(),
            )
            if date_start is not None:
                stmt = stmt.where(WatchlistData.trade_date >= date_start)
            if date_end is not None:
                stmt = stmt.where(WatchlistData.trade_date <= date_end)
            rows = session.scalars(
                stmt.order_by(WatchlistData.trade_date.desc()).limit(
                    max(1, min(int(limit), 500))
                )
            ).all()
            return [_serialize(row) for row in rows]

    def count_by_code(self, code: str) -> dict[str, int]:
        """统计某标的各维度数据条数"""
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


def _serialize(row: WatchlistData) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "data_type": row.data_type,
        "trade_date": row.trade_date,
        "data": row.data,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
