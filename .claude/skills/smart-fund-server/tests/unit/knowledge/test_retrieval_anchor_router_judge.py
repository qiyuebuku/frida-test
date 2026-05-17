"""Tests for Query Anchor, routing and candidate judgement."""

from __future__ import annotations

from src.domain.knowledge.enums import NodeStatus
from src.domain.knowledge.retrieval import RetrievalHit
from src.domain.knowledge.retrieval_anchor import build_guarded_query_anchor
from src.domain.knowledge.retrieval_judge import filter_hits_by_judgement, judge_hits
from src.domain.knowledge.retrieval_router import (
    RetrievalQualityMetrics,
    apply_post_check,
    fast_route,
)
from src.domain.knowledge.schemas import CompiledNode


def test_guarded_anchor_preserves_strong_constraints() -> None:
    node = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        aliases=["300750"],
        status=NodeStatus.ACTIVE,
        version="v1",
    )

    anchor = build_guarded_query_anchor(
        "宁德时代 300750 最近受哪些事件影响 ft_news:77543",
        known_nodes=[node],
    )

    constraints = {(item.constraint_type, item.value) for item in anchor.guard_constraints}
    assert ("source_id", "ft_news:77543") in constraints
    assert ("instrument_code", "300750") in constraints
    assert ("exact_entity", "宁德时代") in constraints


def test_fast_router_uses_explicit_mode_and_complex_intent() -> None:
    anchor = build_guarded_query_anchor("为什么并购重组会影响半导体和券商")

    decision = fast_route("为什么并购重组会影响半导体和券商", anchor, "auto")

    assert decision.initial_mode == "agentic_arag"
    assert decision.reasons == ["complex_intent"]

    explicit = fast_route("普通查询", anchor, "deterministic_plan")
    assert explicit.final_mode == "deterministic_plan"
    assert explicit.reasons == ["explicit_mode"]


def test_auto_route_starts_with_agentic_judge_instead_of_late_upgrade() -> None:
    anchor = build_guarded_query_anchor("中东冲突影响哪些资产")
    decision = fast_route("中东冲突影响哪些资产", anchor, "auto")

    routed = apply_post_check(
        decision,
        RetrievalQualityMetrics(
            anchor_coverage=0.2,
            keep_candidates=1,
            drop_ratio=0.1,
            evidence_refs=1,
            context_precision=0.9,
        ),
        anchor,
    )

    assert decision.initial_mode == "agentic_arag"
    assert routed.final_mode == "agentic_arag"
    assert routed.upgrade_reason is None


def test_candidate_judge_drops_unrelated_semantic_noise() -> None:
    anchor = build_guarded_query_anchor("俄就波法联合军演发出警告：欧洲对俄全面对抗正在回归")
    relevant = RetrievalHit(
        hit_id="kg_ev:military",
        hit_type="evidence",
        title="俄就波法联合军演发出警告",
        snippet="欧洲对俄全面对抗正在回归，佩斯科夫提到波兰和法国军演。",
        score=1.0,
        source="chunk",
        evidence_refs=["kg_ev:military"],
    )
    noisy = RetrievalHit(
        hit_id="kg_ev:catl",
        hit_type="evidence",
        title="固态电池政策支持带动宁德时代产业链预期",
        snippet="宁德时代 固态电池 产业链",
        score=1.0,
        source="semantic_hybrid",
        evidence_refs=["kg_ev:catl"],
    )

    judgements = judge_hits(anchor, [relevant, noisy])
    kept = filter_hits_by_judgement([relevant, noisy], judgements)

    assert [hit.hit_id for hit in kept] == ["kg_ev:military"]
    assert {item.candidate_id: item.decision for item in judgements}["kg_ev:catl"] == "drop"


def test_candidate_judge_marks_weak_graph_context_non_expandable() -> None:
    anchor = build_guarded_query_anchor("宁德时代 300750 最近受哪些事件影响")
    graph_context = RetrievalHit(
        hit_id="kg_edge:financial:mentions:military",
        hit_type="edge",
        title="mentions",
        snippet="kg:financial:event:military -> kg:financial:region:russia",
        score=0.7,
        source="graph",
        edge_refs=["kg_edge:financial:mentions:military"],
        evidence_refs=["kg_ev:financial:news_articles:ft_news:77543"],
    )

    judgement = judge_hits(anchor, [graph_context])[0]

    assert judgement.decision == "weak_keep"
    assert judgement.can_expand_graph is False
    assert judgement.reason == "graph_seed_context"
