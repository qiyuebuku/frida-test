"""新增 ft_news 入知识图谱的应用层编排服务。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select

from src.application.dto.knowledge_dto import KnowledgeCompileCommand, Target
from src.application.services.knowledge_service import KnowledgeService, create_knowledge_service
from src.domain.knowledge_adapters.financial.source_projection import project_ft_news_row
from src.infrastructure.connections import get_session
from src.infrastructure.db import redis_lock
from src.infrastructure.persistence.models.collection import News
from src.infrastructure.tasks.jettask_dispatcher import (
    build_kg_news_workflow_id,
    send_kg_relation_discovery,
)

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
        target: Target = "prod",
    ):
        self._target = target
        self._knowledge_service = knowledge_service or create_knowledge_service(target=target)

    async def ingest_ft_news_ids(
        self,
        news_ids: list[int],
        *,
        workflow_id: str = "",
    ) -> dict[str, Any]:
        """编译新闻并把新增 Card 投递到独立关系发现任务。"""

        return await self._run_ft_news_ids(
            news_ids,
            dispatch_relation_tasks=True,
            workflow_id=workflow_id,
        )

    async def compile_ft_news_ids(
        self,
        news_ids: list[int],
        *,
        workflow_id: str = "",
    ) -> dict[str, Any]:
        """同步工作流入口：完成 Card 发布，但不额外投递关系发现消息。"""

        return await self._run_ft_news_ids(
            news_ids,
            dispatch_relation_tasks=False,
            workflow_id=workflow_id,
        )

    async def _run_ft_news_ids(
        self,
        news_ids: list[int],
        *,
        dispatch_relation_tasks: bool,
        workflow_id: str = "",
    ) -> dict[str, Any]:
        unique_ids = _ordered_unique_ints(news_ids)
        if not unique_ids:
            return {
                "skipped": True,
                "reason": "no_news_ids",
                "consumed_ids": 0,
                "compiled_evidence": 0,
                "failed_records": 0,
            }
        identity = str(workflow_id or "").strip() or build_kg_news_workflow_id(
            unique_ids
        )

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
                result = await self._compile_news_ids(
                    unique_ids,
                    lock_lost,
                    dispatch_relation_tasks=dispatch_relation_tasks,
                    workflow_id=identity,
                )
                result["duration_seconds"] = round(time.time() - t0, 3)
                return result
            finally:
                stop_renew.set()
                await renew_task

    async def _compile_news_ids(
        self,
        news_ids: list[int],
        lock_lost: asyncio.Event,
        *,
        dispatch_relation_tasks: bool = True,
        workflow_id: str = "",
    ) -> dict[str, Any]:
        batches = 0
        consumed_ids = 0
        compiled_evidence = 0
        failed_records = 0
        missing_ids: list[int] = []
        relation_card_ids: list[str] = []
        relation_event_ids: list[str] = []
        intra_chunk_changed_edge_ids: list[str] = []
        intra_chunk_graph_event_ids: list[str] = []
        intra_chunk_relations = 0
        intra_chunk_observed = 0
        intra_chunk_inferred = 0

        for start in range(0, len(news_ids), KG_NEWS_INGEST_BATCH_SIZE):
            if lock_lost.is_set():
                raise RuntimeError("kg_news_ingest 在编译 ft_news 前丢失分布式锁")

            batch_ids = news_ids[start:start + KG_NEWS_INGEST_BATCH_SIZE]
            records, batch_missing = _records_from_ft_news_ids(batch_ids, target=self._target)
            missing_ids.extend(batch_missing)
            if not records:
                logger.warning("[kg_news_ingest] 未找到可用 ft_news 行: ids=%s", batch_ids)
                continue

            request_id = (
                f"{workflow_id}:batch:{start // KG_NEWS_INGEST_BATCH_SIZE}"
                if workflow_id
                else f"kg_news_ingest:ft_news:{batch_ids[0]}-{batch_ids[-1]}"
            )
            result = await self._knowledge_service.compile_kg(
                KnowledgeCompileCommand(
                    adapter_name="financial",
                    records=records,
                    target=self._target,
                    dry_run=False,
                    request_id=request_id,
                    concurrency=KG_NEWS_INGEST_COMPILE_CONCURRENCY,
                )
            )
            if result.failed_records:
                failed_sources = [
                    str(item.get("source_id") or item.get("reason") or "").strip()
                    for item in (result.failures or [])
                    if isinstance(item, dict)
                ]
                raise RuntimeError(
                    "[kg_news_ingest] 编译批次存在失败记录，必须由 Jettask 幂等重试 "
                    f"ids={batch_ids} failed_records={result.failed_records} "
                    f"failures={failed_sources[:10]}"
                )
            batches += 1
            consumed_ids += len(batch_ids)
            compiled_evidence += result.evidence
            cognitive_index = (result.index_refresh or {}).get("cognitive_index") or {}
            card_ids = [
                str(item)
                for item in (
                    cognitive_index.get("card_ids") or []
                )
                if item
            ]
            graph_persistence = cognitive_index.get("graph_persistence") or {}
            intra_chunk_relations += int(graph_persistence.get("relations") or 0)
            intra_chunk_observed += int(graph_persistence.get("observed") or 0)
            intra_chunk_inferred += int(graph_persistence.get("inferred") or 0)
            intra_chunk_changed_edge_ids.extend(
                str(item)
                for item in graph_persistence.get("changed_edge_ids") or []
                if str(item).strip()
            )
            intra_chunk_graph_event_ids.extend(
                str(item)
                for item in graph_persistence.get("graph_event_ids") or []
                if str(item).strip()
            )
            if card_ids:
                relation_card_ids.extend(card_ids)
                if dispatch_relation_tasks:
                    event_ids = await send_kg_relation_discovery(
                        card_ids,
                        workflow_id=workflow_id,
                    )
                    relation_event_ids.extend(event_ids)
                    logger.info(
                        "[kg_news_ingest] 已投递原子 Card 到 kg_relation_discovery: cards=%s event_ids=%s",
                        card_ids,
                        event_ids,
                    )
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
            "relation_card_ids": list(dict.fromkeys(relation_card_ids)),
            "relation_event_ids": relation_event_ids,
            "relation_dispatch_skipped": not dispatch_relation_tasks,
            "workflow_id": workflow_id,
            "intra_chunk_relations": intra_chunk_relations,
            "intra_chunk_observed": intra_chunk_observed,
            "intra_chunk_inferred": intra_chunk_inferred,
            "intra_chunk_changed_edge_ids": list(
                dict.fromkeys(intra_chunk_changed_edge_ids)
            ),
            "intra_chunk_graph_event_ids": list(
                dict.fromkeys(intra_chunk_graph_event_ids)
            ),
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


def _records_from_ft_news_ids(
    news_ids: list[int],
    *,
    target: Target = "prod",
) -> tuple[list[dict[str, Any]], list[int]]:
    unique_ids = _ordered_unique_ints(news_ids)
    if not unique_ids:
        return [], []
    with get_session(target) as session:
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
