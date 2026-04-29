"""Tests for Milvus semantic hybrid reranking."""

from __future__ import annotations

import pytest

from src.domain.knowledge.retrieval import RetrievalOptions
from src.infrastructure.vector_store import semantic_hybrid_retriever as retriever_module
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridHit
from src.infrastructure.vector_store.semantic_hybrid_retriever import (
    MilvusSemanticHybridRetriever,
    _strong_query_terms,
)


class _Store:
    def ensure_ready(self):
        return None

    def hybrid_search(self, **_kwargs):
        return [
            MilvusHybridHit(
                chunk_id="chunk:noise",
                evidence_id="ev:noise",
                text="sentiment guba_posts 37 sentiment.guba_posts 300502 SZ:300502",
                score=0.05,
                metadata={},
            ),
            MilvusHybridHit(
                chunk_id="chunk:catl",
                evidence_id="ev:catl",
                text="技术发布会简析 宁德时代 300750 SZ:300750",
                score=0.04,
                metadata={},
            ),
        ]


@pytest.mark.asyncio
async def test_semantic_hybrid_reranks_strong_identifier_matches(monkeypatch) -> None:
    async def fake_embed_texts(_texts):
        return [[0.1, 0.2]]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)

    hits = await MilvusSemanticHybridRetriever(store=_Store()).search(
        "宁德时代 300750 最近受哪些事件影响",
        RetrievalOptions(adapter_name="financial", target="prod", semantic_hybrid_limit=10),
    )

    assert hits[0].evidence_refs == ["ev:catl"]
    assert hits[0].score > hits[1].score


def test_strong_query_terms_ignore_generic_words() -> None:
    terms = _strong_query_terms("宁德时代 300750 最近受哪些事件影响")

    assert "宁德时代" in terms
    assert "300750" in terms
    assert "最近" not in terms
    assert "哪些" not in terms
