"""事件抽取应用服务

3 个 use case:
- extract_events_from_news: ft_news → claude → ft_events (含 embedding)
- aggregate_event_streams: ft_events → 贪心聚类 → ft_event_streams
- backfill_market_reaction: T+1/T+2 市场反应回填到 ft_events
"""
import logging

from src.application.dto.extraction_dto import (
    EventExtractionResult,
    EventStreamResult,
    FeedbackResult,
)

logger = logging.getLogger(__name__)


class ExtractionAppService:
    """事件抽取 use case 入口"""

    async def extract_events_from_news(self) -> EventExtractionResult:
        """从未抽取的新闻批量抽取事件 (agg_event_extraction task)"""
        from src.domain.extraction.services.event_extraction import (
            EventExtractionAggregator,
        )
        result = await EventExtractionAggregator().tick() or {}
        return EventExtractionResult(
            processed=result.get("processed", 0),
            saved=result.get("saved", 0),
        )

    async def aggregate_event_streams(self) -> EventStreamResult:
        """事件流聚合 (agg_event_stream task)"""
        from src.domain.extraction.services.event_stream import EventStreamAggregator
        result = await EventStreamAggregator().tick() or {}
        return EventStreamResult(
            events=result.get("events", 0),
            streams=result.get("streams", 0),
            active=result.get("active", 0),
        )

    async def backfill_market_reaction(self) -> FeedbackResult:
        """回填事件市场反应 (agg_event_feedback task)"""
        from src.domain.extraction.services.event_feedback import (
            EventFeedbackAggregator,
        )
        await EventFeedbackAggregator().tick()
        # tick 不返回值,取 stats 用 EventRepository
        from src.infrastructure.persistence.repositories import EventRepositoryImpl
        # 简单返回(数量统计可由 monitor 看 ft_events.feedback_at)
        return FeedbackResult(filled=0)
