"""Tests for Milvus semantic hybrid reranking."""

from __future__ import annotations

import pytest

from src.application.services.graph_index_profiles import FINANCIAL_GRAPH_PROJECTIONS
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, NodeStatus
from src.domain.knowledge.retrieval import RetrievalOptions
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, EvidenceChunk
from src.infrastructure.vector_store import semantic_hybrid_retriever as retriever_module
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridHit
from src.infrastructure.vector_store.semantic_hybrid_retriever import (
    MilvusSemanticHybridRetriever,
    _candidate_limit,
    _expanded_query_text,
    _roles_for_target_ids,
    _strong_query_terms,
)
from src.domain.knowledge.atomic_cognitive_card import (
    AtomicCognitiveCard,
    atomic_card_summary_document,
)
from src.domain.knowledge.semantic_index_materials import (
    SEMANTIC_COLLECTION_CARD_RELATION,
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
)


class _Store:
    def __init__(self):
        self.calls = []
        self.documents = []
        self.documents_by_role = {}

    def ensure_ready(self):
        return None

    def hybrid_search(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("collection_role") != "chunk":
            return []
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

    def replace_documents_by_role(self, **kwargs):
        self.documents_by_role = kwargs["documents_by_role"]
        self.documents = [
            document
            for role in ("chunk", "cognitive_card", "entity", "relation", "community")
            for document in self.documents_by_role.get(role, [])
        ]
        self.calls.append(kwargs)
        return len(self.documents)

    def upsert_documents_by_role(self, **kwargs):
        self.documents_by_role = kwargs["documents_by_role"]
        self.documents = [
            document
            for role in ("chunk", "cognitive_card", "entity", "relation", "community")
            for document in self.documents_by_role.get(role, [])
        ]
        self.calls.append(kwargs)
        return len(self.documents)

    def delete_evidence(self, **kwargs):
        self.calls.append(kwargs)

    def delete_documents(self, **kwargs):
        self.calls.append(kwargs)

    def delete_scope(self, **kwargs):
        self.calls.append(kwargs)

    def get_documents(self, **kwargs):
        self.calls.append(kwargs)
        requested = set(kwargs["target_ids"])
        docs = [
            MilvusHybridHit(
                chunk_id="chunk:noise",
                evidence_id="ev:noise",
                text="Document Type: Evidence Chunk\nEvidence Text: 噪声",
                score=1.0,
                metadata={
                    "target_id": "chunk:noise",
                    "source_type": "kg_evidence_chunk",
                    "source_id": "ev:noise",
                },
            ),
            MilvusHybridHit(
                chunk_id="chunk:catl",
                evidence_id="ev:catl",
                text="Document Type: Evidence Chunk\nEvidence Text: 宁德时代 海外产能",
                score=1.0,
                metadata={
                    "target_id": "chunk:catl",
                    "source_type": "kg_evidence_chunk",
                    "source_id": "ev:catl",
                },
            ),
        ]
        return [doc for doc in docs if doc.target_id in requested]


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

    assert "300750" in terms
    assert "宁德时代" not in terms
    assert "最近" not in terms
    assert "哪些" not in terms


def test_expanded_query_text_keeps_raw_query_without_ngram_expansion() -> None:
    expanded = _expanded_query_text("宁德时代 300750 最近受哪些事件影响")

    assert expanded == "宁德时代 300750 最近受哪些事件影响"


def test_candidate_limit_overfetches_without_unbounded_growth() -> None:
    assert _candidate_limit(1) == 3
    assert _candidate_limit(10) == 30
    assert _candidate_limit(50) == 80


def test_roles_for_target_ids_routes_cognitive_community_ids_to_community_collection() -> None:
    assert _roles_for_target_ids(["kgc:financial:l0:3"]) == ("community",)
    assert _roles_for_target_ids(["kg_edge:financial:affects:catl"]) == (
        "chunk",
        "cognitive_card",
        "card_relation",
        "community",
        "community_insight",
    )
    assert _roles_for_target_ids(["kg_card_relation:abc123"]) == (
        SEMANTIC_COLLECTION_CARD_RELATION,
    )


@pytest.mark.asyncio
async def test_rebuild_index_writes_enriched_vector_documents(monkeypatch) -> None:
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
    event = CompiledNode(
        node_id="kg:financial:event:capacity",
        adapter_name="financial",
        node_type="event",
        canonical_name="海外产能扩张",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:catl",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=node.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
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
        nodes=[node, event],
        edges=[edge],
    )

    assert count == 4
    source_types = {doc.metadata["source_type"] for doc in store.documents}
    assert source_types == {"kg_evidence_chunk", "kg_node_card", "kg_event_card", "kg_edge_card"}
    evidence_doc = next(doc for doc in store.documents if doc.metadata["source_type"] == "kg_evidence_chunk")
    node_doc = next(doc for doc in store.documents if doc.metadata["source_type"] == "kg_node_card")
    edge_doc = next(doc for doc in store.documents if doc.metadata["source_type"] == "kg_edge_card")
    assert "Document Type: Evidence Chunk" in evidence_doc.text
    assert "Relation Preview:" in evidence_doc.text
    assert evidence_doc.text.index("Relation Preview:") < evidence_doc.text.index("Evidence Text:")
    assert "Document Type: Node Card" in node_doc.text
    assert "Relation Preview:" in node_doc.text
    assert node_doc.evidence_id == "kg_ev:financial:news:1"
    assert "300750" in node_doc.text
    assert "Document Type: Edge Card" in edge_doc.text
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
    )

    assert count == 1
    assert store.documents[0].chunk_id == "kg_card:node_card:kg:financial:stock:300750"
    assert store.documents[0].metadata["source_type"] == "kg_node_card"
    assert store.calls[-1]["documents_by_role"]["entity"] == store.documents


