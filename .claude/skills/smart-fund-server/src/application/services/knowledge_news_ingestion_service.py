"""新增 ft_news 入知识图谱的应用层编排服务。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select

from src.application.dto.knowledge_dto import KnowledgeCompileCommand
from src.application.services.knowledge_service import KnowledgeService, create_knowledge_service
from src.domain.knowledge_adapters.financial.source_projection import project_ft_news_row
from src.infrastructure.connections import get_session
from src.infrastructure.db import redis_lock
from src.infrastructure.persistence.models.collection import News

logger = logging.getLogger(__name__)

KG_NEWS_INGEST_LOCK_NAME = "kg:news_ingest"
KG_NEWS_INGEST_LOCK_TTL_SECONDS = 180
KG_NEWS_INGEST_LOCK_RENEW_SECONDS = 30
KG_NEWS_INGEST_BATCH_SIZE = 30
KG_NEWS_INGEST_COMPILE_CONCURRENCY = 1


class KnowledgeNewsIngestionService:
    """新增新闻高维索引构建 use case。"""

    def __init__(
        self,
        *,
        knowledge_service: KnowledgeService | None = None,
    ):
        self._knowledge_service = knowledge_service or create_knowledge_service(target="prod")

    async def ingest_ft_news_ids(self, news_ids: list[int]) -> dict[str, Any]:
        unique_ids = _ordered_unique_ints(news_ids)
        if not unique_ids:
            return {
                "skipped": True,
                "reason": "no_news_ids",
                "consumed_ids": 0,
                "compiled_evidence": 0,
                "failed_records": 0,
            }

        t0 = time.time()
        with redis_lock.acquire(KG_NEWS_INGEST_LOCK_NAME, ttl=KG_NEWS_INGEST_LOCK_TTL_SECONDS) as lock:
            if not lock:
                raise RuntimeError(
                    "[kg_news_ingest] 分布式锁已被占用，交给 jettask-rs 重试 "
                    f"ttl={KG_NEWS_INGEST_LOCK_TTL_SECONDS}s news_ids={unique_ids[:20]}"
                )

            stop_renew = asyncio.Event()
            lock_lost = asyncio.Event()
            renew_task = asyncio.create_task(_renew_lock_loop(lock, stop_renew, lock_lost))
            try:
                result = await self._compile_news_ids(unique_ids, lock_lost)
                result["duration_seconds"] = round(time.time() - t0, 3)
                return result
            finally:
                stop_renew.set()
                await renew_task

    async def _compile_news_ids(self, news_ids: list[int], lock_lost: asyncio.Event) -> dict[str, Any]:
        batches = 0
        consumed_ids = 0
        compiled_evidence = 0
        failed_records = 0
        missing_ids: list[int] = []

        for start in range(0, len(news_ids), KG_NEWS_INGEST_BATCH_SIZE):
            if lock_lost.is_set():
                raise RuntimeError("kg_news_ingest 在编译 ft_news 前丢失分布式锁")

            batch_ids = news_ids[start:start + KG_NEWS_INGEST_BATCH_SIZE]
            records, batch_missing = _records_from_ft_news_ids(batch_ids)
            missing_ids.extend(batch_missing)
            if not records:
                logger.warning("[kg_news_ingest] 未找到可用 ft_news 行: ids=%s", batch_ids)
                continue

            request_id = f"kg_news_ingest:ft_news:{batch_ids[0]}-{batch_ids[-1]}:{int(time.time())}"
            result = await self._knowledge_service.compile_kg(
                KnowledgeCompileCommand(
                    adapter_name="financial",
                    records=records,
                    target="prod",
                    dry_run=False,
                    request_id=request_id,
                    concurrency=KG_NEWS_INGEST_COMPILE_CONCURRENCY,
                )
            )
            batches += 1
            consumed_ids += len(batch_ids)
            compiled_evidence += result.evidence
            failed_records += result.failed_records
            logger.info(
                "[kg_news_ingest] 批次完成 ids=%s evidence=%s failed_records=%s request_id=%s",
                batch_ids,
                result.evidence,
                result.failed_records,
                request_id,
            )

        return {
            "skipped": False,
            "batches": batches,
            "consumed_ids": consumed_ids,
            "compiled_evidence": compiled_evidence,
            "failed_records": failed_records,
            "missing_ids": missing_ids[:20],
        }


async def _renew_lock_loop(
    lock,
    stop_event: asyncio.Event,
    lost_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=KG_NEWS_INGEST_LOCK_RENEW_SECONDS)
        except asyncio.TimeoutError:
            renewed = lock.renew()
            if renewed:
                logger.info("[kg_news_ingest] 分布式锁续租成功 ttl=%ss", KG_NEWS_INGEST_LOCK_TTL_SECONDS)
                continue
            logger.error("[kg_news_ingest] 分布式锁续租失败，当前锁持有者可能已经变化")
            lost_event.set()
            return


def _records_from_ft_news_ids(news_ids: list[int]) -> tuple[list[dict[str, Any]], list[int]]:
    unique_ids = _ordered_unique_ints(news_ids)
    if not unique_ids:
        return [], []
    with get_session() as session:
        rows = session.scalars(select(News).where(News.id.in_(unique_ids))).all()
    row_by_id = {int(row.id): row for row in rows}
    records: list[dict[str, Any]] = []
    missing: list[int] = []
    for news_id in unique_ids:
        row = row_by_id.get(news_id)
        if row is None:
            missing.append(news_id)
            continue
        record = project_ft_news_row(_news_model_row(row))
        if record:
            records.append(record)
    return records, missing


def _news_model_row(row: News) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "summary": row.summary,
        "source": row.source,
        "source_name": row.source_name,
        "source_reliability": row.source_reliability,
        "category": row.category,
        "url": row.url,
        "tags": row.tags,
        "related_stocks": row.related_stocks,
        "published_at": row.published_at,
        "fingerprint": row.fingerprint,
        "created_at": row.created_at,
    }


def _ordered_unique_ints(values: list[Any]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
