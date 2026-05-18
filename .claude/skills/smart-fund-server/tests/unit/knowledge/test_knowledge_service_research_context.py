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
from src.application.services import openai_agents_retrieval_runtime as openai_agents_runtime_module
from src.application.services.knowledge_service import (
    KnowledgeService,
    _graph_time_window_for_plan,
)
from src.domain.knowledge.agentic_retrieval import RetrievalControllerDecision
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.retrieval import (
    RetrievalHit,
    RetrievalOptions,
    RetrievalStep,
    RetrievalTrace,
    SemanticHybridRetriever,
)
from src.domain.knowledge.retrieval_judge import DeterministicCandidateJudge
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


class _NoisyRepo(_Repo):
    military_event = CompiledNode(
        node_id="kg:financial:event:military",
        adapter_name="financial",
        node_type="event",
        canonical_name="波兰法国联合军事演习",
        aliases=[],
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    france = CompiledNode(
        node_id="kg:financial:region:france",
        adapter_name="financial",
        node_type="region",
        canonical_name="法国",
        aliases=[],
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    military_edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:military:france",
        adapter_name="financial",
        source_node_id=military_event.node_id,
        target_node_id=france.node_id,
        relation_type="affects",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.7,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:military"],
        version="v1",
    )
    military_evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news:military",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="ft_news:military",
        content="波兰和法国计划举行联合军事演习，欧洲对俄全面对抗正在回归。",
        version="v1",
    )

    def list_nodes(self, adapter_name: str):
        return [self.stock, self.event, self.military_event, self.france]

    def list_edges(self, adapter_name: str):
        return [self.edge, self.military_edge]

    def get_evidence(self, evidence_id: str):
        return {
            self.evidence.evidence_id: self.evidence,
            self.military_evidence.evidence_id: self.military_evidence,
        }.get(evidence_id)

    def get_node(self, node_id: str):
        return {
            self.stock.node_id: self.stock,
            self.event.node_id: self.event,
            self.military_event.node_id: self.military_event,
            self.france.node_id: self.france,
        }.get(node_id)

    def get_edge(self, edge_id: str):
        return {
            self.edge.edge_id: self.edge,
            self.military_edge.edge_id: self.military_edge,
        }.get(edge_id)


class _NoisyMilvusRetriever(SemanticHybridRetriever):
    enabled = True
    backend_name = "milvus"

    async def search(self, query: str, options: RetrievalOptions):
        return [
            RetrievalHit(
                hit_id="kg_chunk:catl",
                hit_type="semantic_hybrid",
                title="catl semantic chunk",
                snippet="宁德时代 海外产能",
                score=0.9,
                source="semantic_hybrid",
                evidence_refs=["kg_ev:financial:news:overseas_capacity"],
            ),
            RetrievalHit(
                hit_id="kg_chunk:military",
                hit_type="semantic_hybrid",
                title="military semantic chunk",
                snippet="波兰 法国 联合军事演习",
                score=0.8,
                source="semantic_hybrid",
                evidence_refs=["kg_ev:financial:news:military"],
            ),
        ]


class _ScriptedAgenticStrategy:
    async def next_decision(self, *, query, working_set, observations, constraints):
        if not observations:
            return RetrievalControllerDecision(next_tool="search", query_rewrites=[query])
        if observations[-1].tool == "search":
            return RetrievalControllerDecision(
                next_tool="open",
                target_evidence_refs=working_set.evidence_refs,
                expected_gain="context_window",
            )
        return RetrievalControllerDecision(next_tool="stop", stop_reason="evidence_sufficient")


class _StopAfterSemanticStrategy:
    async def next_decision(self, *, query, working_set, observations, constraints):
        if not observations:
            return RetrievalControllerDecision(next_tool="search", query_rewrites=[query])
        return RetrievalControllerDecision(next_tool="stop", stop_reason="evidence_sufficient")


class _BootstrapAwareStrategy:
    def __init__(self) -> None:
        self.first_observation_count = None

    async def next_decision(self, *, query, working_set, observations, constraints):
        if self.first_observation_count is None:
            self.first_observation_count = len(observations)
        if observations and observations[-1].tool == "search":
            evidence_refs = [
                ref
                for hit in observations[-1].hits
                for ref in hit.evidence_refs
            ]
            return RetrievalControllerDecision(
                next_tool="open",
                target_evidence_refs=evidence_refs[:1],
                expected_gain="context_window",
            )
        return RetrievalControllerDecision(next_tool="stop", stop_reason="evidence_sufficient")


class _ShouldNotCallStrategy:
    async def next_decision(self, *, query, working_set, observations, constraints):  # pragma: no cover
        raise AssertionError("hand-written controller strategy should not run in SDK branch")


class _FakeSDK:
    class ModelSettings:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Agent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Runner:
        called = False

        @classmethod
        async def run(cls, agent, input, max_turns):
            cls.called = True
            await agent.tools[1](
                evidence_ids=["kg_ev:financial:news:overseas_capacity"],
                candidate_ids=[],
                limit=5,
            )
            return type(
                "RunResult",
                (),
                {
                    "final_output": (
                        '{"stop_reason":"fake_sdk_done",'
                        '"selected_candidate_ids":["kg_ev:financial:news:overseas_capacity"],'
                        '"evidence_ids":["kg_ev:financial:news:overseas_capacity"],'
                        '"reason":"fake runner selected opened evidence"}'
                    )
                },
            )()

    class AsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class OpenAIChatCompletionsModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    @staticmethod
    def function_tool(func, **kwargs):
        return func

    @staticmethod
    def set_tracing_disabled(disabled):
        return None


