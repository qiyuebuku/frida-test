"""Tests for GraphRAG-style Graph Index construction."""

from __future__ import annotations

from datetime import datetime, timezone

from src.application.services.graph_index_profiles import FINANCIAL_GRAPH_PROJECTIONS
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, NodeStatus
from src.domain.knowledge.graph_index import (
    GraphIndexCommunity,
    GraphIndexDirtyRefs,
    build_rolling_delta_index,
    build_graph_index,
    expand_community_scope,
    plan_graph_index_refresh,
    resolve_graph_index_lineage,
    select_replacement_communities,
)
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, EvidenceChunk


def test_build_graph_index_creates_canonical_communities_with_projection_scores() -> None:
    event = _node("kg:financial:event:ai-chain", "event", "AI算力链叙事")
    semi = _node("kg:financial:industry:semi", "industry", "半导体")
    optical = _node("kg:financial:industry:optical", "industry", "光模块")
    policy = _node("kg:financial:concept:policy", "concept", "科创板八条")
    edges = [
        _edge("kg_edge:financial:mentions:semi", event.node_id, semi.node_id, "mentions", "kg_ev:financial:news:1"),
        _edge("kg_edge:financial:mentions:optical", event.node_id, optical.node_id, "mentions", "kg_ev:financial:news:1"),
        _edge("kg_edge:financial:affects:semi", event.node_id, semi.node_id, "affects", "kg_ev:financial:news:1"),
        _edge("kg_edge:financial:benefits_from:semi", semi.node_id, policy.node_id, "benefits_from", "kg_ev:financial:news:2"),
        _edge("kg_edge:financial:related_to:policy", event.node_id, policy.node_id, "related_to", "kg_ev:financial:news:2"),
    ]
    chunks = [
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:1:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:1",
            content="AI算力链提到半导体和光模块。",
            payload={"source_type": "news", "source_id": "source-a"},
        ),
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:2:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:2",
            content="半导体受益于科创板八条。",
            payload={"source_type": "news", "source_id": "source-b"},
        ),
    ]

    result = build_graph_index(
        nodes=[event, semi, optical, policy],
        edges=edges,
        chunks=chunks,
        projections=FINANCIAL_GRAPH_PROJECTIONS,
    )

    assert result.communities
    assert result.findings
    assert result.deltas == []
    assert result.documents
    assert result.diagnostics["community_algorithm"] == "hierarchical_leiden"
    assert result.diagnostics["projection_mode"] == "canonical_graph_with_projection_scores"
    assert result.diagnostics["edge_support"]["edges"] == 5
    assert result.diagnostics["edge_support"]["avg_chunk_count"] == 1.0
    assert {community.projection for community in result.communities} == {"default_graph_projection"}
    structural_signatures = {
        (
            community.level,
            tuple(sorted(community.member_node_ids)),
            tuple(sorted(community.member_edge_ids)),
        )
        for community in result.communities
    }
    assert len(structural_signatures) == len(result.communities)
    assert all("projection_scores" in community.metrics for community in result.communities)
    assert all("avg_edge_support_weight" in community.metrics for community in result.communities)
    assert all("maturity_level" in community.metrics for community in result.communities)
    assert all("support_score" in community.metrics for community in result.communities)
    assert all("topic_fingerprint" in community.metrics for community in result.communities)
    assert any(community.metrics["support_source_count"] == 2 for community in result.communities)
    assert any(community.metrics["edge_support_chunk_refs"] >= 3 for community in result.communities)
    assert any(
        community.metrics["projection_scores"].get("narrative", 0.0) > 0
        for community in result.communities
    )
    assert all(
        community.metrics["projection_scores"].get("risk") == 0.0
        for community in result.communities
    )
    assert all(community.community_id.startswith("kg_community:") for community in result.communities)
    assert all(finding.cited_chunk_ids for finding in result.findings)
    assert {document.collection_role for document in result.documents} == {"community"}
    assert {document.source_type for document in result.documents} >= {"kg_community_report", "kg_finding"}


