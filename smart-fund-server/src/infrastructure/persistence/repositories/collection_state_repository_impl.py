"""CollectionStateRepository SQLAlchemy 实现

R5: checkpoint JSONB 字段已提升为独立列（mode/target_time/newest_time/oldest_time/backfill_status/cursor）
"""
import logging

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from src.domain.collection.repositories.collection_state_repository import (
    CollectionStateRepository,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.collection import CollectionState

logger = logging.getLogger(__name__)


def _row_to_dict(row: CollectionState) -> dict:
    """ORM 行转 dict"""
    return {
        "id": row.id,
        "aggregator": row.aggregator,
        "source_name": row.source_name,
        # 采集进度（独立列）
        "mode": row.mode or "incremental",
        "target_time": row.target_time,
        "newest_time": row.newest_time,
        "oldest_time": row.oldest_time,
        "backfill_status": row.backfill_status,
        "cursor": row.cursor,
        # 运行时状态
        "last_run_at": row.last_run_at,
        "last_success_at": row.last_success_at,
        "last_error": row.last_error or "",
        "consecutive_failures": row.consecutive_failures or 0,
        "enabled": row.enabled if row.enabled is not None else True,
        "interval_override": row.interval_override,
        "total_runs": row.total_runs or 0,
        "total_saved": row.total_saved or 0,
        "task_id": row.task_id,
        "task_type": row.task_type,
        "status": row.status or "pending",
        "last_started_at": row.last_started_at,
        "last_received_at": row.last_received_at,
        "last_source_at": row.last_source_at,
        "last_persisted_at": row.last_persisted_at,
        "last_finished_at": row.last_finished_at,
        "last_duration_ms": row.last_duration_ms,
        "last_fetched_count": row.last_fetched_count or 0,
        "last_saved_count": row.last_saved_count or 0,
        "total_received": row.total_received or 0,
        "runtime_details": row.runtime_details or {},
        "config": row.config or {},
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class CollectionStateRepositoryImpl(CollectionStateRepository):
    def ensure_task_state(
        self, *, task_id: str, aggregator: str, source_name: str, task_type: str,
    ) -> None:
        with get_session() as s:
            s.execute(pg_insert(CollectionState).values(
                aggregator=aggregator,
                source_name=source_name,
                task_id=task_id,
                task_type=task_type,
                status="pending",
            ).on_conflict_do_nothing(
                index_elements=["aggregator", "source_name"],
            ))

    def get_by_task_id(self, task_id: str) -> dict | None:
        try:
            with get_session() as s:
                row = s.scalars(
                    select(CollectionState)
                    .where(CollectionState.task_id == task_id)
                    .order_by(CollectionState.updated_at.desc())
                ).first()
                return _row_to_dict(row) if row else None
        except Exception as e:
            logger.debug("CollectionStateRepo.get_by_task_id(%s) 失败: %s", task_id, e)
            return None

    def mark_started(
        self, *, task_id: str, aggregator: str, source_name: str,
        task_type: str, details: dict | None = None,
    ) -> None:
        now = func.now()
        values = {
            "aggregator": aggregator, "source_name": source_name,
            "task_id": task_id, "task_type": task_type, "status": "running",
            "last_started_at": now, "last_run_at": now,
            "runtime_details": details or {}, "updated_at": now,
        }
        update_set = {key: value for key, value in values.items() if key not in {"aggregator", "source_name"}}
        with get_session() as s:
            s.execute(pg_insert(CollectionState).values(**values).on_conflict_do_update(
                index_elements=["aggregator", "source_name"], set_=update_set,
            ))

    def mark_received(
        self, *, task_id: str, aggregator: str, source_name: str,
        task_type: str = "push", received_count: int = 1,
        source_at=None, details: dict | None = None,
    ) -> None:
        now = func.now()
        values = {
            "aggregator": aggregator, "source_name": source_name,
            "task_id": task_id, "task_type": task_type, "status": "running",
            "last_received_at": now, "last_source_at": source_at,
            "total_received": max(0, int(received_count)),
            "runtime_details": details or {}, "updated_at": now,
        }
        update_set = {
            "task_id": task_id, "task_type": task_type, "status": "running",
            "last_received_at": now,
            "total_received": func.coalesce(CollectionState.total_received, 0) + max(0, int(received_count)),
            "runtime_details": details or {}, "updated_at": now,
        }
        if source_at is not None:
            update_set["last_source_at"] = source_at
        with get_session() as s:
            s.execute(pg_insert(CollectionState).values(**values).on_conflict_do_update(
                index_elements=["aggregator", "source_name"], set_=update_set,
            ))

    def mark_finished(
        self, *, task_id: str, aggregator: str, source_name: str,
        task_type: str, status: str, started_at=None, fetched_count: int = 0,
        saved_count: int = 0, source_at=None, error: str = "",
        details: dict | None = None,
    ) -> None:
        from datetime import datetime, timezone
        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - started_at).total_seconds() * 1000) if started_at else None
        now = func.now()
        success = status in {"success", "partial_success", "skipped"}
        values = {
            "aggregator": aggregator, "source_name": source_name,
            "task_id": task_id, "task_type": task_type, "status": status,
            "last_finished_at": finished, "last_duration_ms": duration_ms,
            "last_fetched_count": fetched_count, "last_saved_count": saved_count,
            "last_source_at": source_at, "last_error": (error or "")[:500],
            "consecutive_failures": 0 if success else 1,
            "last_success_at": finished if success else None,
            "last_persisted_at": finished if success and saved_count >= 0 else None,
            "total_runs": 1, "total_saved": max(0, saved_count),
            "runtime_details": details or {}, "updated_at": now,
        }
        update_set = {
            "task_id": task_id, "task_type": task_type, "status": status,
            "last_finished_at": finished, "last_duration_ms": duration_ms,
            "last_fetched_count": fetched_count, "last_saved_count": saved_count,
            "last_error": (error or "")[:500],
            "consecutive_failures": 0 if success else func.coalesce(CollectionState.consecutive_failures, 0) + 1,
            "total_runs": func.coalesce(CollectionState.total_runs, 0) + 1,
            "total_saved": func.coalesce(CollectionState.total_saved, 0) + max(0, saved_count),
            "runtime_details": details or {}, "updated_at": now,
        }
        if source_at is not None:
            update_set["last_source_at"] = source_at
        if success:
            update_set["last_success_at"] = finished
            update_set["last_persisted_at"] = finished
        with get_session() as s:
            s.execute(pg_insert(CollectionState).values(**values).on_conflict_do_update(
                index_elements=["aggregator", "source_name"], set_=update_set,
            ))

    def get(self, aggregator: str, source_name: str) -> dict | None:
        try:
            with get_session() as s:
                row = s.scalars(
                    select(CollectionState).where(
                        CollectionState.aggregator == aggregator,
                        CollectionState.source_name == source_name,
                    )
                ).first()
                return _row_to_dict(row) if row else None
        except Exception as e:
            logger.debug(f"CollectionStateRepo.get({aggregator},{source_name}) 失败: {e}")
            return None

    def get_checkpoint(self, aggregator: str, source_name: str) -> dict:
        """兼容旧接口：返回 checkpoint 风格的 dict"""
        state = self.get(aggregator, source_name)
        if not state:
            return {}
        return {
            "mode": state.get("mode", "incremental"),
            "target_time": state.get("target_time"),
            "newest_time": state.get("newest_time"),
            "oldest_time": state.get("oldest_time"),
            "backfill_status": state.get("backfill_status"),
            "cursor": state.get("cursor"),
        }

    def is_enabled(self, aggregator: str, source_name: str) -> bool:
        state = self.get(aggregator, source_name)
        if not state:
            return True
        return bool(state.get("enabled", True))

    def update_success(
        self,
        aggregator: str,
        source_name: str,
        new_checkpoint: dict | None,
        saved_count: int = 0,
        task_id: str | None = None,
        task_type: str = "pull",
    ) -> None:
        """更新成功状态

        new_checkpoint 可以包含 mode/target_time/newest_time/oldest_time/backfill_status/cursor，
        会分别写入对应的独立列。
        """
        try:
            cp = new_checkpoint or {}

            with get_session() as s:
                # INSERT 值
                values = {
                    "aggregator": aggregator,
                    "source_name": source_name,
                    "last_run_at": func.now(),
                    "last_success_at": func.now(),
                    "last_error": "",
                    "consecutive_failures": 0,
                    "total_runs": 1,
                    "total_saved": saved_count,
                    "updated_at": func.now(),
                    "task_id": task_id or f"collect_{aggregator}_{source_name}",
                    "task_type": task_type,
                    "status": "success",
                    "last_received_at": func.now(),
                    "last_persisted_at": func.now(),
                    "last_finished_at": func.now(),
                    "last_saved_count": saved_count,
                }

                # UPDATE 值
                update_set = {
                    "last_run_at": func.now(),
                    "last_success_at": func.now(),
                    "last_error": "",
                    "consecutive_failures": 0,
                    "total_runs": CollectionState.total_runs + 1,
                    "total_saved": CollectionState.total_saved + saved_count,
                    "updated_at": func.now(),
                    "task_id": task_id or f"collect_{aggregator}_{source_name}",
                    "task_type": task_type,
                    "status": "success",
                    "last_received_at": func.now(),
                    "last_persisted_at": func.now(),
                    "last_finished_at": func.now(),
                    "last_saved_count": saved_count,
                }

                # 从 checkpoint dict 提取字段写入独立列
                if new_checkpoint is not None:
                    for field in ("mode", "target_time", "newest_time", "oldest_time", "backfill_status", "cursor"):
                        if field in cp:
                            values[field] = cp[field]
                            update_set[field] = cp[field]

                stmt = pg_insert(CollectionState).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["aggregator", "source_name"],
                    set_=update_set,
                )
                s.execute(stmt)
        except Exception as e:
            logger.warning(f"update_success({aggregator},{source_name}) 失败: {e}")

    def update_failure(
        self, aggregator: str, source_name: str, error_msg: str,
        task_id: str | None = None, task_type: str = "pull",
    ) -> None:
        try:
            with get_session() as s:
                stmt = pg_insert(CollectionState).values(
                    aggregator=aggregator,
                    source_name=source_name,
                    last_run_at=func.now(),
                    last_error=(error_msg or "")[:500],
                    consecutive_failures=1,
                    total_runs=1,
                    updated_at=func.now(),
                    task_id=task_id or f"collect_{aggregator}_{source_name}",
                    task_type=task_type,
                    status="failed",
                    last_finished_at=func.now(),
                ).on_conflict_do_update(
                    index_elements=["aggregator", "source_name"],
                    set_={
                        "last_run_at": func.now(),
                        "last_error": (error_msg or "")[:500],
                        "consecutive_failures": CollectionState.consecutive_failures + 1,
                        "total_runs": CollectionState.total_runs + 1,
                        "updated_at": func.now(),
                        "task_id": task_id or f"collect_{aggregator}_{source_name}",
                        "task_type": task_type,
                        "status": "failed",
                        "last_finished_at": func.now(),
                    },
                )
                s.execute(stmt)
        except Exception as e:
            logger.warning(f"update_failure({aggregator},{source_name}) 失败: {e}")

    def reset(self, aggregator: str, source_name: str) -> bool:
        try:
            with get_session() as s:
                result = s.execute(
                    update(CollectionState)
                    .where(
                        CollectionState.aggregator == aggregator,
                        CollectionState.source_name == source_name,
                    )
                    .values(
                        mode="backfill",
                        target_time=None,
                        newest_time=None,
                        oldest_time=None,
                        backfill_status=None,
                        cursor=None,
                        consecutive_failures=0,
                        last_error="",
                        updated_at=func.now(),
                    )
                )
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.warning(f"reset 失败: {e}")
            return False

    def arm_backfill(
        self,
        aggregator: str,
        source_name: str,
        target_time: str,
        cursor=None,
    ) -> bool:
        """定向开启历史回填，保留 newest_time/oldest_time 和累计统计。"""
        try:
            with get_session() as s:
                result = s.execute(
                    update(CollectionState)
                    .where(
                        CollectionState.aggregator == aggregator,
                        CollectionState.source_name == source_name,
                    )
                    .values(
                        mode="backfill",
                        target_time=target_time,
                        backfill_status=None,
                        cursor=cursor,
                        last_run_at=None,
                        consecutive_failures=0,
                        last_error="",
                        updated_at=func.now(),
                    )
                )
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.warning(f"arm_backfill({aggregator},{source_name}) 失败: {e}")
            return False

    def disable(self, aggregator: str, source_name: str) -> bool:
        return self._set_enabled(aggregator, source_name, False)

    def enable(self, aggregator: str, source_name: str) -> bool:
        return self._set_enabled(aggregator, source_name, True)

    def _set_enabled(self, aggregator: str, source_name: str, value: bool) -> bool:
        try:
            with get_session() as s:
                stmt = pg_insert(CollectionState).values(
                    aggregator=aggregator, source_name=source_name, enabled=value,
                ).on_conflict_do_update(
                    index_elements=["aggregator", "source_name"],
                    set_={"enabled": value, "updated_at": func.now()},
                )
                s.execute(stmt)
                return True
        except Exception as e:
            logger.warning(f"set_enabled 失败: {e}")
            return False

    def ensure_initialized(
        self,
        aggregator: str,
        source_name: str,
        checkpoint: dict,
        config: dict,
    ) -> bool:
        """确保该源有 DB 记录，不存在则创建，已存在则跳过

        checkpoint dict 中的 mode/target_time/... 写入独立列。
        """
        try:
            cp = checkpoint or {}
            with get_session() as s:
                values = {
                    "aggregator": aggregator,
                    "source_name": source_name,
                    "config": config,
                    "mode": cp.get("mode", "incremental"),
                    "target_time": cp.get("target_time"),
                    "newest_time": cp.get("newest_time"),
                    "oldest_time": cp.get("oldest_time"),
                    "backfill_status": cp.get("backfill_status"),
                    "cursor": cp.get("cursor"),
                }
                stmt = pg_insert(CollectionState).values(**values).on_conflict_do_nothing(
                    index_elements=["aggregator", "source_name"],
                )
                result = s.execute(stmt)
                created = (result.rowcount or 0) > 0
                if created:
                    logger.info(f"[{aggregator}:{source_name}] 自动初始化 mode={cp.get('mode')}")
                return created
        except Exception as e:
            logger.warning(f"ensure_initialized({aggregator},{source_name}) 失败: {e}")
            return False

    def list_all(self, aggregator: str | None = None) -> list[dict]:
        try:
            with get_session() as s:
                stmt = select(CollectionState)
                if aggregator:
                    stmt = stmt.where(CollectionState.aggregator == aggregator)
                stmt = stmt.order_by(CollectionState.aggregator, CollectionState.source_name)
                return [_row_to_dict(r) for r in s.scalars(stmt).all()]
        except Exception as e:
            logger.warning(f"list_all 失败: {e}")
            return []
