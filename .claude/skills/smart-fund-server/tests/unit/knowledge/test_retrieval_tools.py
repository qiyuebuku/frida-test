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
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode, EvidenceChunk


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

    def list_evidence(self, adapter_name: str):
        return [self.evidence]

    def list_evidence_chunks(self, adapter_name: str):
        return [
            EvidenceChunk(
                chunk_id=f"kg_chunk:{self.evidence.evidence_id}:0",
                adapter_name=self.evidence.adapter_name,
                evidence_id=self.evidence.evidence_id,
                content="证据分片：宁德时代受海外产能事件影响。",
            )
        ]

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

    async def get_by_ids(self, target_ids: list[str], options: RetrievalOptions):
        return []


class _FindingSemanticHybrid(_SemanticHybrid):
    async def get_by_ids(self, target_ids: list[str], options: RetrievalOptions):
        if "kg_finding:financial:ai_supply_chain" not in target_ids:
            return []
        return [
            RetrievalHit(
                hit_id="kg_finding:financial:ai_supply_chain",
                hit_type="semantic_hybrid",
                title="community:AI算力链叙事",
                snippet="AI算力链相关发现，引用宁德时代海外产能证据。",
                source="semantic_hybrid",
                source_channels=["semantic_hybrid"],
                node_refs=["kg:financial:stock:300750"],
                evidence_refs=["kg_ev:financial:news:1"],
                matched_fields=["milvus.community"],
            )
        ]


class _PreciseChunkRepo(_Repo):
    def list_evidence_chunks(self, adapter_name: str):
        return [
            EvidenceChunk(
                chunk_id=f"kg_chunk:{self.evidence.evidence_id}:0",
                adapter_name=self.evidence.adapter_name,
                evidence_id=self.evidence.evidence_id,
                content="第一段：宏观背景，不是 finding 引用重点。",
                chunk_index=0,
            ),
            EvidenceChunk(
                chunk_id=f"kg_chunk:{self.evidence.evidence_id}:1",
                adapter_name=self.evidence.adapter_name,
                evidence_id=self.evidence.evidence_id,
                content="第二段：AI算力链 finding 引用的核心证据。",
                chunk_index=1,
            ),
        ]


class _PreciseChunkSemanticHybrid(_SemanticHybrid):
    def __init__(self) -> None:
        self.get_by_ids_calls: list[list[str]] = []

    async def search(self, query: str, options: RetrievalOptions):
        return [
            RetrievalHit(
                hit_id="kg_finding:financial:ai_supply_chain",
                hit_type="semantic_hybrid",
                title="community finding",
                snippet="AI算力链 finding",
                source="semantic_hybrid",
                chunk_refs=["kg_chunk:kg_ev:financial:news:1:1"],
                evidence_refs=["kg_ev:financial:news:1"],
            )
        ]

    async def get_by_ids(self, target_ids: list[str], options: RetrievalOptions):
        self.get_by_ids_calls.append(list(target_ids))
        if target_ids == ["kg_chunk:kg_ev:financial:news:1:1"]:
            return [
                RetrievalHit(
                    hit_id="kg_chunk:kg_ev:financial:news:1:1",
                    hit_type="evidence",
                    title="精确 chunk",
                    snippet="第二段：AI算力链 finding 引用的核心证据。",
                    source="semantic_hybrid",
                    chunk_refs=["kg_chunk:kg_ev:financial:news:1:1"],
                    evidence_refs=["kg_ev:financial:news:1"],
                )
            ]
        return []


class _Reranker:
    async def rerank(self, *, query: str, documents: list[str], top_n: int | None = None):
        count = min(len(documents), top_n or len(documents))
        return type(
            "RerankResponse",
            (),
            {
                "model": "fake-reranker",
                "latency_ms": 1.0,
                "total_documents": len(documents),
                "results": [
                    type(
                        "RerankResult",
                        (),
                        {
                            "index": index,
                            "relevance_score": 1.0 - index * 0.1,
                        },
                    )()
                    for index in range(count)
                ],
            },
        )()


class _EnabledSemanticHybrid(SemanticHybridRetriever):
    enabled = True
    backend_name = "test"