def test_build_graph_index_keeps_single_evidence_mentions_as_unassigned_signal() -> None:
    event = _node("kg:financial:event:single-news", "event", "单篇新闻事件")
    nodes = [event]
    edges = []
    for index in range(14):
        node = _node(f"kg:financial:concept:item-{index}", "concept", f"主题{index}")
        nodes.append(node)
        edges.append(
            _edge(
                f"kg_edge:financial:mentions:item-{index}",
                event.node_id,
                node.node_id,
                "mentions",
                "kg_ev:financial:news:single",
            )
        )
    chunks = [
        EvidenceChunk(
            chunk_id=f"kg_chunk:kg_ev:financial:news:single:{index}",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:single",
            content=f"单篇新闻第 {index} 段提到多个主题。",
            payload={"source_type": "news", "source_id": "single-source"},
        )
        for index in range(2)
    ]

    result = build_graph_index(nodes=nodes, edges=edges, chunks=chunks, projections=FINANCIAL_GRAPH_PROJECTIONS)

    assert result.communities == []
    assert result.findings == []
    assert result.documents == []
    assert len(result.unassigned_signals) == 1
    [signal] = result.unassigned_signals
    assert signal.reason == "insufficient_signal_fusion"
    assert signal.metrics["maturity_level"] == "single_source_signal"
    assert signal.metrics["non_mentions_edge_count"] == 0


def test_build_graph_index_caps_single_evidence_multi_chunk_support() -> None:
    event = _node("kg:financial:event:single-multi-chunk", "event", "单篇长新闻事件")
    semi = _node("kg:financial:industry:semi", "industry", "半导体")
    equipment = _node("kg:financial:industry:equipment", "industry", "高端装备制造")
    policy = _node("kg:financial:concept:policy", "concept", "并购六条")
    signal = {
        "topic_tags": ["并购重组政策窗口"],
        "impact_tags": ["产业链整合"],
        "support_role": "core",
        "boundary_strength": "strong",
        "relationship_strength": 0.9,
    }
    edges = [
        _edge(
            "kg_edge:financial:affects:semi",
            event.node_id,
            semi.node_id,
            "affects",
            "kg_ev:financial:news:single",
            properties=signal,
        ),
        _edge(
            "kg_edge:financial:affects:equipment",
            event.node_id,
            equipment.node_id,
            "affects",
            "kg_ev:financial:news:single",
            properties=signal,
        ),
        _edge(
            "kg_edge:financial:benefits_from:policy",
            equipment.node_id,
            policy.node_id,
            "benefits_from",
            "kg_ev:financial:news:single",
            properties=signal,
        ),
    ]
    chunks = [
        EvidenceChunk(
            chunk_id=f"kg_chunk:kg_ev:financial:news:single:{index}",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:single",
            content=f"单篇长新闻第 {index} 段。",
            payload={"source_type": "news", "source_id": "single-source"},
        )
        for index in range(3)
    ]

    result = build_graph_index(nodes=[event, semi, equipment, policy], edges=edges, chunks=chunks)

    assert result.communities == []
    assert result.findings == []
    assert result.documents == []
    assert len(result.unassigned_signals) == 1
    [signal] = result.unassigned_signals
    assert signal.metrics["evidence_count"] == 1
    assert signal.metrics["chunk_count"] == 3
    assert signal.metrics["maturity_level"] == "single_source_signal"
    assert signal.support_score <= 0.72


def test_build_graph_index_filters_tiny_single_edge_side_signal() -> None:
    policy = _node("kg:financial:policy:fifteen-five", "policy", "十五五规划")
    new_energy = _node("kg:financial:concept:new-energy", "concept", "新能源")
    edge = _edge(
        "kg_edge:financial:affects:policy-new-energy",
        policy.node_id,
        new_energy.node_id,
        "affects",
        "kg_ev:financial:news:single",
        properties={
            "topic_tags": ["十五五规划"],
            "impact_tags": ["政策利好"],
            "support_role": "core",
            "boundary_strength": "strong",
        },
    )
    chunk = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:single:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:single",
        content="工信部推进十五五规划，利好新能源。",
        payload={"source_type": "news", "source_id": "single-source"},
    )

    result = build_graph_index(nodes=[policy, new_energy], edges=[edge], chunks=[chunk])

    assert result.communities == []
    assert result.findings == []
    assert result.documents == []
    assert len(result.unassigned_signals) == 1
    [signal] = result.unassigned_signals
    assert signal.status == "active"
    assert signal.reason == "insufficient_signal_fusion"
    assert signal.edge_ids == [edge.edge_id]
    assert signal.chunk_ids == [chunk.chunk_id]
    assert signal.support_score > 0


