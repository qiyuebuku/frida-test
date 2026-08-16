from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace

import pytest

from src.application.services import relation_graph_community_service as service_module
from src.application.services.relation_graph_community_service import (
    RelationGraphCommunityService,
)
from src.domain.knowledge import relation_graph_community as community_module
from src.domain.knowledge.relation_graph_community import (
    AffectedRelationGraph,
    ExistingRelationGraphCommunity,
    RelationGraphClusteringConfig,
    RelationGraphEdge,
    discover_relation_graph_components,
    discover_relation_graph_partition,
    derive_community_relations_from_membership,
    project_edges_to_fact_representatives,
    relation_graph_community_id,
)
from src.infrastructure.persistence.repositories.relation_graph_community_repository import (
    GraphCommunityApplyResult,
)


def _edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    relation_kind: str = "causal_influence",
    decision_class: str = "observed",
    content_version: str = "v1",
) -> RelationGraphEdge:
    return RelationGraphEdge(
        edge_id=edge_id,
        source_card_id=source,
        target_card_id=target,
        relation_kind=relation_kind,
        decision_class=decision_class,
        content_version=content_version,
    )


def _graph(*edges: RelationGraphEdge) -> AffectedRelationGraph:
    return AffectedRelationGraph(
        adapter_name="financial",
        seed_card_ids=("c1",),
        touched_community_ids=(),
        edges=edges,
    )


def test_single_edge_forms_one_relation_community() -> None:
    components = discover_relation_graph_components(_graph(_edge("e1", "c1", "c2")))

    assert len(components) == 1
    assert components[0].community_id == relation_graph_community_id(
        "financial", "c1"
    )
    assert components[0].identity_anchor_card_id == "c1"
    assert components[0].member_card_ids == ("c1", "c2")
    assert components[0].member_edge_ids == ("e1",)


def test_fact_projection_removes_duplicate_edge_and_keeps_external_support() -> None:
    projected = project_edges_to_fact_representatives(
        [
            _edge("same", "c1", "c2", relation_kind="same_fact"),
            _edge("support:1", "c1", "c3"),
            _edge("support:2", "c2", "c4"),
        ],
        representative_by_card_id={
            "c1": "c1",
            "c2": "c1",
            "c3": "c3",
            "c4": "c3",
        },
    )

    assert [edge.edge_id for edge in projected] == [
        "support:1",
        "support:2",
    ]
    assert {
        (edge.source_card_id, edge.target_card_id)
        for edge in projected
    } == {("c1", "c3")}


def test_same_event_remains_a_business_edge_between_distinct_facts() -> None:
    projected = project_edges_to_fact_representatives(
        [_edge("event", "c1", "c2", relation_kind="same_event")],
        representative_by_card_id={"c1": "c1", "c2": "c2"},
    )

    assert [edge.edge_id for edge in projected] == ["event"]


def test_boundary_relation_rebinds_after_membership_split() -> None:
    edges = [_edge("boundary", "a1", "outside", relation_kind="confirmation")]

    before = derive_community_relations_from_membership(
        edges=edges,
        community_by_card={"a1": "community:a", "outside": "community:x"},
    )
    after = derive_community_relations_from_membership(
        edges=edges,
        community_by_card={"a1": "community:b", "outside": "community:x"},
    )

    assert before[0].source_community_id == "community:a"
    assert after[0].source_community_id == "community:b"
    assert before[0].relation_id != after[0].relation_id


def test_boundary_relation_disappears_after_membership_merge() -> None:
    relations = derive_community_relations_from_membership(
        edges=[_edge("merged", "a1", "b1")],
        community_by_card={"a1": "community:a", "b1": "community:a"},
    )

    assert relations == []


