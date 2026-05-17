"""Tests for agentic retrieval ranker preselection."""

from __future__ import annotations

from src.domain.knowledge.agentic_retrieval import RetrievalSearchPlan
from src.domain.knowledge.retrieval import RetrievalHit
from src.domain.knowledge.retrieval_anchor import build_guarded_query_anchor
from src.domain.knowledge.retrieval_ranker import judge_preselect


def test_judge_preselect_uses_complex_top_k_for_which_queries() -> None:
    hits = [
        RetrievalHit(
            hit_id=f"kg_ev:financial:news:{index}",
            hit_type="evidence",
            title=f"宁德时代事件 {index}",
            snippet="宁德时代 300750 影响 事件",
            source="keyword",
            score=float(30 - index),
            evidence_refs=[f"kg_ev:financial:news:{index}"],
        )
        for index in range(20)
    ]

    result = judge_preselect(
        hits,
        query="宁德时代 300750 最近受哪些事件影响",
        anchor=build_guarded_query_anchor("宁德时代 300750 最近受哪些事件影响", known_nodes=[]),
        search_plan=RetrievalSearchPlan(answer_targets=["宁德时代近期影响事件"]),
        top_k_simple=8,
        top_k_complex=12,
        top_k_max=15,
    )

    assert result.top_k_requested == 12
    assert result.top_k_reason == "broad_which_query"
    assert len(result.selected) == 12


def test_judge_preselect_promotes_answer_and_limits_background_when_possible() -> None:
    hits = [
        RetrievalHit(
            hit_id="kg_wiki:financial:1",
            hit_type="wiki",
            title="快充电池",
            snippet="背景概念",
            source="wiki",
            score=100.0,
        ),
        RetrievalHit(
            hit_id="kg_edge:financial:affects:1",
            hit_type="edge",
            title="affects",
            snippet="宁德时代海外产能扩张 affects 宁德时代",
            source="graph",
            score=90.0,
            edge_refs=["kg_edge:financial:affects:1"],
            evidence_refs=["kg_ev:financial:news:1"],
        ),
        RetrievalHit(
            hit_id="kg_ev:financial:news:1",
            hit_type="evidence",
            title="宁德时代海外产能扩张",
            snippet="宁德时代海外产能扩张带动储能供应链订单",
            source="keyword",
            score=1.0,
            evidence_refs=["kg_ev:financial:news:1"],
            matched_terms=["宁德时代", "海外产能扩张"],
            matched_fields=["title", "evidence_summary"],
        ),
    ]

    result = judge_preselect(
        hits,
        query="宁德时代 300750 最近受哪些事件影响",
        anchor=build_guarded_query_anchor("宁德时代 300750 最近受哪些事件影响", known_nodes=[]),
        search_plan=RetrievalSearchPlan(answer_targets=["海外产能扩张"], relation_intents=["impact"]),
        top_k_simple=2,
        top_k_complex=3,
        top_k_max=3,
    )

    assert result.selected[0].hit.hit_id == "kg_ev:financial:news:1"
    assert result.selected[0].feature_score > result.selected[-1].feature_score
    assert "answer_type" in result.selected[0].rank_reasons


def test_judge_preselect_penalizes_generic_evidence_without_stock_anchor() -> None:
    hits = [
        RetrievalHit(
            hit_id="kg_ev:financial:middle_east",
            hit_type="evidence",
            title="中东冲突影响大宗商品",
            snippet="中东冲突影响原油、黄金和航运。",
            source="keyword",
            score=100.0,
            evidence_refs=["kg_ev:financial:middle_east"],
            matched_terms=["影响", "事件"],
            matched_fields=["search_text"],
        ),
        RetrievalHit(
            hit_id="kg_ev:financial:catl_capacity",
            hit_type="evidence",
            title="宁德时代海外产能扩张",
            snippet="宁德时代 300750 海外产能扩张改善储能供应链订单。",
            source="keyword",
            score=1.0,
            evidence_refs=["kg_ev:financial:catl_capacity"],
            matched_terms=["宁德时代", "300750", "影响"],
            matched_fields=["title", "aliases"],
        ),
    ]

    result = judge_preselect(
        hits,
        query="宁德时代 300750 最近受哪些事件影响",
        anchor=build_guarded_query_anchor("宁德时代 300750 最近受哪些事件影响", known_nodes=[]),
        search_plan=RetrievalSearchPlan(
            answer_targets=["宁德时代近期影响事件"],
            expected_evidence=["新闻报道", "公司公告"],
            relation_intents=["impact"],
        ),
        top_k_simple=1,
        top_k_complex=1,
        top_k_max=1,
    )

    assert result.selected[0].hit.hit_id == "kg_ev:financial:catl_capacity"
    middle_east = next(item for item in result.remaining_high_potential if item.hit.hit_id == "kg_ev:financial:middle_east")
    assert "anchor_miss" in middle_east.rank_reasons
    assert "generic_only_no_anchor" in middle_east.rank_reasons


