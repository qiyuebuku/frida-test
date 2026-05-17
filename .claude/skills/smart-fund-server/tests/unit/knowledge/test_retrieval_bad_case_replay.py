"""Tests for research-context bad case replay."""

from __future__ import annotations

import pytest

from src.application.dto.knowledge_dto import (
    KnowledgeBadCaseReplayCommand,
    KnowledgeResearchContextBadCase,
)
from src.application.services import knowledge_service as knowledge_service_module
from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.agentic_retrieval import RetrievalControllerDecision
from src.domain.knowledge.retrieval_judge import DeterministicCandidateJudge
from src.domain.knowledge.retrieval import RetrievalHit, RetrievalOptions, SemanticHybridRetriever
from src.domain.knowledge.retrieval_eval import RetrievalBadCase, evaluate_retrieval_bad_case
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode


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
        node_id="kg:financial:event:74342",
        adapter_name="financial",
        node_type="event",
        canonical_name="技术发布会简析",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:mentions:74342:300750",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=stock.node_id,
        relation_type="mentions",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.7,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news_articles:ft_news:74342"],
        version="v1",
    )
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news_articles:ft_news:74342",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="ft_news:74342",
        content="技术发布会简析：技术迭代驱动多维增长，补能生态加速布局",
        version="v1",
    )

    def list_nodes(self, adapter_name: str):
        return [self.stock, self.event]

    def list_edges(self, adapter_name: str):
        return [self.edge]

    def search_wiki_pages(self, adapter_name: str, query: str, limit: int = 20):
        return []

    def get_evidence(self, evidence_id: str):
        return self.evidence if evidence_id == self.evidence.evidence_id else None

    def get_node(self, node_id: str):
        return {self.stock.node_id: self.stock, self.event.node_id: self.event}.get(node_id)

    def get_edge(self, edge_id: str):
        return self.edge if edge_id == self.edge.edge_id else None


class _Milvus(SemanticHybridRetriever):
    enabled = True
    backend_name = "milvus"

    async def search(self, query: str, options: RetrievalOptions):
        return [
            RetrievalHit(
                hit_id="kg_chunk:74342",
                hit_type="semantic_hybrid",
                title="evidence_chunk:74342",
                snippet="宁德时代 300750 技术发布会",
                score=0.8,
                source="semantic_hybrid",
                evidence_refs=["kg_ev:financial:news_articles:ft_news:74342"],
            )
        ]


class _StopAfterSemanticStrategy:
    async def next_decision(self, *, query, working_set, observations, constraints):
        if not observations:
            return RetrievalControllerDecision(next_tool="search", query_rewrites=[query])
        return RetrievalControllerDecision(next_tool="stop", stop_reason="evidence_sufficient")


def test_evaluate_retrieval_bad_case_reports_missing_items() -> None:
    result = evaluate_retrieval_bad_case(
        RetrievalBadCase(
            case_id="case-1",
            query="宁德时代",
            expected_evidence_refs=["ev:missing"],
            expected_hit_titles=["news_articles:ft_news:missing"],
            expected_top_hit_titles=["news_articles:ft_news:missing"],
            expected_node_names=["宁德时代"],
            expected_relation_types=["mentions"],
            expected_channels_used=["search"],
            min_hits=2,
            min_evidence_refs=2,
            min_matched_nodes=2,
            min_matched_edges=1,
        ),
        evidence_refs=["ev:actual"],
        hit_titles=["news_articles:ft_news:actual"],
        channels_used=["entity_resolve"],
        matched_nodes=[_Repo.stock],
        matched_edges=[],
    )

    assert result.passed is False
    assert result.missing_evidence_refs == ["ev:missing"]
    assert result.missing_hit_titles == ["news_articles:ft_news:missing"]
    assert result.missing_top_hit_titles == ["news_articles:ft_news:missing"]
    assert result.missing_relation_types == ["mentions"]
    assert result.missing_channels_used == ["search"]
    assert result.metric_failures == {
        "hits": {"actual": 1, "expected_min": 2},
        "evidence_refs": {"actual": 1, "expected_min": 2},
        "matched_nodes": {"actual": 1, "expected_min": 2},
        "matched_edges": {"actual": 0, "expected_min": 1},
    }
    assert result.missing_node_names == []


def test_evaluate_retrieval_bad_case_reports_forbidden_noise() -> None:
    result = evaluate_retrieval_bad_case(
        RetrievalBadCase(
            case_id="noise-case",
            query="俄就波法联合军演发出警告",
            forbidden_node_names=["宁德时代"],
            forbidden_topics=["固态电池"],
            max_forbidden_hits=0,
        ),
        evidence_refs=["kg_ev:financial:l1_events:solid_state"],
        hit_titles=["固态电池政策支持带动宁德时代产业链预期"],
        channels_used=["semantic_hybrid_search"],
        matched_nodes=[_Repo.stock],
        matched_edges=[],
    )

    assert result.passed is False
    assert result.forbidden_node_names_hit == ["宁德时代"]
    assert result.forbidden_topics_hit == ["固态电池"]
    assert result.metrics["forbidden_hits"] == 2
    assert result.metric_failures["forbidden_hits"] == {"actual": 2, "expected_max": 0}


@pytest.mark.asyncio
async def test_service_replays_research_context_bad_cases(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _Milvus(),
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

    result = await service.replay_research_context_bad_cases(
        KnowledgeBadCaseReplayCommand(
            adapter_name="financial",
            cases=[
                KnowledgeResearchContextBadCase(
                    case_id="catl-events",
                    query="宁德时代 300750 最近受哪些事件影响",
                    expected_hit_titles=["news_articles:ft_news:74342"],
                    expected_top_hit_titles=["news_articles:ft_news:74342"],
                    expected_node_names=["宁德时代"],
                    expected_relation_types=["mentions"],
                    expected_channels_used=["search", "open"],
                    min_hits=1,
                    min_evidence_refs=1,
                    min_matched_nodes=1,
                    min_matched_edges=1,
                )
            ],
        )
    )

    assert result.total == 1
    assert result.passed == 1
    assert result.failed == 0
    assert result.metrics["pass_rate"] == 1.0
    assert result.metrics["channel_coverage"]["search"] == 1
    assert result.results[0]["missing_channels_used"] == []
    assert result.results[0]["metric_failures"] == {}
    assert "search" in result.results[0]["channels_used"]
    assert "open" in result.results[0]["channels_used"]
    assert result.results[0]["query_anchor"]
    assert result.results[0]["routing_decision"]["final_mode"] in {
        "deterministic_plan",
        "agentic_arag",
    }
    assert result.results[0]["candidate_judgement_summary"]["total"] >= 1
    assert sum(result.metrics["route_coverage"].values()) == 1
