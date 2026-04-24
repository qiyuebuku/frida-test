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


@dataclass
class L1bResult:
    """L1b 数值事件检测 use case 输出"""
    checked: int
    fired: int
    saved: int

    def to_dict(self) -> dict:
        return {"checked": self.checked, "fired": self.fired, "saved": self.saved}


@dataclass
class L1aClassifyResult:
    """L1a 分类 use case 输出"""
    classified: int
    skipped: int

    def to_dict(self) -> dict:
        return {"classified": self.classified, "skipped": self.skipped}


@dataclass
class ThresholdResult:
    """阈值刷新 use case 输出"""
    refreshed: int

    def to_dict(self) -> dict:
        return {"refreshed": self.refreshed}


@dataclass
class QualityResult:
    """质量监控 use case 输出"""
    metrics: dict = field(default_factory=dict)
    alerts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"metrics": self.metrics, "alerts": self.alerts}
