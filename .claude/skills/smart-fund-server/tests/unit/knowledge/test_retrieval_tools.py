"""Tests for the retrieval tool registry."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.retrieval import (
    HybridRetrievalRuntime,
    RetrievalHit,
    RetrievalOptions,
    SemanticHybridRetriever,
)
from src.domain.knowledge.retrieval_tools import RetrievalToolCall, RetrievalToolRegistry
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode


class _Repo:
    node = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        aliases=["300750"],
        status=NodeStatus.CANDIDATE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:1",
        adapter_name="financial",
        source_node_id="kg:financial:event:1",
        target_node_id="kg:financial:stock:300750",
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.8,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news:1",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="ft_news:1",
        content="宁德时代受海外产能事件影响。",
        version="v1",
    )

    def list_nodes(self, adapter_name: str):
        return [self.node]

    def list_edges(self, adapter_name: str):
        return [self.edge]

    def search_wiki_pages(self, adapter_name: str, query: str, limit: int = 20):
        return []

    def get_evidence(self, evidence_id: str):
        return self.evidence if evidence_id == self.evidence.evidence_id else None

    def get_node(self, node_id: str):
        return self.node if node_id == self.node.node_id else None

    def get_edge(self, edge_id: str):
        return self.edge if edge_id == self.edge.edge_id else None


class _SemanticHybrid(SemanticHybridRetriever):
    enabled = True
    backend_name = "milvus"

    async def search(self, query: str, options: RetrievalOptions):
        return [
            RetrievalHit(
                hit_id="kg_chunk:1",
                hit_type="semantic_hybrid",
                title="chunk",
                snippet="宁德时代 海外产能",
                source="semantic_hybrid",
                evidence_refs=["kg_ev:financial:news:1"],
            )
        ]


class _TimedRepo(_Repo):
    recent_edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:recent",
        adapter_name="financial",
        source_node_id="kg:financial:event:recent",
        target_node_id="kg:financial:stock:300750",
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        valid_from=datetime(2026, 4, 20, tzinfo=timezone.utc),
        valid_to=datetime(2026, 4, 21, tzinfo=timezone.utc),
        version="v1",
    )
    stale_edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:stale",
        adapter_name="financial",
        source_node_id="kg:financial:event:stale",
        target_node_id="kg:financial:stock:300750",
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.7,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2025, 1, 2, tzinfo=timezone.utc),
        version="v1",
    )

    def list_edges(self, adapter_name: str):
        return [self.recent_edge, self.stale_edge]


class _PathRepo(_Repo):
    event_to_stock = CompiledEdge(
        edge_id="kg_edge:financial:affects:event_stock",
        adapter_name="financial",
        source_node_id="kg:financial:event:1",
        target_node_id="kg:financial:stock:300750",
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.8,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )
    event_to_industry = CompiledEdge(
        edge_id="kg_edge:financial:mentions:event_industry",
        adapter_name="financial",
        source_node_id="kg:financial:event:1",
        target_node_id="kg:financial:industry:ev",
        relation_type="mentions",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.7,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )

    def list_edges(self, adapter_name: str):
        return [self.event_to_stock, self.event_to_industry]


def _registry() -> RetrievalToolRegistry:
    return RetrievalToolRegistry(
        HybridRetrievalRuntime(_Repo(), semantic_retriever=_SemanticHybrid()),
        RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5),
    )


def test_tool_registry_exposes_no_standalone_keyword_search() -> None:
    assert "keyword_search" not in RetrievalToolRegistry.available_tools
    assert RetrievalToolRegistry.available_tools == (
        "entity_resolve",
        "semantic_hybrid_search",
        "graph_search",
        "wiki_search",
        "chunk_read",
    )


@pytest.mark.asyncio
async def test_tool_registry_executes_entity_graph_and_chunk_tools() -> None:
    registry = _registry()

    entity_result = await registry.execute(
        RetrievalToolCall(tool="entity_resolve", query="宁德时代 300750")
    )
    graph_result = await registry.execute(
        RetrievalToolCall(
            tool="graph_search",
            seed_node_ids=entity_result.hits[0].node_refs,
            limit=5,
        )
    )
    chunk_result = await registry.execute(
        RetrievalToolCall(tool="chunk_read", evidence_ids=graph_result.hits[0].evidence_refs)
    )

    assert entity_result.step.tool == "entity_resolve"
    assert entity_result.hits[0].title == "宁德时代"
    assert graph_result.hits[0].edge_refs == ["kg_edge:financial:affects:1"]
    assert chunk_result.hits[0].evidence_refs == ["kg_ev:financial:news:1"]


@pytest.mark.asyncio
async def test_tool_registry_graph_search_supports_direction_and_relation_filters() -> None:
    registry = _registry()

    incoming = await registry.execute(
        RetrievalToolCall(
            tool="graph_search",
            seed_node_ids=["kg:financial:stock:300750"],
            direction="incoming",
            relation_filters=["affects"],
            limit=5,
        )
    )
    outgoing = await registry.execute(
        RetrievalToolCall(
            tool="graph_search",
            seed_node_ids=["kg:financial:stock:300750"],
            direction="outgoing",
            relation_filters=["affects"],
            limit=5,
        )
    )
    filtered = await registry.execute(
        RetrievalToolCall(
            tool="graph_search",
            seed_node_ids=["kg:financial:stock:300750"],
            direction="incoming",
            relation_filters=["mentions"],
            limit=5,
        )
    )

    assert incoming.hits[0].edge_refs == ["kg_edge:financial:affects:1"]
    assert outgoing.hits == []
    assert filtered.hits == []


@pytest.mark.asyncio
async def test_tool_registry_graph_search_supports_time_window() -> None:
    registry = RetrievalToolRegistry(
        HybridRetrievalRuntime(_TimedRepo(), semantic_retriever=_SemanticHybrid()),
        RetrievalOptions(adapter_name="financial"),
    )

    result = await registry.execute(
        RetrievalToolCall(
            tool="graph_search",
            seed_node_ids=["kg:financial:stock:300750"],
            direction="incoming",
            relation_filters=["affects"],
            time_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
            time_end=datetime(2026, 4, 30, tzinfo=timezone.utc),
            limit=5,
        )
    )

    assert [hit.edge_refs[0] for hit in result.hits] == [
        "kg_edge:financial:affects:recent"
    ]


@pytest.mark.asyncio
async def test_tool_registry_graph_search_can_return_path_hits() -> None:
    registry = RetrievalToolRegistry(
        HybridRetrievalRuntime(_PathRepo(), semantic_retriever=_SemanticHybrid()),
        RetrievalOptions(adapter_name="financial", graph_depth=2),
    )

    result = await registry.execute(
        RetrievalToolCall(
            tool="graph_search",
            seed_node_ids=["kg:financial:stock:300750"],
            direction="path",
            relation_filters=["affects", "mentions"],
            depth=2,
            limit=5,
        )
    )

    assert result.hits
    assert {hit.hit_type for hit in result.hits} == {"path"}
    assert any(
        hit.path_node_refs == [
            "kg:financial:stock:300750",
            "kg:financial:event:1",
            "kg:financial:industry:ev",
        ]
        and hit.path_edge_refs == [
            "kg_edge:financial:affects:event_stock",
            "kg_edge:financial:mentions:event_industry",
        ]
        for hit in result.hits
    )


@pytest.mark.asyncio
async def test_tool_registry_executes_semantic_hybrid_tool() -> None:
    result = await _registry().execute(
        RetrievalToolCall(tool="semantic_hybrid_search", query="海外产能影响")
    )

    assert result.step.tool == "semantic_hybrid_search"
    assert result.hits[0].hit_type == "semantic_hybrid"
    assert result.hits[0].evidence_refs == ["kg_ev:financial:news:1"]


def test_tool_call_validates_required_inputs() -> None:
    with pytest.raises(ValueError, match="graph_search requires seed_node_ids"):
        RetrievalToolCall(tool="graph_search")