@pytest.mark.parametrize(
    ("before_edges", "after_edges", "changed_cards"),
    [
        ([], [_edge("e1", "a", "b")], {"a", "b"}),
        (
            [_edge("e1", "a", "b"), _edge("e2", "c", "d")],
            [
                _edge("e1", "a", "b"),
                _edge("e2", "c", "d"),
                _edge("bridge", "b", "c"),
            ],
            {"b", "c"},
        ),
        (
            [_edge("e1", "a", "b"), _edge("e2", "b", "c")],
            [_edge("e1", "a", "b")],
            {"b", "c"},
        ),
        (
            [
                _edge("e1", "a", "b"),
                _edge("e2", "b", "c"),
                _edge("e3", "c", "d"),
            ],
            [_edge("e1", "a", "b"), _edge("e3", "c", "d")],
            {"b", "c"},
        ),
    ],
)
def test_local_incremental_replay_matches_full_rebuild(
    before_edges: list[RelationGraphEdge],
    after_edges: list[RelationGraphEdge],
    changed_cards: set[str],
) -> None:
    before = discover_relation_graph_partition(_graph(*before_edges))
    touched = [
        component
        for component in before.communities
        if changed_cards.intersection(component.member_card_ids)
    ]
    scoped_cards = set(changed_cards)
    for component in touched:
        scoped_cards.update(component.member_card_ids)
    # Directly bridged existing Communities are included once.
    for edge in after_edges:
        if edge.source_card_id in scoped_cards or edge.target_card_id in scoped_cards:
            for component in before.communities:
                if {
                    edge.source_card_id,
                    edge.target_card_id,
                }.intersection(component.member_card_ids):
                    scoped_cards.update(component.member_card_ids)
    local_edges = [
        edge
        for edge in after_edges
        if edge.source_card_id in scoped_cards and edge.target_card_id in scoped_cards
    ]
    local = discover_relation_graph_partition(
        AffectedRelationGraph(
            adapter_name="financial",
            seed_card_ids=tuple(sorted(changed_cards)),
            touched_community_ids=tuple(item.community_id for item in touched),
            edges=tuple(local_edges),
            existing_communities=tuple(
                ExistingRelationGraphCommunity(
                    community_id=item.community_id,
                    identity_anchor_card_id=item.identity_anchor_card_id,
                    member_card_ids=item.member_card_ids,
                )
                for item in touched
            ),
        )
    )
    untouched = [
        component
        for component in before.communities
        if component not in touched
        and not scoped_cards.intersection(component.member_card_ids)
    ]
    incremental_members = sorted(
        tuple(item.member_card_ids) for item in [*untouched, *local.communities]
    )
    full = discover_relation_graph_partition(_graph(*after_edges))
    full_members = sorted(tuple(item.member_card_ids) for item in full.communities)

    assert incremental_members == full_members
    incremental_components = [*untouched, *local.communities]
    incremental_membership = {
        card_id: component.community_id
        for component in incremental_components
        for card_id in component.member_card_ids
    }
    incremental_relations = derive_community_relations_from_membership(
        edges=after_edges,
        community_by_card=incremental_membership,
    )
    assert [
        (
            item.source_community_id,
            item.target_community_id,
            item.relation_kind,
            item.supporting_edge_ids,
        )
        for item in incremental_relations
    ] == [
        (
            item.source_community_id,
            item.target_community_id,
            item.relation_kind,
            item.supporting_edge_ids,
        )
        for item in full.community_relations
    ]


def test_chain_and_star_edges_use_connectivity_without_dropping_leaves() -> None:
    components = discover_relation_graph_components(
        _graph(
            _edge("e1", "c1", "c2"),
            _edge("e2", "c2", "c3", relation_kind="temporal_progression"),
            _edge("e3", "c2", "c4", relation_kind="confirmation"),
        )
    )

    assert len(components) == 1
    assert components[0].member_card_ids == ("c1", "c2", "c3", "c4")
    assert components[0].member_edge_ids == ("e1", "e2", "e3")


def test_disconnected_edges_form_independent_communities() -> None:
    components = discover_relation_graph_components(
        _graph(
            _edge("e1", "c3", "c4"),
            _edge("e2", "c1", "c2"),
        )
    )

    assert [item.identity_anchor_card_id for item in components] == ["c1", "c3"]
    assert [item.member_card_ids for item in components] == [
        ("c1", "c2"),
        ("c3", "c4"),
    ]


