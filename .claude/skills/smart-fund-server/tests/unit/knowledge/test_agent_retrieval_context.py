from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.domain.knowledge.agent_retrieval_context import (
    AgentExpandRequest,
    AgentOpenRequest,
    AgentRefineRequest,
    AgentRetrievalContextFacade,
    AgentSearchRequest,
    AgentTimeRange,
)
from src.domain.knowledge.cognitive_index import CognitiveCard
from src.domain.knowledge.graph_index import GraphIndexCommunity
from src.domain.knowledge.retrieval import RetrievalHit
from src.domain.knowledge.schemas import EvidenceChunk


class _Repo:
    def __init__(self):
        self.community = GraphIndexCommunity(
            community_id="kgc:financial:l0:3",
            version_id="kgc:financial:l0:3:v1",
            adapter_name="financial",
            projection="cognitive_topic",
            level=0,
            parent_community_id="",
            title="AI算力链",
            summary="AI 芯片、光模块、数据中心和算电协同主线。",
            member_node_ids=[],
            member_edge_ids=[],
            evidence_ids=["ev1"],
            chunk_ids=["chunk1", "chunk2"],
            metrics={
                "source_ids": ["ft_news:1"],
                "cognitive_card_ids": ["card1"],
                "maturity": "multi_evidence",
            },
        )
        self.card = CognitiveCard(
            cognitive_card_id="card1",
            adapter_name="financial",
            source_type="news_articles",
            source_id="ft_news:1",
            evidence_id="ev1",
            primary_chunk_id="chunk1",
            chunk_ids=["chunk1"],
            chunk_index=0,
            summary="AI 芯片供应紧张推动算力硬件链关注度提升。",
            title_candidates=["AI算力链", "AI芯片供需"],
            topic_intents=[
                {
                    "parent_themes": ["AI算力链"],
                    "broad_topics": ["人工智能基础设施"],
                    "risk_type": ["供应瓶颈"],
                    "impact_target": ["AI芯片", "光模块"],
                    "actors": ["特斯拉"],
                    "impact_direction": "mixed",
                }
            ],
            risk_signals=[{"risk_type": "供应瓶颈"}],
            local_impact_signals=[{"local_impact_target": ["AI芯片"]}],
            actor_signals={"actors": ["特斯拉"], "industries": ["半导体"]},
            supporting_text=["AI芯片将严重不足"],
            system_pointers={},
            payload={"published_at": "2026-06-01T00:00:00+00:00"},
        )
        self.chunk1 = EvidenceChunk(
            chunk_id="chunk1",
            adapter_name="financial",
            evidence_id="ev1",
            content="AI芯片供应紧张。",
            chunk_index=0,
            previous_chunk_id=None,
            next_chunk_id="chunk2",
            payload={
                "source_type": "news_articles",
                "source_id": "ft_news:1",
                "published_at": "2026-06-01T00:00:00+00:00",
            },
        )
        self.chunk2 = EvidenceChunk(
            chunk_id="chunk2",
            adapter_name="financial",
            evidence_id="ev1",
            content="光模块需求提升。",
            chunk_index=1,
            previous_chunk_id="chunk1",
            next_chunk_id=None,
            payload={
                "source_type": "news_articles",
                "source_id": "ft_news:1",
                "published_at": "2026-06-01T00:00:00+00:00",
            },
        )

    def list_graph_communities(self, adapter_name: str):
        return [self.community]

    def list_cognitive_cards(self, adapter_name: str, *, status: str = "active"):
        return [self.card]

    def list_evidence_chunks(self, adapter_name: str):
        return [self.chunk1, self.chunk2]