class _LimitRecordingRuntime(HybridRetrievalRuntime):
    def __init__(self):
        super().__init__(_Repo(), semantic_retriever=_EnabledSemanticHybrid())
        self.deterministic_limits: list[int | None] = []
        self.semantic_limits: list[int | None] = []
        self.semantic_queries: list[str] = []

    def entity_resolve(self, query: str, options: RetrievalOptions, limit: int | None = None):
        return []

    def pg_deterministic_search(self, query: str, options: RetrievalOptions, limit: int | None = None):
        self.deterministic_limits.append(limit)
        return [
            RetrievalHit(
                hit_id=f"kg:financial:event:{index}",
                hit_type="node",
                title=f"事件{index}",
                snippet=query,
                source="pg_deterministic",
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
        self.semantic_queries.append(query)
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


class _RelationScoreRepo(_Repo):
    event = CompiledNode(
        node_id="kg:financial:event:ma",
        adapter_name="financial",
        node_type="event",
        canonical_name="A股并购重组市场呈现三方面新变化",
        status=NodeStatus.CANDIDATE,
        version="v1",
    )
    merger = CompiledNode(
        node_id="kg:financial:concept:merger",
        adapter_name="financial",
        node_type="concept",
        canonical_name="并购重组",
        status=NodeStatus.CANDIDATE,
        version="v1",
    )
    semiconductor = CompiledNode(
        node_id="kg:financial:industry:semi",
        adapter_name="financial",
        node_type="industry",
        canonical_name="半导体",
        status=NodeStatus.CANDIDATE,
        version="v1",
    )
    star_policy = CompiledNode(
        node_id="kg:financial:policy:star",
        adapter_name="financial",
        node_type="policy",
        canonical_name="科创板八条",
        status=NodeStatus.CANDIDATE,
        version="v1",
    )
    mention_edge = CompiledEdge(
        edge_id="kg_edge:financial:mentions:ma_merger",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=merger.node_id,
        relation_type="mentions",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.8,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:ma"],
        version="v1",
    )
    benefit_edge = CompiledEdge(
        edge_id="kg_edge:financial:benefits_from:semi_star",
        adapter_name="financial",
        source_node_id=semiconductor.node_id,
        target_node_id=star_policy.node_id,
        relation_type="benefits_from",
        confidence_label=ConfidenceLabel.INFERRED,
        confidence_score=0.7,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:ma"],
        version="v1",
    )
    affects_edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:event_merger",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=merger.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.INFERRED,
        confidence_score=0.7,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:ma"],
        version="v1",
    )
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news:ma",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="ft_news:83904",
        content=(
            "A股并购重组市场呈现三方面新变化。"
            "新闻涉及主体、行业和资产影响，包括半导体、科创板八条、并购重组。"
        ),
        version="v1",
    )

    def list_nodes(self, adapter_name: str):
        return [self.event, self.merger, self.semiconductor, self.star_policy]

    def list_edges(self, adapter_name: str):
        return [self.mention_edge, self.benefit_edge, self.affects_edge]

    def list_evidence(self, adapter_name: str):
        return [self.evidence]


class _NeighborChunkRepo(_Repo):
    def list_evidence_chunks(self, adapter_name: str):
        return [
            EvidenceChunk(
                chunk_id=f"kg_chunk:{self.evidence.evidence_id}:0",
                adapter_name=self.evidence.adapter_name,
                evidence_id=self.evidence.evidence_id,
                content="上文：海外产能规划。",
                chunk_index=0,
                next_chunk_id=f"kg_chunk:{self.evidence.evidence_id}:1",
            ),
            EvidenceChunk(
                chunk_id=f"kg_chunk:{self.evidence.evidence_id}:1",
                adapter_name=self.evidence.adapter_name,
                evidence_id=self.evidence.evidence_id,
                content="正文：宁德时代受海外产能事件影响。",
                chunk_index=1,
                previous_chunk_id=f"kg_chunk:{self.evidence.evidence_id}:0",
                next_chunk_id=f"kg_chunk:{self.evidence.evidence_id}:2",
            ),
            EvidenceChunk(
                chunk_id=f"kg_chunk:{self.evidence.evidence_id}:2",
                adapter_name=self.evidence.adapter_name,
                evidence_id=self.evidence.evidence_id,
                content="下文：市场关注兑现节奏。",
                chunk_index=2,
                previous_chunk_id=f"kg_chunk:{self.evidence.evidence_id}:1",
            ),
        ]


