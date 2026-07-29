"""定向历史回填与停机断档追赶测试。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from src.application.services.collection_backfill_service import (
    CollectionBackfillError,
    CollectionBackfillService,
)
from src.domain.collection.services import base as base_service_module
from src.domain.collection.services.base import BaseAggregator, SourceDef
from src.domain.collection.services.news import NewsAggregator
from src.infrastructure.time_utils import app_today


class FakeRepository:
    def __init__(self, state: dict):
        self.state = dict(state)
        self.arm_calls: list[dict] = []

    def get(self, aggregator: str, source_name: str) -> dict | None:
        if (
            self.state.get("aggregator") == aggregator
            and self.state.get("source_name") == source_name
        ):
            return dict(self.state)
        return None

    def list_all(self, aggregator: str | None = None) -> list[dict]:
        if aggregator and self.state.get("aggregator") != aggregator:
            return []
        return [dict(self.state)]

    def arm_backfill(
        self,
        aggregator: str,
        source_name: str,
        target_time: str,
        cursor=None,
    ) -> bool:
        self.arm_calls.append(
            {
                "aggregator": aggregator,
                "source_name": source_name,
                "target_time": target_time,
                "cursor": cursor,
            }
        )
        self.state.update(
            {
                "mode": "backfill",
                "target_time": target_time,
                "cursor": cursor,
                "backfill_status": None,
            }
        )
        return True


@contextmanager
def acquired_lock(*args, **kwargs):
    class TestLock:
        def renew(self):
            return True

    yield TestLock()


@contextmanager
def busy_lock(*args, **kwargs):
    yield False


def make_state(**overrides) -> dict:
    state = {
        "aggregator": "news",
        "source_name": "ths",
        "mode": "incremental",
        "target_time": "2026-07-05",
        "newest_time": "2026-07-20T12:00:00+08:00",
        "oldest_time": "2026-07-05T10:00:00+08:00",
        "backfill_status": None,
        "cursor": None,
        "enabled": True,
        "config": {"target_days": 60, "interval": 1800},
    }
    state.update(overrides)
    return state


@pytest.mark.unit
def test_prepare_backfill_preserves_existing_time_boundaries():
    repo = FakeRepository(make_state())
    service = CollectionBackfillService(repo, acquired_lock)
    target = (app_today() - timedelta(days=90)).isoformat()

    result = service.prepare(
        aggregator="news",
        source_name="ths",
        start_date=target,
    )

    assert result.status == "armed"
    assert result.changed is True
    assert result.newest_time == "2026-07-20T12:00:00+08:00"
    assert result.oldest_time == "2026-07-05T10:00:00+08:00"
    assert repo.arm_calls == [
        {
            "aggregator": "news",
            "source_name": "ths",
            "target_time": target,
            "cursor": None,
        }
    ]


@pytest.mark.unit
def test_prepare_backfill_dry_run_does_not_write():
    repo = FakeRepository(make_state())
    service = CollectionBackfillService(repo, acquired_lock)

    result = service.prepare(
        aggregator="news",
        source_name="ths",
        start_date=(app_today() - timedelta(days=90)).isoformat(),
        dry_run=True,
    )

    assert result.status == "preview"
    assert result.changed is True
    assert repo.arm_calls == []


@pytest.mark.unit
def test_prepare_backfill_skips_already_covered_target():
    target = (app_today() - timedelta(days=30)).isoformat()
    repo = FakeRepository(make_state(oldest_time=target))
    service = CollectionBackfillService(repo, acquired_lock)

    result = service.prepare(
        aggregator="news",
        source_name="ths",
        start_date=target,
    )

    assert result.status == "already_covered"
    assert result.changed is False
    assert repo.arm_calls == []


@pytest.mark.unit
def test_prepare_backfill_extends_active_job_without_losing_cursor():
    old_target = (app_today() - timedelta(days=30)).isoformat()
    new_target = (app_today() - timedelta(days=60)).isoformat()
    repo = FakeRepository(
        make_state(
            mode="backfill",
            target_time=old_target,
            oldest_time=(app_today() - timedelta(days=10)).isoformat(),
            cursor={"page": 8},
        )
    )
    service = CollectionBackfillService(repo, acquired_lock)

    result = service.prepare(
        aggregator="news",
        source_name="ths",
        start_date=new_target,
    )

    assert result.status == "armed"
    assert result.cursor_preserved is True
    assert repo.arm_calls[0]["cursor"] == {"page": 8}


@pytest.mark.unit
def test_prepare_backfill_rejects_snapshot_source():
    repo = FakeRepository(
        make_state(
            aggregator="market",
            source_name="market_overview",
            config={"target_days": 0, "interval": 120},
        )
    )
    service = CollectionBackfillService(repo, acquired_lock)

    with pytest.raises(CollectionBackfillError, match="快照型"):
        service.prepare(
            aggregator="market",
            source_name="market_overview",
            start_date=(app_today() - timedelta(days=7)).isoformat(),
        )


@pytest.mark.unit
def test_prepare_backfill_does_not_race_running_worker():
    repo = FakeRepository(make_state())
    service = CollectionBackfillService(repo, busy_lock)

    with pytest.raises(CollectionBackfillError, match="正在采集"):
        service.prepare(
            aggregator="news",
            source_name="ths",
            start_date=(app_today() - timedelta(days=90)).isoformat(),
        )

    assert repo.arm_calls == []


@pytest.mark.unit
def test_auto_catchup_uses_previous_newest_time_with_overlap():
    now = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
    source = SourceDef("ths", lambda cp: [], 1800, lambda items: items)
    aggregator = NewsAggregator.__new__(NewsAggregator)
    state = make_state(
        last_success_at=now - timedelta(days=7),
        newest_time="2026-07-19T12:00:00+08:00",
    )

    checkpoint = aggregator._build_runtime_checkpoint(
        state=state,
        source=source,
        now_utc=now,
        lock=True,
    )

    assert checkpoint["mode"] == "backfill"
    assert checkpoint["target_time"] == "2026-07-18"
    assert checkpoint["newest_time"] == "2026-07-19T12:00:00+08:00"
    assert checkpoint["_auto_catchup"] is True


@pytest.mark.unit
def test_auto_catchup_does_not_run_for_recent_success():
    now = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
    source = SourceDef("ths", lambda cp: [], 1800, lambda items: items)
    aggregator = NewsAggregator.__new__(NewsAggregator)
    state = make_state(last_success_at=now - timedelta(minutes=40))

    checkpoint = aggregator._build_runtime_checkpoint(
        state=state,
        source=source,
        now_utc=now,
        lock=True,
    )

    assert checkpoint["mode"] == "incremental"
    assert "_auto_catchup" not in checkpoint


@pytest.mark.unit
def test_auto_catchup_does_not_apply_to_unsupported_source():
    now = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
    source = SourceDef("snapshot", lambda cp: [], 60, lambda items: items)
    aggregator = BaseAggregator()
    state = make_state(last_success_at=now - timedelta(days=7))

    checkpoint = aggregator._build_runtime_checkpoint(
        state=state,
        source=source,
        now_utc=now,
        lock=True,
    )

    assert checkpoint["mode"] == "incremental"


@pytest.mark.unit
def test_backfill_without_time_progress_switches_to_ceiling():
    aggregator = BaseAggregator()
    aggregator.data_domain = "test"
    checkpoint = aggregator._compute_checkpoint(
        "bounded",
        [{"published_at": "2026-07-10T00:00:00+08:00"}],
        {
            "mode": "backfill",
            "target_time": "2026-06-01",
            "oldest_time": "2026-07-10T00:00:00+08:00",
            "newest_time": "2026-07-20T00:00:00+08:00",
        },
    )

    assert checkpoint["mode"] == "incremental"
    assert checkpoint["backfill_status"] == "ceiling"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_source_tick_catches_up_and_returns_to_incremental(monkeypatch):
    now = datetime.now(timezone.utc)
    previous_newest = now - timedelta(days=7)
    target_date = (previous_newest.date() - timedelta(days=1)).isoformat()
    state = make_state(
        aggregator="test",
        source_name="history",
        last_run_at=now - timedelta(days=7),
        last_success_at=now - timedelta(days=7),
        newest_time=previous_newest.isoformat(),
        oldest_time=(previous_newest - timedelta(days=30)).isoformat(),
    )
    received_checkpoints: list[dict] = []
    success_updates: list[dict | None] = []

    async def fetch(checkpoint):
        received_checkpoints.append(dict(checkpoint))
        return [
            {"published_at": now.isoformat()},
            {"published_at": f"{target_date}T00:00:00+00:00"},
        ]

    class CatchupAggregator(BaseAggregator):
        data_domain = "test"
        AUTO_CATCHUP_SOURCES = frozenset({"history"})

        def __init__(self):
            self.sources = [
                SourceDef("history", fetch, 1800, lambda items: items),
            ]

        def _save(self, items: list[dict]) -> int:
            return len(items)

    monkeypatch.setattr(
        base_service_module.checkpoint_store,
        "get",
        lambda aggregator, source_name: dict(state),
    )
    monkeypatch.setattr(
        base_service_module.checkpoint_store,
        "update_success",
        lambda aggregator, source_name, checkpoint, saved: success_updates.append(
            checkpoint
        ),
    )
    monkeypatch.setattr(
        base_service_module.checkpoint_store,
        "update_failure",
        lambda *args, **kwargs: pytest.fail("不应写入失败状态"),
    )
    monkeypatch.setattr(
        base_service_module.redis_lock,
        "acquire",
        acquired_lock,
    )

    result = await CatchupAggregator().tick()

    assert result == {"sources_run": 1, "total_saved": 2}
    assert received_checkpoints[0]["mode"] == "backfill"
    assert received_checkpoints[0]["target_time"] == target_date
    assert success_updates[-1]["mode"] == "incremental"
    assert success_updates[-1]["backfill_status"] == "done"


@pytest.mark.unit
def test_auto_catchup_does_not_use_global_oldest_as_gap_completion():
    aggregator = BaseAggregator()
    aggregator.data_domain = "test"

    checkpoint = aggregator._compute_checkpoint(
        "bounded",
        [{"published_at": "2026-07-25T00:00:00+00:00"}],
        {
            "mode": "backfill",
            "target_time": "2026-07-18",
            "oldest_time": "2020-01-01T00:00:00+00:00",
            "newest_time": "2026-07-19T00:00:00+00:00",
            "_auto_catchup": True,
        },
    )

    assert checkpoint["mode"] == "incremental"
    assert checkpoint["backfill_status"] == "ceiling"
