"""LLM 调用日志仓储。"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.llm import LLMCallLog


class LLMCallLogRepository:
    """批量持久化公共 LLM Gateway 调用日志。"""

    def save_batch(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        with get_session() as session:
            stmt = pg_insert(LLMCallLog).values(rows).on_conflict_do_nothing(
                index_elements=["id"]
            )
            result = session.execute(stmt)
            return result.rowcount or 0