@pytest.mark.asyncio
async def test_upsert_index_filters_non_retrievable_facts(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)
    store = _Store()
    active_node = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    deprecated_node = CompiledNode(
        node_id="kg:financial:stock:000001",
        adapter_name="financial",
        node_type="stock",
        canonical_name="旧节点",
        status=NodeStatus.DEPRECATED,
        version="v1",
    )
    rejected_edge = CompiledEdge(
        edge_id="kg_edge:financial:related_to:rejected",
        adapter_name="financial",
        source_node_id=active_node.node_id,
        target_node_id=deprecated_node.node_id,
        relation_type="related_to",
        confidence_label=ConfidenceLabel.REJECTED,
        confidence_score=0.1,
        status=EdgeStatus.REVIEW_REQUIRED,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )

    count = await MilvusSemanticHybridRetriever(store=store).upsert_index(
        adapter_name="financial",
        target="prod",
        chunks=[
            EvidenceChunk(
                chunk_id="kg_chunk:kg_ev:financial:news:1:0",
                adapter_name="financial",
                evidence_id="kg_ev:financial:news:1",
                content="宁德时代 有效证据",
                payload={"status": "active"},
            ),
            EvidenceChunk(
                chunk_id="kg_chunk:kg_ev:financial:news:old:0",
                adapter_name="financial",
                evidence_id="kg_ev:financial:news:old",
                content="旧证据",
                payload={"status": "superseded"},
            ),
        ],
        nodes=[active_node, deprecated_node],
        edges=[rejected_edge],
    )

    assert count == 2
    source_types = [document.metadata["source_type"] for document in store.documents]
    assert source_types == ["kg_evidence_chunk", "kg_node_card"]
    assert all("旧节点" not in document.text for document in store.documents)