class _Retriever:
    enabled = True

    def __init__(self):
        self.last_search_diagnostics = {}
        self.last_options = None
        self.by_id = {
            "kgc:financial:l0:3": RetrievalHit(
                hit_id="kgc:financial:l0:3",
                hit_type="semantic_hybrid",
                title="AI算力链",
                snippet="社区报告",
                score=0.92,
                source="semantic_hybrid",
                chunk_refs=["chunk1", "chunk2"],
                evidence_refs=["ev1"],
                matched_fields=["milvus.community"],
            ),
            "card1": RetrievalHit(
                hit_id="card1",
                hit_type="cognitive_card",
                title="AI芯片供需",
                snippet="认知卡片",
                score=0.83,
                source="semantic_hybrid",
                chunk_refs=["chunk1"],
                evidence_refs=["ev1"],
                matched_fields=["milvus.cognitive_card"],
            ),
            "chunk1": RetrievalHit(
                hit_id="chunk1",
                hit_type="evidence",
                title="AI芯片供应紧张",
                snippet="原文片段",
                score=0.71,
                source="semantic_hybrid",
                chunk_refs=["chunk1"],
                evidence_refs=["ev1"],
            ),
            "chunk2": RetrievalHit(
                hit_id="chunk2",
                hit_type="evidence",
                title="光模块需求提升",
                snippet="邻接片段",
                score=0.62,
                source="semantic_hybrid",
                chunk_refs=["chunk2"],
                evidence_refs=["ev1"],
            ),
        }

    async def search(self, query, options):
        self.last_options = options
        self.last_search_diagnostics = {"query": query}
        return [self.by_id["kgc:financial:l0:3"], self.by_id["card1"], self.by_id["chunk1"]]

    async def get_by_ids(self, target_ids, options):
        self.last_options = options
        return [self.by_id[item] for item in target_ids if item in self.by_id]


class _LayerCoverageRepo(_Repo):
    def __init__(self):
        super().__init__()
        self.unrelated_card = CognitiveCard(
            cognitive_card_id="card_unrelated",
            adapter_name="financial",
            source_type="news_articles",
            source_id="ft_news:2",
            evidence_id="ev2",
            primary_chunk_id="chunk_unrelated",
            chunk_ids=["chunk_unrelated"],
            chunk_index=0,
            summary="商品价格短期波动，带有宏观风险字样但不属于 AI 算力链。",
            title_candidates=["商品价格波动"],
            topic_intents=[],
            risk_signals=[],
            local_impact_signals=[],
            actor_signals={},
            supporting_text=[],
            system_pointers={},
            payload={},
        )

    def list_cognitive_cards(self, adapter_name: str, *, status: str = "active"):
        return [self.card, self.unrelated_card]


class _LayerCoverageRetriever(_Retriever):
    def __init__(self):
        super().__init__()
        self.by_id["card1"] = self.by_id["card1"].model_copy(update={"score": -0.2})
        self.by_id["card_unrelated"] = RetrievalHit(
            hit_id="card_unrelated",
            hit_type="cognitive_card",
            title="商品价格波动",
            snippet="带有宏观和流动性字样，但不是已命中 community 的支撑 card。",
            score=-0.1,
            source="semantic_hybrid",
            chunk_refs=["chunk_unrelated"],
            evidence_refs=["ev2"],
            matched_fields=["milvus.cognitive_card"],
        )

    async def search(self, query, options):
        self.last_options = options
        self.last_search_diagnostics = {"query": query}
        return [
            self.by_id["kgc:financial:l0:3"],
            self.by_id["chunk1"],
            self.by_id["card_unrelated"],
            self.by_id["card1"],
        ]


class _EqualScoreRetriever(_Retriever):
    def __init__(self):
        super().__init__()
        for target_id, hit in list(self.by_id.items()):
            self.by_id[target_id] = hit.model_copy(update={"score": 0.5})


class _WeakRetriever(_Retriever):
    def __init__(self):
        super().__init__()
        self.by_id["chunk2"] = self.by_id["chunk2"].model_copy(
            update={
                "score": 0.02,
                "raw_scores": {"semantic_score": 0.02},
            }
        )

    async def search(self, query, options):
        self.last_options = options
        self.last_search_diagnostics = {"query": query}
        return [self.by_id["chunk2"]]