def test_build_graph_index_keeps_single_source_strong_component_as_unassigned_signal() -> None:
    event = _node("kg:financial:event:single-strong", "event", "AI芯片供应瓶颈")
    chip = _node("kg:financial:concept:ai-chip", "concept", "AI芯片")
    capacity = _node("kg:financial:concept:capacity", "concept", "半导体产能")
    tesla = _node("kg:financial:institution:tesla", "institution", "特斯拉")
    edges = [
        _edge("kg_edge:financial:affects:chip", event.node_id, chip.node_id, "affects", "kg_ev:financial:news:single"),
        _edge(
            "kg_edge:financial:related_to:capacity",
            chip.node_id,
            capacity.node_id,
            "related_to",
            "kg_ev:financial:news:single",
        ),
        _edge(
            "kg_edge:financial:mentions:tesla",
            event.node_id,
            tesla.node_id,
            "mentions",
            "kg_ev:financial:news:single",
        ),
    ]
    chunks = [
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:single:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:single",
            content="特斯拉认为未来 AI 芯片供应严重不足，并启动芯片工厂应对产能瓶颈。",
            payload={"source_type": "news", "source_id": "single-source"},
        )
    ]

    result = build_graph_index(nodes=[event, chip, capacity, tesla], edges=edges, chunks=chunks)

    assert result.communities == []
    assert result.findings == []
    assert result.documents == []
    assert len(result.unassigned_signals) == 1
    [signal] = result.unassigned_signals
    assert signal.metrics["evidence_count"] == 1
    assert signal.metrics["maturity_level"] == "single_source_signal"
    assert signal.support_score > 0
    assert signal.metrics["non_mentions_edge_count"] == 2


def test_build_graph_index_consumes_front_loaded_fact_signals() -> None:
    event = _node("kg:financial:event:merger", "event", "并购重组政策窗口")
    semi = _node("kg:financial:industry:semi", "industry", "半导体")
    equipment = _node("kg:financial:industry:equipment", "industry", "高端装备制造")
    policy = _node("kg:financial:concept:policy", "concept", "并购六条")
    signal_properties = {
        "topic_tags": ["并购重组政策窗口"],
        "impact_tags": ["产业链整合"],
        "domain_tags": ["半导体", "高端装备制造"],
        "narrative_tags": ["硬科技资产重估"],
        "fact_signals": [
            {
                "signal_type": "impact",
                "topic_tags": ["并购重组政策窗口"],
                "impact_tags": ["产业链整合"],
                "domain_tags": ["半导体", "高端装备制造"],
                "narrative_tags": ["硬科技资产重估"],
                "sentiment": "positive",
            }
        ],
    }
    edges = [
        _edge("kg_edge:financial:mentions:semi", event.node_id, semi.node_id, "mentions", "kg_ev:financial:news:1"),
        _edge(
            "kg_edge:financial:affects:semi",
            event.node_id,
            semi.node_id,
            "affects",
            "kg_ev:financial:news:1",
            properties=signal_properties,
        ),
        _edge(
            "kg_edge:financial:affects:equipment",
            event.node_id,
            equipment.node_id,
            "affects",
            "kg_ev:financial:news:2",
            properties=signal_properties,
        ),
        _edge(
            "kg_edge:financial:benefits_from:policy",
            equipment.node_id,
            policy.node_id,
            "benefits_from",
            "kg_ev:financial:news:2",
            properties=signal_properties,
        ),
    ]
    chunks = [
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:1:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:1",
            content="并购重组政策推动半导体产业链整合。",
            payload={"source_type": "news", "source_id": "source-a"},
        ),
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:2:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:2",
            content="高端装备制造受益于并购六条。",
            payload={"source_type": "news", "source_id": "source-b"},
        ),
    ]

    result = build_graph_index(nodes=[event, semi, equipment, policy], edges=edges, chunks=chunks)

    assert result.communities
    [community] = result.communities
    assert community.metrics["non_mentions_edge_count"] == 3
    assert community.metrics["topic_tags"] == ["并购重组政策窗口"]
    assert community.metrics["impact_tags"] == ["产业链整合"]
    assert community.metrics["maturity_level"] in {"stable_multi_source_topic", "growing_topic"}
    assert community.metrics["topic_fingerprint"]["topic_tags"] == ["并购重组政策窗口"]
    assert community.metrics["projection_scores"]["impact"] > 0
    assert "Topic Tags: 并购重组政策窗口" in result.documents[0].text


