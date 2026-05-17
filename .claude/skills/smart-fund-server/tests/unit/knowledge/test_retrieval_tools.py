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


class _EnabledSemanticHybrid(SemanticHybridRetriever):
    enabled = True
    backend_name = "test"


class _LimitRecordingRuntime(HybridRetrievalRuntime):
    def __init__(self):
        super().__init__(_Repo(), semantic_retriever=_EnabledSemanticHybrid())
        self.keyword_limits: list[int | None] = []
        self.semantic_limits: list[int | None] = []

    def entity_resolve(self, query: str, options: RetrievalOptions, limit: int | None = None):
        return []

    def keyword_search(self, query: str, options: RetrievalOptions, limit: int | None = None):
        self.keyword_limits.append(limit)
        return [
            RetrievalHit(
                hit_id=f"kg:financial:event:{index}",
                hit_type="node",
                title=f"事件{index}",
                snippet=query,
                source="keyword",
                evidence_refs=[f"kg_ev:financial:news:{index}"],
            )
            for index in range(limit or 0)
        ]

    async def semantic_hybrid_search(
        self,
        query: str,
        options: RetrievalOptions,
        limit: int | None = None,
    ):
        self.semantic_limits.append(limit)
        return []


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


class _CandidateEdgeRepo(_Repo):
    candidate_edge = _Repo.edge.model_copy(
        update={
            "edge_id": "kg_edge:financial:affects:candidate",
            "status": EdgeStatus.CANDIDATE,
        }
    )

    def list_edges(self, adapter_name: str):
        return [self.candidate_edge]

    def get_edge(self, edge_id: str):
        return self.candidate_edge if edge_id == self.candidate_edge.edge_id else None


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
        "search",
        "find",
        "open",
        "summarize",
    )


@pytest.mark.asyncio
async def test_search_call_limit_expands_keyword_and_semantic_channel_limits() -> None:
    runtime = _LimitRecordingRuntime()
    registry = RetrievalToolRegistry(
        runtime,
        RetrievalOptions(
            adapter_name="financial",
            keyword_limit=5,
            semantic_hybrid_limit=7,
            max_hits=10,
        ),
    )

    result = await registry.execute(RetrievalToolCall(tool="search", query="并购重组", limit=60))

    assert runtime.keyword_limits == [60]
    assert runtime.semantic_limits == [60]
    assert len(result.hits) == 60


@pytest.mark.asyncio
async def test_tool_registry_search_packages_multi_channel_candidates() -> None:
    registry = _registry()

    result = await registry.execute(RetrievalToolCall(tool="search", query="宁德时代 300750"))

    assert result.step.tool == "search"
    assert any(hit.title == "宁德时代" for hit in result.hits)
    assert any(hit.edge_refs == ["kg_edge:financial:affects:1"] for hit in result.hits)
    assert any(hit.evidence_refs == ["kg_ev:financial:news:1"] for hit in result.hits)


@pytest.mark.asyncio
async def test_tool_registry_open_reads_evidence_window() -> None:
    registry = _registry()

    result = await registry.execute(
        RetrievalToolCall(tool="open", evidence_ids=["kg_ev:financial:news:1"])
    )

    assert result.step.tool == "open"
    assert result.hits[0].hit_type == "evidence"
    assert result.hits[0].evidence_refs == ["kg_ev:financial:news:1"]


@pytest.mark.asyncio
async def test_tool_registry_open_expands_candidate_ids_to_evidence() -> None:
    registry = _registry()

    node_result = await registry.execute(
        RetrievalToolCall(tool="open", candidate_ids=["kg:financial:stock:300750"])
    )
    edge_result = await registry.execute(
        RetrievalToolCall(tool="open", candidate_ids=["kg_edge:financial:affects:1"])
    )

    assert node_result.hits[0].evidence_refs == ["kg_ev:financial:news:1"]
    assert edge_result.hits[0].evidence_refs == ["kg_ev:financial:news:1"]


@pytest.mark.asyncio
async def test_tool_registry_find_searches_inside_known_evidence() -> None:
    registry = _registry()

    result = await registry.execute(
        RetrievalToolCall(
            tool="find",
            query="海外产能",
            evidence_ids=["kg_ev:financial:news:1"],
        )
    )

    assert result.step.tool == "find"
    assert result.hits[0].evidence_refs == ["kg_ev:financial:news:1"]


@pytest.mark.asyncio
async def test_tool_registry_summarize_is_controller_signal() -> None:
    registry = _registry()

    result = await registry.execute(RetrievalToolCall(tool="summarize"))

    assert result.hits == []
    assert result.summary == "summary_requested"


def test_tool_call_validates_required_inputs() -> None:
    with pytest.raises(ValueError, match="search requires query"):
        RetrievalToolCall(tool="search")
    with pytest.raises(ValueError, match="find requires query and evidence_ids"):
        RetrievalToolCall(tool="find", query="宁德时代")
    with pytest.raises(ValueError, match="open requires evidence_ids or candidate_ids"):
        RetrievalToolCall(tool="open")