def test_merge_keeps_the_smaller_identity_anchor() -> None:
    before = discover_relation_graph_components(
        _graph(
            _edge("e1", "c1", "c2"),
            _edge("e2", "c3", "c4"),
        )
    )
    after = discover_relation_graph_components(
        _graph(
            _edge("e1", "c1", "c2"),
            _edge("e2", "c3", "c4"),
            _edge("e3", "c2", "c3"),
        )
    )

    assert len(before) == 2
    assert len(after) == 1
    assert after[0].community_id == before[0].community_id
    assert after[0].member_card_ids == ("c1", "c2", "c3", "c4")


def test_split_keeps_old_id_only_for_component_containing_old_anchor() -> None:
    before = discover_relation_graph_components(
        _graph(
            _edge("e1", "c1", "c2"),
            _edge("e2", "c2", "c3"),
            _edge("e3", "c3", "c4"),
        )
    )[0]
    after = discover_relation_graph_components(
        _graph(
            _edge("e1", "c1", "c2"),
            _edge("e3", "c3", "c4"),
        )
    )

    assert len(after) == 2
    assert after[0].community_id == before.community_id
    assert after[0].member_card_ids == ("c1", "c2")
    assert after[1].community_id != before.community_id
    assert after[1].member_card_ids == ("c3", "c4")


def test_no_edge_does_not_create_singleton_community() -> None:
    assert discover_relation_graph_components(_graph()) == []


def test_edge_content_change_updates_graph_fingerprint() -> None:
    original = discover_relation_graph_components(
        _graph(_edge("e1", "c1", "c2", content_version="v1"))
    )[0]
    changed = discover_relation_graph_components(
        _graph(_edge("e1", "c1", "c2", content_version="v2"))
    )[0]

    assert original.community_id == changed.community_id
    assert original.graph_fingerprint != changed.graph_fingerprint


def test_invalid_relation_kind_is_rejected() -> None:
    invalid = replace(_edge("e1", "c1", "c2"), relation_kind="related")

    with pytest.raises(ValueError, match="relation_kind"):
        discover_relation_graph_components(_graph(invalid))


def test_large_sparse_bridge_region_is_partitioned_into_flat_communities() -> None:
    edges = _two_dense_groups_with_bridges()
    partition = discover_relation_graph_partition(
        _graph(*edges),
        config=RelationGraphClusteringConfig(
            node_threshold=4,
            edge_threshold=4,
        ),
    )

    assert partition.connected_region_count == 1
    assert partition.clustered_region_count == 1
    assert [len(item.member_card_ids) for item in partition.communities] == [6, 6]
    assert len(partition.community_relations) == 1
    relation = partition.community_relations[0]
    assert relation.relation_kind == "market_co_movement"
    assert relation.supporting_edge_ids == ("bridge:observed",)
    assert relation.observed_edge_count == 1
    assert relation.inferred_edge_count == 0


def test_oversized_leiden_partitions_are_recursively_reclustered(
    monkeypatch,
) -> None:
    edges = _four_dense_groups_with_bridges()
    calls: list[tuple[str, ...]] = []

    def _hierarchical_leiden(weighted_edges, **_kwargs):
        nodes = tuple(
            sorted(
                {
                    node_id
                    for source, target, _weight in weighted_edges
                    for node_id in (source, target)
                }
            )
        )
        calls.append(nodes)
        prefixes = {node_id[0] for node_id in nodes}
        if prefixes == {"a", "b", "c", "d"}:
            assignments = {
                node_id: int(node_id[0] in {"c", "d"})
                for node_id in nodes
            }
        else:
            assignments = {
                node_id: int(node_id[0] == max(prefixes))
                for node_id in nodes
            }
        return 0.0, assignments

    monkeypatch.setattr(community_module, "leiden", _hierarchical_leiden)

    partition = discover_relation_graph_partition(
        _graph(*edges),
        config=RelationGraphClusteringConfig(
            node_threshold=4,
            edge_threshold=100,
        ),
    )

    assert len(calls) == 3
    assert partition.connected_region_count == 1
    assert partition.clustered_region_count == 1
    assert partition.retained_region_count == 0
    assert [len(item.member_card_ids) for item in partition.communities] == [
        3,
        3,
        3,
        3,
    ]
    assert [item.member_card_ids for item in partition.communities] == [
        ("a0", "a1", "a2"),
        ("b0", "b1", "b2"),
        ("c0", "c1", "c2"),
        ("d0", "d1", "d2"),
    ]


