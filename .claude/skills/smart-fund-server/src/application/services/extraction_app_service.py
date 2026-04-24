"""事件抽取应用服务

3 个原有 use case + L1b/L1a 新增 use case
"""
import asyncio
import logging

from src.application.dto.extraction_dto import (
    EventExtractionResult,
    EventStreamResult,
    FeedbackResult,
    L1aClassifyResult,
    L1bResult,
    ThresholdResult,
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
        return FeedbackResult(filled=0)

    # ==================== L1b 数值事件检测 ====================

    async def run_l1b_fund_flow(self) -> L1bResult:
        from src.domain.extraction.services.l1b import L1bDetector
        r = await asyncio.to_thread(L1bDetector().detect_fund_flow)
        return L1bResult(**r)

    async def run_l1b_macro(self) -> L1bResult:
        from src.domain.extraction.services.l1b import L1bDetector
        r = await asyncio.to_thread(L1bDetector().detect_macro)
        return L1bResult(**r)

    async def run_l1b_sentiment(self) -> L1bResult:
        from src.domain.extraction.services.l1b import L1bDetector
        r = await asyncio.to_thread(L1bDetector().detect_sentiment)
        return L1bResult(**r)

    async def run_l1b_market(self) -> L1bResult:
        from src.domain.extraction.services.l1b import L1bDetector
        r = await asyncio.to_thread(L1bDetector().detect_market)
        return L1bResult(**r)

    async def refresh_l1_thresholds(self) -> ThresholdResult:
        from src.domain.extraction.services.l1b import ThresholdCalculator
        refreshed = await asyncio.to_thread(ThresholdCalculator().refresh_all)
        return ThresholdResult(refreshed=refreshed)

    # ==================== L1a AI 文本抽取 ====================

    async def run_l1a_classify(self) -> L1aClassifyResult:
        from src.domain.extraction.services.l1a.l1a_orchestrator import classify_tick
        r = await asyncio.to_thread(classify_tick)
        return L1aClassifyResult(**r)

    async def run_l1a_extract(self) -> EventExtractionResult:
        from src.domain.extraction.services.l1a.l1a_orchestrator import extract_tick
        r = await asyncio.to_thread(extract_tick)
        return EventExtractionResult(processed=r.get("processed", 0), saved=r.get("saved", 0))
