"""事件抽取 use case 的 DTO"""
from dataclasses import dataclass, field


@dataclass
class EventExtractionResult:
    """agg_event_extraction use case 输出"""
    processed: int
    saved: int

    def to_dict(self) -> dict:
        return {"processed": self.processed, "saved": self.saved}


@dataclass
class EventStreamResult:
    """agg_event_stream use case 输出"""
    events: int
    streams: int
    active: int

    def to_dict(self) -> dict:
        return {"events": self.events, "streams": self.streams, "active": self.active}


@dataclass
class FeedbackResult:
    """agg_event_feedback use case 输出"""
    filled: int = 0

    def to_dict(self) -> dict:
        return {"filled": self.filled}