class _ScopedRepo(_Repo):
    def list_graph_communities(self, adapter_name: str):
        raise AssertionError("search should not use full community load when scoped loader exists")

    def list_cognitive_cards(self, adapter_name: str, *, status: str = "active"):
        raise AssertionError("search should not use full card load when scoped loader exists")

    def list_evidence_chunks(self, adapter_name: str):
        raise AssertionError("search should not use full chunk load when scoped loader exists")

    def list_graph_communities_by_ids(self, adapter_name: str, *, community_ids: list[str]):
        ids = set(community_ids)
        return [self.community] if self.community.community_id in ids else []

    def list_cognitive_cards_by_ids(
        self,
        adapter_name: str,
        *,
        cognitive_card_ids: list[str],
        status: str = "active",
    ):
        ids = set(cognitive_card_ids)
        return [self.card] if self.card.cognitive_card_id in ids else []

    def list_evidence_chunks_by_refs(
        self,
        adapter_name: str,
        *,
        chunk_ids: list[str],
        evidence_ids: list[str],
    ):
        ids = set(chunk_ids)
        evidence = set(evidence_ids)
        return [
            chunk
            for chunk in [self.chunk1, self.chunk2]
            if chunk.chunk_id in ids or chunk.evidence_id in evidence
        ]


@pytest.mark.asyncio
async def test_agent_search_returns_decision_context_and_pushes_time_range():
    retriever = _Retriever()
    facade = AgentRetrievalContextFacade(_Repo(), retriever)

    context = await facade.search(
        AgentSearchRequest(
            query="AI算力链 风险",
            limit=3,
            time_range=AgentTimeRange(start=datetime(2026, 6, 1, tzinfo=timezone.utc)),
            focus_aspects=["risk", "impact"],
        )
    )

    assert retriever.last_options.semantic_time_start == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert [item.layer for item in context.evidence_package] == [
        "community",
        "cognitive_card",
        "evidence_chunk",
    ]
    assert context.coverage_summary.topics
    assert "risk" in context.quality_diagnostics.coverage["covered_aspects"]
    assert context.available_operations


@pytest.mark.asyncio
async def test_agent_search_loads_pg_indexes_scoped_after_semantic_hits():
    retriever = _Retriever()
    facade = AgentRetrievalContextFacade(_ScopedRepo(), retriever)

    context = await facade.search(AgentSearchRequest(query="AI算力链 风险", limit=3))

    assert context.trace["index_load_mode"] == "scoped_by_semantic_hits"
    assert [item.layer for item in context.evidence_package] == [
        "community",
        "cognitive_card",
        "evidence_chunk",
    ]


@pytest.mark.asyncio
async def test_agent_search_filters_repeated_results_with_same_session_id():
    facade = AgentRetrievalContextFacade(_Repo(), _Retriever())
    session_id = "unit-session-dedup"

    first = await facade.search(AgentSearchRequest(query="AI算力链", session_id=session_id, limit=1))
    second = await facade.search(AgentSearchRequest(query="AI算力链", session_id=session_id, limit=1))

    first_id = first.evidence_package[0].result_id
    second_id = second.evidence_package[0].result_id
    assert first_id == "kgc:financial:l0:3"
    assert second_id != first_id
    assert second.trace["session_dedup"]["search_filter"]["removed_count"] >= 1
    assert second.trace["session_dedup"]["record"]["new_result_count"] == 1


@pytest.mark.asyncio
async def test_agent_refine_does_not_fallback_to_repeated_results():
    facade = AgentRetrievalContextFacade(_Repo(), _Retriever())
    session_id = "unit-refine-no-repeat"

    await facade.search(AgentSearchRequest(query="AI算力链", session_id=session_id, limit=3))
    context = await facade.refine(
        AgentRefineRequest(
            query="AI算力链",
            refinement="补充更多不同证据",
            session_id=session_id,
            limit=3,
        )
    )

    assert context.mode == "refine"
    assert context.evidence_package == []
    assert context.trace["session_dedup"]["search_filter"]["reason"] == "all_candidates_were_seen"
    assert context.trace["session_dedup"]["search_filter"]["fallback_return_repeated"] is False


