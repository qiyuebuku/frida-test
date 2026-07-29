"""Asynchronous relationship-first Graph Community refresh use case."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import redis

from src.domain.knowledge.relation_graph_community import (
    RelationGraphClusteringConfig,
    discover_relation_graph_partition,
)
from src.infrastructure.config.settings import (
    JETTASK_PREFIX,
    KG_GRAPH_COMMUNITY_CLUSTER_EDGE_THRESHOLD,
    KG_GRAPH_COMMUNITY_CLUSTER_NODE_THRESHOLD,
    KG_GRAPH_COMMUNITY_LEIDEN_MIN_MODULARITY,
    KG_GRAPH_COMMUNITY_LEIDEN_RESOLUTION,
    KG_GRAPH_COMMUNITY_MAX_CROSS_EDGE_RATIO,
    REDIS_URL,
)
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_update_span,
)
from src.infrastructure.persistence.repositories.relation_graph_community_repository import (
    RelationGraphCommunityRepository,
)


logger = logging.getLogger(__name__)

GRAPH_COMMUNITY_REFRESH_LOCK_TIMEOUT_SECONDS = 300
GRAPH_COMMUNITY_REFRESH_LOCK_BLOCKING_TIMEOUT_SECONDS = 300
GRAPH_COMMUNITY_REFRESH_LOCK_RENEW_SECONDS = 30


class RelationGraphCommunityService:
    """Recompute only the relation closure touched by one graph-change event."""

    def __init__(
        self,
        *,
        target: str = "prod",
        repository: RelationGraphCommunityRepository | Any | None = None,
        redis_client: Any | None = None,
        clustering_config: RelationGraphClusteringConfig | None = None,
    ) -> None:
        self._target = target
        self._repository = repository or RelationGraphCommunityRepository(
            target=target  # type: ignore[arg-type]
        )
        self._redis = redis_client
        self._clustering_config = clustering_config or RelationGraphClusteringConfig(
            node_threshold=KG_GRAPH_COMMUNITY_CLUSTER_NODE_THRESHOLD,
            edge_threshold=KG_GRAPH_COMMUNITY_CLUSTER_EDGE_THRESHOLD,
            resolution=KG_GRAPH_COMMUNITY_LEIDEN_RESOLUTION,
            min_modularity=KG_GRAPH_COMMUNITY_LEIDEN_MIN_MODULARITY,
            max_cross_edge_ratio=KG_GRAPH_COMMUNITY_MAX_CROSS_EDGE_RATIO,
        )

    async def refresh_from_graph_change(
        self,
        *,
        adapter_name: str,
        changed_edge_ids: list[str],
        affected_card_ids: list[str],
        changes: dict[str, Any] | None = None,
        event_identity: str = "",
    ) -> dict[str, Any]:
        adapter = str(adapter_name or "").strip()
        card_ids = _ordered_unique(affected_card_ids)
        edge_ids = _ordered_unique(changed_edge_ids)
        if not adapter:
            raise ValueError("kg_graph_changed adapter_name 不能为空")
        if not edge_ids:
            return {
                "status": "skipped",
                "reason": "no_changed_edges",
                "event_identity": event_identity,
            }
        if not card_ids:
            raise ValueError("kg_graph_changed 缺少 affected_card_ids")

        lock = self._adapter_lock(adapter)
        acquired = await asyncio.to_thread(
            lambda: lock.acquire(
                blocking=True,
                blocking_timeout=GRAPH_COMMUNITY_REFRESH_LOCK_BLOCKING_TIMEOUT_SECONDS,
            )
        )
        if not acquired:
            raise TimeoutError(f"Graph Community adapter lock 获取超时: {adapter}")
        stop_renew = asyncio.Event()
        lock_lost = asyncio.Event()
        renew_task = asyncio.create_task(
            _renew_lock_loop(lock, stop_renew, lock_lost, adapter_name=adapter)
        )
        try:
            _raise_if_lock_lost(lock_lost, adapter_name=adapter)
            with langfuse_observation(
                name="kg.community.subgraph.load",
                as_type="span",
                input={
                    "adapter_name": adapter,
                    "changed_edge_ids": edge_ids,
                    "affected_card_ids": card_ids,
                    "changes": dict(changes or {}),
                    "event_identity": event_identity,
                },
            ):
                affected = await asyncio.to_thread(
                    self._repository.load_affected_graph,
                    adapter_name=adapter,
                    seed_card_ids=card_ids,
                )
                langfuse_update_span(
                    output={
                        "edges": len(affected.edges),
                        "touched_communities": len(
                            affected.touched_community_ids
                        ),
                        "rejected_edge_ids": list(affected.rejected_edge_ids),
                    },
                    status_message="completed",
                )

            _raise_if_lock_lost(lock_lost, adapter_name=adapter)
            with langfuse_observation(
                name="kg.community.partition.discover",
                as_type="span",
                input={
                    "nodes": len(
                        {
                            card_id
                            for edge in affected.edges
                            for card_id in (
                                edge.source_card_id,
                                edge.target_card_id,
                            )
                        }
                    ),
                    "edges": len(affected.edges),
                },
            ):
                partition = discover_relation_graph_partition(
                    affected,
                    config=self._clustering_config,
                )
                langfuse_update_span(
                    output={
                        "communities": len(partition.communities),
                        "community_relations": len(
                            partition.community_relations
                        ),
                        "connected_regions": partition.connected_region_count,
                        "clustered_regions": partition.clustered_region_count,
                        "retained_regions": partition.retained_region_count,
                        "community_ids": [
                            item.community_id
                            for item in partition.communities
                        ],
                    },
                    status_message="completed",
                )

            _raise_if_lock_lost(lock_lost, adapter_name=adapter)
            with langfuse_observation(
                name="kg.community.pg.apply",
                as_type="span",
                input={
                    "touched_community_ids": list(
                        affected.touched_community_ids
                    ),
                    "communities": len(partition.communities),
                    "community_relations": len(
                        partition.community_relations
                    ),
                },
            ):
                applied = await asyncio.to_thread(
                    self._repository.apply_components,
                    adapter_name=adapter,
                    touched_community_ids=list(
                        affected.touched_community_ids
                    ),
                    components=list(partition.communities),
                    community_relations=list(
                        partition.community_relations
                    ),
                )
                result = {
                    "status": "completed",
                    "event_identity": event_identity,
                    "changed_edge_ids": edge_ids,
                    "affected_card_ids": card_ids,
                    "affected_edges": len(affected.edges),
                    "rejected_edge_ids": list(affected.rejected_edge_ids),
                    "components": len(partition.communities),
                    "community_relations": len(
                        partition.community_relations
                    ),
                    "connected_regions": partition.connected_region_count,
                    "clustered_regions": partition.clustered_region_count,
                    "retained_regions": partition.retained_region_count,
                    "created_community_ids": list(
                        applied.created_community_ids
                    ),
                    "updated_community_ids": list(
                        applied.updated_community_ids
                    ),
                    "unchanged_community_ids": list(
                        applied.unchanged_community_ids
                    ),
                    "deleted_community_ids": list(
                        applied.deleted_community_ids
                    ),
                    "dirty_community_ids": list(applied.dirty_community_ids),
                    "created_community_relation_ids": list(
                        applied.created_community_relation_ids
                    ),
                    "updated_community_relation_ids": list(
                        applied.updated_community_relation_ids
                    ),
                    "unchanged_community_relation_ids": list(
                        applied.unchanged_community_relation_ids
                    ),
                    "deleted_community_relation_ids": list(
                        applied.deleted_community_relation_ids
                    ),
                }
                _raise_if_lock_lost(lock_lost, adapter_name=adapter)
                langfuse_update_span(
                    output=result,
                    status_message="completed",
                )
                return result
        finally:
            stop_renew.set()
            await renew_task
            try:
                await asyncio.to_thread(lock.release)
            except Exception:
                logger.exception(
                    "Graph Community adapter lock 释放失败 adapter=%s",
                    adapter,
                )

    def _adapter_lock(self, adapter_name: str) -> Any:
        return self._redis_client().lock(
            f"{JETTASK_PREFIX}:lock:kg_graph_community_refresh:{adapter_name}",
            timeout=GRAPH_COMMUNITY_REFRESH_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=GRAPH_COMMUNITY_REFRESH_LOCK_BLOCKING_TIMEOUT_SECONDS,
            thread_local=False,
        )

    def _redis_client(self) -> Any:
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
        return self._redis


def _ordered_unique(values: list[str]) -> list[str]:
    return [
        item
        for item in dict.fromkeys(str(value).strip() for value in values)
        if item
    ]


async def _renew_lock_loop(
    lock: Any,
    stop_renew: asyncio.Event,
    lock_lost: asyncio.Event,
    *,
    adapter_name: str,
) -> None:
    while True:
        try:
            await asyncio.wait_for(
                stop_renew.wait(),
                timeout=GRAPH_COMMUNITY_REFRESH_LOCK_RENEW_SECONDS,
            )
            return
        except TimeoutError:
            pass

        try:
            extended = await asyncio.to_thread(
                lock.extend,
                GRAPH_COMMUNITY_REFRESH_LOCK_TIMEOUT_SECONDS,
                replace_ttl=True,
            )
        except Exception:
            logger.exception(
                "Graph Community adapter lock 续租失败 adapter=%s",
                adapter_name,
            )
            lock_lost.set()
            return
        if not extended:
            logger.error(
                "Graph Community adapter lock 已失去所有权 adapter=%s",
                adapter_name,
            )
            lock_lost.set()
            return


def _raise_if_lock_lost(
    lock_lost: asyncio.Event,
    *,
    adapter_name: str,
) -> None:
    if lock_lost.is_set():
        raise RuntimeError(
            f"Graph Community adapter lock 已失去，终止当前刷新: {adapter_name}"
        )
