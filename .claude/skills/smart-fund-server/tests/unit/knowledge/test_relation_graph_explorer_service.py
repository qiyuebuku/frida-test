"""Tests for the read-only Graph Community explorer."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.application.services.relation_graph_explorer_service import (
    RelationGraphExplorerService,
)
from src.infrastructure.persistence.repositories.relation_graph_explorer_repository import (
    ExplorerCardRecord,
    ExplorerCommunityRecord,
    ExplorerCommunityRelationRecord,
    ExplorerCommunityRelationSnapshot,
    ExplorerCommunitySnapshot,
    ExplorerEdgeRecord,
    _community_snapshot,
)
from src.infrastructure.vector_store.relation_candidate_store import RelationCardText
from src.interfaces.api.routes import knowledge as knowledge_routes


@pytest.mark.asyncio
async def test_list_communities_exposes_relation_statistics_and_core_card() -> None:
    repository = _FakeRepository(_snapshot())
    service = RelationGraphExplorerService(
        target="test",
        repository=repository,
        card_store=_FakeCardStore(),
    )

    result = await service.list_communities(
        sort_by="edge_count",
        sort_order="desc",
        limit=20,
        offset=0,
    )

    assert result["total"] == 1
    assert result["sort_by"] == "edge_count"
    assert result["sort_order"] == "desc"
    assert repository.last_list_kwargs["sort_by"] == "edge_count"
    assert repository.last_list_kwargs["sort_order"] == "desc"
    item = result["items"][0]
    assert item["card_count"] == 3
    assert item["edge_count"] == 2
    assert item["observed_edge_count"] == 1
    assert item["inferred_edge_count"] == 1
    assert item["relation_kind_counts"] == {
        "causal_influence": 1,
        "temporal_progression": 1,
    }
    assert item["core_card_id"] == "card:b"
    assert item["core_card_degree"] == 2


@pytest.mark.asyncio
async def test_get_community_joins_pg_graph_with_milvus_card_views() -> None:
    snapshot = _snapshot()
    service = RelationGraphExplorerService(
        target="test",
        repository=_FakeRepository(snapshot),
        card_store=_FakeCardStore(),
    )

    result = await service.get_community(community_id="community:1")

    assert result is not None
    assert result["community"]["graph_density"] == pytest.approx(2 / 3)
    assert result["community"]["missing_card_content_count"] == 0
    assert result["community"]["source_published_at_start"] == "2026-07-01T08:00:00+00:00"
    assert result["nodes"][1]["summary"] == "Card B summary"
    assert result["nodes"][1]["focus_evidence"] == "Card B evidence"
    assert result["nodes"][1]["degree"] == 2
    assert result["nodes"][1]["in_degree"] == 1
    assert result["nodes"][1]["out_degree"] == 1
    assert result["edges"][0]["cross_chunk"] is True
    assert result["edges"][1]["cross_chunk"] is False


def test_community_snapshot_projects_raw_edge_endpoint_to_fact_representative() -> None:
    now = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    source = _stored_card("card:source", "fact:source", now)
    representative = _stored_card("card:representative", "fact:target", now)
    alias = _stored_card("card:alias", "fact:target", now)
    edge = _stored_edge(
        "edge:alias-target",
        "card:source",
        "card:alias",
        now,
    )
    community = _stored_community(
        member_card_ids=("card:source", "card:representative"),
        member_edge_ids=("edge:alias-target",),
        now=now,
    )

    snapshot = _community_snapshot(
        community,
        card_row_by_id={
            source.cognitive_card_id: source,
            representative.cognitive_card_id: representative,
            alias.cognitive_card_id: alias,
        },
        edge_row_by_id={edge.id: edge},
    )

    assert [card.card_id for card in snapshot.cards] == [
        "card:source",
        "card:representative",
    ]
    assert len(snapshot.edges) == 1
    assert snapshot.edges[0].source_card_id == "card:source"
    assert snapshot.edges[0].target_card_id == "card:representative"


@pytest.mark.asyncio
async def test_get_community_returns_none_when_current_graph_is_missing() -> None:
    service = RelationGraphExplorerService(
        target="test",
        repository=_FakeRepository(None),
        card_store=_FakeCardStore(),
    )

    assert await service.get_community(community_id="missing") is None


@pytest.mark.asyncio
async def test_overview_exposes_flat_communities_and_cross_relations() -> None:
    relation = _community_relation()
    repository = _FakeRepository(
        [_snapshot(), _second_snapshot()],
        relations=[relation],
    )
    service = RelationGraphExplorerService(
        target="test",
        repository=repository,
        card_store=_FakeCardStore(),
    )

    result = await service.get_overview(
        sort_by="relation_count",
        sort_order="desc",
        limit=20,
        offset=0,
    )

    assert result["visible_community_count"] == 2
    assert result["visible_relation_count"] == 1
    assert result["nodes"][0]["representative_summary"]
    assert result["nodes"][0]["community_relation_count"] == 1
    assert result["edges"][0]["relation_id"] == relation.relation_id
    assert result["edges"][0]["supporting_edge_ids"] == ["edge:cross"]


@pytest.mark.asyncio
async def test_overview_limit_zero_loads_every_community_page() -> None:
    repository = _PagedOverviewRepository()
    service = RelationGraphExplorerService(
        target="test",
        repository=repository,
        card_store=_FakeCardStore(),
    )

    result = await service.get_overview(limit=0)

    assert result["total"] == 501
    assert result["visible_community_count"] == 501
    assert repository.offsets == [0, 500]
    assert all(
        node["representative_summary"]
        for node in result["nodes"]
    )


@pytest.mark.asyncio
async def test_community_relation_detail_exposes_supporting_card_edges() -> None:
    relation = _community_relation()
    cross_edge = _edge(
        "edge:cross",
        "card:a",
        "card:d",
        "market_co_movement",
        "observed",
        datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc),
    )
    repository = _FakeRepository(
        [_snapshot(), _second_snapshot()],
        relations=[relation],
        relation_snapshot=ExplorerCommunityRelationSnapshot(
            relation=relation,
            edges=(cross_edge,),
        ),
    )
    service = RelationGraphExplorerService(
        target="test",
        repository=repository,
        card_store=_FakeCardStore(),
    )

    result = await service.get_community_relation(
        relation_id=relation.relation_id,
        adapter_name="financial",
    )

    assert result is not None
    assert result["relation"]["supporting_edge_count"] == 1
    assert result["supporting_edges"][0]["source_summary"] == "Card A summary"
    assert result["supporting_edges"][0]["target_summary"] == "Card D summary"


def test_graph_community_api_and_viewer_redirect(monkeypatch) -> None:
    monkeypatch.setattr(
        knowledge_routes,
        "create_relation_graph_explorer_service",
        lambda target: _FakeApiService(target),
    )
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    client = TestClient(app)

    response = client.get(
        "/api/kg/graph-communities",
        params={
            "target": "test",
            "adapter_name": "financial",
            "sort_by": "edge_count",
            "sort_order": "asc",
        },
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["community_id"] == "community:api"

    overview = client.get(
        "/api/kg/graph-community-overview",
        params={"target": "test"},
    )
    assert overview.status_code == 200
    assert overview.json()["nodes"][0]["community_id"] == "community:api"

    invalid_sort = client.get(
        "/api/kg/graph-communities",
        params={"sort_by": "unknown"},
    )
    assert invalid_sort.status_code == 422

    detail = client.get(
        "/api/kg/graph-communities/community%3Aapi",
        params={"target": "test"},
    )
    assert detail.status_code == 200
    assert detail.json()["community"]["community_id"] == "community:api"

    missing = client.get(
        "/api/kg/graph-communities/missing",
        params={"target": "test"},
    )
    assert missing.status_code == 404

    relation = client.get(
        "/api/kg/graph-community-relations/relation%3Aapi",
        params={"target": "test"},
    )
    assert relation.status_code == 200
    assert relation.json()["relation"]["relation_id"] == "relation:api"

    viewer = client.get("/api/kg/graph-viewer", follow_redirects=False)
    assert viewer.status_code == 307
    assert viewer.headers["location"] == "/static/kg_graph_explorer.html"

    viewer_with_scope = client.get(
        "/api/kg/graph-viewer?target=test&community=community%3Aapi",
        follow_redirects=False,
    )
    assert viewer_with_scope.headers["location"] == (
        "/static/kg_graph_explorer.html?target=test&community=community%3Aapi"
    )


class _FakeRepository:
    def __init__(
        self,
        snapshot: (
            ExplorerCommunitySnapshot
            | list[ExplorerCommunitySnapshot]
            | None
        ),
        *,
        relations: list[ExplorerCommunityRelationRecord] | None = None,
        relation_snapshot: ExplorerCommunityRelationSnapshot | None = None,
    ) -> None:
        self.snapshots = (
            list(snapshot)
            if isinstance(snapshot, list)
            else ([snapshot] if snapshot else [])
        )
        self.snapshot = self.snapshots[0] if self.snapshots else None
        self.relations = list(relations or [])
        self.relation_snapshot = relation_snapshot
        self.last_list_kwargs = {}

    def list_communities(self, **kwargs):
        self.last_list_kwargs = kwargs
        return len(self.snapshots), self.snapshots

    def list_community_records(self, **kwargs):
        self.last_list_kwargs = kwargs
        return (
            len(self.snapshots),
            [snapshot.community for snapshot in self.snapshots],
        )

    def load_community(self, **_kwargs):
        return self.snapshot

    def list_community_relations(self, **_kwargs):
        return self.relations

    def load_community_relation(self, **_kwargs):
        return self.relation_snapshot


class _PagedOverviewRepository:
    def __init__(self) -> None:
        self.offsets: list[int] = []

    def list_community_records(self, **kwargs):
        offset = kwargs["offset"]
        self.offsets.append(offset)
        if offset == 0:
            return 501, [_snapshot().community] * 500
        if offset == 500:
            return 501, [_second_snapshot().community]
        return 501, []

    def list_community_relations(self, **_kwargs):
        return []


class _FakeCardStore:
    async def get_summaries(self, card_ids, **_kwargs):
        return {
            card_id: RelationCardText(
                card_id=card_id,
                text=f"Card {card_id[-1].upper()} summary",
                metadata={
                    "source_published_at": (
                        f"2026-07-0{index}T08:00:00+00:00"
                    ),
                    "chunk_summary": "Chunk summary",
                },
            )
            for index, card_id in enumerate(card_ids, start=1)
        }

    async def get_focus_evidence(self, card_ids, **_kwargs):
        return {
            card_id: RelationCardText(
                card_id=card_id,
                text=f"Card {card_id[-1].upper()} evidence",
                metadata={},
            )
            for card_id in card_ids
        }


class _FakeApiService:
    def __init__(self, target: str) -> None:
        self.target = target

    async def list_communities(self, **_kwargs):
        return {
            "items": [{"community_id": "community:api"}],
            "total": 1,
            "limit": 100,
            "offset": 0,
        }

    async def get_overview(self, **_kwargs):
        return {
            "nodes": [{"community_id": "community:api"}],
            "edges": [],
            "total": 1,
        }

    async def get_community(self, *, community_id: str):
        if community_id == "missing":
            return None
        return {"community": {"community_id": community_id}, "nodes": [], "edges": []}

    async def get_community_relation(
        self,
        *,
        relation_id: str,
        adapter_name: str,
    ):
        assert adapter_name == "financial"
        if relation_id == "missing":
            return None
        return {"relation": {"relation_id": relation_id}, "supporting_edges": []}


def _snapshot() -> ExplorerCommunitySnapshot:
    now = datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc)
    community = ExplorerCommunityRecord(
        community_id="community:1",
        adapter_name="financial",
        identity_anchor_card_id="card:a",
        member_card_ids=("card:a", "card:b", "card:c"),
        member_edge_ids=("edge:1", "edge:2"),
        graph_fingerprint="fingerprint",
        graph_version=2,
        graph_status="active",
        title="测试关系社区",
        fact_report="事实报告",
        fact_report_version=1,
        fact_report_status="ready",
        conditional_projections=({"judgement": "条件预测"},),
        projection_version=1,
        projection_status="ready",
        graph_changed_at=now,
        fact_report_generated_at=now,
        projection_generated_at=now,
        created_at=now,
        updated_at=now,
    )
    cards = (
        _card("card:a", "chunk:a", now),
        _card("card:b", "chunk:b", now),
        _card("card:c", "chunk:b", now),
    )
    edges = (
        _edge(
            "edge:1",
            "card:a",
            "card:b",
            "causal_influence",
            "observed",
            now,
        ),
        _edge(
            "edge:2",
            "card:b",
            "card:c",
            "temporal_progression",
            "inferred",
            now,
        ),
    )
    return ExplorerCommunitySnapshot(community=community, cards=cards, edges=edges)


def _stored_card(card_id: str, fact_id: str, now: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        cognitive_card_id=card_id,
        adapter_name="financial",
        source_type="news_articles",
        source_id=f"ft_news:{card_id}",
        evidence_id=f"evidence:{card_id}",
        primary_chunk_id=f"chunk:{card_id}",
        chunk_ids=[f"chunk:{card_id}"],
        focus_evidence_refs=["s0001"],
        relation_probes=[],
        fact_id=fact_id,
        status="active",
        created_at=now,
        updated_at=now,
    )


def _stored_edge(
    edge_id: str,
    source_card_id: str,
    target_card_id: str,
    now: datetime,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=edge_id,
        source_card_id=source_card_id,
        target_card_id=target_card_id,
        relation_kind="same_event",
        relation_type="事件关联",
        direction="source -> target",
        decision_class="observed",
        basis="原文支持",
        source_evidence_refs=["s0001"],
        target_evidence_refs=["s0001"],
        relation_evidence_refs=["s0001"],
        inference_mechanism="",
        confidence=0.9,
        status="active",
        created_at=now,
        updated_at=now,
    )


def _stored_community(
    *,
    member_card_ids: tuple[str, ...],
    member_edge_ids: tuple[str, ...],
    now: datetime,
) -> SimpleNamespace:
    return SimpleNamespace(
        community_id="community:fact-projection",
        adapter_name="financial",
        identity_anchor_card_id=member_card_ids[0],
        member_card_ids=list(member_card_ids),
        member_edge_ids=list(member_edge_ids),
        graph_fingerprint="fingerprint",
        graph_version=1,
        graph_status="active",
        title="",
        fact_report="",
        fact_report_version=0,
        fact_report_status="missing",
        conditional_projections=[],
        projection_version=0,
        projection_status="missing",
        graph_changed_at=now,
        fact_report_generated_at=None,
        projection_generated_at=None,
        created_at=now,
        updated_at=now,
    )


def _second_snapshot() -> ExplorerCommunitySnapshot:
    now = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)
    community = ExplorerCommunityRecord(
        community_id="community:2",
        adapter_name="financial",
        identity_anchor_card_id="card:d",
        member_card_ids=("card:d", "card:e"),
        member_edge_ids=("edge:3",),
        graph_fingerprint="fingerprint-2",
        graph_version=1,
        graph_status="active",
        title="第二关系社区",
        fact_report="",
        fact_report_version=0,
        fact_report_status="missing",
        conditional_projections=(),
        projection_version=0,
        projection_status="missing",
        graph_changed_at=now,
        fact_report_generated_at=None,
        projection_generated_at=None,
        created_at=now,
        updated_at=now,
    )
    return ExplorerCommunitySnapshot(
        community=community,
        cards=(
            _card("card:d", "chunk:d", now),
            _card("card:e", "chunk:e", now),
        ),
        edges=(
            _edge(
                "edge:3",
                "card:d",
                "card:e",
                "confirmation",
                "observed",
                now,
            ),
        ),
    )


def _community_relation() -> ExplorerCommunityRelationRecord:
    now = datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc)
    return ExplorerCommunityRelationRecord(
        relation_id="relation:1",
        source_community_id="community:1",
        target_community_id="community:2",
        relation_kind="market_co_movement",
        supporting_edge_ids=("edge:cross",),
        observed_edge_count=1,
        inferred_edge_count=0,
        relation_fingerprint="relation-fingerprint",
        status="active",
        created_at=now,
        updated_at=now,
    )


def _card(
    card_id: str,
    chunk_id: str,
    now: datetime,
) -> ExplorerCardRecord:
    return ExplorerCardRecord(
        card_id=card_id,
        source_type="ft_news",
        source_id=f"source:{card_id[-1]}",
        evidence_id=f"evidence:{card_id[-1]}",
        primary_chunk_id=chunk_id,
        chunk_ids=(chunk_id,),
        focus_evidence_refs=("s0001",),
        relation_probes=(),
        status="active",
        created_at=now,
        updated_at=now,
    )


def _edge(
    edge_id: str,
    source: str,
    target: str,
    kind: str,
    decision_class: str,
    now: datetime,
) -> ExplorerEdgeRecord:
    return ExplorerEdgeRecord(
        edge_id=edge_id,
        source_card_id=source,
        target_card_id=target,
        relation_kind=kind,
        relation_type=kind,
        direction=f"{source} -> {target}",
        decision_class=decision_class,
        basis="关系依据",
        source_evidence_refs=("s0001",),
        target_evidence_refs=("s0002",),
        relation_evidence_refs=("s0001", "s0002"),
        inference_mechanism="",
        confidence=0.9,
        status="active",
        created_at=now,
        updated_at=now,
    )
