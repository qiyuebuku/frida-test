"""Semantic candidate provider tests for write-time normalization."""

from __future__ import annotations

import pytest

from src.application.services.normalization_semantic_candidates import MilvusNormalizationCandidateProvider
from src.domain.knowledge.semantic_index_materials import SEMANTIC_COLLECTION_ENTITY, SEMANTIC_COLLECTION_RELATION
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridHit


class _Store:
    def __init__(self) -> None:
        self.calls = []

    def hybrid_search(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["collection_role"] == SEMANTIC_COLLECTION_RELATION:
            return [
                MilvusHybridHit(
                    chunk_id="kg_card:edge:1",
                    evidence_id="ev:1",
                    text="Document Type: Edge Card\nEdge Key: 政策 --affects--> 半导体",
                    score=0.77,
                    metadata={
                        "target_id": "kg_card:edge:1",
                        "source_type": "kg_edge_card",
                        "edge_id": "edge:1",
                        "relation_type": "affects",
                        "source_name": "政策",
                        "target_name": "半导体",
                    },
                )
            ]
        return [
            MilvusHybridHit(
                chunk_id="kg_card:node_card:1",
                evidence_id="ev:1",
                text="Document Type: Node Card\nNode Key: 高股息\nNode Type: concept",
                score=0.91,
                metadata={
                    "target_id": "kg_card:node_card:1",
                    "source_type": "kg_node_card",
                    "node_id": "node:dividend",
                    "canonical_name": "高股息",
                    "node_type": "concept",
                    "aliases": ["红利策略"],
                },
            )
        ]


@pytest.mark.asyncio
async def test_semantic_candidate_provider_searches_entity_collection(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    import src.application.services.normalization_semantic_candidates as module

    monkeypatch.setattr(module, "embed_texts", fake_embed_texts)
    store = _Store()
    provider = MilvusNormalizationCandidateProvider(
        adapter_name="financial",
        target="prod",
        store=store,  # type: ignore[arg-type]
    )

    candidates = await provider.search(query="红利策略", entity_type="concept", context="红利策略走强", limit=5)

    assert store.calls[0]["collection_role"] == SEMANTIC_COLLECTION_ENTITY
    assert candidates == [
        {
            "id": "node:dividend",
            "target_id": "kg_card:node_card:1",
            "canonical_name": "高股息",
            "entity_type": "concept",
            "aliases": ["红利策略"],
            "score": 0.91,
            "summary": "Document Type: Node Card\nNode Key: 高股息\nNode Type: concept",
        }
    ]


@pytest.mark.asyncio
async def test_semantic_candidate_provider_searches_relation_collection(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    import src.application.services.normalization_semantic_candidates as module

    monkeypatch.setattr(module, "embed_texts", fake_embed_texts)
    store = _Store()
    provider = MilvusNormalizationCandidateProvider(
        adapter_name="financial",
        target="prod",
        store=store,  # type: ignore[arg-type]
    )

    candidates = await provider.search_relations(query="政策影响半导体", relation_type="affects", limit=3)

    assert store.calls[0]["collection_role"] == SEMANTIC_COLLECTION_RELATION
    assert candidates[0]["id"] == "edge:1"
    assert candidates[0]["relation_type"] == "affects"
    assert candidates[0]["source_name"] == "政策"
    assert candidates[0]["target_name"] == "半导体"