def test_build_graph_index_does_not_merge_unrelated_evidence_by_generic_industry_bridge() -> None:
    merger_event = _node("kg:financial:event:merger", "event", "A股并购重组政策窗口")
    merger = _node("kg:financial:concept:merger", "concept", "并购重组")
    semi = _node("kg:financial:industry:semi", "industry", "半导体")
    chip_event = _node("kg:financial:event:ai-chip-shortage", "event", "AI芯片供应短缺")
    ai_chip = _node("kg:financial:concept:ai-chip", "concept", "AI芯片")
    capacity = _node("kg:financial:concept:capacity", "concept", "自建产能")
    merger_signal = {
        "topic_tags": ["并购重组政策窗口"],
        "event_type_tags": ["政策窗口", "产业并购"],
        "impact_tags": ["产业链整合"],
        "support_role": "core",
        "boundary_strength": "strong",
        "relationship_strength": 0.86,
        "fact_signals": [
            {
                "signal_type": "impact",
                "topic_tags": ["并购重组政策窗口"],
                "event_type_tags": ["政策窗口", "产业并购"],
                "impact_tags": ["产业链整合"],
                "support_role": "core",
                "boundary_strength": "strong",
            }
        ],
    }
    chip_signal = {
        "topic_tags": ["AI芯片供应短缺"],
        "event_type_tags": ["供应链短缺", "产能约束"],
        "impact_tags": ["供给约束", "自建产能"],
        "support_role": "core",
        "boundary_strength": "strong",
        "relationship_strength": 0.88,
        "fact_signals": [
            {
                "signal_type": "risk",
                "topic_tags": ["AI芯片供应短缺"],
                "event_type_tags": ["供应链短缺", "产能约束"],
                "impact_tags": ["供给约束", "自建产能"],
                "support_role": "core",
                "boundary_strength": "strong",
            }
        ],
    }
    edges = [
        _edge(
            "kg_edge:financial:affects:merger-semi",
            merger_event.node_id,
            semi.node_id,
            "affects",
            "kg_ev:financial:news:merger",
            properties=merger_signal,
        ),
        _edge(
            "kg_edge:financial:related_to:merger",
            merger_event.node_id,
            merger.node_id,
            "related_to",
            "kg_ev:financial:news:merger",
            properties=merger_signal,
        ),
        _edge(
            "kg_edge:financial:affects:chip",
            chip_event.node_id,
            ai_chip.node_id,
            "affects",
            "kg_ev:financial:news:chip",
            properties=chip_signal,
        ),
        _edge(
            "kg_edge:financial:related_to:capacity",
            ai_chip.node_id,
            capacity.node_id,
            "related_to",
            "kg_ev:financial:news:chip",
            properties=chip_signal,
        ),
        _edge(
            "kg_edge:financial:mentions:chip-semi",
            chip_event.node_id,
            semi.node_id,
            "mentions",
            "kg_ev:financial:news:chip",
        ),
        _edge(
            "kg_edge:financial:related_to:chip-semi",
            ai_chip.node_id,
            semi.node_id,
            "related_to",
            "kg_ev:financial:news:chip",
        ),
    ]
    chunks = [
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:merger:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:merger",
            content="并购重组政策窗口推动半导体产业链整合。",
            payload={"source_type": "news", "source_id": "source-merger"},
        ),
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:chip:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:chip",
            content="特斯拉担心 AI 芯片供应短缺，并考虑自建产能。",
            payload={"source_type": "news", "source_id": "source-chip"},
        ),
    ]

    result = build_graph_index(
        nodes=[merger_event, merger, semi, chip_event, ai_chip, capacity],
        edges=edges,
        chunks=chunks,
    )

    assert result.communities == []
    assert result.findings == []
    assert result.documents == []
    assert {tuple(signal.evidence_ids) for signal in result.unassigned_signals} == {
        ("kg_ev:financial:news:merger",),
        ("kg_ev:financial:news:chip",),
    }


