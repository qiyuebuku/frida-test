from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class JetTaskBase(DeclarativeBase):
    pass


class ScheduledTaskRecord(JetTaskBase):
    """Read-only ORM projection of JetTask's scheduled_tasks table."""

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scheduler_id: Mapped[str] = mapped_column(String(255))
    task_type: Mapped[str | None] = mapped_column(String(50))
    queue_name: Mapped[str | None] = mapped_column(String(100))
    task_args: Mapped[list | dict | None] = mapped_column(JSONB)
    task_kwargs: Mapped[dict | None] = mapped_column(JSONB)
    cron_expression: Mapped[str | None] = mapped_column(String(100))
    interval_seconds: Mapped[float | None] = mapped_column(Numeric(10, 2))
    next_run_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool | None] = mapped_column(Boolean)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSONB)
    tags: Mapped[list | None] = mapped_column(JSONB)
    schedule_timezone: Mapped[str | None] = mapped_column(String(64))
    active_windows: Mapped[list | None] = mapped_column(JSONB)
    calendar_config: Mapped[dict | None] = mapped_column(JSONB)
