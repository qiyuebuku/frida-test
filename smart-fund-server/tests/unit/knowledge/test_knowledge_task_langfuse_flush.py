from __future__ import annotations

import pytest

from src.interfaces.tasks import knowledge_tasks


@pytest.mark.asyncio
async def test_knowledge_tasks_flush_langfuse_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    flushes: list[None] = []

    class Ingestion:
        async def ingest_ft_news_ids(self, news_ids, *, workflow_id):
            return {"news_ids": news_ids, "workflow_id": workflow_id}

    class Discovery:
        async def discover_card_relations(self, card_ids, *, workflow_id):
            return {"card_ids": card_ids, "workflow_id": workflow_id}

    class Community:
        async def refresh_from_graph_change(self, **kwargs):
            return kwargs

    monkeypatch.setattr(knowledge_tasks, "_knowledge_ingestion", Ingestion())
    monkeypatch.setattr(knowledge_tasks, "_relation_discovery", Discovery())
    monkeypatch.setattr(knowledge_tasks, "_relation_graph_community", Community())
    monkeypatch.setattr(knowledge_tasks, "langfuse_flush", lambda: flushes.append(None))

    await knowledge_tasks.kg_news_ingest([1], workflow_id="workflow:1")
    await knowledge_tasks.kg_relation_discovery(["card:1"], workflow_id="workflow:1")
    await knowledge_tasks.kg_graph_community_refresh(
        "financial",
        changed_edge_ids=["edge:1"],
        workflow_id="workflow:1",
    )

    assert len(flushes) == 3


@pytest.mark.asyncio
async def test_kg_news_ingest_flushes_langfuse_after_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    flushes: list[None] = []

    class FailingIngestion:
        async def ingest_ft_news_ids(self, news_ids, *, workflow_id):
            raise RuntimeError("failed")

    monkeypatch.setattr(knowledge_tasks, "_knowledge_ingestion", FailingIngestion())
    monkeypatch.setattr(knowledge_tasks, "langfuse_flush", lambda: flushes.append(None))

    with pytest.raises(RuntimeError, match="failed"):
        await knowledge_tasks.kg_news_ingest([1], workflow_id="workflow:1")

    assert len(flushes) == 1
