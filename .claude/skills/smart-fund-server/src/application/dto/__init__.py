"""应用层 DTO — 跨层数据传输对象

只用 dataclass,不带任何业务逻辑。
"""
from src.application.dto.collection_dto import (
    CollectionResult,
)
from src.application.dto.extraction_dto import (
    EventExtractionResult,
    EventStreamResult,
    FeedbackResult,
)
from src.application.dto.trading_dto import (
    DecisionResult,
    ExecutionResult,
    MonitorResult,
)
from src.application.dto.reflection_dto import ReviewResult

__all__ = [
    "CollectionResult",
    "EventExtractionResult",
    "EventStreamResult",
    "FeedbackResult",
    "DecisionResult",
    "ExecutionResult",
    "MonitorResult",
    "ReviewResult",
]
