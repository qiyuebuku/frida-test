"""Tests for Milvus semantic hybrid reranking."""

from __future__ import annotations

import pytest

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, NodeStatus
from src.domain.knowledge.retrieval import RetrievalOptions
from src.domain.knowledge.retrieval_document import RetrievalDocument
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, EvidenceChunk
from src.domain.knowledge.wiki import WikiPage
from src.infrastructure.vector_store import semantic_hybrid_retriever as retriever_module
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridHit
from src.infrastructure.vector_store.semantic_hybrid_retriever import (
    MilvusSemanticHybridRetriever,
    _candidate_limit,
    _expanded_query_text,
    _strong_query_terms,
)


class _Store:
    def __init__(self):
        self.calls = []
        self.documents = []

    def ensure_ready(self):
        return None

    def hybrid_search(self, **kwargs):
        self.calls.append(kwargs)
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

    def replace_documents(self, **kwargs):
        self.documents = kwargs["documents"]
        self.calls.append(kwargs)
        return len(self.documents)

    def upsert_documents(self, **kwargs):
        self.documents = kwargs["documents"]
        self.calls.append(kwargs)
        return len(self.documents)

    def delete_evidence(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_semantic_hybrid_reranks_strong_identifier_matches(monkeypatch) -> None:
    async def fake_embed_texts(_texts):
        return [[0.1, 0.2]]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)

    store = _Store()

    hits = await MilvusSemanticHybridRetriever(store=store).search(
        "宁德时代 300750 最近受哪些事件影响",
        RetrievalOptions(adapter_name="financial", target="prod", semantic_hybrid_limit=10),
    )

    assert hits[0].evidence_refs == ["ev:catl"]
    assert hits[0].score > hits[1].score
    assert store.calls[0]["limit"] == 30
    assert "宁德时代" in store.calls[0]["query_text"]
    assert "300750" in store.calls[0]["query_text"]


def test_strong_query_terms_ignore_generic_words() -> None:
    terms = _strong_query_terms("宁德时代 300750 最近受哪些事件影响")

    assert "宁德时代" in terms
    assert "300750" in terms
    assert "最近" not in terms
    assert "哪些" not in terms


def test_expanded_query_text_dedupes_original_query_and_strong_terms() -> None:
    expanded = _expanded_query_text("宁德时代 300750 最近受哪些事件影响")

    assert expanded.startswith("宁德时代 300750 最近受哪些事件影响")
    assert expanded.count("300750") == 2
    assert "宁德时代" in expanded


def test_candidate_limit_overfetches_without_unbounded_growth() -> None:
    assert _candidate_limit(1) == 3
    assert _candidate_limit(10) == 30
    assert _candidate_limit(50) == 80


@pytest.mark.asyncio
async def test_rebuild_index_writes_lightrag_node_edge_wiki_documents(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)
    store = _Store()

    node = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        aliases=["300750"],
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:catl",
        adapter_name="financial",
        source_node_id="kg:financial:event:capacity",
        target_node_id=node.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )
    wiki = WikiPage(
        page_id="kg_wiki:financial:entity_page:catl",
        adapter_name="financial",
        page_type="entity_page",
        subject_type="stock",
        subject_id=node.node_id,
        title="宁德时代",
        summary="宁德时代 summary",
        content="宁德时代 wiki content",
        source_node_ids=[node.node_id],
        version="v1",
    )

    count = await MilvusSemanticHybridRetriever(store=store).rebuild_index(
        adapter_name="financial",
        target="prod",
        chunks=[
            EvidenceChunk(
                chunk_id="kg_chunk:kg_ev:financial:news:1:0",
                adapter_name="financial",
                evidence_id="kg_ev:financial:news:1",
                content="宁德时代 海外产能",
            )
        ],
        nodes=[node],
        edges=[edge],
        wiki_pages=[wiki],
    )

    assert count == 4
    source_types = {doc.metadata["source_type"] for doc in store.documents}
    assert source_types == {"kg_evidence", "kg_node", "kg_edge", "kg_wiki"}
    node_doc = next(doc for doc in store.documents if doc.metadata["source_type"] == "kg_node")
    edge_doc = next(doc for doc in store.documents if doc.metadata["source_type"] == "kg_edge")
    assert "300750" in node_doc.text
    assert "affects" in edge_doc.text


@pytest.mark.asyncio
async def test_upsert_index_only_writes_changed_lightrag_documents(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)
    store = _Store()
    node = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        aliases=["300750"],
        status=NodeStatus.ACTIVE,
        version="v1",
    )

    count = await MilvusSemanticHybridRetriever(store=store).upsert_index(
        adapter_name="financial",
        target="prod",
        chunks=[],
        nodes=[node],
        edges=[],
        wiki_pages=[],
    )

    assert count == 1
    assert store.documents[0].chunk_id == "kg_kv:node:kg:financial:stock:300750"
    assert store.calls[-1]["documents"] == store.documents


