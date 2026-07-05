"""高维知识索引任务入口。"""

from __future__ import annotations

import logging
import time

from jettask import TaskRouter

from src.application.services import KnowledgeNewsIngestionService

logger = logging.getLogger(__name__)

router = TaskRouter()

_knowledge_ingestion = KnowledgeNewsIngestionService()


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