@pytest.mark.asyncio
async def test_upsert_index_writes_event_card_with_relation_preview(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)
    store = _Store()
    event = CompiledNode(
        node_id="kg:financial:event:capacity",
        adapter_name="financial",
        node_type="event",
        canonical_name="海外产能扩张",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    stock = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        aliases=["300750"],
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:capacity:catl",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=stock.node_id,
        relation_type="affects",
        properties={"direction": "negative"},
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )

    count = await MilvusSemanticHybridRetriever(store=store).upsert_index(
        adapter_name="financial",
        target="prod",
        chunks=[],
        nodes=[event, stock],
        edges=[edge],
    )

    assert count == 3
    event_doc = next(doc for doc in store.documents if doc.metadata["source_type"] == "kg_event_card")
    assert event_doc.chunk_id == "kg_card:event_card:kg:financial:event:capacity"
    assert event_doc.evidence_id == "kg_ev:financial:news:1"
    assert "Document Type: Event Card" in event_doc.text
    assert "海外产能扩张 --affects--> 宁德时代 direction=negative" in event_doc.text
    assert "Expandable Handles: node_id=kg:financial:event:capacity" in event_doc.text


@pytest.mark.asyncio
async def test_upsert_semantic_documents_writes_cognitive_card_collection(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)
    store = _Store()
    card = AtomicCognitiveCard(
        cognitive_card_id="kg_cognitive_card:test",
        adapter_name="financial",
        source_type="news_articles",
        source_id="ft_news:1",
        evidence_id="kg_ev:financial:news:1",
        primary_chunk_id="kg_chunk:kg_ev:financial:news:1:0",
        chunk_ids=["kg_chunk:kg_ev:financial:news:1:0"],
        chunk_index=0,
        summary="AI芯片供需紧张影响算力链。",
        focus_evidence_refs=["s0001"],
        focus_span_offsets=[{"ref": "s0001", "start_offset": 0, "end_offset": 14}],
        source_published_at="2026-04-23T00:00:00+00:00",
        source_title="AI芯片新闻",
    )

    count = await MilvusSemanticHybridRetriever(store=store).upsert_semantic_documents(
        adapter_name="financial",
        target="prod",
        documents=[atomic_card_summary_document(card)],
    )

    assert count == 1
    assert store.calls[-1]["documents_by_role"][SEMANTIC_COLLECTION_COGNITIVE_CARD][0].chunk_id == "kg_cognitive_card:test"
    document = store.calls[-1]["documents_by_role"][SEMANTIC_COLLECTION_COGNITIVE_CARD][0]
    assert document.metadata["source_type"] == "kg_cognitive_card"
    assert document.metadata["target_type"] == "atomic_cognitive_card_summary"
    assert document.text == "AI芯片供需紧张影响算力链。"


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
async def test_delete_documents_deletes_unique_chunk_ids() -> None:
    store = _Store()

    count = await MilvusSemanticHybridRetriever(store=store).delete_documents(
        adapter_name="financial",
        target="prod",
        chunk_ids=["chunk:1", "chunk:1", "", "chunk:2"],
    )

    assert count == 2
    assert store.calls[-1] == {
        "adapter_name": "financial",
        "target": "prod",
        "chunk_ids": ["chunk:1", "chunk:2"],
    }


@pytest.mark.asyncio
async def test_delete_scope_deletes_adapter_target_scope() -> None:
    store = _Store()

    result = await MilvusSemanticHybridRetriever(store=store).delete_scope(
        adapter_name="financial",
        target="prod",
    )

    assert result == {
        "adapter_name": "financial",
        "target": "prod",
        "scope": "adapter_target",
    }
    assert store.calls[-1] == {
        "adapter_name": "financial",
        "target": "prod",
    }


@pytest.mark.asyncio
async def test_get_by_ids_reads_milvus_targets_and_preserves_requested_order() -> None:
    store = _Store()

    hits = await MilvusSemanticHybridRetriever(store=store).get_by_ids(
        ["chunk:catl", "missing", "chunk:noise", "chunk:catl"],
        RetrievalOptions(adapter_name="financial", target="prod"),
    )

    assert [hit.hit_id for hit in hits] == ["chunk:catl", "chunk:noise"]
    assert [hit.hit_type for hit in hits] == ["evidence", "evidence"]
    assert hits[0].evidence_refs == ["ev:catl"]
    assert hits[0].snippet.endswith("宁德时代 海外产能")
    assert store.calls[-1] == {
        "collection_role": "chunk",
        "adapter_name": "financial",
        "target": "prod",
        "target_ids": ["chunk:catl", "missing", "chunk:noise"],
    }