def test_judge_preselect_reports_channel_contribution_and_new_coverage() -> None:
    hits = [
        RetrievalHit(
            hit_id="kg_ev:financial:catl_capacity",
            hit_type="evidence",
            title="宁德时代海外产能扩张",
            snippet="宁德时代海外产能扩张影响储能供应链。",
            source="keyword",
            score=10.0,
            evidence_refs=["kg_ev:financial:catl_capacity"],
        ),
        RetrievalHit(
            hit_id="kg_ev:financial:catl_capacity",
            hit_type="evidence",
            title="宁德时代海外产能扩张",
            snippet="宁德时代海外产能扩张影响储能供应链。",
            source="semantic_hybrid",
            score=9.0,
            evidence_refs=["kg_ev:financial:catl_capacity"],
        ),
        RetrievalHit(
            hit_id="kg_ev:financial:catl_flow",
            hit_type="evidence",
            title="宁德时代资金净流入改善",
            snippet="宁德时代资金净流入改善。",
            source="semantic_hybrid",
            score=8.0,
            evidence_refs=["kg_ev:financial:catl_flow"],
        ),
    ]

    result = judge_preselect(
        hits,
        query="宁德时代 300750 最近受哪些事件影响",
        anchor=build_guarded_query_anchor("宁德时代 300750 最近受哪些事件影响", known_nodes=[]),
        search_plan=RetrievalSearchPlan(expected_evidence=["新闻报道"], relation_intents=["impact"]),
        top_k_simple=2,
        top_k_complex=2,
        top_k_max=2,
    )

    assert result.channel_contribution["pool_counts"]["keyword"] == 1
    assert result.channel_contribution["pool_counts"]["semantic_hybrid"] == 2
    assert result.channel_contribution["multi_channel_selected"] >= 1
    merged_capacity = next(
        item for item in result.selected if item.hit.hit_id == "kg_ev:financial:catl_capacity"
    )
    assert merged_capacity.candidate.source_channels == ["keyword", "semantic_hybrid"]
    assert set(merged_capacity.candidate.channel_ranks) == {"keyword", "semantic_hybrid"}
    assert any(item.new_coverage_terms for item in result.selected)


def test_judge_preselect_diversifies_same_evidence_edges() -> None:
    hits = [
        RetrievalHit(
            hit_id="kg_ev:financial:ma",
            hit_type="evidence",
            title="A股并购重组市场呈现三方面新变化",
            snippet="并购重组 新闻报道 半导体 汽车零部件 高端装备制造 生物医药",
            source="keyword",
            score=100.0,
            evidence_refs=["kg_ev:financial:ma"],
            matched_terms=["并购重组"],
        ),
        *[
            RetrievalHit(
                hit_id=f"kg_edge:financial:mentions:{index}",
                hit_type="edge",
                title=f"A股并购重组市场呈现三方面新变化 mentions 行业{index}",
                snippet=f"并购重组 mentions 行业{index}",
                source="keyword",
                score=95.0 - index,
                edge_refs=[f"kg_edge:financial:mentions:{index}"],
                evidence_refs=["kg_ev:financial:ma"],
                matched_terms=["并购重组"],
            )
            for index in range(8)
        ],
        *[
            RetrievalHit(
                hit_id=f"kg_ev:financial:other:{index}",
                hit_type="evidence",
                title=f"并购重组相关补充新闻 {index}",
                snippet=f"并购重组 相关行业 补充证据 {index}",
                source="keyword",
                score=30.0 - index,
                evidence_refs=[f"kg_ev:financial:other:{index}"],
                matched_terms=["并购重组"],
            )
            for index in range(8)
        ],
    ]

    result = judge_preselect(
        hits,
        query="并购重组涉及哪些行业和资产",
        anchor=build_guarded_query_anchor("并购重组涉及哪些行业和资产", known_nodes=[]),
        search_plan=RetrievalSearchPlan(answer_targets=["并购重组相关行业和资产"]),
        top_k_simple=5,
        top_k_complex=8,
        top_k_max=8,
    )

    selected_same_evidence_edges = [
        item
        for item in result.selected
        if item.hit.hit_type == "edge" and item.hit.evidence_refs == ["kg_ev:financial:ma"]
    ]
    remaining_same_evidence_edges = [
        item
        for item in result.remaining_high_potential
        if item.hit.hit_type == "edge" and item.hit.evidence_refs == ["kg_ev:financial:ma"]
    ]

    assert len(selected_same_evidence_edges) <= 1
    assert len(remaining_same_evidence_edges) <= 1


def test_judge_preselect_keeps_single_evidence_cluster_capped() -> None:
    hits = [
        RetrievalHit(
            hit_id=f"kg:financial:industry:{index}",
            hit_type="node",
            title=f"并购重组相关行业{index}",
            snippet=f"并购重组 相关行业{index} 资产影响",
            source="keyword",
            score=100.0 - index,
            node_refs=[f"kg:financial:industry:{index}"],
            evidence_refs=["kg_ev:financial:ma"],
            matched_terms=["并购重组", "行业"],
            matched_fields=["title", "search_text", "evidence_summary"],
        )
        for index in range(20)
    ]

    result = judge_preselect(
        hits,
        query="A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
        anchor=build_guarded_query_anchor(
            "A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
            known_nodes=[],
        ),
        search_plan=RetrievalSearchPlan(
            answer_targets=["新闻涉及的主体", "相关行业", "受影响的资产"],
            expected_evidence=["新闻报道"],
            relation_intents=["impact"],
        ),
        top_k_simple=8,
        top_k_complex=12,
        top_k_max=15,
    )

    assert result.top_k_requested == 12
    assert 0 < len(result.selected) <= 5
    assert all(item.hit.evidence_refs == ["kg_ev:financial:ma"] for item in result.selected)
    assert not any("backfill_to_top_k" in item.rank_reasons for item in result.selected)
