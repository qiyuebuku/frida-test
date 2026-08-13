from types import SimpleNamespace

import pytest

from src.application.services import collection_backfill_chain_service as module
from src.application.services.collection_backfill_chain_service import (
    CollectionBackfillChainService,
)


class FakeStates:
    def __init__(self, state):
        self.state = state

    def get(self, aggregator, source_name):
        return dict(self.state) if self.state else None


class FakeRedis:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.released = []

    def lock(self, *args, **kwargs):
        owner = self

        class Lock:
            def acquire(self, blocking=False):
                return owner.acquired

            def release(self):
                owner.released.append(True)

        return Lock()


    def close(self):
        pass


@pytest.mark.asyncio
async def test_chain_schedules_next_batch_while_checkpoint_backfills(monkeypatch):
    client = FakeRedis()
    sent = []
    monkeypatch.setattr(module.redis, "from_url", lambda *a, **k: client)
    monkeypatch.setattr(module, "send_collection_backfill", _sender(sent))
    monkeypatch.setattr(
        module.CollectionAppService,
        "run_collection_source",
        _result({"mode": "backfill", "cursor": 6}),
    )
    result = await CollectionBackfillChainService(
        FakeStates({"mode": "backfill"})
    ).advance(aggregator="news", source_name="cls")
    assert result["status"] == "pending"
    assert sent == [("news", "cls", 5)]
    assert client.released


@pytest.mark.asyncio
async def test_chain_stops_when_checkpoint_returns_incremental(monkeypatch):
    client = FakeRedis()
    sent = []
    monkeypatch.setattr(module.redis, "from_url", lambda *a, **k: client)
    monkeypatch.setattr(module, "send_collection_backfill", _sender(sent))
    monkeypatch.setattr(
        module.CollectionAppService,
        "run_collection_source",
        _result({"mode": "incremental", "backfill_status": "done"}),
    )
    result = await CollectionBackfillChainService(
        FakeStates({"mode": "backfill"})
    ).advance(aggregator="news", source_name="cls")
    assert result["status"] == "completed"
    assert sent == []


@pytest.mark.asyncio
async def test_duplicate_message_does_not_release_another_owner_lease(monkeypatch):
    client = FakeRedis(acquired=False)
    monkeypatch.setattr(module.redis, "from_url", lambda *a, **k: client)
    result = await CollectionBackfillChainService(
        FakeStates({"mode": "backfill"})
    ).advance(aggregator="news", source_name="cls")
    assert result["reason"] == "chain_already_running"
    assert client.released == []


@pytest.mark.asyncio
async def test_failure_keeps_checkpoint_and_schedules_backoff(monkeypatch):
    client = FakeRedis()
    sent = []
    states = FakeStates({"mode": "backfill", "consecutive_failures": 3})
    monkeypatch.setattr(module.redis, "from_url", lambda *a, **k: client)
    monkeypatch.setattr(module, "send_collection_backfill", _sender(sent))

    async def fail(self, aggregator, source_name):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(module.CollectionAppService, "run_collection_source", fail)
    result = await CollectionBackfillChainService(states).advance(
        aggregator="news", source_name="cls"
    )
    assert result["status"] == "retry_scheduled"
    assert sent == [("news", "cls", 20)]


def _sender(target):
    async def send(aggregator, source_name, *, delay_seconds=0):
        target.append((aggregator, source_name, delay_seconds))
        return ["event"]
    return send


def _result(checkpoint):
    async def run(self, aggregator, source_name):
        return SimpleNamespace(checkpoint_after=checkpoint)
    return run