def test_partition_reuses_existing_id_for_cluster_containing_old_anchor() -> None:
    previous = ExistingRelationGraphCommunity(
        community_id="community:stable",
        identity_anchor_card_id="a0",
        member_card_ids=tuple(
            [f"a{index}" for index in range(6)]
            + [f"b{index}" for index in range(6)]
        ),
    )
    graph = _graph(*_two_dense_groups_with_bridges())
    graph = replace(
        graph,
        touched_community_ids=(previous.community_id,),
        existing_communities=(previous,),
    )

    partition = discover_relation_graph_partition(
        graph,
        config=RelationGraphClusteringConfig(
            node_threshold=4,
            edge_threshold=4,
        ),
    )

    containing_anchor = next(
        item
        for item in partition.communities
        if "a0" in item.member_card_ids
    )
    assert containing_anchor.community_id == "community:stable"
    assert containing_anchor.identity_anchor_card_id == "a0"


def test_partition_is_stable_when_replayed_with_previous_membership() -> None:
    graph = _graph(*_two_dense_groups_with_bridges())
    config = RelationGraphClusteringConfig(
        node_threshold=4,
        edge_threshold=4,
    )
    first = discover_relation_graph_partition(graph, config=config)
    existing = tuple(
        ExistingRelationGraphCommunity(
            community_id=item.community_id,
            identity_anchor_card_id=item.identity_anchor_card_id,
            member_card_ids=item.member_card_ids,
        )
        for item in first.communities
    )
    replay_graph = replace(
        graph,
        touched_community_ids=tuple(
            item.community_id for item in first.communities
        ),
        existing_communities=existing,
    )

    second = discover_relation_graph_partition(
        replay_graph,
        config=config,
    )

    assert second.communities == first.communities
    assert second.community_relations == first.community_relations


def test_cross_edge_ratio_uses_collapsed_pair_weight(monkeypatch) -> None:
    edges = _two_dense_groups_with_bridges()
    edges.extend(
        _edge(
            f"bridge:extra:{index}",
            "a0",
            "b0",
            relation_kind="market_co_movement",
        )
        for index in range(40)
    )
    monkeypatch.setattr(
        community_module,
        "leiden",
        lambda *_args, **_kwargs: (
            0.0,
            {
                **{f"a{index}": 0 for index in range(6)},
                **{f"b{index}": 1 for index in range(6)},
            },
        ),
    )

    partition = discover_relation_graph_partition(
        _graph(*edges),
        config=RelationGraphClusteringConfig(
            node_threshold=4,
            edge_threshold=4,
            max_cross_edge_ratio=0.35,
        ),
    )

    assert len(partition.communities) == 1
    assert partition.community_relations == ()


