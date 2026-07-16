"""LLM 公共网关调用日志 ORM。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


class LLMCallLog(Base):
    """一次经过公共 LLM Gateway 的逻辑模型调用。"""

    __tablename__ = "llm_call_logs"
    __table_args__ = (
        Index("ix_llm_call_logs_created_at", "created_at"),
        Index("ix_llm_call_logs_task_created", "task", "created_at"),
        Index("ix_llm_call_logs_model_created", "resolved_model", "created_at"),
        Index("ix_llm_call_logs_status_created", "status", "created_at"),
        Index("ix_llm_call_logs_source", "source_type", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task: Mapped[str | None] = mapped_column(String(128))
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(256))
    provider: Mapped[str | None] = mapped_column(String(64))
    requested_model: Mapped[str | None] = mapped_column(String(128))
    resolved_model: Mapped[str | None] = mapped_column(String(128))
    upstream_model: Mapped[str | None] = mapped_column(String(128))
    route_reason: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cache_store: Mapped[str | None] = mapped_column(String(32))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    response_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reasoning_content: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    prompt_cache_hit_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    prompt_cache_miss_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    input_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    output_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    cache_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    currency: Mapped[str | None] = mapped_column(String(16))
    cost_source: Mapped[str | None] = mapped_column(String(32))
    cost_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    session_id: Mapped[str | None] = mapped_column(String(128))
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