@pytest.mark.asyncio
async def test_get_by_ids_routes_finding_targets_to_community_collection() -> None:
    store = _Store()

    await MilvusSemanticHybridRetriever(store=store).get_by_ids(
        ["kg_finding:financial:ai_supply_chain"],
        RetrievalOptions(adapter_name="financial", target="prod"),
    )

    assert store.calls[-1] == {
        "collection_role": "community",
        "adapter_name": "financial",
        "target": "prod",
        "target_ids": ["kg_finding:financial:ai_supply_chain"],
    }


@pytest.mark.asyncio
async def test_semantic_hybrid_search_skips_legacy_entity_and_relation_collections(monkeypatch) -> None:
    async def fake_embed_texts(_texts):
        return [[0.1, 0.2]]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)

    class Store(_Store):
        def hybrid_search(self, **kwargs):
            self.calls.append(kwargs)
            collection_role = kwargs.get("collection_role")
            if collection_role in {"entity", "relation"}:
                raise AssertionError("Agent semantic search must not query legacy collections")
            if collection_role == "chunk":
                return [
                    MilvusHybridHit(
                        chunk_id="kg_chunk:kg_ev:financial:news:1:0",
                        evidence_id="kg_ev:financial:news:1",
                        text="Document Type: Evidence Chunk\nEvidence Text: 宁德时代 海外产能",
                        score=0.75,
                        metadata={
                            "source_type": "kg_evidence_chunk",
                            "source_id": "kg_ev:financial:news:1",
                        },
                    )
                ]
            return []

    store = Store()
    hits = await MilvusSemanticHybridRetriever(store=store).search(
        "宁德时代 300750",
        RetrievalOptions(adapter_name="financial", target="prod", semantic_hybrid_limit=10),
    )

    hit_by_type = {hit.hit_type: hit for hit in hits}
    assert hit_by_type["evidence"].evidence_refs == ["kg_ev:financial:news:1"]
    assert {call["collection_role"] for call in store.calls if "collection_role" in call} == {
        "chunk",
        "cognitive_card",
        "card_relation",
        "community",
    }