class _FakeRerankerClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def rerank(self, *, query, documents, top_n=None):
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
                            "relevance_score": 1.0 - index * 0.01,
                        },
                    )()
                    for index in range(count)
                ],
            },
        )()


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
    assert result.retrieval_channels_used == ["search", "open"]
    assert result.semantic_enabled is True
    assert result.milvus_enabled is True
    assert result.evidence_refs == ["kg_ev:financial:news:overseas_capacity"]
    assert result.warnings == []


@pytest.mark.asyncio
async def test_research_context_filters_unanchored_noise_after_context_assembly(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _NoisyMilvusRetriever(),
    )
    service = KnowledgeService(repository=_NoisyRepo())

    result = await service.build_research_context_for(
        KnowledgeResearchContextCommand(
            query="宁德时代 300750 最近受哪些事件影响",
            adapter_name="financial",
            retrieval_mode="deterministic_plan",
            graph_limit=10,
            wiki_limit=5,
            evidence_limit=10,
        )
    )

    assert "kg_ev:financial:news:overseas_capacity" in result.evidence_refs
    assert "kg_ev:financial:news:military" not in result.evidence_refs
    assert "法国" not in {node["canonical_name"] for node in result.matched_nodes}
    assert "kg_edge:financial:affects:military:france" not in {
        edge["edge_id"] for edge in result.matched_edges
    }


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
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_candidate_judge",
        lambda: DeterministicCandidateJudge(),
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
    assert result.retrieval_channels_used == ["search", "open"]
    assert result.evidence_refs == ["kg_ev:financial:news:overseas_capacity"]
    assert [edge["edge_id"] for edge in result.matched_edges] == [_Repo.edge.edge_id]
    assert {node["canonical_name"] for node in result.matched_nodes} == {"宁德时代", "海外产能事件"}


@pytest.mark.asyncio
async def test_research_context_auto_uses_agentic_semantic_judge_path(monkeypatch) -> None:
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
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_candidate_judge",
        lambda: DeterministicCandidateJudge(),
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
    assert result.retrieval_channels_used == ["search", "open"]


@pytest.mark.asyncio
async def test_openai_agents_mode_fails_when_sdk_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _FakeMilvusRetriever(),
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_retrieval_strategy",
        lambda: _ShouldNotCallStrategy(),
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_candidate_judge",
        lambda: DeterministicCandidateJudge(),
    )
    monkeypatch.setattr(openai_agents_runtime_module, "_load_agents_sdk", lambda: None)
    service = KnowledgeService(repository=_Repo())

    with pytest.raises(RuntimeError, match="openai-agents is not installed"):
        await service.build_research_context_for(
            KnowledgeResearchContextCommand(
                query="宁德时代 300750 最近受哪些事件影响",
                adapter_name="financial",
                retrieval_mode="openai_agents_arag",
                evidence_limit=5,
            )
        )


@pytest.mark.asyncio
async def test_openai_agents_mode_can_use_sdk_tool_loop(monkeypatch) -> None:
    _FakeSDK.Runner.called = False
    monkeypatch.setenv("KG_LANGFUSE_ENABLED", "0")
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _FakeMilvusRetriever(),
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_retrieval_strategy",
        lambda: _ShouldNotCallStrategy(),
    )
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_candidate_judge",
        lambda: DeterministicCandidateJudge(),
    )
    monkeypatch.setattr(openai_agents_runtime_module, "_load_agents_sdk", lambda: _FakeSDK)
    monkeypatch.setattr(openai_agents_runtime_module, "RerankerClient", _FakeRerankerClient)
    service = KnowledgeService(repository=_Repo())

    result = await service.build_research_context_for(
        KnowledgeResearchContextCommand(
            query="宁德时代 300750 最近受哪些事件影响",
            adapter_name="financial",
            retrieval_mode="openai_agents_arag",
            evidence_limit=5,
        )
    )

    assert _FakeSDK.Runner.called is True
    assert result.mode == "openai_agents_arag"
    assert result.retrieval_channels_used == ["search", "rerank", "open"]
    assert result.retrieval_trace["planner_enabled"] is True
    assert result.retrieval_trace["controller_decisions"][1]["auto_action"] == "system_bootstrap"
    assert result.retrieval_trace["controller_decisions"][2]["auto_action"] == "agent_tool"
    assert result.retrieval_trace["controller_decisions"][-1]["auto_action"] == "agent_final"
    assert result.retrieval_trace["candidate_judgements"] == []
    assert result.retrieval_trace["warnings"] == []


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
    monkeypatch.setattr(
        knowledge_service_module,
        "_agentic_candidate_judge",
        lambda: DeterministicCandidateJudge(),
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

    assert result.retrieval_channels_used == ["search", "open"]
    assert any(hit["hit_type"] == "evidence" for hit in result.hits)


@pytest.mark.asyncio
async def test_bad_case_replay_can_replay_recorded_agentic_trace(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _FakeMilvusRetriever(),
    )
    semantic_call = RetrievalToolCall(
        tool="search",
        query="宁德时代 300750 最近受哪些事件影响",
    )
    chunk_call = RetrievalToolCall(
        tool="open",
        evidence_ids=["kg_ev:financial:news:overseas_capacity"],
    )
    recorded_trace = RetrievalTrace(
        mode="agentic_arag",
        agentic_enabled=True,
        steps=[
            RetrievalStep(
                tool="search",
                input=semantic_call.model_dump(mode="json"),
                output_refs=[
                    "kg:financial:stock:300750",
                    "kg:financial:event:overseas_capacity",
                    "kg_edge:financial:affects:overseas_capacity:300750",
                    "kg_chunk:fake",
                ],
                hit_count=4,
            ),
            RetrievalStep(
                tool="open",
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
    assert result.results[0]["channels_used"] == ["search", "open"]


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
