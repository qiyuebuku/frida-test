"""自选标的管理服务

提供自选标的的增删改查，底层复用 ft_collection_state 表（aggregator='watchlist'）。
"""
from datetime import date, timedelta
from dataclasses import dataclass

from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import CollectionState
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert


AGGREGATOR = "watchlist"

# 默认采集配置
DEFAULT_CONFIG = {
    "interval": 1800,
    "target_days": 10,
}


@dataclass
class WatchlistItem:
    code: str
    name: str
    type: str           # stock / fund / index
    source: str         # manual / position / event
    enabled: bool
    mode: str
    newest_time: str | None
    oldest_time: str | None
    backfill_status: str | None
    config: dict
    total_runs: int
    total_saved: int

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "type": self.type,
            "source": self.source,
            "enabled": self.enabled,
            "mode": self.mode,
            "newest_time": self.newest_time,
            "oldest_time": self.oldest_time,
            "backfill_status": self.backfill_status,
            "config": self.config,
            "total_runs": self.total_runs,
            "total_saved": self.total_saved,
        }


def _row_to_item(row: CollectionState) -> WatchlistItem:
    config = row.config or {}
    return WatchlistItem(
        code=row.source_name,
        name=config.get("name", ""),
        type=config.get("type", "stock"),
        source=config.get("source", "manual"),
        enabled=row.enabled if row.enabled is not None else True,
        mode=row.mode or "incremental",
        newest_time=row.newest_time,
        oldest_time=row.oldest_time,
        backfill_status=row.backfill_status,
        config=config,
        total_runs=row.total_runs or 0,
        total_saved=row.total_saved or 0,
    )


class WatchlistService:

    def list_all(self, enabled_only: bool = False) -> list[WatchlistItem]:
        with get_session() as s:
            stmt = select(CollectionState).where(
                CollectionState.aggregator == AGGREGATOR
            ).order_by(CollectionState.source_name)
            if enabled_only:
                stmt = stmt.where(CollectionState.enabled == True)  # noqa: E712
            rows = s.scalars(stmt).all()
            return [_row_to_item(r) for r in rows]

    def get(self, code: str) -> WatchlistItem | None:
        with get_session() as s:
            row = s.scalars(
                select(CollectionState).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name == code,
                )
            ).first()
            return _row_to_item(row) if row else None

    def add(
        self,
        code: str,
        name: str = "",
        type: str = "stock",
        source: str = "manual",
        target_days: int | None = None,
        interval: int | None = None,
    ) -> bool:
        """添加自选，已存在则跳过。返回 True=新建, False=已存在"""
        td = target_days or DEFAULT_CONFIG["target_days"]
        itv = interval or DEFAULT_CONFIG["interval"]
        target_time = (date.today() - timedelta(days=td)).isoformat()

        config = {
            "name": name,
            "type": type,
            "source": source,
            "interval": itv,
            "target_days": td,
        }

        with get_session() as s:
            stmt = pg_insert(CollectionState).values(
                aggregator=AGGREGATOR,
                source_name=code,
                mode="backfill",
                target_time=target_time,
                config=config,
            ).on_conflict_do_nothing(
                index_elements=["aggregator", "source_name"],
            )
            result = s.execute(stmt)
            return (result.rowcount or 0) > 0

    def add_batch(self, items: list[dict]) -> int:
        """批量添加自选，返回新增数量

        items: [{"code": "sh600036", "name": "招商银行", "type": "stock"}, ...]
        """
        count = 0
        for item in items:
            created = self.add(
                code=item["code"],
                name=item.get("name", ""),
                type=item.get("type", "stock"),
                source=item.get("source", "manual"),
                target_days=item.get("target_days"),
                interval=item.get("interval"),
            )
            if created:
                count += 1
        return count

    def remove(self, code: str) -> bool:
        """删除自选，返回是否成功"""
        from sqlalchemy import delete
        with get_session() as s:
            result = s.execute(
                delete(CollectionState).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name == code,
                )
            )
            return (result.rowcount or 0) > 0

    def update(self, code: str, **kwargs) -> bool:
        """更新自选配置

        支持的 kwargs: enabled, name, type, interval, target_days
        """
        from sqlalchemy import update
        from sqlalchemy.sql import func

        with get_session() as s:
            # 先读当前 config
            row = s.scalars(
                select(CollectionState).where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name == code,
                )
            ).first()
            if not row:
                return False

            update_values = {"updated_at": func.now()}
            config = dict(row.config or {})

            # 表级字段
            if "enabled" in kwargs:
                update_values["enabled"] = kwargs["enabled"]

            # config 里的字段
            for key in ("name", "type", "interval", "target_days", "source"):
                if key in kwargs:
                    config[key] = kwargs[key]

            update_values["config"] = config

            s.execute(
                update(CollectionState)
                .where(
                    CollectionState.aggregator == AGGREGATOR,
                    CollectionState.source_name == code,
                )
                .values(**update_values)
            )
            return True

    def sync_from_positions(self) -> int:
        """从 ft_positions 同步持仓到自选，返回新增数量"""
        from src.infrastructure.persistence.models.trading import Position

        with get_session() as s:
            positions = s.scalars(
                select(Position).where(Position.shares > 0)
            ).all()

        count = 0
        for p in positions:
            created = self.add(
                code=p.fund_code,
                name=p.fund_name or "",
                type="fund",
                source="position",
            )
            if created:
                count += 1
        return count
