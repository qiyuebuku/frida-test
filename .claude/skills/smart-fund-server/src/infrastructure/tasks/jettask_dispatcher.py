"""jettask-rs 消息投递工具。"""

from __future__ import annotations

from jettask import Jettask, TaskMessage

from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL


KG_NEWS_INGEST_QUEUE = "kg_news_ingest"
KG_NEWS_INGEST_TIMEOUT_SECONDS = 5000
KG_NEWS_INGEST_MAX_RETRIES = 10
KG_RELATION_DISCOVERY_QUEUE = "kg_relation_discovery"
KG_RELATION_DISCOVERY_TIMEOUT_SECONDS = 5000
KG_RELATION_DISCOVERY_MAX_RETRIES = 5
KG_GRAPH_CHANGED_QUEUE = "kg_graph_changed"
KG_GRAPH_CHANGED_TIMEOUT_SECONDS = 5000
KG_GRAPH_CHANGED_MAX_RETRIES = 10


async def send_kg_news_ingest(news_ids: list[int]) -> list[str]:
    """把新增 ft_news.id 直接投递给 kg_news_ingest 队列。

    这里不使用 Redis Stream 作为二级队列；消息可靠性交给 jettask-rs。
    """

    normalized_ids = _ordered_unique_positive_ints(news_ids)
    if not normalized_ids:
        return []

    app = Jettask(redis_url=REDIS_URL, prefix=JETTASK_PREFIX)
    try:
        return await app.send(
            [
                TaskMessage(
                    queue=KG_NEWS_INGEST_QUEUE,
                    kwargs={"news_ids": normalized_ids},
                    max_retries=KG_NEWS_INGEST_MAX_RETRIES,
                    timeout=KG_NEWS_INGEST_TIMEOUT_SECONDS,
                )
            ]
        )
    finally:
        app.close()


async def send_kg_relation_discovery(card_ids: list[str]) -> list[str]:
    """在 Card 双视图发布成功后投递独立关系发现任务。"""

    normalized_ids = [item for item in dict.fromkeys(card_ids) if str(item).strip()]
    if not normalized_ids:
        return []
    app = Jettask(redis_url=REDIS_URL, prefix=JETTASK_PREFIX)
    try:
        return await app.send(
            [
                TaskMessage(
                    queue=KG_RELATION_DISCOVERY_QUEUE,
                    kwargs={"card_ids": normalized_ids},
                    max_retries=KG_RELATION_DISCOVERY_MAX_RETRIES,
                    timeout=KG_RELATION_DISCOVERY_TIMEOUT_SECONDS,
                )
            ]
        )
    finally:
        app.close()


async def send_kg_graph_changed(
    *,
    adapter_name: str,
    changed_edge_ids: list[str],
    affected_card_ids: list[str],
    changes: dict,
    event_identity: str,
) -> list[str]:
    """投递正式 Edge 图变化事件，供第三阶段增量构建 Community。"""

    edge_ids = [item for item in dict.fromkeys(changed_edge_ids) if str(item).strip()]
    card_ids = [item for item in dict.fromkeys(affected_card_ids) if str(item).strip()]
    if not edge_ids:
        return []
    app = Jettask(redis_url=REDIS_URL, prefix=JETTASK_PREFIX)
    try:
        return await app.send(
            [
                TaskMessage(
                    queue=KG_GRAPH_CHANGED_QUEUE,
                    kwargs={
                        "adapter_name": adapter_name,
                        "changed_edge_ids": edge_ids,
                        "affected_card_ids": card_ids,
                        "changes": dict(changes),
                        "event_identity": event_identity,
                    },
                    max_retries=KG_GRAPH_CHANGED_MAX_RETRIES,
                    timeout=KG_GRAPH_CHANGED_TIMEOUT_SECONDS,
                )
            ]
        )
    finally:
        app.close()


def _ordered_unique_positive_ints(values: list[int]) -> list[int]:
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
