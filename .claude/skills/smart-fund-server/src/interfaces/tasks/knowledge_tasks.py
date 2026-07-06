"""高维知识索引任务入口。"""

from __future__ import annotations

import logging
import time

from jettask import TaskRouter

from src.application.services import CommunityInsightService, KnowledgeNewsIngestionService
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)

logger = logging.getLogger(__name__)

router = TaskRouter()

_knowledge_ingestion = KnowledgeNewsIngestionService()
_community_insight = CommunityInsightService()


@router.task(
    queue="kg_news_ingest",
    max_retries=10,
    retry_backoff=10.0,
    retry_backoff_max=60,
)
async def kg_news_ingest(news_ids: list[int] | None = None):
    """处理指定 ft_news ID，构建知识图谱认知索引。"""

    t0 = time.time()
    result = await _knowledge_ingestion.ingest_ft_news_ids(news_ids or [])
    logger.info("[kg_news_ingest] 完成,耗时 %.1fs %s", time.time() - t0, result)


@router.task(
    queue="kg_community_insight_refresh",
    max_retries=3,
    retry_backoff=30.0,
    retry_backoff_max=300,
)
async def kg_community_insight_refresh(limit: int | None = None):
    """扫描并刷新需要生成的 Community Insight 高级认知报告。"""

    t0 = time.time()
    effective_limit = limit or 5
    metadata = {
        "task": "kg_community_insight_refresh",
        "queue": "kg_community_insight_refresh",
        "limit": effective_limit,
    }
    with langfuse_propagation_context(
        trace_name="task.kg_community_insight_refresh",
        tags=["task", "kg", "community_insight"],
        metadata=metadata,
    ):
        with langfuse_observation(
            name="task:kg_community_insight_refresh",
            as_type="span",
            input=metadata,
            metadata=metadata,
        ):
            try:
                result = await _community_insight.refresh_due_insights(limit=effective_limit)
                langfuse_update_span(
                    output={"duration_seconds": round(time.time() - t0, 3), **result},
                    status_message="completed",
                )
                logger.info("[kg_community_insight_refresh] 完成,耗时 %.1fs %s", time.time() - t0, result)
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise
