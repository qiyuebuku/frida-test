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
        ),
        evidence_refs=["ev:actual"],
        hit_titles=["news_articles:ft_news:actual"],
        matched_nodes=[_Repo.stock],
        matched_edges=[],
    )

    assert result.passed is False
    assert result.missing_evidence_refs == ["ev:missing"]
    assert result.missing_hit_titles == ["news_articles:ft_news:missing"]
    assert result.missing_top_hit_titles == ["news_articles:ft_news:missing"]
    assert result.missing_relation_types == ["mentions"]
    assert result.missing_node_names == []


@pytest.mark.asyncio
async def test_service_replays_research_context_bad_cases(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_service_module,
        "_semantic_hybrid_retriever",
        lambda: _Milvus(),
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
                )
            ],
        )
    )

    assert result.total == 1
    assert result.passed == 1
    assert result.failed == 0
    assert result.results[0]["channels_used"] == [
        "entity_resolve",
        "graph_search",
        "semantic_hybrid_search",
        "chunk_read",
    ]
