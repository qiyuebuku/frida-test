"""EventStreamRepository SQLAlchemy 实现"""
import logging

from sqlalchemy import delete, select

from src.domain.extraction.repositories.event_stream_repository import (
    EventStreamRepository,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.extraction import EventStream

logger = logging.getLogger(__name__)


class EventStreamRepositoryImpl(EventStreamRepository):
    def delete_active_streams(self) -> int:
        try:
            with get_session() as s:
                result = s.execute(
                    delete(EventStream).where(EventStream.state != "closed")
                )
                return result.rowcount or 0
        except Exception as e:
            logger.warning(f"delete_active_streams 失败: {e}")
            return 0

    def insert_stream(self, stream_data: dict) -> bool:
        try:
            with get_session() as s:
                row = EventStream(
                    industry=stream_data["industry"][:64],
                    state=stream_data["state"],
                    momentum=stream_data.get("momentum"),
                    event_ids=stream_data.get("event_ids") or [],
                    event_count=stream_data.get("event_count") or 0,
                    avg_strength=stream_data.get("avg_strength") or 0,
                    max_strength=stream_data.get("max_strength") or 0,
                    avg_sentiment=stream_data.get("avg_sentiment") or 0.5,
                    first_event_at=stream_data.get("first_event_at"),
                    last_event_at=stream_data.get("last_event_at"),
                )
                s.add(row)
                return True
        except Exception as e:
            logger.warning(f"insert_stream 失败: {e}")
            return False

    def find_active(self) -> list[dict]:
        with get_session() as s:
            rows = s.scalars(
                select(EventStream)
                .where(EventStream.state.in_(["emerging", "fermenting", "peak"]))
                .order_by(EventStream.avg_strength.desc(), EventStream.event_count.desc())
            ).all()
            return [
                {
                    "id": r.id,
                    "industry": r.industry,
                    "state": r.state,
                    "momentum": r.momentum,
                    "event_ids": r.event_ids or [],
                    "event_count": r.event_count,
                    "avg_strength": r.avg_strength,
                    "max_strength": r.max_strength,
                    "avg_sentiment": r.avg_sentiment,
                    "first_event_at": r.first_event_at,
                    "last_event_at": r.last_event_at,
                }
                for r in rows
            ]

    def find_latest_by_industries(self, industries: list[str]) -> dict | None:
        if not industries:
            return None
        with get_session() as s:
            r = s.scalars(
                select(EventStream)
                .where(EventStream.industry.in_(industries))
                .order_by(EventStream.last_event_at.desc())
                .limit(1)
            ).first()
            if not r:
                return None
            return {
                "id": r.id,
                "industry": r.industry,
                "state": r.state,
                "momentum": r.momentum,
                "event_count": r.event_count,
                "avg_strength": r.avg_strength,
                "last_event_at": r.last_event_at,
            }