def test_build_graph_index_fuses_detached_same_topic_signals() -> None:
    event_a = _node("kg:financial:event:merger-a", "event", "并购重组政策窗口")
    event_b = _node("kg:financial:event:merger-b", "event", "并购重组交易活跃")
    semi_a = _node("kg:financial:industry:semi-a", "industry", "半导体设备")
    semi_b = _node("kg:financial:industry:semi-b", "industry", "先进封装")
    signal = {
        "topic_tags": ["并购重组政策窗口"],
        "event_type_tags": ["政策窗口", "产业并购"],
        "impact_tags": ["产业链整合"],
        "domain_tags": ["半导体"],
        "support_role": "core",
        "boundary_strength": "strong",
        "relationship_strength": 0.9,
    }
    edges = [
        _edge(
            "kg_edge:financial:affects:semi-a",
            event_a.node_id,
            semi_a.node_id,
            "affects",
            "kg_ev:financial:news:a",
            properties=signal,
        ),
        _edge(
            "kg_edge:financial:related_to:semi-a",
            event_a.node_id,
            semi_a.node_id,
            "related_to",
            "kg_ev:financial:news:a",
            properties=signal,
        ),
        _edge(
            "kg_edge:financial:affects:semi-b",
            event_b.node_id,
            semi_b.node_id,
            "affects",
            "kg_ev:financial:news:b",
            properties=signal,
        ),
        _edge(
            "kg_edge:financial:related_to:semi-b",
            event_b.node_id,
            semi_b.node_id,
            "related_to",
            "kg_ev:financial:news:b",
            properties=signal,
        ),
    ]
    chunks = [
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:a:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:a",
            content="并购重组政策窗口推动半导体设备整合。",
            payload={"source_type": "news", "source_id": "source-a"},
        ),
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:b:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:b",
            content="并购重组交易活跃，先进封装产业链整合加速。",
            payload={"source_type": "news", "source_id": "source-b"},
        ),
    ]

    result = build_graph_index(
        nodes=[event_a, event_b, semi_a, semi_b],
        edges=edges,
        chunks=chunks,
    )

    assert len(result.communities) == 1
    [community] = result.communities
    assert community.metrics["signal_fusion_algorithm"] == "topic_fingerprint_graph"
    assert community.metrics["signal_count"] == 2
    assert community.metrics["fusion_edge_count"] == 1
    assert community.metrics["topic_tags"] == ["并购重组政策窗口"]
    assert community.evidence_ids == ["kg_ev:financial:news:a", "kg_ev:financial:news:b"]


def test_build_graph_index_uses_broad_l0_title_for_company_project_signals() -> None:
    event_a = _node("kg:financial:event:hithium-spain", "event", "海辰储能西班牙建厂计划")
    event_b = _node("kg:financial:event:storage-overseas", "event", "储能企业海外产能布局")
    company = _node("kg:financial:company:hithium", "company", "海辰储能")
    storage = _node("kg:financial:industry:storage", "industry", "储能")
    signal = {
        "topic_tags": ["海辰储能西班牙建厂计划"],
        "event_type_tags": ["海外建厂"],
        "impact_tags": ["海外产能"],
        "domain_tags": ["储能"],
        "support_role": "core",
        "boundary_strength": "strong",
        "relationship_strength": 0.9,
    }
    edges = [
        _edge(
            "kg_edge:financial:affects:hithium",
            event_a.node_id,
            company.node_id,
            "affects",
            "kg_ev:financial:news:hithium",
            properties=signal,
        ),
        _edge(
            "kg_edge:financial:related_to:storage-a",
            event_a.node_id,
            storage.node_id,
            "related_to",
            "kg_ev:financial:news:hithium",
            properties=signal,
        ),
        _edge(
            "kg_edge:financial:affects:storage",
            event_b.node_id,
            storage.node_id,
            "affects",
            "kg_ev:financial:news:storage",
            properties=signal,
        ),
        _edge(
            "kg_edge:financial:related_to:storage-b",
            event_b.node_id,
            storage.node_id,
            "related_to",
            "kg_ev:financial:news:storage",
            properties=signal,
        ),
    ]
    chunks = [
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:hithium:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:hithium",
            content="海辰储能推进西班牙建厂计划，扩展海外产能。",
            payload={"source_type": "news", "source_id": "source-hithium"},
        ),
        EvidenceChunk(
            chunk_id="kg_chunk:kg_ev:financial:news:storage:0",
            adapter_name="financial",
            evidence_id="kg_ev:financial:news:storage",
            content="储能企业加快海外产能布局。",
            payload={"source_type": "news", "source_id": "source-storage"},
        ),
    ]

    result = build_graph_index(nodes=[event_a, event_b, company, storage], edges=edges, chunks=chunks)

    assert len(result.communities) == 1
    [community] = result.communities
    assert community.level == 0
    assert "海辰储能" not in community.title
    assert "建厂计划" not in community.title
    assert community.title in {"储能海外产能", "储能 / 海外产能"}
    assert community.metrics["title_scope"] == "topic_container"


