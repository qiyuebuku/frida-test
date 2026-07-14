"""ft_news 到正式 Card Relation 同步工作流测试。"""

from __future__ import annotations

import pytest

from src.application.services.ft_news_knowledge_graph_workflow_service import (
    FtNewsKnowledgeGraphWorkflowService,
)


class _IngestionService:
    def __init__(self) -> None:
        self.calls: list[list[int]] = []

    async def compile_ft_news_ids(self, news_ids: list[int]) -> dict:
        self.calls.append(list(news_ids))
        return {
            "compiled_evidence": 2,
            "relation_card_ids": ["card:1", "card:2"],
            "relation_event_ids": [],
            "relation_dispatch_skipped": True,
            "intra_chunk_relations": 2,
            "intra_chunk_observed": 1,
            "intra_chunk_inferred": 1,
            "intra_chunk_changed_edge_ids": ["edge:local"],
            "intra_chunk_graph_event_ids": ["event:local"],
        }


class _RelationDiscoveryService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def discover_card_relations(self, card_ids: list[str], **kwargs) -> dict:
        self.calls.append({"card_ids": list(card_ids), **kwargs})
        return {
            "observed": 1,
            "inferred": 0,
            "no_relation": 1,
            "edge_persistence": {"changed_edge_ids": ["edge:1"]},
        }


@pytest.mark.asyncio
async def test_workflow_compiles_then_runs_relation_discovery_synchronously() -> None:
    ingestion = _IngestionService()
    relation = _RelationDiscoveryService()
    service = FtNewsKnowledgeGraphWorkflowService(
        target="test",
        ingestion_service=ingestion,
        relation_discovery_service=relation,
    )

    result = await service.run(
        [102, 101, 102],
        include_evaluation_details=True,
    )

    assert ingestion.calls == [[102, 101]]
    assert relation.calls == [
        {
            "card_ids": ["card:1", "card:2"],
            "adapter_name": "financial",
            "target": "test",
            "include_evaluation_details": True,
            "persist_edges": True,
        }
    ]
    assert result["status"] == "completed"
    assert result["relation_discovery"]["edge_persistence"]["changed_edge_ids"] == [
        "edge:1"
    ]
    assert result["edge_persistence"] == {
        "changed_edge_ids": ["edge:local", "edge:1"],
        "graph_event_ids": ["event:local"],
        "intra_chunk_changed_edge_ids": ["edge:local"],
        "cross_chunk_changed_edge_ids": ["edge:1"],
    }
    assert result["relation_statistics"] == {
        "intra_chunk": {
            "observed": 1,
            "inferred": 1,
            "positive_relations": 2,
        },
        "cross_chunk": {
            "observed": 1,
            "inferred": 0,
            "no_relation": 1,
            "positive_relations": 1,
        },
        "total": {
            "observed": 2,
            "inferred": 1,
            "positive_relations": 3,
        },
    }