@pytest.mark.asyncio
async def test_upsert_index_prefers_retrieval_documents(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)
    store = _Store()
    retrieval_document = RetrievalDocument(
        document_id="kg_rdoc:prod:node:kg:financial:stock:300750",
        adapter_name="financial",
        target="prod",
        source_fact_type="node",
        source_fact_id="kg:financial:stock:300750",
        title="宁德时代",
        search_text="宁德时代 300750 海外产能扩张 affects 储能供应链",
        key_phrases=["宁德时代", "海外产能扩张"],
        aliases=["300750"],
        readable_relations=["宁德时代海外产能扩张 affects 宁德时代"],
        evidence_refs=["kg_ev:financial:news:1"],
        answer_candidate_type="support",
    )

    count = await MilvusSemanticHybridRetriever(store=store).upsert_index(
        adapter_name="financial",
        target="prod",
        chunks=[],
        nodes=[],
        edges=[],
        wiki_pages=[],
        retrieval_documents=[retrieval_document],
    )

    assert count == 1
    assert store.documents[0].chunk_id == retrieval_document.document_id
    assert store.documents[0].metadata == {
        "source_type": "kg_retrieval_node",
        "source_id": "kg:financial:stock:300750",
    }
    assert "Retrieval Key: 宁德时代" in store.documents[0].text
    assert "宁德时代海外产能扩张 affects 宁德时代" in store.documents[0].text


@pytest.mark.asyncio
async def test_delete_evidence_deletes_unique_evidence_ids() -> None:
    store = _Store()

    count = await MilvusSemanticHybridRetriever(store=store).delete_evidence(
        adapter_name="financial",
        target="prod",
        evidence_ids=["ev:1", "ev:1", "", "ev:2"],
    )

    assert count == 2
    assert store.calls[-1] == {
        "adapter_name": "financial",
        "target": "prod",
        "evidence_ids": ["ev:1", "ev:2"],
    }


@pytest.mark.asyncio
async def test_semantic_hybrid_maps_lightrag_hits_to_graph_refs(monkeypatch) -> None:
    async def fake_embed_texts(_texts):
        return [[0.1, 0.2]]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)

    class Store(_Store):
        def hybrid_search(self, **kwargs):
            return [
                MilvusHybridHit(
                    chunk_id="kg_kv:node:kg:financial:stock:300750",
                    evidence_id="",
                    text="Node Key: 宁德时代\nAliases: 300750",
                    score=0.9,
                    metadata={
                        "source_type": "kg_node",
                        "source_id": "kg:financial:stock:300750",
                    },
                ),
                MilvusHybridHit(
                    chunk_id="kg_kv:edge:kg_edge:financial:affects:catl",
                    evidence_id="kg_ev:financial:news:1",
                    text="Edge Key: 海外产能 affects 宁德时代",
                    score=0.8,
                    metadata={
                        "source_type": "kg_edge",
                        "source_id": "kg_edge:financial:affects:catl",
                    },
                ),
                MilvusHybridHit(
                    chunk_id="kg_kv:wiki:kg_wiki:financial:entity_page:catl",
                    evidence_id="",
                    text="Wiki Key: 宁德时代",
                    score=0.7,
                    metadata={
                        "source_type": "kg_wiki",
                        "source_id": "kg_wiki:financial:entity_page:catl",
                    },
                ),
            ]

    hits = await MilvusSemanticHybridRetriever(store=Store()).search(
        "宁德时代 300750",
        RetrievalOptions(adapter_name="financial", target="prod", semantic_hybrid_limit=10),
    )

    assert hits[0].hit_type == "node"
    assert hits[0].node_refs == ["kg:financial:stock:300750"]
    assert hits[1].hit_type == "edge"
    assert hits[1].edge_refs == ["kg_edge:financial:affects:catl"]
    assert hits[1].evidence_refs == ["kg_ev:financial:news:1"]
    assert hits[2].hit_type == "wiki"


@pytest.mark.asyncio
async def test_semantic_hybrid_maps_retrieval_document_hits_to_fact_refs(monkeypatch) -> None:
    async def fake_embed_texts(_texts):
        return [[0.1, 0.2]]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)

    class Store(_Store):
        def hybrid_search(self, **kwargs):
            return [
                MilvusHybridHit(
                    chunk_id="kg_rdoc:prod:node:kg:financial:stock:300750",
                    evidence_id="kg_ev:financial:news:1",
                    text="Retrieval Key: 宁德时代\nValue: 宁德时代 300750 海外产能",
                    score=0.9,
                    metadata={
                        "source_type": "kg_retrieval_node",
                        "source_id": "kg:financial:stock:300750",
                    },
                )
            ]

    hits = await MilvusSemanticHybridRetriever(store=Store()).search(
        "宁德时代 300750",
        RetrievalOptions(adapter_name="financial", target="prod", semantic_hybrid_limit=10),
    )

    assert hits[0].hit_id == "kg:financial:stock:300750"
    assert hits[0].hit_type == "node"
    assert hits[0].node_refs == ["kg:financial:stock:300750"]
    assert hits[0].evidence_refs == ["kg_ev:financial:news:1"]
    assert hits[0].matched_fields == ["retrieval_document.search_text"]
