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
            return ["event:1"]

        def close(self):
            return None

    monkeypatch.setattr(dispatcher, "Jettask", FakeJettask)

    event_ids = await dispatcher.send_kg_relation_discovery(["card:1", "card:1", "card:2"])

    assert event_ids == ["event:1"]
    assert len(sent) == 1
    assert sent[0].queue == "kg_relation_discovery"
    assert sent[0].kwargs == {"card_ids": ["card:1", "card:2"]}


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

    async def fake_dispatch(card_ids):
        dispatched.append(list(card_ids))
        return ["relation:event"]

    monkeypatch.setattr(
        ingestion_module,
        "_records_from_ft_news_ids",
        lambda ids, **_kwargs: ([{"source_type": "test", "payload": {"id": ids[0]}}], []),
    )
    monkeypatch.setattr(ingestion_module, "send_kg_relation_discovery", fake_dispatch)
    service = KnowledgeNewsIngestionService(knowledge_service=FakeKnowledgeService())

    result = await service._compile_news_ids([101], asyncio.Event())

    assert dispatched == [["card:1", "card:2"]]
    assert result["relation_card_ids"] == ["card:1", "card:2"]
    assert result["relation_event_ids"] == ["relation:event"]
    assert result["intra_chunk_relations"] == 2
    assert result["intra_chunk_observed"] == 1
    assert result["intra_chunk_inferred"] == 1
    assert result["intra_chunk_changed_edge_ids"] == ["edge:local"]
    assert result["intra_chunk_graph_event_ids"] == ["event:local"]


@pytest.mark.asyncio
async def test_news_compile_only_returns_cards_without_dispatch(monkeypatch) -> None:
    class FakeKnowledgeService:
        async def compile_kg(self, _command):
            return SimpleNamespace(
                evidence=1,
                failed_records=0,
                index_refresh={"cognitive_index": {"card_ids": ["card:1"]}},
            )

    async def fail_dispatch(_card_ids):
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