def _registry() -> RetrievalToolRegistry:
    return RetrievalToolRegistry(
        HybridRetrievalRuntime(_Repo(), semantic_retriever=_SemanticHybrid()),
        RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5),
        reranker_client=_Reranker(),
    )


def test_pg_deterministic_edge_score_uses_field_and_relation_intent_weights() -> None:
    runtime = HybridRetrievalRuntime(_RelationScoreRepo(), semantic_retriever=_EnabledSemanticHybrid())
    hits = runtime.pg_deterministic_search(
        "A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
        RetrievalOptions(adapter_name="financial"),
        limit=20,
    )

    by_id = {hit.hit_id: hit for hit in hits}
    mention_score = by_id["kg_edge:financial:mentions:ma_merger"].score
    benefit_score = by_id["kg_edge:financial:benefits_from:semi_star"].score
    affects_score = by_id["kg_edge:financial:affects:event_merger"].score

    assert len({mention_score, benefit_score, affects_score}) == 3
    assert mention_score > benefit_score
    assert affects_score > benefit_score


def test_pg_deterministic_hits_use_readable_node_and_edge_snippets() -> None:
    runtime = HybridRetrievalRuntime(_RelationScoreRepo(), semantic_retriever=_EnabledSemanticHybrid())
    hits = runtime.pg_deterministic_search(
        "A股并购重组市场呈现三方面新变化 半导体",
        RetrievalOptions(adapter_name="financial"),
        limit=20,
    )
    by_id = {hit.hit_id: hit for hit in hits}

    assert "节点事实: A股并购重组市场呈现三方面新变化" in by_id[_RelationScoreRepo.event.node_id].snippet
    assert "{\"aliases\"" not in by_id[_RelationScoreRepo.event.node_id].snippet
    edge_snippet = by_id["kg_edge:financial:mentions:ma_merger"].snippet
    assert "关系事实: A股并购重组市场呈现三方面新变化（event） --mentions--> 并购重组（concept）" in edge_snippet
    assert "关系焦点: 并购重组；焦点类型: concept" in edge_snippet
    assert "证据分片:" in edge_snippet
    assert "命中目标=" not in edge_snippet


def test_tool_registry_exposes_no_standalone_keyword_search() -> None:
    assert "keyword_search" not in RetrievalToolRegistry.available_tools
    assert RetrievalToolRegistry.available_tools == (
        "search",
        "scoped_search",
        "find",
        "open",
        "expand",
        "summarize",
        "rerank",
    )


@pytest.mark.asyncio
async def test_search_call_limit_expands_pg_and_semantic_channel_limits() -> None:
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

    assert runtime.deterministic_limits == []
    assert runtime.semantic_limits == [60]


@pytest.mark.asyncio
async def test_search_projects_community_hit_by_cited_chunk_refs_before_evidence() -> None:
    semantic = _PreciseChunkSemanticHybrid()
    registry = RetrievalToolRegistry(
        HybridRetrievalRuntime(_PreciseChunkRepo(), semantic_retriever=semantic),
        RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5, keyword_limit=0, max_hits=5),
        reranker_client=_Reranker(),
    )

    result = await registry.execute(RetrievalToolCall(tool="search", query="AI算力链 finding", limit=5))

    assert [hit.hit_id for hit in result.hits] == ["kg_chunk:kg_ev:financial:news:1:1"]
    assert semantic.get_by_ids_calls == [["kg_chunk:kg_ev:financial:news:1:1"]]


@pytest.mark.asyncio
async def test_search_call_limit_uses_pg_only_for_strong_identifiers() -> None:
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

    await registry.execute(RetrievalToolCall(tool="search", query="300750 并购重组", limit=60))

    assert runtime.deterministic_limits == [60]
    assert runtime.semantic_limits == [60]


@pytest.mark.asyncio
async def test_search_cleans_strong_identifiers_only_for_vector_branch() -> None:
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

    result = await registry.execute(
        RetrievalToolCall(tool="search", query="300750 最近有哪些海外产能相关的负面事件", limit=10)
    )

    assert runtime.semantic_queries == ["最近有哪些海外产能相关的负面事件"]
    assert result.step.input["search_diagnostics"]["query_parser"]["strong_identifiers"] == ["300750"]