@pytest.mark.asyncio
async def test_agent_refine_suppresses_low_score_noise_after_dedup():
    facade = AgentRetrievalContextFacade(_Repo(), _WeakRetriever())

    context = await facade.refine(
        AgentRefineRequest(
            query="新能源出海",
            refinement="补充更多不同证据",
            session_id="unit-refine-weak-results",
            limit=3,
        )
    )

    assert context.evidence_package == []
    assert context.trace["weak_result_guard"]["applied"] is True
    assert context.trace["weak_result_guard"]["reason"] == "top_score_below_refine_floor"


@pytest.mark.asyncio
async def test_agent_open_expands_neighbors_from_pg_refs():
    facade = AgentRetrievalContextFacade(_Repo(), _Retriever())

    context = await facade.open(
        AgentOpenRequest(target_ids=["card1"], include_neighbors=True, limit=5)
    )

    ids = [item.result_id for item in context.evidence_package]
    assert "card1" in ids
    assert "kgc:financial:l0:3" in ids
    assert "chunk1" in ids
    assert "chunk2" in ids


@pytest.mark.asyncio
async def test_agent_open_query_aware_ranks_neighbor_context():
    facade = AgentRetrievalContextFacade(_Repo(), _EqualScoreRetriever())

    context = await facade.open(
        AgentOpenRequest(target_ids=["card1"], query="光模块 需求", include_neighbors=True, limit=5)
    )

    ids = [item.result_id for item in context.evidence_package]
    assert ids[0] == "card1"
    assert context.trace["query_aware_rank"] is True
    assert ids.index("chunk2") < ids.index("chunk1")


@pytest.mark.asyncio
async def test_agent_expand_community_to_supporting_cards_without_loading_chunks():
    facade = AgentRetrievalContextFacade(_Repo(), _Retriever())

    cards = await facade.expand(
        AgentExpandRequest(target_id="kgc:financial:l0:3", direction="supporting_cards")
    )
    chunks = await facade.expand(
        AgentExpandRequest(target_id="kgc:financial:l0:3", direction="supporting_chunks")
    )

    assert [item.result_id for item in cards.evidence_package] == ["card1"]
    assert chunks.evidence_package == []


@pytest.mark.asyncio
async def test_agent_expand_query_aware_ranks_expanded_context():
    facade = AgentRetrievalContextFacade(_Repo(), _EqualScoreRetriever())

    context = await facade.expand(
        AgentExpandRequest(
            target_id="kgc:financial:l0:3",
            direction="supporting_cards",
            query="光模块 需求",
        )
    )

    assert context.trace["query_aware_rank"] is True
    assert [item.result_id for item in context.evidence_package] == ["card1"]


@pytest.mark.asyncio
async def test_agent_search_layer_backfill_prefers_selected_community_supporting_card():
    facade = AgentRetrievalContextFacade(_LayerCoverageRepo(), _LayerCoverageRetriever())

    context = await facade.search(AgentSearchRequest(query="AI算力链", limit=3))

    ids = [item.result_id for item in context.evidence_package]
    assert "card1" in ids
    assert "card_unrelated" not in ids


@pytest.mark.asyncio
async def test_agent_search_uses_structural_fusion_without_overriding_semantic_rank():
    retriever = _Retriever()
    retriever.by_id["kgc:financial:l0:3"] = retriever.by_id["kgc:financial:l0:3"].model_copy(
        update={"score": 0.90}
    )
    retriever.by_id["chunk1"] = retriever.by_id["chunk1"].model_copy(update={"score": 0.88})
    facade = AgentRetrievalContextFacade(_Repo(), retriever)

    context = await facade.search(
        AgentSearchRequest(query="原文证据 AI芯片供应紧张", limit=3)
    )

    assert context.trace["fusion_diagnostics"]["query_intent"] == "evidence_lookup"
    assert context.trace["fusion_diagnostics"]["query_anchor_count"] > 0
    assert context.evidence_package[0].result_id == "kgc:financial:l0:3"
    assert context.evidence_package[1].result_id == "chunk1"
    assert (
        context.evidence_package[1].metadata["raw_scores"]["agent_fused_score"]
        > context.evidence_package[1].metadata["raw_scores"]["semantic_score"]
    )
