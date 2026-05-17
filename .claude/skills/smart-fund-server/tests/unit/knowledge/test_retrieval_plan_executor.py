"""Tests for deterministic retrieval plan execution."""

from __future__ import annotations

import pytest

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.retrieval import (
    HybridRetrievalRuntime,
    RetrievalHit,
    RetrievalOptions,
    SemanticHybridRetriever,
)
from src.domain.knowledge.retrieval_plan import RetrievalPlan
from src.domain.knowledge.retrieval_plan_executor import RetrievalPlanExecutor
from src.domain.knowledge.retrieval_tools import RetrievalToolCall, RetrievalToolRegistry
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode
from src.domain.knowledge_adapters.financial.query_planner import FinancialQueryPlanner

from .test_retrieval_tools import _SemanticHybrid, _registry


class _NoisyEntityRepo:
    stock = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        aliases=["300750"],
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    noisy_event = CompiledNode(
        node_id="kg:financial:event:noisy",
        adapter_name="financial",
        node_type="event",
        canonical_name="宁德时代事件噪声",
        aliases=[],
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:affects:stock",
        adapter_name="financial",
        source_node_id="kg:financial:event:source",
        target_node_id=stock.node_id,
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
        content="宁德时代受事件影响。",
        version="v1",
    )

    def list_nodes(self, adapter_name: str):
        return [self.stock, self.noisy_event]

    def list_edges(self, adapter_name: str):
        return [self.edge]

    def search_wiki_pages(self, adapter_name: str, query: str, limit: int = 20):
        return []

    def get_evidence(self, evidence_id: str):
        return self.evidence if evidence_id == self.evidence.evidence_id else None

    def get_node(self, node_id: str):
        return {
            self.stock.node_id: self.stock,
            self.noisy_event.node_id: self.noisy_event,
        }.get(node_id)

    def get_edge(self, edge_id: str):
        return self.edge if edge_id == self.edge.edge_id else None


class _TwoEvidenceRepo:
    evidences = {
        "kg_ev:a_noisy": CompiledEvidence(
            evidence_id="kg_ev:a_noisy",
            adapter_name="financial",
            evidence_type=EvidenceType.TEXT_SPAN,
            source_type="news_articles",
            source_id="ft_news:noisy",
            content="无关市场新闻。",
            version="v1",
        ),
        "kg_ev:z_relevant": CompiledEvidence(
            evidence_id="kg_ev:z_relevant",
            adapter_name="financial",
            evidence_type=EvidenceType.TEXT_SPAN,
            source_type="news_articles",
            source_id="ft_news:relevant",
            content="宁德时代受海外产能事件影响。",
            version="v1",
        ),
    }

    def list_nodes(self, adapter_name: str):
        return []

    def list_edges(self, adapter_name: str):
        return []

    def search_wiki_pages(self, adapter_name: str, query: str, limit: int = 20):
        return []

    def get_evidence(self, evidence_id: str):
        return self.evidences.get(evidence_id)

    def get_node(self, node_id: str):
        return None

    def get_edge(self, edge_id: str):
        return None


class _RankedSemanticHybrid(SemanticHybridRetriever):
    enabled = True
    backend_name = "milvus"

    async def search(self, query: str, options: RetrievalOptions):
        return [
            RetrievalHit(
                hit_id="kg_chunk:a_noisy",
                hit_type="semantic_hybrid",
                title="noisy",
                snippet="无关市场新闻",
                score=0.01,
                source="semantic_hybrid",
                evidence_refs=["kg_ev:a_noisy"],
            ),
            RetrievalHit(
                hit_id="kg_chunk:z_relevant",
                hit_type="semantic_hybrid",
                title="relevant",
                snippet="宁德时代 海外产能",
                score=0.9,
                source="semantic_hybrid",
                evidence_refs=["kg_ev:z_relevant"],
            ),
        ]


@pytest.mark.asyncio
async def test_plan_executor_fills_graph_seed_nodes_and_evidence_ids() -> None:
    query = "宁德时代 300750 最近受哪些事件影响"
    plan = FinancialQueryPlanner().plan(query)

    result = await RetrievalPlanExecutor(_registry()).execute(query=query, plan=plan)

    assert result.trace.mode == "deterministic_plan"
    assert result.trace.planner_enabled is True
    assert result.trace.channels_used == ["search", "open"]
    assert result.evidence_refs == ["kg_ev:financial:news:1"]
    assert any(hit.hit_type == "evidence" for hit in result.hits)
    assert any("planner time_range" in warning for warning in result.trace.warnings)


@pytest.mark.asyncio
async def test_plan_executor_can_read_chunks_from_semantic_hits_without_entity_seed() -> None:
    plan = RetrievalPlan(
        intent="general",
        steps=[RetrievalToolCall(tool="search", query="海外产能影响")],
    )

    result = await RetrievalPlanExecutor(_registry()).execute(
        query="海外产能影响",
        plan=plan,
    )

    assert result.trace.channels_used == ["search", "open"]
    assert result.evidence_refs == ["kg_ev:financial:news:1"]


@pytest.mark.asyncio
async def test_plan_executor_preserves_semantic_score_when_reading_chunks() -> None:
    registry = RetrievalToolRegistry(
        HybridRetrievalRuntime(_TwoEvidenceRepo(), semantic_retriever=_RankedSemanticHybrid()),
        RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5, max_hits=2),
    )
    plan = RetrievalPlan(
        intent="general",
        steps=[RetrievalToolCall(tool="search", query="宁德时代")],
    )

    result = await RetrievalPlanExecutor(registry).execute(query="宁德时代", plan=plan)

    assert result.hits[0].evidence_refs == ["kg_ev:z_relevant"]
    assert result.hits[0].hit_type == "evidence"


@pytest.mark.asyncio
async def test_plan_executor_respects_max_hits() -> None:
    registry = _registry()
    registry.options = RetrievalOptions(
        adapter_name="financial",
        semantic_hybrid_limit=5,
        max_hits=1,
    )
    plan = FinancialQueryPlanner().plan("宁德时代 300750 最近受哪些事件影响")

    result = await RetrievalPlanExecutor(registry).execute(
        query="宁德时代 300750 最近受哪些事件影响",
        plan=plan,
    )

    assert len(result.hits) == 1


@pytest.mark.asyncio
async def test_plan_executor_search_step_hides_low_level_tool_contract() -> None:
    plan = FinancialQueryPlanner().plan("宁德时代 300750 最近受哪些事件影响")

    assert [step.tool for step in plan.steps] == ["search"]
