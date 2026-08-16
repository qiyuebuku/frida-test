"""jettask-rs 消息投递工具。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from uuid import uuid4

from jettask import Jettask, TaskMessage
import redis.asyncio as async_redis

from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL


logger = logging.getLogger(__name__)

KG_NEWS_INGEST_QUEUE = "kg_news_ingest"
KG_NEWS_INGEST_TIMEOUT_SECONDS = 5000
KG_NEWS_INGEST_MAX_RETRIES = 10
KG_RELATION_DISCOVERY_QUEUE = "kg_relation_discovery"
KG_RELATION_DISCOVERY_TIMEOUT_SECONDS = 5000
KG_RELATION_DISCOVERY_MAX_RETRIES = 5
KG_GRAPH_CHANGED_QUEUE = "kg_graph_changed"
KG_GRAPH_CHANGED_TIMEOUT_SECONDS = 5000
KG_GRAPH_CHANGED_MAX_RETRIES = 10
KG_GRAPH_CHANGE_COALESCE_SECONDS = 3
KG_GRAPH_CHANGE_BUFFER_TTL_SECONDS = 900
KG_GRAPH_COMMUNITY_REPORT_QUEUE = "kg_graph_community_report"
KG_GRAPH_COMMUNITY_PROJECTION_QUEUE = "kg_graph_community_projection"
KG_GRAPH_COMMUNITY_DERIVATION_TIMEOUT_SECONDS = 1800
KG_GRAPH_COMMUNITY_DERIVATION_MAX_RETRIES = 5
KG_TASK_SEND_MAX_ATTEMPTS = 3
KG_TASK_SEND_RETRY_BASE_SECONDS = 0.5
WATCHLIST_INSTRUMENT_COLLECTION_QUEUE = "collect_watchlist_instruments"
WATCHLIST_INSTRUMENT_COLLECTION_TIMEOUT_SECONDS = 900
WATCHLIST_INSTRUMENT_COLLECTION_MAX_RETRIES = 3
COLLECTION_BACKFILL_QUEUE = "advance_collection_backfill"


async def send_collection_backfill(
    aggregator: str,
    source_name: str,
    *,
    delay_seconds: int = 0,
) -> list[str]:
    """Wake one business-owned backfill checkpoint; no Schedule is created."""
    return await _send_messages_with_retry(
        [TaskMessage(
            queue=COLLECTION_BACKFILL_QUEUE,
            kwargs={"aggregator": aggregator, "source_name": source_name},
            delay=max(0, int(delay_seconds)),
            priority=8,
            max_retries=3,
            timeout=900,
        )],
        queue=COLLECTION_BACKFILL_QUEUE,
    )
async def send_kg_news_ingest(
    news_ids: list[int],
    *,
    workflow_id: str = "",
) -> list[str]:
    """把新增 ft_news.id 直接投递给 kg_news_ingest 队列。

    这里不使用 Redis Stream 作为二级队列；消息可靠性交给 jettask-rs。
    """

    normalized_ids = _ordered_unique_positive_ints(news_ids)
    if not normalized_ids:
        return []

    identity = str(workflow_id or "").strip() or build_kg_news_workflow_id(
        normalized_ids
    )
    return await _send_messages_with_retry(
        [
            TaskMessage(
                queue=KG_NEWS_INGEST_QUEUE,
                kwargs={
                    "news_ids": normalized_ids,
                    "workflow_id": identity,
                },
                max_retries=KG_NEWS_INGEST_MAX_RETRIES,
                timeout=KG_NEWS_INGEST_TIMEOUT_SECONDS,
            )
        ],
        queue=KG_NEWS_INGEST_QUEUE,
    )


async def send_watchlist_instrument_collection(
    codes: list[str],
    *,
    scope: str = "bootstrap",
) -> list[str]:
    """Immediately collect newly created or reactivated instruments."""

    normalized_codes = [
        code
        for code in dict.fromkeys(str(code).strip().lower() for code in codes)
        if code
    ]
    if not normalized_codes:
        return []
    if scope not in {"bootstrap", "realtime", "daily", "reference"}:
        raise ValueError("invalid watchlist collection scope")
    return await _send_messages_with_retry(
        [
            TaskMessage(
                queue=WATCHLIST_INSTRUMENT_COLLECTION_QUEUE,
                kwargs={"codes": [code], "scope": scope},
                max_retries=WATCHLIST_INSTRUMENT_COLLECTION_MAX_RETRIES,
                timeout=WATCHLIST_INSTRUMENT_COLLECTION_TIMEOUT_SECONDS,
            )
            for code in normalized_codes
        ],
        queue=WATCHLIST_INSTRUMENT_COLLECTION_QUEUE,
    )


async def send_kg_relation_discovery(
    card_ids: list[str],
    *,
    workflow_id: str = "",
) -> list[str]:
    """在 Card 双视图发布成功后，按单 Card 分片投递关系发现任务。"""

    normalized_ids = [item for item in dict.fromkeys(card_ids) if str(item).strip()]
    if not normalized_ids:
        return []
    identity = str(workflow_id or "").strip()
    messages: list[TaskMessage] = []
    for card_id in normalized_ids:
        kwargs: dict[str, object] = {"card_ids": [card_id]}
        if identity:
            kwargs["workflow_id"] = identity
        messages.append(
            TaskMessage(
                queue=KG_RELATION_DISCOVERY_QUEUE,
                kwargs=kwargs,
                max_retries=KG_RELATION_DISCOVERY_MAX_RETRIES,
                timeout=KG_RELATION_DISCOVERY_TIMEOUT_SECONDS,
            )
        )
    return await _send_messages_with_retry(
        messages,
        queue=KG_RELATION_DISCOVERY_QUEUE,
    )


async def send_kg_graph_changed(
    *,
    adapter_name: str,
    changed_edge_ids: list[str],
    affected_card_ids: list[str],
    changes: dict,
    event_identity: str,
    workflow_id: str = "",
) -> list[str]:
    """投递正式 Edge 图变化事件，供第三阶段增量构建 Community。"""

    edge_ids = [item for item in dict.fromkeys(changed_edge_ids) if str(item).strip()]
    card_ids = [item for item in dict.fromkeys(affected_card_ids) if str(item).strip()]
    if not edge_ids:
        return []
    should_schedule = await buffer_kg_graph_change(
        adapter_name=adapter_name,
        changed_edge_ids=edge_ids,
        affected_card_ids=card_ids,
    )
    if not should_schedule:
        return []
    batch_identity = f"kg_graph_batch:{uuid4().hex}"
    kwargs = {
        "adapter_name": adapter_name,
        "changed_edge_ids": [],
        "affected_card_ids": [],
        "changes": {"coalesced": True},
        "event_identity": batch_identity,
    }
    identity = str(workflow_id or "").strip()
    if identity:
        kwargs["workflow_id"] = identity
    return await _send_messages_with_retry(
        [
            TaskMessage(
                queue=KG_GRAPH_CHANGED_QUEUE,
                kwargs=kwargs,
                delay=KG_GRAPH_CHANGE_COALESCE_SECONDS,
                max_retries=KG_GRAPH_CHANGED_MAX_RETRIES,
                timeout=KG_GRAPH_CHANGED_TIMEOUT_SECONDS,
            )
        ],
        queue=KG_GRAPH_CHANGED_QUEUE,
    )


async def buffer_kg_graph_change(
    *,
    adapter_name: str,
    changed_edge_ids: list[str],
    affected_card_ids: list[str],
) -> bool:
    keys = _graph_change_buffer_keys(adapter_name)
    client = async_redis.from_url(REDIS_URL, decode_responses=True)
    try:
        pipe = client.pipeline(transaction=True)
        if changed_edge_ids:
            pipe.sadd(keys["edges"], *changed_edge_ids)
        if affected_card_ids:
            pipe.sadd(keys["cards"], *affected_card_ids)
        pipe.expire(keys["edges"], KG_GRAPH_CHANGE_BUFFER_TTL_SECONDS)
        pipe.expire(keys["cards"], KG_GRAPH_CHANGE_BUFFER_TTL_SECONDS)
        pipe.set(
            keys["scheduled"],
            "1",
            ex=KG_GRAPH_CHANGE_BUFFER_TTL_SECONDS,
            nx=True,
        )
        results = await pipe.execute()
        return bool(results[-1])
    finally:
        await client.aclose()


async def claim_kg_graph_change_batch(
    *, adapter_name: str, batch_identity: str
) -> tuple[list[str], list[str]]:
    keys = _graph_change_buffer_keys(adapter_name, batch_identity=batch_identity)
    client = async_redis.from_url(REDIS_URL, decode_responses=True)
    script = """
