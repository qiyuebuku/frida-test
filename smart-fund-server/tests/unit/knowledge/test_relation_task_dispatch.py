"""关系发现 jettask 消息投递测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.application.services import knowledge_news_ingestion_service as ingestion_module
from src.application.services.knowledge_news_ingestion_service import KnowledgeNewsIngestionService
from src.infrastructure.tasks import jettask_dispatcher as dispatcher


@pytest.mark.asyncio
async def test_relation_dispatch_message_contains_only_card_ids(monkeypatch) -> None:
    sent = []

    class FakeJettask:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def send(self, messages):
            sent.extend(messages)
            return [f"event:{index}" for index, _message in enumerate(messages, 1)]

        def close(self):
            return None

    monkeypatch.setattr(dispatcher, "Jettask", FakeJettask)

    event_ids = await dispatcher.send_kg_relation_discovery(["card:1", "card:1", "card:2"])

    assert event_ids == ["event:1", "event:2"]
    assert len(sent) == 2
    assert [message.queue for message in sent] == [
        "kg_relation_discovery",
        "kg_relation_discovery",
    ]
    assert [message.kwargs for message in sent] == [
        {"card_ids": ["card:1"]},
        {"card_ids": ["card:2"]},
    ]


@pytest.mark.asyncio
async def test_watchlist_dispatch_isolated_per_instrument(monkeypatch) -> None:
    sent = []

    class FakeJettask:
        def __init__(self, **_kwargs):
            pass

        async def send(self, messages):
            sent.extend(messages)
            return [f"event:{index}" for index, _message in enumerate(messages, 1)]

        def close(self):
            return None

    monkeypatch.setattr(dispatcher, "Jettask", FakeJettask)

    event_ids = await dispatcher.send_watchlist_instrument_collection(
        ["sh600036", "sh600036", "159915"]
    )

    assert event_ids == ["event:1", "event:2"]
    assert [message.queue for message in sent] == [
        "collect_watchlist_instruments",
        "collect_watchlist_instruments",
    ]
    assert [message.kwargs for message in sent] == [
        {"codes": ["sh600036"]},
        {"codes": ["159915"]},
    ]


@pytest.mark.asyncio
async def test_news_dispatch_uses_stable_workflow_id(monkeypatch) -> None:
    sent = []

    class FakeJettask:
        def __init__(self, **_kwargs):
            pass

        async def send(self, messages):
            sent.extend(messages)
            return ["news:event:1"]

        def close(self):
            return None

    monkeypatch.setattr(dispatcher, "Jettask", FakeJettask)
    event_ids = await dispatcher.send_kg_news_ingest([102, 101, 102])

    assert event_ids == ["news:event:1"]
    assert sent[0].queue == "kg_news_ingest"
    assert sent[0].kwargs == {
        "news_ids": [102, 101],
        "workflow_id": dispatcher.build_kg_news_workflow_id([102, 101]),
    }
    assert dispatcher.build_kg_news_workflow_id([102, 101]) == (
        dispatcher.build_kg_news_workflow_id([101, 102, 101])
    )


@pytest.mark.asyncio
async def test_dispatch_retries_transient_send_failure(monkeypatch) -> None:
    attempts = 0

    class FakeJettask:
        def __init__(self, **_kwargs):
            pass

        async def send(self, _messages):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("temporary redis error")
            return ["event:retry"]

        def close(self):
            return None

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(dispatcher, "Jettask", FakeJettask)
    monkeypatch.setattr(dispatcher.asyncio, "sleep", no_sleep)

    event_ids = await dispatcher.send_kg_relation_discovery(["card:1"])

    assert event_ids == ["event:retry"]
    assert attempts == 3


@pytest.mark.asyncio
async def test_graph_changed_message_contains_stable_edge_event(monkeypatch) -> None:
    sent = []

    class FakeJettask:
        def __init__(self, **_kwargs):
            pass

        async def send(self, messages):
            sent.extend(messages)
            return ["graph:event:1"]

        def close(self):
            return None

    monkeypatch.setattr(dispatcher, "Jettask", FakeJettask)
    event_ids = await dispatcher.send_kg_graph_changed(
        adapter_name="financial",
        changed_edge_ids=["edge:1", "edge:1"],
        affected_card_ids=["card:1", "card:2"],
        changes={"upserted_edge_ids": ["edge:1"], "invalidated_edge_ids": []},
        event_identity="kg_graph_change:stable",
    )

    assert event_ids == ["graph:event:1"]
    assert sent[0].queue == "kg_graph_changed"
    assert sent[0].kwargs == {
        "adapter_name": "financial",
        "changed_edge_ids": ["edge:1"],
        "affected_card_ids": ["card:1", "card:2"],
        "changes": {"upserted_edge_ids": ["edge:1"], "invalidated_edge_ids": []},
        "event_identity": "kg_graph_change:stable",
    }


@pytest.mark.asyncio
async def test_news_ingestion_dispatches_cards_after_compile(monkeypatch) -> None:
    class FakeKnowledgeService:
        async def compile_kg(self, _command):
            return SimpleNamespace(
                evidence=1,
                failed_records=0,
                index_refresh={
                    "cognitive_index": {
                        "status": "cards_ready",
                        "card_ids": ["card:1", "card:2"],
                        "graph_persistence": {
                            "relations": 2,
                            "observed": 1,
                            "inferred": 1,
                            "changed_edge_ids": ["edge:local"],
                            "graph_event_ids": ["event:local"],
                        },
                    }
                },
            )

    dispatched = []

    async def fake_dispatch(card_ids, *, workflow_id=""):
        dispatched.append(
            {
                "card_ids": list(card_ids),
                "workflow_id": workflow_id,
            }
        )
        return ["relation:event"]

    monkeypatch.setattr(
        ingestion_module,
        "_records_from_ft_news_ids",
        lambda ids, **_kwargs: ([{"source_type": "test", "payload": {"id": ids[0]}}], []),
    )
    monkeypatch.setattr(ingestion_module, "send_kg_relation_discovery", fake_dispatch)
    service = KnowledgeNewsIngestionService(knowledge_service=FakeKnowledgeService())

    result = await service._compile_news_ids(
        [101],
        asyncio.Event(),
        workflow_id="workflow:test",
    )

    assert dispatched == [
        {
            "card_ids": ["card:1", "card:2"],
            "workflow_id": "workflow:test",
        }
    ]
    assert result["relation_card_ids"] == ["card:1", "card:2"]
    assert result["relation_event_ids"] == ["relation:event"]
    assert result["workflow_id"] == "workflow:test"
    assert result["intra_chunk_relations"] == 2
    assert result["intra_chunk_observed"] == 1
    assert result["intra_chunk_inferred"] == 1
    assert result["intra_chunk_changed_edge_ids"] == ["edge:local"]
    assert result["intra_chunk_graph_event_ids"] == ["event:local"]


@pytest.mark.asyncio
async def test_news_ingestion_raises_when_compile_is_partial(monkeypatch) -> None:
    class FakeKnowledgeService:
        async def compile_kg(self, _command):
            return SimpleNamespace(
                evidence=0,
                failed_records=1,
                failures=[
                    {
                        "source_id": "ft_news:101",
                        "reason": "Milvus unavailable",
                    }
                ],
                index_refresh={},
            )

    dispatched = []

    async def fake_dispatch(card_ids, *, workflow_id=""):
        dispatched.append((list(card_ids), workflow_id))
        return []

    monkeypatch.setattr(
        ingestion_module,
        "_records_from_ft_news_ids",
        lambda ids, **_kwargs: ([{"source_type": "test", "payload": {"id": ids[0]}}], []),
    )
    monkeypatch.setattr(ingestion_module, "send_kg_relation_discovery", fake_dispatch)
    service = KnowledgeNewsIngestionService(knowledge_service=FakeKnowledgeService())

    with pytest.raises(RuntimeError, match="必须由 Jettask 幂等重试"):
        await service._compile_news_ids(
            [101],
            asyncio.Event(),
            workflow_id="workflow:partial",
        )

    assert dispatched == []


@pytest.mark.asyncio
async def test_news_compile_only_returns_cards_without_dispatch(monkeypatch) -> None:
    class FakeKnowledgeService:
        async def compile_kg(self, _command):
            return SimpleNamespace(
                evidence=1,
                failed_records=0,
                index_refresh={"cognitive_index": {"card_ids": ["card:1"]}},
            )

    async def fail_dispatch(_card_ids, **_kwargs):
        raise AssertionError("同步工作流不应额外投递 kg_relation_discovery")

    monkeypatch.setattr(
        ingestion_module,
        "_records_from_ft_news_ids",
        lambda ids, **_kwargs: ([{"source_type": "test", "payload": {"id": ids[0]}}], []),
    )
    monkeypatch.setattr(ingestion_module, "send_kg_relation_discovery", fail_dispatch)
    service = KnowledgeNewsIngestionService(knowledge_service=FakeKnowledgeService())

    result = await service._compile_news_ids(
        [101],
        asyncio.Event(),
        dispatch_relation_tasks=False,
    )

    assert result["relation_card_ids"] == ["card:1"]
    assert result["relation_event_ids"] == []
    assert result["relation_dispatch_skipped"] is True
