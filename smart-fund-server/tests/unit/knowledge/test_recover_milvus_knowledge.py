import asyncio
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import recover_milvus_knowledge as recovery
from scripts.recover_milvus_knowledge import _news_id_from_source_id


def test_news_id_from_source_id_accepts_only_exact_ft_news_identity() -> None:
    assert _news_id_from_source_id("ft_news:123") == 123
    assert _news_id_from_source_id(" ft_news:456 ") == 456
    assert _news_id_from_source_id("ft_news:0") == 0
    assert _news_id_from_source_id("other:123") is None
    assert _news_id_from_source_id("ft_news:12:3") is None


@pytest.mark.asyncio
async def test_timeout_batch_is_moved_to_queue_tail(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[int]] = []

    class FakeService:
        def __init__(self, *, target):
            del target

        async def ingest_ft_news_ids(self, news_ids, *, workflow_id):
            del workflow_id
            calls.append(list(news_ids))
            if news_ids == [3]:
                await asyncio.sleep(1)
            return {"consumed_ids": 1, "relation_card_ids": []}

    monkeypatch.setattr(recovery, "_load_recovery_news_ids", lambda _target: [3, 2, 1])
    monkeypatch.setattr(recovery, "KnowledgeNewsIngestionService", FakeService)

    await recovery._run(
        Namespace(
            target="test",
            batch_size=1,
            pause_seconds=0,
            retry_seconds=0,
            batch_timeout_seconds=0.01,
            max_batches=2,
            state_file=tmp_path / "state.json",
        )
    )

    assert calls[:4] == [[3], [3], [2], [1]]


@pytest.mark.asyncio
async def test_permanent_record_failure_is_quarantined_without_stopping_queue(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[list[int]] = []

    class FakeService:
        def __init__(self, *, target):
            del target

        async def ingest_ft_news_ids(self, news_ids, *, workflow_id):
            del workflow_id
            calls.append(list(news_ids))
            if news_ids == [3]:
                raise ValueError("provider rejected this record")
            return {"consumed_ids": 1, "relation_card_ids": []}

    monkeypatch.setattr(recovery, "_load_recovery_news_ids", lambda _target: [3, 2, 1])
    monkeypatch.setattr(recovery, "KnowledgeNewsIngestionService", FakeService)
    state_file = tmp_path / "state.json"

    await recovery._run(
        Namespace(
            target="test",
            batch_size=1,
            pause_seconds=0,
            retry_seconds=0,
            batch_timeout_seconds=1,
            max_batches=0,
            max_record_attempts=2,
            state_file=state_file,
        )
    )

    completed, failures = recovery._load_state(state_file)
    assert completed == {1, 2}
    assert failures[3]["status"] == "quarantined"
    assert failures[3]["attempts"] == 2
    assert calls.count([3]) == 4
