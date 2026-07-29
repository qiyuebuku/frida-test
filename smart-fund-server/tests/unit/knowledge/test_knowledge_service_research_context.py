"""Tests for research-context orchestration in KnowledgeService."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.application.dto.knowledge_dto import KnowledgeResearchContextCommand
from src.application.services import knowledge_service as knowledge_service_module
from src.application.services.knowledge_service import (
    KnowledgeService,
    _graph_time_window_for_plan,
)
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.retrieval import (
    RetrievalHit,
    RetrievalOptions,
    SemanticHybridRetriever,
)
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode, EvidenceChunk
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

    def get_evidence(self, evidence_id: str):
        return self.evidence if evidence_id == self.evidence.evidence_id else None

    def list_evidence_chunks(self, adapter_name: str):
        assert adapter_name == "financial"
        return [
            EvidenceChunk(
                chunk_id="kg_chunk:fake",
                adapter_name="financial",
                evidence_id=self.evidence.evidence_id,
                content=self.evidence.content,
            )
        ]

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
