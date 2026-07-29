"""高维知识索引任务入口。"""

from __future__ import annotations

import logging
import time

from jettask import TaskRouter

from src.application.services import (
    KnowledgeNewsIngestionService,
    RelationGraphCommunityService,
    RelationDiscoveryService,
)
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.tasks.jettask_dispatcher import (
    build_kg_news_workflow_id,
)

logger = logging.getLogger(__name__)

router = TaskRouter()

_knowledge_ingestion = KnowledgeNewsIngestionService()
_relation_discovery: RelationDiscoveryService | None = None
_relation_graph_community: RelationGraphCommunityService | None = None


@router.task(
    queue="kg_news_ingest",
    max_retries=10,
    retry_backoff=10.0,
    retry_backoff_max=60,
)
async def kg_news_ingest(
    news_ids: list[int] | None = None,
    workflow_id: str = "",
):
    """处理指定 ft_news ID，构建知识图谱认知索引。"""

    normalized_ids = _positive_news_ids(news_ids or [])
    identity = str(workflow_id or "").strip() or build_kg_news_workflow_id(
        normalized_ids
    )
    t0 = time.time()
    metadata = {
        "task": "kg_news_ingest",
        "queue": "kg_news_ingest",
        "workflow_id": identity,
        "news_ids": normalized_ids,
    }
    try:
        with langfuse_propagation_context(
            trace_name="kg.news_ingest",
            session_id=identity,
            tags=["task", "kg", "news_ingest"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="task:kg_news_ingest",
                as_type="span",
                input=metadata,
                metadata=metadata,
            ):
                try:
                    result = await _knowledge_ingestion.ingest_ft_news_ids(
                        normalized_ids,
                        workflow_id=identity,
                    )
                    langfuse_update_span(
                        output={
                            "duration_seconds": round(time.time() - t0, 3),
                            **result,
                        },
                        status_message="completed",
                    )
                    logger.info(
                        "[kg_news_ingest] 完成,耗时 %.1fs %s",
                        time.time() - t0,
                        result,
                    )
                    return result
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise
    finally:
        langfuse_flush()


def _positive_news_ids(values: list[int]) -> list[int]:
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


@router.task(
    queue="kg_relation_discovery",
    max_retries=5,
    retry_backoff=10.0,
    retry_backoff_max=120,
)
async def kg_relation_discovery(
    card_ids: list[str] | None = None,
    workflow_id: str = "",
):
    """发现并核验 Card 关系，写入正式 Edge 后发布图变化事件。"""

    global _relation_discovery
    if _relation_discovery is None:
        _relation_discovery = RelationDiscoveryService()
    normalized_ids = [
        item
        for item in dict.fromkeys(str(value).strip() for value in card_ids or [])
        if item
    ]
    identity = str(workflow_id or "").strip()
    t0 = time.time()
    metadata = {
        "task": "kg_relation_discovery",
        "queue": "kg_relation_discovery",
        "workflow_id": identity,
        "card_ids": normalized_ids,
    }
    try:
        with langfuse_propagation_context(
            trace_name="kg.relation_discovery",
            session_id=identity or None,
            tags=["task", "kg", "relation_discovery"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="task:kg_relation_discovery",
                as_type="span",
                input=metadata,
                metadata=metadata,
            ):
                try:
                    result = await _relation_discovery.discover_card_relations(
                        normalized_ids,
                        workflow_id=identity,
                    )
                    langfuse_update_span(
                        output={
                            "duration_seconds": round(time.time() - t0, 3),
                            **result,
                        },
                        status_message="completed",
                    )
                    logger.info(
                        "[kg_relation_discovery] 完成,耗时 %.1fs %s",
                        time.time() - t0,
                        result,
                    )
                    return result
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise
    finally:
        langfuse_flush()


@router.task(
    queue="kg_graph_changed",
    max_retries=10,
    retry_backoff=10.0,
    retry_backoff_max=120,
)
async def kg_graph_community_refresh(
    adapter_name: str,
    changed_edge_ids: list[str] | None = None,
    affected_card_ids: list[str] | None = None,
    changes: dict | None = None,
    event_identity: str = "",
    workflow_id: str = "",
):
    """消费正式 Edge 图变化并刷新受影响的平行 Community 分区。"""

    global _relation_graph_community
    if _relation_graph_community is None:
        _relation_graph_community = RelationGraphCommunityService()
    t0 = time.time()
    metadata = {
        "task": "kg_graph_community_refresh",
        "queue": "kg_graph_changed",
        "adapter_name": adapter_name,
        "changed_edge_ids": list(changed_edge_ids or []),
        "affected_card_ids": list(affected_card_ids or []),
        "event_identity": event_identity,
        "workflow_id": workflow_id,
    }
    try:
        with langfuse_propagation_context(
            trace_name="kg.graph_community.refresh",
            session_id=str(workflow_id or "").strip() or None,
            tags=["task", "kg", "graph_community"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="task:kg_graph_community_refresh",
                as_type="span",
                input=metadata,
                metadata=metadata,
            ):
                try:
                    result = await _relation_graph_community.refresh_from_graph_change(
                        adapter_name=adapter_name,
                        changed_edge_ids=list(changed_edge_ids or []),
                        affected_card_ids=list(affected_card_ids or []),
                        changes=dict(changes or {}),
                        event_identity=event_identity,
                    )
                    langfuse_update_span(
                        output={"duration_seconds": round(time.time() - t0, 3), **result},
                        status_message="completed",
                    )
                    logger.info(
                        "[kg_graph_community_refresh] 完成,耗时 %.1fs %s",
                        time.time() - t0,
                        result,
                    )
                    return result
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise
    finally:
        langfuse_flush()
