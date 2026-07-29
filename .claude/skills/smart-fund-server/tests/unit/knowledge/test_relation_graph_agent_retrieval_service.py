"""Tests for Agent-facing relationship graph retrieval tools."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.application.services.relation_graph_agent_retrieval_service import (
    RelationGraphAgentRetrievalService,
)
from src.infrastructure.persistence.repositories.relation_graph_agent_repository import (
    AgentCommunityGraphSnapshot,
    AgentGraphCardRecord,
    AgentGraphCommunityRecord,
    AgentGraphCommunityRelationRecord,
    AgentGraphEdgeRecord,
    AgentGraphSnapshot,
)
from src.infrastructure.vector_store.relation_candidate_store import (
    MilvusRelationCandidateStore,
    RelationCardSearchHit,
    RelationCardText,
)
from src.infrastructure.vector_store import relation_candidate_store as candidate_store_module
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridHit
from src.interfaces.api.routes import knowledge as knowledge_routes


@pytest.mark.asyncio
async def test_search_returns_ranked_cards_without_expanding_edges() -> None:
    repository = _FakeRepository()
    service = _service(repository)

    result = await service.search(
        query="存储芯片涨价",
        seed_limit=2,
        candidate_limit=4,
    )

    assert result["operation"] == "search"
    assert [item["card_id"] for item in result["cards"]] == [
        "card:b",
        "card:a",
    ]
    assert result["cards"][0]["summary"] == "产品价格上涨"
    assert result["cards"][0]["community_ids"] == ["community:1"]
    assert result["cards"][0]["relation_ids"] == []
    assert result["cards"][0]["retrieval"]["retrieval_rank"] == 1
    assert result["diagnostics"]["recalled_candidate_count"] == 3
    assert result["diagnostics"]["active_candidate_count"] == 2
    assert repository.last_subgraph_kwargs["hop_limit"] == 0


@pytest.mark.asyncio
async def test_search_returns_only_highest_ranked_card_per_fact() -> None:
    class DuplicateFactRepository(_FakeRepository):
        def load_cards(self, **kwargs):
            cards = super().load_cards(**kwargs)
            return [
                AgentGraphCardRecord(
                    **{
                        **card.__dict__,
                        "fact_id": "fact:shared",
                    }
                )
                for card in cards
            ]

        def load_fact_card_counts(self, **kwargs):
            return {"fact:shared": 2}

        def load_subgraph(self, **kwargs):
            snapshot = super().load_subgraph(**kwargs)
            representative_community = AgentGraphCommunityRecord(
                community_id="community:1",
                title="",
                identity_anchor_card_id="card:a",
                member_card_ids=("card:a",),
                member_edge_ids=("edge:1",),
                graph_version=1,
                graph_changed_at=datetime(
                    2026,
                    7,
                    3,
                    tzinfo=timezone.utc,
                ),
            )
            return AgentGraphSnapshot(
                cards=tuple(
                    AgentGraphCardRecord(
                        **{
                            **card.__dict__,
                            "fact_id": "fact:shared",
                        }
                    )
                    for card in snapshot.cards
                ),
                edges=snapshot.edges,
                communities=(representative_community,),
                community_relations=snapshot.community_relations,
                hop_by_card_id=snapshot.hop_by_card_id,
                truncated=snapshot.truncated,
                community_ids_by_card_id={
                    "card:a": ("community:1",),
                    "card:b": ("community:1",),
                },
            )

    result = await _service(DuplicateFactRepository()).search(
        query="存储芯片涨价",
        seed_limit=2,
        candidate_limit=4,
    )

    assert [item["card_id"] for item in result["cards"]] == ["card:b"]
    assert result["cards"][0]["fact_id"] == "fact:shared"
    assert result["cards"][0]["fact_card_count"] == 2
    assert result["cards"][0]["community_ids"] == ["community:1"]
    assert result["diagnostics"]["fact_duplicate_count"] == 1


@pytest.mark.asyncio
async def test_seed_limit_truncation_is_not_reported_as_fact_duplication() -> None:
    result = await _service(_FakeRepository()).search(
        query="存储芯片涨价",
        seed_limit=1,
        candidate_limit=4,
    )

    assert len(result["cards"]) == 1
    assert result["diagnostics"]["fact_duplicate_count"] == 0


@pytest.mark.asyncio
async def test_milvus_card_search_fuses_summary_and_focus_views(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        candidate_store_module,
        "embed_texts",
        _fake_embed_texts,
    )
    store = MilvusRelationCandidateStore(store=_FakeTypedMilvusStore())

    result = await store.search_cards(
        "存储芯片涨价",
        adapter_name="financial",
        target="test",
        limit=3,
    )

    assert [item.card_id for item in result] == [
        "card:a",
        "card:b",
        "card:c",
    ]
    assert result[0].matched_views == ("focus_evidence", "summary")
    assert result[0].summary == "上游供应收紧"


@pytest.mark.asyncio
async def test_card_expand_returns_verified_edges_and_hop_distance() -> None:
    service = _service(_FakeRepository())

    result = await service.expand_cards(
        card_ids=["card:a"],
        hop_limit=1,
        decision_classes=["observed"],
    )

    assert result["operation"] == "card_expand"
    assert [card["hop"] for card in result["cards"]] == [0, 1]
    assert result["edges"][0]["edge_id"] == "edge:1"
    assert result["edges"][0]["decision_class"] == "observed"
    assert "basis" not in result["edges"][0]
    assert "source_evidence_refs" not in result["edges"][0]


@pytest.mark.asyncio
async def test_open_card_and_edge_return_focus_evidence_separately() -> None:
    service = _service(_FakeRepository())

    cards = await service.open_cards(card_ids=["card:a"])
    edges = await service.open_edges(edge_ids=["edge:1"])

    assert cards["cards"][0]["focus_evidence"] == "供应收紧原文"
    assert cards["cards"][0]["relation_ids"] == ["edge:1"]
    assert edges["edges"][0]["source_card"]["summary"] == "上游供应收紧"
    assert edges["edges"][0]["target_card"]["focus_evidence"] == "价格上涨原文"
    assert edges["edges"][0]["relation_kind"] == "causal_influence"
    assert edges["edges"][0]["basis"] == "供应收紧推动价格上涨"


@pytest.mark.asyncio
async def test_community_open_and_expand_keep_distinct_contracts() -> None:
    service = _service(_FakeRepository())

    opened = await service.open_communities(
        community_ids=["community:1"],
        member_limit=10,
        edge_limit=10,
    )
    expanded = await service.expand_communities(
        community_ids=["community:1"],
    )

    assert opened["communities"][0]["members"][0]["summary"]
    assert opened["communities"][0]["edges"][0]["edge_id"] == "edge:1"
    assert "basis" not in opened["communities"][0]["edges"][0]
    assert "members" not in expanded["communities"][0]
    assert expanded["community_relations"][0]["relation_id"] == "crel:1"
    assert expanded["communities"][1]["hop"] == 1


def test_relation_graph_agent_http_contracts(monkeypatch) -> None:
    service = _FakeApiService()
    monkeypatch.setattr(
        knowledge_routes,
        "create_relation_graph_agent_retrieval_service",
        lambda target: service,
    )
    monkeypatch.setattr(knowledge_routes, "langfuse_flush", lambda: None)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    client = TestClient(app)

    cases = [
        (
            "/api/kg/agent/relation-graph/search",
            {"query": "存储芯片涨价", "session_id": "eval-1"},
            "search",
        ),
        (
            "/api/kg/agent/relation-graph/cards/expand",
            {"card_ids": ["card:a"]},
            "card_expand",
        ),
        (
            "/api/kg/agent/relation-graph/communities/expand",
            {"community_ids": ["community:1"]},
            "community_expand",
        ),
        (
            "/api/kg/agent/relation-graph/cards/open",
            {"card_ids": ["card:a"]},
            "card_open",
        ),
        (
            "/api/kg/agent/relation-graph/edges/open",
            {"edge_ids": ["edge:1"]},
            "edge_open",
        ),
        (
            "/api/kg/agent/relation-graph/communities/open",
            {"community_ids": ["community:1"]},
            "community_open",
        ),
    ]
    for endpoint, payload, expected in cases:
        response = client.post(endpoint, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["operation"] == expected

    assert service.calls == [
        "search",
        "card_expand",
        "community_expand",
        "card_open",
        "edge_open",
        "community_open",
    ]


class _FakeReranker:
    async def rerank(self, *, query, documents, top_n=None):
        return type(
            "Response",
            (),
            {
                "results": [
                    type(
                        "Item",
                        (),
                        {
                            "index": index,
                            "relevance_score": 0.8 + index * 0.1,
                        },
                    )()
                    for index in reversed(
                        range(
                            min(
                                top_n or len(documents),
                                len(documents),
                            )
                        )
                    )
                ]
            },
        )()


class _FakeCardStore:
    async def search_cards(self, query, **kwargs):
        return [
            RelationCardSearchHit(
                card_id="card:a",
                summary="上游供应收紧",
                metadata={"source_published_at": "2026-07-01T00:00:00+00:00"},
                matched_views=("summary", "focus_evidence"),
                fusion_score=0.03,
                semantic_score=0.8,
            ),
            RelationCardSearchHit(
                card_id="card:b",
                summary="产品价格上涨",
                metadata={"source_published_at": "2026-07-02T00:00:00+00:00"},
                matched_views=("summary",),
                fusion_score=0.02,
                semantic_score=0.7,
            ),
            RelationCardSearchHit(
                card_id="card:inactive",
                summary="已经失效的旧 Card",
                metadata={},
                matched_views=("summary",),
                fusion_score=0.01,
                semantic_score=0.6,
            ),
        ]

    async def get_summaries(self, card_ids, **kwargs):
        values = {
            "card:a": ("上游供应收紧", "2026-07-01T00:00:00+00:00"),
            "card:b": ("产品价格上涨", "2026-07-02T00:00:00+00:00"),
            "card:c": ("下游利润承压", "2026-07-03T00:00:00+00:00"),
        }
        return {
            card_id: RelationCardText(
                card_id=card_id,
                text=values[card_id][0],
                metadata={"source_published_at": values[card_id][1]},
            )
            for card_id in card_ids
            if card_id in values
        }

    async def get_focus_evidence(self, card_ids, **kwargs):
        values = {
            "card:a": "供应收紧原文",
            "card:b": "价格上涨原文",
            "card:c": "利润承压原文",
        }
        return {
            card_id: RelationCardText(
                card_id=card_id,
                text=values[card_id],
                metadata={},
            )
            for card_id in card_ids
            if card_id in values
        }


class _FakeTypedMilvusStore:
    def ensure_ready(self):
        return None

    def hybrid_search(self, *, collection_role, **kwargs):
        if collection_role == "cognitive_card":
            return [
                _milvus_hit("card:a", "上游供应收紧", 0.9),
                _milvus_hit("card:b", "产品价格上涨", 0.8),
            ]
        return [
            _milvus_hit("card:a", "供应收紧原文", 0.85),
            _milvus_hit("card:c", "利润承压原文", 0.75),
        ]

    def get_documents(self, *, collection_role, target_ids, **kwargs):
        summaries = {
            "card:a": "上游供应收紧",
            "card:b": "产品价格上涨",
            "card:c": "下游利润承压",
        }
        return [
            _milvus_hit(card_id, summaries[card_id], 1.0)
            for card_id in target_ids
            if card_id in summaries
        ]


class _FakeRepository:
    def __init__(self):
        self.last_subgraph_kwargs = {}

    def load_subgraph(self, **kwargs):
        self.last_subgraph_kwargs = kwargs
        seeds = kwargs["seed_card_ids"]
        if kwargs["hop_limit"] == 0:
            cards = tuple(_card(card_id) for card_id in seeds)
            edges = ()
            hops = {card_id: 0 for card_id in seeds}
        else:
            cards = (_card("card:a"), _card("card:b"))
            edges = (_edge(),)
            hops = {"card:a": 0, "card:b": 1}
        return AgentGraphSnapshot(
            cards=cards,
            edges=edges,
            communities=(_community("community:1"),),
            community_relations=(),
            hop_by_card_id=hops,
            truncated=False,
        )

    def load_cards(self, **kwargs):
        return [
            _card(card_id)
            for card_id in kwargs["card_ids"]
            if card_id != "card:inactive"
        ]

    def load_fact_card_counts(self, **kwargs):
        return {
            fact_id: 1
            for fact_id in kwargs["fact_ids"]
        }

    def load_edges(self, **kwargs):
        return [_edge()] if "edge:1" in kwargs["edge_ids"] else []

    def load_communities(self, **kwargs):
        return [
            _community(community_id)
            for community_id in kwargs["community_ids"]
        ]

    def load_community_neighborhood(self, **kwargs):
        return AgentCommunityGraphSnapshot(
            communities=(
                _community("community:1"),
                _community("community:2"),
            ),
            relations=(
                AgentGraphCommunityRelationRecord(
                    relation_id="crel:1",
                    source_community_id="community:1",
                    target_community_id="community:2",
                    relation_kind="causal_influence",
                    supporting_edge_ids=("edge:cross",),
                    observed_edge_count=1,
                    inferred_edge_count=0,
                ),
            ),
            hop_by_community_id={
                "community:1": 0,
                "community:2": 1,
            },
            truncated=False,
        )


class _FakeApiService:
    def __init__(self):
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append("search")
        return {"operation": "search"}

    async def expand_cards(self, **kwargs):
        self.calls.append("card_expand")
        return {"operation": "card_expand"}

    async def expand_communities(self, **kwargs):
        self.calls.append("community_expand")
        return {"operation": "community_expand"}

    async def open_cards(self, **kwargs):
        self.calls.append("card_open")
        return {"operation": "card_open"}

    async def open_edges(self, **kwargs):
        self.calls.append("edge_open")
        return {"operation": "edge_open"}

    async def open_communities(self, **kwargs):
        self.calls.append("community_open")
        return {"operation": "community_open"}


def _service(repository):
    return RelationGraphAgentRetrievalService(
        target="test",
        repository=repository,
        card_store=_FakeCardStore(),
        reranker=_FakeReranker(),
    )


def _card(card_id: str) -> AgentGraphCardRecord:
    return AgentGraphCardRecord(
        card_id=card_id,
        source_type="news_articles",
        source_id=f"ft_news:{card_id[-1]}",
        evidence_id=f"evidence:{card_id[-1]}",
        primary_chunk_id=f"chunk:{card_id[-1]}",
        chunk_ids=(f"chunk:{card_id[-1]}",),
        focus_evidence_refs=("s0001",),
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        fact_id=f"fact:{card_id}",
    )


def _edge() -> AgentGraphEdgeRecord:
    return AgentGraphEdgeRecord(
        edge_id="edge:1",
        source_card_id="card:a",
        target_card_id="card:b",
        relation_kind="causal_influence",
        relation_type="因果影响",
        direction="source_to_target",
        decision_class="observed",
        basis="供应收紧推动价格上涨",
        inference_mechanism="",
        confidence=0.95,
        source_evidence_refs=("s0001",),
        target_evidence_refs=("s0002",),
        relation_evidence_refs=(
            {"chunk_id": "chunk:a", "refs": ["s0001", "s0002"]},
        ),
        created_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )


def _community(community_id: str) -> AgentGraphCommunityRecord:
    return AgentGraphCommunityRecord(
        community_id=community_id,
        title="",
        identity_anchor_card_id="card:a",
        member_card_ids=("card:a", "card:b"),
        member_edge_ids=("edge:1",),
        graph_version=1,
        graph_changed_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )


async def _fake_embed_texts(texts):
    return [[0.1, 0.2] for _ in texts]


def _milvus_hit(
    card_id: str,
    text: str,
    score: float,
) -> MilvusHybridHit:
    return MilvusHybridHit(
        chunk_id=card_id,
        evidence_id=f"evidence:{card_id}",
        text=text,
        score=score,
        metadata={
            "target_id": card_id,
            "source_published_at": "2026-07-01T00:00:00+00:00",
        },
    )
