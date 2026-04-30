"""Tests for research-context orchestration in KnowledgeService."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.application.dto.knowledge_dto import (
    KnowledgeBadCaseReplayCommand,
    KnowledgeResearchContextBadCase,
    KnowledgeResearchContextCommand,
)
from src.application.services import knowledge_service as knowledge_service_module
from src.application.services.knowledge_service import (
    KnowledgeService,
    _graph_time_window_for_plan,
)
from src.domain.knowledge.agentic_retrieval import AgenticRetrievalDecision
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.retrieval import (
    RetrievalHit,
    RetrievalOptions,
    RetrievalStep,
    RetrievalTrace,
    SemanticHybridRetriever,
)
from src.domain.knowledge.retrieval_tools import RetrievalToolCall
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode
from src.domain.knowledge_adapters.financial.query_planner import FinancialQueryPlanner


class _Repo:
    stock = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        aliases=["300750"],
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    event = CompiledNode(
        node_id="kg:financial:event:overseas_capacity",
        adapter_name="financial",
        node_type="event",
        canonical_name="海外产能事件",
        aliases=[],
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:overseas_capacity:300750",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=stock.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.82,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:overseas_capacity"],
        version="v1",
    )
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news:overseas_capacity",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="ft_news:overseas_capacity",
        content="宁德时代近期受海外产能建设与储能业务进展影响。",
        version="v1",
    )

    def list_nodes(self, adapter_name: str):
        assert adapter_name == "financial"
        return [self.stock, self.event]

    def list_edges(self, adapter_name: str):
        assert adapter_name == "financial"
        return [self.edge]

    def search_wiki_pages(self, adapter_name: str, query: str, limit: int = 20):
        assert adapter_name == "financial"
        return []

    def get_evidence(self, evidence_id: str):
        return self.evidence if evidence_id == self.evidence.evidence_id else None

    def get_node(self, node_id: str):
        return {self.stock.node_id: self.stock, self.event.node_id: self.event}.get(node_id)

    def get_edge(self, edge_id: str):
        return self.edge if edge_id == self.edge.edge_id else None


class _FakeMilvusRetriever(SemanticHybridRetriever):
    enabled = True
    backend_name = "milvus"

    async def search(self, query: str, options: RetrievalOptions):
        return [
            RetrievalHit(
                hit_id="kg_chunk:fake",
                hit_type="semantic_hybrid",
                title="fake semantic chunk",
                snippet="宁德时代 海外产能",
                source="semantic_hybrid",
                evidence_refs=["kg_ev:financial:news:overseas_capacity"],
            )
        ]


class _ScriptedAgenticStrategy:
    async def next_decision(self, *, query, observations, constraints):
        if not observations:
            return AgenticRetrievalDecision(
                tool_call=RetrievalToolCall(tool="semantic_hybrid_search", query=query)
            )
        if observations[-1].tool == "semantic_hybrid_search":
            return AgenticRetrievalDecision(
                tool_call=RetrievalToolCall(
                    tool="chunk_read",
                    evidence_ids=[
                        evidence_id
                        for hit in observations[-1].hits
                        for evidence_id in hit.evidence_refs
                    ],
                )
            )
        return AgenticRetrievalDecision(stop=True, stop_reason="evidence_sufficient")


class _StopAfterSemanticStrategy:
    async def next_decision(self, *, query, observations, constraints):
        if not observations:
            return AgenticRetrievalDecision(
                tool_call=RetrievalToolCall(tool="semantic_hybrid_search", query=query)
            )
        return AgenticRetrievalDecision(stop=True, stop_reason="evidence_sufficient")


@pytest.mark.asyncio
async def test_research_context_returns_financial_retrieval_plan(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _FakeMilvusRetriever(),
    )
    service = KnowledgeService(repository=_Repo())

    result = await service.build_research_context_for(
        KnowledgeResearchContextCommand(
            query="宁德时代 300750 最近受哪些事件影响",
            adapter_name="financial",
            retrieval_mode="deterministic_plan",
            graph_limit=5,
            wiki_limit=5,
            evidence_limit=5,
        )
    )

    assert result.planner_enabled is True
    assert result.retrieval_plan["intent"] == "impact_events_for_entity"
    assert result.retrieval_plan["direction"] == "incoming"
    assert result.retrieval_plan["time_range"]["preset"] == "recent"
    assert "keyword_search" not in {step["tool"] for step in result.retrieval_plan["steps"]}
    assert result.retrieval_trace["planner_enabled"] is True
    assert result.retrieval_channels_used == [
        "entity_resolve",
        "graph_search",
        "semantic_hybrid_search",
        "chunk_read",
    ]
    assert result.semantic_enabled is True
    assert result.milvus_enabled is True
    assert result.evidence_refs == ["kg_ev:financial:news:overseas_capacity"]
    assert result.warnings == []


@pytest.mark.asyncio
async def test_research_context_can_use_agentic_retrieval_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _FakeMilvusRetriever(),
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_retrieval_strategy",
        lambda: _ScriptedAgenticStrategy(),
    )
    service = KnowledgeService(repository=_Repo())

    result = await service.build_research_context_for(
        KnowledgeResearchContextCommand(
            query="宁德时代 300750 最近受哪些事件影响",
            adapter_name="financial",
            retrieval_mode="agentic_arag",
            evidence_limit=5,
        )
    )

    assert result.mode == "agentic_arag"
    assert result.agentic_enabled is True
    assert result.planner_enabled is False
    assert result.retrieval_channels_used == ["semantic_hybrid_search", "chunk_read"]
    assert result.evidence_refs == ["kg_ev:financial:news:overseas_capacity"]
    assert [edge["edge_id"] for edge in result.matched_edges] == [_Repo.edge.edge_id]
    assert {node["canonical_name"] for node in result.matched_nodes} == {"宁德时代", "海外产能事件"}


@pytest.mark.asyncio
async def test_research_context_defaults_to_agentic_retrieval_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _FakeMilvusRetriever(),
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_retrieval_strategy",
        lambda: _ScriptedAgenticStrategy(),
    )
    service = KnowledgeService(repository=_Repo())

    result = await service.build_research_context_for(
        KnowledgeResearchContextCommand(
            query="宁德时代 300750 最近受哪些事件影响",
            adapter_name="financial",
            evidence_limit=5,
        )
    )

    assert result.mode == "agentic_arag"
    assert result.agentic_enabled is True
    assert result.planner_enabled is False


@pytest.mark.asyncio
async def test_agentic_mode_materializes_evidence_refs_when_strategy_stops(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _FakeMilvusRetriever(),
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_retrieval_strategy",
        lambda: _StopAfterSemanticStrategy(),
    )
    service = KnowledgeService(repository=_Repo())

    result = await service.build_research_context_for(
        KnowledgeResearchContextCommand(
            query="宁德时代 300750 最近受哪些事件影响",
            adapter_name="financial",
            retrieval_mode="agentic_arag",
            evidence_limit=5,
        )
    )

    assert result.retrieval_channels_used == ["semantic_hybrid_search", "chunk_read"]
    assert any(hit["hit_type"] == "evidence" for hit in result.hits)


@pytest.mark.asyncio
async def test_bad_case_replay_can_replay_recorded_agentic_trace(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _FakeMilvusRetriever(),
    )
    semantic_call = RetrievalToolCall(
        tool="semantic_hybrid_search",
        query="宁德时代 300750 最近受哪些事件影响",
    )
    chunk_call = RetrievalToolCall(
        tool="chunk_read",
        evidence_ids=["kg_ev:financial:news:overseas_capacity"],
    )
    recorded_trace = RetrievalTrace(
        mode="agentic_arag",
        agentic_enabled=True,
        steps=[
            RetrievalStep(
                tool="semantic_hybrid_search",
                input=semantic_call.model_dump(mode="json"),
                output_refs=["kg_chunk:fake"],
                hit_count=1,
            ),
            RetrievalStep(
                tool="chunk_read",
                input=chunk_call.model_dump(mode="json"),
                output_refs=["kg_ev:financial:news:overseas_capacity"],
                hit_count=1,
            ),
        ],
    )
    service = KnowledgeService(repository=_Repo())

    result = await service.replay_research_context_bad_cases(
        KnowledgeBadCaseReplayCommand(
            adapter_name="financial",
            target="test",
            cases=[
                KnowledgeResearchContextBadCase(
                    case_id="trace-replay",
                    query="宁德时代 300750 最近受哪些事件影响",
                    expected_evidence_refs=["kg_ev:financial:news:overseas_capacity"],
                    replay_trace=True,
                    recorded_trace=recorded_trace.model_dump(mode="json"),
                )
            ],
            evidence_limit=5,
        )
    )

    assert result.passed == 1
    assert result.results[0]["trace_replay"] is True
    assert result.results[0]["trace_mismatches"] == []
    assert result.results[0]["channels_used"] == ["semantic_hybrid_search", "chunk_read"]


def test_graph_time_window_for_recent_plan_is_anchored() -> None:
    plan = FinancialQueryPlanner().plan("宁德时代 300750 最近受哪些事件影响")
    start, end = _graph_time_window_for_plan(
        plan,
        now=datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc),
    )

    assert start.isoformat() == "2026-03-30T12:00:00+00:00"
    assert end.isoformat() == "2026-04-29T12:00:00+00:00"


def test_milvus_is_required_by_configuration() -> None:
    assert knowledge_service_module.settings.MILVUS_ENABLED is True