local function claim(active, processing, ttl)
  if redis.call('EXISTS', processing) == 1 then
    return redis.call('SMEMBERS', processing)
  end
  if redis.call('EXISTS', active) == 0 then return {} end
  redis.call('RENAME', active, processing)
  redis.call('EXPIRE', processing, ttl)
  return redis.call('SMEMBERS', processing)
end
local edges = claim(KEYS[1], KEYS[3], ARGV[1])
local cards = claim(KEYS[2], KEYS[4], ARGV[1])
redis.call('DEL', KEYS[5])
return {edges, cards}
"""
    try:
        result = await client.eval(
            script,
            5,
            keys["edges"],
            keys["cards"],
            keys["processing_edges"],
            keys["processing_cards"],
            keys["scheduled"],
            KG_GRAPH_CHANGE_BUFFER_TTL_SECONDS,
        )
        return sorted(result[0] or []), sorted(result[1] or [])
    finally:
        await client.aclose()


async def ack_kg_graph_change_batch(
    *, adapter_name: str, batch_identity: str
) -> None:
    keys = _graph_change_buffer_keys(adapter_name, batch_identity=batch_identity)
    client = async_redis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.delete(keys["processing_edges"], keys["processing_cards"])
    finally:
        await client.aclose()


def _graph_change_buffer_keys(
    adapter_name: str, *, batch_identity: str = ""
) -> dict[str, str]:
    adapter_digest = hashlib.sha256(adapter_name.encode("utf-8")).hexdigest()[:16]
    batch_digest = hashlib.sha256(batch_identity.encode("utf-8")).hexdigest()[:16]
    base = f"{JETTASK_PREFIX}:kg_graph_change:{adapter_digest}"
    return {
        "edges": f"{base}:edges",
        "cards": f"{base}:cards",
        "scheduled": f"{base}:scheduled",
        "processing_edges": f"{base}:processing:{batch_digest}:edges",
        "processing_cards": f"{base}:processing:{batch_digest}:cards",
    }


async def send_kg_graph_community_reports(
    reports: list[dict],
) -> list[str]:
    """批量投递 Community 事实报告任务。"""

    messages: list[TaskMessage] = []
    seen: set[tuple[str, str]] = set()
    for item in reports:
        community_id = str(item.get("community_id") or "").strip()
        fingerprint = str(item.get("graph_fingerprint") or "").strip()
        identity = (community_id, fingerprint)
        if not all(identity) or identity in seen:
            continue
        seen.add(identity)
        messages.append(
            TaskMessage(
                queue=KG_GRAPH_COMMUNITY_REPORT_QUEUE,
                kwargs={
                    "community_id": community_id,
                    "expected_graph_fingerprint": fingerprint,
                },
                delay=max(0, int(item.get("delay_seconds") or 0)),
                max_retries=KG_GRAPH_COMMUNITY_DERIVATION_MAX_RETRIES,
                timeout=KG_GRAPH_COMMUNITY_DERIVATION_TIMEOUT_SECONDS,
            )
        )
    if not messages:
        return []
    return await _send_messages_with_retry(
        messages,
        queue=KG_GRAPH_COMMUNITY_REPORT_QUEUE,
    )


async def send_kg_graph_community_projections(
    projections: list[dict],
) -> list[str]:
    """批量投递 Community 条件性预测任务。"""

    messages: list[TaskMessage] = []
    seen: set[tuple[str, str, int]] = set()
    for item in projections:
        community_id = str(item.get("community_id") or "").strip()
        fingerprint = str(item.get("graph_fingerprint") or "").strip()
        report_version = int(item.get("fact_report_version") or 0)
        identity = (community_id, fingerprint, report_version)
        if not community_id or not fingerprint or report_version <= 0 or identity in seen:
            continue
        seen.add(identity)
        messages.append(
            TaskMessage(
                queue=KG_GRAPH_COMMUNITY_PROJECTION_QUEUE,
                kwargs={
                    "community_id": community_id,
                    "expected_graph_fingerprint": fingerprint,
                    "expected_fact_report_version": report_version,
                },
                max_retries=KG_GRAPH_COMMUNITY_DERIVATION_MAX_RETRIES,
                timeout=KG_GRAPH_COMMUNITY_DERIVATION_TIMEOUT_SECONDS,
            )
        )
    if not messages:
        return []
    return await _send_messages_with_retry(
        messages,
        queue=KG_GRAPH_COMMUNITY_PROJECTION_QUEUE,
    )


def build_kg_news_workflow_id(news_ids: list[int]) -> str:
    normalized_ids = _ordered_unique_positive_ints(news_ids)
    if not normalized_ids:
        return ""
    raw = ",".join(str(item) for item in sorted(normalized_ids))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"kg_news_workflow:{digest}"


async def _send_messages_with_retry(
    messages: list[TaskMessage],
    *,
    queue: str,
) -> list[str]:
    last_error: Exception | None = None
    for attempt in range(1, KG_TASK_SEND_MAX_ATTEMPTS + 1):
        app = Jettask(redis_url=REDIS_URL, prefix=JETTASK_PREFIX)
        try:
            return await app.send(messages)
        except Exception as exc:
            last_error = exc
            if attempt >= KG_TASK_SEND_MAX_ATTEMPTS:
                raise
            delay = KG_TASK_SEND_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "KG task 投递失败，准备重试 queue=%s attempt=%s/%s delay=%.1fs error=%s",
                queue,
                attempt,
                KG_TASK_SEND_MAX_ATTEMPTS,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
        finally:
            app.close()
    if last_error is not None:
        raise last_error
    return []


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
