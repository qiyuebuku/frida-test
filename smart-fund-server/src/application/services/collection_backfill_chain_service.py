from __future__ import annotations

import asyncio

import redis

from src.application.services.collection_app_service import CollectionAppService
from src.application.services.collection_backfill_service import CollectionBackfillService
from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL
from src.infrastructure.persistence.repositories.collection_state_repository_impl import CollectionStateRepositoryImpl
from src.infrastructure.tasks.jettask_dispatcher import send_collection_backfill


class CollectionBackfillChainService:
    """Advance the single backfill checkpoint stored in ft_collection_state."""

    def __init__(self, repository=None):
        self._states = repository or CollectionStateRepositoryImpl()

    async def start(self, *, aggregator: str, source_name: str, start_date: str) -> dict:
        prepared = CollectionBackfillService(repository=self._states).prepare(
            aggregator=aggregator,
            source_name=source_name,
            start_date=start_date,
        )
        state = self._states.get(aggregator, source_name) or {}
        if state.get("mode") == "backfill":
            await send_collection_backfill(aggregator, source_name)
        return prepared.to_dict()

    async def advance(self, *, aggregator: str, source_name: str) -> dict:
        state = await asyncio.to_thread(self._states.get, aggregator, source_name)
        if not state or state.get("mode") != "backfill":
            return {"status": "completed", "reason": "checkpoint_not_backfill"}

        client = redis.from_url(REDIS_URL, decode_responses=True)
        lease_key = f"{JETTASK_PREFIX}:backfill-chain:{aggregator}:{source_name}"
        lock = client.lock(
            lease_key,
            timeout=900,
            blocking_timeout=0,
            thread_local=False,
        )
        acquired = False
        try:
            acquired = await asyncio.to_thread(lock.acquire, blocking=False)
            if not acquired:
                return {"status": "skipped", "reason": "chain_already_running"}

            try:
                result = await CollectionAppService().run_collection_source(
                    aggregator, source_name
                )
            except Exception as exc:
                latest = await asyncio.to_thread(
                    self._states.get, aggregator, source_name
                ) or {}
                if latest.get("mode") != "backfill":
                    raise
                failures = max(1, int(latest.get("consecutive_failures") or 1))
                delay = min(300, 5 * (2 ** min(failures - 1, 6)))
                await send_collection_backfill(
                    aggregator, source_name, delay_seconds=delay
                )
                return {
                    "status": "retry_scheduled",
                    "delay_seconds": delay,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            checkpoint = result.checkpoint_after or {}
            if checkpoint.get("mode") != "backfill":
                return {
                    "status": "completed",
                    "backfill_status": checkpoint.get("backfill_status"),
                    "checkpoint": checkpoint,
                }
            await send_collection_backfill(
                aggregator, source_name, delay_seconds=5
            )
            return {"status": "pending", "checkpoint": checkpoint}
        finally:
            if acquired:
                try:
                    await asyncio.to_thread(lock.release)
                except redis.exceptions.LockError:
                    pass
            client.close()