def test_plan_graph_index_refresh_detects_affected_communities() -> None:
    existing = [
        _community(
            "kg_community:market:l0:a",
            projection="market_narrative",
            nodes=["kg:financial:industry:semi"],
            edges=["kg_edge:financial:benefits_from:semi"],
            evidence=["kg_ev:financial:news:1"],
            chunks=["kg_chunk:kg_ev:financial:news:1:0"],
        ),
        _community(
            "kg_community:risk:l0:b",
            projection="risk_event",
            nodes=["kg:financial:industry:new_energy"],
            edges=["kg_edge:financial:risk:new_energy"],
            evidence=["kg_ev:financial:news:2"],
            chunks=["kg_chunk:kg_ev:financial:news:2:0"],
        ),
    ]

    plan = plan_graph_index_refresh(
        existing_communities=existing,
        changed_node_ids=["kg:financial:industry:semi"],
        changed_edge_ids=["kg_edge:financial:benefits_from:semi"],
        changed_evidence_ids=["kg_ev:financial:news:1"],
        changed_chunk_ids=["kg_chunk:kg_ev:financial:news:1:0"],
        total_node_count=20,
        total_edge_count=30,
        total_chunk_count=10,
    )

    assert plan.affected_community_ids == ["kg_community:market:l0:a"]
    assert plan.affected_projection_counts == {"market_narrative": 1}
    assert plan.action in {"light_refresh_required", "local_review_required", "local_recompute_required", "full_rebuild"}
    assert plan.score > 0


def test_plan_graph_index_refresh_requests_full_rebuild_without_existing_index() -> None:
    plan = plan_graph_index_refresh(
        existing_communities=[],
        changed_node_ids=["kg:financial:industry:semi"],
        total_node_count=1,
    )

    assert plan.action == "full_rebuild"
    assert plan.score == 1.0
    assert plan.reasons == ["no_existing_graph_index"]


def test_expand_community_scope_includes_ancestors_and_descendants() -> None:
    communities = [
        _community("parent", projection="market_narrative", nodes=["a"], edges=["e1"], evidence=["ev1"], chunks=["c1"]),
        _community(
            "child",
            projection="market_narrative",
            nodes=["b"],
            edges=["e2"],
            evidence=["ev2"],
            chunks=["c2"],
            parent="parent",
        ),
        _community(
            "grandchild",
            projection="market_narrative",
            nodes=["c"],
            edges=["e3"],
            evidence=["ev3"],
            chunks=["c3"],
            parent="child",
        ),
    ]

    assert expand_community_scope(communities, ["child"]) == ["child", "parent", "grandchild"]


def test_select_replacement_communities_uses_dirty_ref_closure() -> None:
    communities = [
        _community("parent", projection="market_narrative", nodes=["a"], edges=["e1"], evidence=["ev1"], chunks=["c1"]),
        _community(
            "child",
            projection="market_narrative",
            nodes=["b"],
            edges=["e2"],
            evidence=["ev2"],
            chunks=["c2"],
            parent="parent",
        ),
        _community("other", projection="risk_event", nodes=["x"], edges=["ex"], evidence=["evx"], chunks=["cx"]),
    ]

    selected = select_replacement_communities(
        communities,
        GraphIndexDirtyRefs(edge_ids=["e2"]),
    )

    assert [community.community_id for community in selected] == ["parent", "child"]


