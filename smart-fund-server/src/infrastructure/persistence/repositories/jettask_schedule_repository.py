from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.infrastructure.config.settings import DB_CONFIG, JETTASK_DB_NAME
from src.infrastructure.persistence.models.jettask_schedule import ScheduledTaskRecord


def _url() -> str:
    cfg = DB_CONFIG
    return (
        f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}@"
        f"{cfg['host']}:{cfg['port']}/{JETTASK_DB_NAME}"
    )


class JetTaskScheduleRepository:
    def __init__(self, engine=None):
        self._engine = engine or create_engine(
            _url(), pool_size=2, max_overflow=3, pool_pre_ping=True,
        )

    def list_all(self) -> list[dict]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(ScheduledTaskRecord).order_by(ScheduledTaskRecord.scheduler_id)
            ).all()
            return [self._to_dict(row) for row in rows]

    def get(self, scheduler_id: str) -> dict | None:
        with Session(self._engine) as session:
            row = session.scalar(
                select(ScheduledTaskRecord).where(
                    ScheduledTaskRecord.scheduler_id == scheduler_id
                )
            )
            return self._to_dict(row) if row else None

    @staticmethod
    def _to_dict(row: ScheduledTaskRecord) -> dict:
        return {
            "scheduler_id": row.scheduler_id,
            "task_type": row.task_type,
            "queue_name": row.queue_name or "",
            "task_args": row.task_args or [],
            "task_kwargs": row.task_kwargs or {},
            "cron_expression": row.cron_expression,
            "interval_seconds": float(row.interval_seconds) if row.interval_seconds is not None else None,
            "next_run_time": row.next_run_time,
            "last_run_time": row.last_run_time,
            "enabled": bool(row.enabled),
            "description": row.description or "",
            "metadata": row.metadata_json or {},
            "tags": row.tags or [],
            "schedule_timezone": row.schedule_timezone or "UTC",
            "active_windows": row.active_windows or [],
            "calendar_config": row.calendar_config or {},
        }