@pytest.mark.asyncio
async def test_tool_registry_search_packages_multi_channel_candidates() -> None:
    registry = _registry()

    result = await registry.execute(RetrievalToolCall(tool="search", query="宁德时代 300750"))

    assert result.step.tool == "search"
    assert [hit.hit_type for hit in result.hits] == ["evidence"]
    assert result.hits[0].hit_id == "kg_chunk:kg_ev:financial:news:1:0"
    assert result.hits[0].edge_refs == ["kg_edge:financial:affects:1"]
    assert result.hits[0].evidence_refs == ["kg_ev:financial:news:1"]
    assert set(result.hits[0].source_channels) == {"pg_deterministic", "semantic_hybrid"}


@pytest.mark.asyncio
async def test_tool_registry_scoped_search_limits_results_to_candidate_scope() -> None:
    registry = _registry()

    result = await registry.execute(
        RetrievalToolCall(
            tool="scoped_search",
            query="海外产能",
            candidate_ids=["kg:financial:stock:300750"],
            limit=10,
        )
    )

    assert result.step.tool == "scoped_search"
    assert result.step.input["search_diagnostics"]["scope"]["node_ids"] == [
        "kg:financial:stock:300750",
        "kg:financial:event:1",
    ]
    channels = {channel for hit in result.hits for channel in hit.source_channels}
    assert channels >= {"pg_deterministic", "graph", "semantic_hybrid"}
    assert result.step.input["search_diagnostics"]["pre_dedupe_counts"] == {
        "pg_deterministic": 2,
        "vector_semantic": 1,
        "graph_scope": 1,
        "scoped_raw": 4,
        "evidence_chunks": 4,
    }
    assert [hit.hit_type for hit in result.hits] == ["evidence"]
    assert result.hits[0].hit_id == "kg_chunk:kg_ev:financial:news:1:0"
    assert set(result.hits[0].source_channels) == {"pg_deterministic", "graph", "semantic_hybrid"}


@pytest.mark.asyncio
async def test_tool_registry_expand_is_explicit_graph_tool() -> None:
    registry = _registry()

    result = await registry.execute(
        RetrievalToolCall(tool="expand", candidate_ids=["kg:financial:stock:300750"], limit=5)
    )

    assert result.step.tool == "expand"
    assert result.hits[0].source == "graph"
    assert result.hits[0].hit_type == "evidence"
    assert result.hits[0].hit_id == "kg_chunk:kg_ev:financial:news:1:0"
    assert result.hits[0].edge_refs == ["kg_edge:financial:affects:1"]


@pytest.mark.asyncio
async def test_tool_registry_expand_accepts_chunk_seed_and_relation_filters() -> None:
    registry = _registry()

    result = await registry.execute(
        RetrievalToolCall(
            tool="expand",
            seed_chunk_ids=["kg_chunk:kg_ev:financial:news:1:0"],
            relation_filters=["affects"],
            hop_limit=1,
            limit=5,
        )
    )

    assert result.step.tool == "expand"
    assert result.hits[0].hit_id == "kg_chunk:kg_ev:financial:news:1:0"
    assert result.hits[0].edge_refs == ["kg_edge:financial:affects:1"]


@pytest.mark.asyncio
async def test_tool_registry_expand_accepts_finding_seed_ids() -> None:
    registry = RetrievalToolRegistry(
        HybridRetrievalRuntime(_Repo(), semantic_retriever=_FindingSemanticHybrid()),
        RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5, graph_limit=5),
        reranker_client=_Reranker(),
    )

    result = await registry.execute(
        RetrievalToolCall(
            tool="expand",
            seed_finding_ids=["kg_finding:financial:ai_supply_chain"],
            relation_filters=["affects"],
            hop_limit=1,
            limit=5,
        )
    )

    assert result.step.tool == "expand"
    assert result.hits[0].hit_id == "kg_chunk:kg_ev:financial:news:1:0"
    assert result.hits[0].edge_refs == ["kg_edge:financial:affects:1"]