def test_leiden_receives_deterministic_singleton_starting_communities(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []

    def _capture_leiden(edges, **kwargs):
        captured.append({"edges": edges, **kwargs})
        return (
            0.0,
            {
                **{f"a{index}": 0 for index in range(6)},
                **{f"b{index}": 1 for index in range(6)},
            },
        )

    monkeypatch.setattr(community_module, "leiden", _capture_leiden)

    discover_relation_graph_partition(
        _graph(*reversed(_two_dense_groups_with_bridges())),
        config=RelationGraphClusteringConfig(
            node_threshold=4,
            edge_threshold=4,
        ),
    )

    expected_nodes = sorted(
        {
            card_id
            for edge in _two_dense_groups_with_bridges()
            for card_id in (edge.source_card_id, edge.target_card_id)
        }
    )
    assert captured[0]["starting_communities"] == {
        node_id: index
        for index, node_id in enumerate(expected_nodes)
    }
    assert len(captured) == 3
    assert all(
        call["starting_communities"]
        == {
            node_id: index
            for index, node_id in enumerate(
                sorted(call["starting_communities"])
            )
        }
        for call in captured
    )
    assert all(
        call["edges"]
        == sorted(
            call["edges"],
            key=lambda item: (item[0], item[1]),
        )
        for call in captured
    )
    assert all(
        source < target
        for call in captured
        for source, target, _weight in call["edges"]
    )


def _two_dense_groups_with_bridges() -> list[RelationGraphEdge]:
    edges: list[RelationGraphEdge] = []
    for prefix in ("a", "b"):
        nodes = [f"{prefix}{index}" for index in range(6)]
        for left_index, left in enumerate(nodes):
            for right in nodes[left_index + 1 :]:
                edges.append(
                    _edge(
                        f"edge:{prefix}:{left}:{right}",
                        left,
                        right,
                        relation_kind="confirmation",
                    )
                )
    edges.append(
        _edge(
            "bridge:observed",
            "a0",
            "b0",
            relation_kind="market_co_movement",
        )
    )
    return edges


def _four_dense_groups_with_bridges() -> list[RelationGraphEdge]:
    edges: list[RelationGraphEdge] = []
    for prefix in ("a", "b", "c", "d"):
        nodes = [f"{prefix}{index}" for index in range(3)]
        for left_index, left in enumerate(nodes):
            for right in nodes[left_index + 1 :]:
                edges.append(
                    _edge(
                        f"edge:{prefix}:{left}:{right}",
                        left,
                        right,
                        relation_kind="confirmation",
                    )
                )
    for left, right in (("a0", "b0"), ("b0", "c0"), ("c0", "d0")):
        edges.append(
            _edge(
                f"bridge:{left}:{right}",
                left,
                right,
                relation_kind="market_co_movement",
            )
        )
    return edges


class _FakeLock:
    def __init__(self, *, extend_result: bool = True) -> None:
        self.released = False
        self.extended = 0
        self.extend_result = extend_result

    def acquire(self, **_: object) -> bool:
        return True

    def extend(self, *_: object, **__: object) -> bool:
        self.extended += 1
        return self.extend_result

    def release(self) -> None:
        self.released = True


class _FakeRedis:
    def __init__(self, *, extend_result: bool = True) -> None:
        self.lock_instance = _FakeLock(extend_result=extend_result)

    def lock(self, *_: object, **__: object) -> _FakeLock:
        return self.lock_instance


class _SerializingLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def acquire(self, *, blocking: bool, blocking_timeout: float) -> bool:
        return self._lock.acquire(
            blocking=blocking,
            timeout=blocking_timeout,
        )

    def extend(self, *_: object, **__: object) -> bool:
        return True

    def release(self) -> None:
        self._lock.release()


class _SerializingRedis:
    def __init__(self) -> None:
        self.lock_instance = _SerializingLock()

    def lock(self, *_: object, **__: object) -> _SerializingLock:
        return self.lock_instance


class _FakeRepository:
    def __init__(self, *, load_delay_seconds: float = 0.0) -> None:
        self.applied_components = []
        self.applied_community_relations = []
        self.load_delay_seconds = load_delay_seconds

    def load_affected_graph(
        self,
        *,
        adapter_name: str,
        seed_card_ids: list[str],
    ) -> AffectedRelationGraph:
        assert adapter_name == "financial"
        assert seed_card_ids == ["c1", "c2"]
        if self.load_delay_seconds:
            time.sleep(self.load_delay_seconds)
        return AffectedRelationGraph(
            adapter_name=adapter_name,
            seed_card_ids=tuple(seed_card_ids),
            touched_community_ids=(),
            edges=(_edge("e1", "c1", "c2"),),
        )

    def apply_components(
        self,
        *,
        adapter_name: str,
        touched_community_ids: list[str],
        components: list,
        community_relations: list,
    ) -> GraphCommunityApplyResult:
        assert adapter_name == "financial"
        assert touched_community_ids == []
        self.applied_components = components
        self.applied_community_relations = community_relations
        return GraphCommunityApplyResult(
            created_community_ids=(components[0].community_id,),
            updated_community_ids=(),
            unchanged_community_ids=(),
            deleted_community_ids=(),
            dirty_community_ids=(components[0].community_id,),
        )


class _ConcurrencyTrackingRepository(_FakeRepository):
    def __init__(self) -> None:
        super().__init__()
        self._guard = threading.Lock()
        self.active_loads = 0
        self.max_active_loads = 0

    def load_affected_graph(
        self,
        *,
        adapter_name: str,
        seed_card_ids: list[str],
    ) -> AffectedRelationGraph:
        with self._guard:
            self.active_loads += 1
            self.max_active_loads = max(
                self.max_active_loads,
                self.active_loads,
            )
        try:
            time.sleep(0.04)
            return super().load_affected_graph(
                adapter_name=adapter_name,
                seed_card_ids=seed_card_ids,
            )
        finally:
            with self._guard:
                self.active_loads -= 1


@pytest.mark.asyncio
async def test_refresh_service_consumes_graph_change_and_releases_lock() -> None:
    repository = _FakeRepository()
    redis_client = _FakeRedis()

    service = RelationGraphCommunityService(
        repository=repository,
        redis_client=redis_client,
    )

    result = await service.refresh_from_graph_change(
        adapter_name="financial",
        changed_edge_ids=["e1"],
        affected_card_ids=["c1", "c2", "c1"],
        event_identity="event-1",
    )

    assert result["status"] == "completed"
    assert result["components"] == 1
    assert len(repository.applied_components) == 1
    assert "report_event_ids" not in result
    assert redis_client.lock_instance.released is True


@pytest.mark.asyncio
async def test_same_adapter_refreshes_are_serialized() -> None:
    repository = _ConcurrencyTrackingRepository()
    service = RelationGraphCommunityService(
        repository=repository,
        redis_client=_SerializingRedis(),
    )

    results = await asyncio.gather(
        service.refresh_from_graph_change(
            adapter_name="financial",
            changed_edge_ids=["e1"],
            affected_card_ids=["c1", "c2"],
            event_identity="event-concurrent-1",
        ),
        service.refresh_from_graph_change(
            adapter_name="financial",
            changed_edge_ids=["e2"],
            affected_card_ids=["c1", "c2"],
            event_identity="event-concurrent-2",
        ),
    )

    assert [item["status"] for item in results] == ["completed", "completed"]
    assert repository.max_active_loads == 1


@pytest.mark.asyncio
async def test_refresh_service_renews_adapter_lock(monkeypatch) -> None:
    monkeypatch.setattr(
        service_module,
        "GRAPH_COMMUNITY_REFRESH_LOCK_RENEW_SECONDS",
        0.01,
    )
    repository = _FakeRepository(load_delay_seconds=0.04)
    redis_client = _FakeRedis()
    service = RelationGraphCommunityService(
        repository=repository,
        redis_client=redis_client,
    )

    result = await service.refresh_from_graph_change(
        adapter_name="financial",
        changed_edge_ids=["e1"],
        affected_card_ids=["c1", "c2"],
        event_identity="event-renew",
    )

    assert result["status"] == "completed"
    assert redis_client.lock_instance.extended >= 1
    assert redis_client.lock_instance.released is True


@pytest.mark.asyncio
async def test_refresh_service_stops_before_apply_when_adapter_lock_is_lost(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        service_module,
        "GRAPH_COMMUNITY_REFRESH_LOCK_RENEW_SECONDS",
        0.01,
    )
    repository = _FakeRepository(load_delay_seconds=0.04)
    redis_client = _FakeRedis(extend_result=False)
    service = RelationGraphCommunityService(
        repository=repository,
        redis_client=redis_client,
    )

    with pytest.raises(RuntimeError, match="lock 已失去"):
        await service.refresh_from_graph_change(
            adapter_name="financial",
            changed_edge_ids=["e1"],
            affected_card_ids=["c1", "c2"],
            event_identity="event-lock-lost",
        )

    assert repository.applied_components == []
    assert redis_client.lock_instance.released is True


@pytest.mark.asyncio
async def test_refresh_service_skips_empty_changed_edges() -> None:
    service = RelationGraphCommunityService(
        repository=_FakeRepository(),
        redis_client=_FakeRedis(),
    )

    result = await service.refresh_from_graph_change(
        adapter_name="financial",
        changed_edge_ids=[],
        affected_card_ids=["c1"],
        event_identity="event-empty",
    )

    assert result == {
        "status": "skipped",
        "reason": "no_changed_edges",
        "event_identity": "event-empty",
    }
