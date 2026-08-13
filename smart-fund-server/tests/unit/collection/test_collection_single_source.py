from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from src.domain.collection.services import base
from src.domain.collection.services.base import BaseAggregator, SourceDef


class SingleSourceAggregator(BaseAggregator):
    data_domain = "single_source_test"

    def __init__(self) -> None:
        self.fetched: list[str] = []
        self.saved: list[dict] = []

        async def fetch_alpha(checkpoint):
            self.fetched.append("alpha")
            return [{"trade_date": "2026-08-07", "value": 1}]

        async def fetch_beta(checkpoint):
            self.fetched.append("beta")
            return [{"trade_date": "2026-08-07", "value": 2}]

        self.sources = [
            SourceDef("alpha", fetch_alpha, 3600, lambda rows: rows),
            SourceDef("beta", fetch_beta, 3600, lambda rows: rows),
        ]

    def _save(self, items: list[dict]) -> int:
        self.saved.extend(items)
        return len(items)


@pytest.mark.asyncio
async def test_run_source_executes_only_requested_source_without_interval_gate(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    state = {
        "mode": "incremental",
        "last_run_at": now,
        "last_success_at": now,
        "interval_override": 86400,
        "config": {},
    }
    successes: list[tuple[str, str, int]] = []

    monkeypatch.setattr(base.checkpoint_store, "get", lambda domain, source: state)
    monkeypatch.setattr(base.checkpoint_store, "mark_started", lambda **kwargs: None)
    monkeypatch.setattr(
        base.checkpoint_store,
        "update_success",
        lambda domain, source, checkpoint, count: successes.append(
            (domain, source, count)
        ),
    )

    @contextmanager
    def acquire(name, ttl):
        class Lock:
            def renew(self):
                return True

        yield Lock()

    monkeypatch.setattr(base.redis_lock, "acquire", acquire)

    aggregator = SingleSourceAggregator()
    result = await aggregator.run_source("beta")

    assert aggregator.fetched == ["beta"]
    assert result["saved_count"] == 1
    assert successes == [("single_source_test", "beta", 1)]