@pytest.mark.asyncio
async def test_rebuild_index_writes_community_report_cards_for_relation_groups(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)
    store = _Store()
    event = CompiledNode(
        node_id="kg:financial:event:ai",
        adapter_name="financial",
        node_type="event",
        canonical_name="AI算力链叙事",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    semi = CompiledNode(
        node_id="kg:financial:industry:semi",
        adapter_name="financial",
        node_type="industry",
        canonical_name="半导体",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    optical = CompiledNode(
        node_id="kg:financial:industry:optical",
        adapter_name="financial",
        node_type="industry",
        canonical_name="光模块",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edges = [
        CompiledEdge(
            edge_id="kg_edge:financial:mentions:semi",
            adapter_name="financial",
            source_node_id=event.node_id,
            target_node_id=semi.node_id,
            relation_type="mentions",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=0.9,
            status=EdgeStatus.ACTIVE,
            evidence_ids=["kg_ev:financial:news:1"],
            version="v1",
        ),
        CompiledEdge(
            edge_id="kg_edge:financial:mentions:optical",
            adapter_name="financial",
            source_node_id=event.node_id,
            target_node_id=optical.node_id,
            relation_type="mentions",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=0.85,
            status=EdgeStatus.ACTIVE,
            evidence_ids=["kg_ev:financial:news:2"],
            version="v1",
        ),
        CompiledEdge(
            edge_id="kg_edge:financial:affects:semi",
            adapter_name="financial",
            source_node_id=event.node_id,
            target_node_id=semi.node_id,
            relation_type="affects",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=0.88,
            status=EdgeStatus.ACTIVE,
            evidence_ids=["kg_ev:financial:news:1"],
            properties={
                "topic_tags": ["AI算力链"],
                "domain_tags": ["半导体"],
                "narrative_tags": ["算力链产业联动"],
            },
            version="v1",
        ),
        CompiledEdge(
            edge_id="kg_edge:financial:affects:optical",
            adapter_name="financial",
            source_node_id=event.node_id,
            target_node_id=optical.node_id,
            relation_type="affects",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=0.88,
            status=EdgeStatus.ACTIVE,
            evidence_ids=["kg_ev:financial:news:2"],
            properties={
                "topic_tags": ["AI算力链"],
                "domain_tags": ["光模块"],
                "narrative_tags": ["算力链产业联动"],
            },
            version="v1",
        ),
        CompiledEdge(
            edge_id="kg_edge:financial:related_to:semi_optical",
            adapter_name="financial",
            source_node_id=semi.node_id,
            target_node_id=optical.node_id,
            relation_type="related_to",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=0.88,
            status=EdgeStatus.ACTIVE,
            evidence_ids=["kg_ev:financial:news:2"],
            properties={
                "topic_tags": ["AI算力链"],
                "domain_tags": ["半导体", "光模块"],
                "narrative_tags": ["算力链产业联动"],
            },
            version="v1",
        ),
    ]

    count = await MilvusSemanticHybridRetriever(store=store).rebuild_index(
        adapter_name="financial",
        target="prod",
        chunks=[
            EvidenceChunk(
                chunk_id="kg_chunk:kg_ev:financial:news:1:0",
                adapter_name="financial",
                evidence_id="kg_ev:financial:news:1",
                content="AI算力链提到半导体。",
                payload={"source_type": "news", "source_id": "source-a"},
            ),
            EvidenceChunk(
                chunk_id="kg_chunk:kg_ev:financial:news:2:0",
                adapter_name="financial",
                evidence_id="kg_ev:financial:news:2",
                content="AI算力链提到光模块。",
                payload={"source_type": "news", "source_id": "source-b"},
            ),
        ],
        nodes=[event, semi, optical],
        edges=edges,
        include_community=True,
        graph_projections=FINANCIAL_GRAPH_PROJECTIONS,
    )

    assert count == len(store.documents)
    community_docs = store.calls[-1]["documents_by_role"]["community"]
    assert community_docs
    reports = [doc for doc in community_docs if doc.metadata["source_type"] == "kg_community_report"]
    findings = [doc for doc in community_docs if doc.metadata["source_type"] == "kg_finding"]
    assert reports
    assert findings
    assert {doc.metadata["projection"] for doc in reports} == {"default_graph_projection"}
    assert any(
        doc.metadata["metrics"].get("projection_scores", {}).get("narrative", 0.0) > 0
        for doc in reports
    )
    assert any(
        doc.metadata["cited_evidence_ids"] == ["kg_ev:financial:news:1", "kg_ev:financial:news:2"]
        for doc in reports
    )
    assert all(doc.metadata["cited_chunk_ids"] for doc in community_docs)
    assert "Document Type: Community Report" in reports[-1].text
    assert "Projection:" in reports[-1].text
    assert "Document Type: Community Finding" in findings[0].text


@pytest.mark.asyncio
async def test_upsert_index_does_not_write_partial_community_documents(monkeypatch) -> None:
    async def fake_embed_texts(texts):
        return [[0.1, 0.2] for _ in texts]

    monkeypatch.setattr(retriever_module, "embed_texts", fake_embed_texts)
    store = _Store()
    event = CompiledNode(
        node_id="kg:financial:event:ai-chain",
        adapter_name="financial",
        node_type="event",
        canonical_name="AI算力链",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    semi = CompiledNode(
        node_id="kg:financial:industry:semi",
        adapter_name="financial",
        node_type="industry",
        canonical_name="半导体",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:mentions:semi",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=semi.node_id,
        relation_type="mentions",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )

    await MilvusSemanticHybridRetriever(store=store).upsert_index(
        adapter_name="financial",
        target="prod",
        chunks=[
            EvidenceChunk(
                chunk_id="kg_chunk:kg_ev:financial:news:1:0",
                adapter_name="financial",
                evidence_id="kg_ev:financial:news:1",
                content="AI算力链提到半导体。",
            )
        ],
        nodes=[event, semi],
        edges=[edge],
    )

    assert store.calls[-1]["documents_by_role"].get("community", []) == []