def test_resolve_graph_index_lineage_detects_merge_and_rename() -> None:
    old_a = _community(
        "old-a",
        projection="market_narrative",
        nodes=["a", "b"],
        edges=["e1"],
        evidence=["ev1"],
        chunks=["c1"],
    )
    old_b = _community(
        "old-b",
        projection="market_narrative",
        nodes=["c", "d"],
        edges=["e2"],
        evidence=["ev2"],
        chunks=["c2"],
    )
    new = _community(
        "new-ab",
        projection="market_narrative",
        nodes=["a", "b", "c", "d"],
        edges=["e1", "e2"],
        evidence=["ev1", "ev2"],
        chunks=["c1", "c2"],
    )

    [resolved] = resolve_graph_index_lineage(communities=[new], existing_communities=[old_a, old_b])

    assert resolved.change_reason == "merge"
    assert resolved.previous_community_ids == ["old-a", "old-b"]
    assert resolved.lineage_id


def test_resolve_graph_index_lineage_uses_topic_fingerprint_when_ids_shift() -> None:
    fingerprint = {
        "core_entities": ["并购重组", "半导体"],
        "core_relation_types": ["affects"],
        "topic_tags": ["并购重组政策窗口"],
        "impact_tags": ["产业链整合"],
    }
    digest = "same-topic"
    old = _community(
        "old-topic",
        projection="default_graph_projection",
        nodes=["old-a", "old-b"],
        edges=["old-edge"],
        evidence=["old-ev"],
        chunks=["old-chunk"],
        metrics={"topic_fingerprint": fingerprint, "topic_fingerprint_digest": digest},
    )
    new = _community(
        "new-topic",
        projection="default_graph_projection",
        nodes=["new-a", "new-b"],
        edges=["new-edge"],
        evidence=["new-ev"],
        chunks=["new-chunk"],
        metrics={"topic_fingerprint": fingerprint, "topic_fingerprint_digest": digest},
    )

    [resolved] = resolve_graph_index_lineage(communities=[new], existing_communities=[old])

    assert resolved.lineage_id == old.lineage_id
    assert resolved.previous_community_ids == ["old-topic"]
    assert resolved.change_reason in {"continued", "rename"}


def test_build_rolling_delta_index_uses_chunk_timestamps() -> None:
    community = _community(
        "kg_community:market:l0:a",
        projection="market_narrative",
        nodes=["a"],
        edges=["e1"],
        evidence=["ev1"],
        chunks=["c1", "c2"],
    )
    recent = EvidenceChunk(
        chunk_id="c1",
        adapter_name="financial",
        evidence_id="ev1",
        content="recent",
        payload={"published_at": "2026-05-30T00:00:00+00:00"},
    )
    old = EvidenceChunk(
        chunk_id="c2",
        adapter_name="financial",
        evidence_id="ev1",
        content="old",
        payload={"published_at": "2026-04-01T00:00:00+00:00"},
    )

    deltas = build_rolling_delta_index(
        communities=[community],
        findings=[],
        chunks=[recent, old],
        now=datetime(2026, 5, 30, 12, tzinfo=timezone.utc),
    )

    assert {delta.window_name for delta in deltas} == {"rolling_24h", "rolling_7d", "rolling_30d"}
    assert all(delta.cited_chunk_ids == ["c1"] for delta in deltas)


def _node(node_id: str, node_type: str, name: str) -> CompiledNode:
    return CompiledNode(
        node_id=node_id,
        adapter_name="financial",
        node_type=node_type,
        canonical_name=name,
        status=NodeStatus.ACTIVE,
        version="v1",
    )


def _edge(
    edge_id: str,
    source: str,
    target: str,
    relation_type: str,
    evidence_id: str,
    *,
    properties: dict | None = None,
) -> CompiledEdge:
    return CompiledEdge(
        edge_id=edge_id,
        adapter_name="financial",
        source_node_id=source,
        target_node_id=target,
        relation_type=relation_type,
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.9,
        status=EdgeStatus.ACTIVE,
        evidence_ids=[evidence_id],
        properties=properties or {},
        version="v1",
    )


def _community(
    community_id: str,
    *,
    projection: str,
    nodes: list[str],
    edges: list[str],
    evidence: list[str],
    chunks: list[str],
    parent: str = "",
    metrics: dict | None = None,
) -> GraphIndexCommunity:
    return GraphIndexCommunity(
        community_id=community_id,
        version_id=f"{community_id}:v1",
        adapter_name="financial",
        projection=projection,
        level=0,
        parent_community_id=parent,
        title=community_id,
        summary="",
        member_node_ids=nodes,
        member_edge_ids=edges,
        evidence_ids=evidence,
        chunk_ids=chunks,
        metrics=metrics or {},
        lineage_id=f"lineage:{community_id}",
    )