@pytest.mark.asyncio
async def test_tool_registry_rerank_uses_configured_reranker() -> None:
    registry = _registry()
    search_result = await registry.execute(RetrievalToolCall(tool="search", query="宁德时代 海外产能"))

    result = await registry.execute(
        RetrievalToolCall(
            tool="rerank",
            query="宁德时代 海外产能",
            candidate_pool=search_result.hits,
            limit=2,
        )
    )

    assert result.step.tool == "rerank"
    assert result.step.input["input_hit_count"] == len(search_result.hits)
    assert result.step.input["rerank_diagnostics"]["model"] == "fake-reranker"
    assert result.step.input["rerank_diagnostics"]["ranked_count"] == 1
    assert len(result.hits) == 1
    assert all("reranker" in hit.source_channels for hit in result.hits)


@pytest.mark.asyncio
async def test_tool_registry_open_reads_evidence_window() -> None:
    registry = _registry()

    result = await registry.execute(
        RetrievalToolCall(tool="open", evidence_ids=["kg_ev:financial:news:1"])
    )

    assert result.step.tool == "open"
    assert result.hits[0].hit_id == "kg_chunk:kg_ev:financial:news:1:0"
    assert result.hits[0].hit_type == "evidence"
    assert result.hits[0].evidence_refs == ["kg_ev:financial:news:1"]
    assert result.hits[0].matched_fields == ["kg_evidence_chunks.manifest", "kg_evidence.content"]


@pytest.mark.asyncio
async def test_tool_registry_open_reads_chunk_ids_with_neighbors() -> None:
    registry = RetrievalToolRegistry(
        HybridRetrievalRuntime(_NeighborChunkRepo(), semantic_retriever=_SemanticHybrid()),
        RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5, evidence_limit=10),
        reranker_client=_Reranker(),
    )

    result = await registry.execute(
        RetrievalToolCall(
            tool="open",
            chunk_ids=["kg_chunk:kg_ev:financial:news:1:1"],
            include_neighbors="one_hop",
        )
    )

    assert [hit.hit_id for hit in result.hits] == [
        "kg_chunk:kg_ev:financial:news:1:0",
        "kg_chunk:kg_ev:financial:news:1:1",
        "kg_chunk:kg_ev:financial:news:1:2",
    ]


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
async def test_tool_registry_find_searches_inside_chunk_ids() -> None:
    registry = _registry()

    result = await registry.execute(
        RetrievalToolCall(
            tool="find",
            query="海外产能",
            chunk_ids=["kg_chunk:kg_ev:financial:news:1:0"],
        )
    )

    assert result.step.tool == "find"
    assert result.hits[0].hit_id == "kg_chunk:kg_ev:financial:news:1:0"


@pytest.mark.asyncio
async def test_tool_registry_summarize_is_controller_signal() -> None:
    registry = _registry()

    result = await registry.execute(RetrievalToolCall(tool="summarize"))

    assert result.hits == []
    assert result.summary == "summary_empty: no candidate_pool or refs supplied"


@pytest.mark.asyncio
async def test_tool_registry_summarize_opens_refs_into_readable_summary() -> None:
    registry = _registry()

    result = await registry.execute(
        RetrievalToolCall(
            tool="summarize",
            evidence_ids=["kg_ev:financial:news:1"],
            limit=3,
        )
    )

    assert result.hits == []
    assert result.summary is not None
    assert result.summary.startswith("summary_opened_evidence:")
    assert "evidence_refs: kg_ev:financial:news:1" in result.summary
    assert "证据分片：宁德时代受海外产能事件影响" in result.summary


def test_tool_call_validates_required_inputs() -> None:
    with pytest.raises(ValueError, match="search requires query"):
        RetrievalToolCall(tool="search")
    with pytest.raises(ValueError, match="scoped_search requires query and evidence_ids or candidate_ids"):
        RetrievalToolCall(tool="scoped_search", query="宁德时代")
    with pytest.raises(ValueError, match="rerank requires query"):
        RetrievalToolCall(tool="rerank", query="宁德时代")
    with pytest.raises(ValueError, match="find requires query and evidence_ids or chunk_ids"):
        RetrievalToolCall(tool="find", query="宁德时代")
    with pytest.raises(ValueError, match="open requires evidence_ids, chunk_ids, or candidate_ids"):
        RetrievalToolCall(tool="open")
    with pytest.raises(ValueError, match="expand requires candidate_ids, seed_node_ids, seed_edge_ids, seed_chunk_ids, seed_finding_ids, or evidence_ids"):
        RetrievalToolCall(tool="expand")
