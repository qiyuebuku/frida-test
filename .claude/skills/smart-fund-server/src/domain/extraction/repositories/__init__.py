"""事件抽取仓储抽象"""
from src.domain.extraction.repositories.event_repository import EventRepository
from src.domain.extraction.repositories.event_stream_repository import (
    EventStreamRepository,
)

__all__ = ["EventRepository", "EventStreamRepository"]
