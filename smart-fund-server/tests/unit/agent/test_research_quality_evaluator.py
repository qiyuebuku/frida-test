from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from src.application.agents.financial_research.quality_evaluator import (
    _clarity_score,
    _exploration_score,
    _temporal_language_failures,
    evaluate_research_quality,
    require_publishable_quality,
)
from types import SimpleNamespace
from src.application.agents.financial_research.audit import (
    collect_opened_evidence,
    prune_unopened_evidence_plan_references,
)
from src.application.agents.financial_research.agent import (
    _bind_opened_daily_history_references,
)
from src.application.agents.financial_research.context import (
    AgentRunContext,
    ToolInvocation,
)
from src.application.agents.financial_research.schemas import ResearchTaskMode
from src.application.services.market_evidence_locator import (
    MarketEvidenceIdentity,
    encode_market_evidence_locator,
)
from src.application.agents.financial_research.schemas import (
    CurrentResearchReportProposal,
)


CUTOFF = datetime(2026, 8, 9, 6, 0, tzinfo=UTC)


def test_exploration_scores_non_trading_day_sector_overview_as_dimension_layer() -> None:
    tools = {
        "market_frame_open",
        "market_sector_overview",
        "market_sector_compare_open",
        "market_instrument_history",
        "market_evidence_open",
    }

    assert _exploration_score(tools, set()) == 10.0


def test_clarity_treats_one_subject_calibration_matrix_as_atomic() -> None:
    claim = SimpleNamespace(
        claim_type=SimpleNamespace(value="inference"),
        statement=(
            "CPO的3日绝对留出不一致；3日相对不一致；"
            "5日绝对不一致；5日相对一致。"
        ),
    )

    assert _clarity_score([claim]) == 10.0


def test_clarity_treats_one_horizon_calibration_with_tail_as_atomic() -> None:
    claim = SimpleNamespace(
        claim_type=SimpleNamespace(value="observed_fact"),
        statement=(
            "CPO概念3日前瞻历史类比中，绝对与相对口径时间留出"
            "中位数方向均不一致；同窗口样本最小收益-10.83%。"
        ),
    )

    assert _clarity_score([claim]) == 10.0


def test_clarity_treats_one_horizon_distribution_and_holdout_as_atomic() -> None:
    claim = SimpleNamespace(
        claim_type=SimpleNamespace(value="observed_fact"),
        statement=(
            "通信设备历史类比5日窗口全样本中位-0.65%、下十分位-5.15%"
            "；时间留出方向不一致。"
        ),
    )

    assert _clarity_score([claim]) == 10.0


def _hypotheses():
    return [
        {
            "hypothesis_id": "h-primary",
            "statement": "产业催化与资金确认共同支持相对强势",
            "role": "primary",
            "expected_observations": ["相对强度延续"],
            "refuting_observations": ["量能下降且宽度收缩"],
            "status": "partially_supported",
        },
        {
            "hypothesis_id": "h-alt",
            "statement": "上涨只是少数龙头推动的短期反弹",
            "role": "alternative",
            "expected_observations": ["龙头集中度上升"],
            "refuting_observations": ["板块内部多数标的同步增强"],
            "status": "challenged",
        },
        {
            "hypothesis_id": "h-quality",
            "statement": "错位快照夸大了相对强度",
            "role": "data_quality",
            "expected_observations": ["对齐后信号减弱"],
            "refuting_observations": ["同截止时间证据保持一致"],
            "status": "inconclusive",
        },
    ]


def _citation(cid: str, support: str = "supports") -> dict:
    return {
        "citation_id": cid,
        "kind": "market",
        "reference": f"market:v1:{cid}",
        "support": support,
        "as_of": CUTOFF.isoformat(),
    }


def _history_locator(snapshot_id: int, day: int) -> str:
    return encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": snapshot_id},
            data_type="sector_history",
            subject_id="sector:technology",
            fact_time=f"2026-08-{day:02d}T15:00:00+08:00",
        )
    )


def _proposal(*, deep: bool) -> CurrentResearchReportProposal:
    revision_id = "view-rev-1"
    view_id = "view-1"
    revision = {
        "view_id": view_id,
        "base_revision_id": None,
        "proposed_revision_id": revision_id,
        "event": "create",
        "status": "active",
        "title": "科技相对强势需要宽度与量能共同确认",
        "thesis": "当前信号值得组合层继续观察，但不能仅凭涨幅追价。",
        "scope": ["cn:a_share", "sector:technology"],
        "hypotheses": _hypotheses(),
        "evidence_plan": [
            {
                "plan_item_id": "p1",
                "hypothesis_ids": ["h-primary", "h-alt"],
                "question": "强势是否具有宽度和持续性",
                "required_evidence": "记录级市场证据、历史和原文",
                "layer": "source",
                "status": "completed",
                "opened_references": ["market:v1:fact"],
            },
            *(
                [{
                    "plan_item_id": "p-history",
                    "hypothesis_ids": ["h-primary"],
                    "question": "主观点对象是否具有多日持续性",
                    "required_evidence": "观点对象自身至少三个交易日历史",
                    "layer": "history",
                    "status": "completed",
                    "opened_references": [
                        _history_locator(101, 8),
                        _history_locator(102, 9),
                        _history_locator(103, 10),
                    ],
                }]
                if deep else []
            ),
        ],
        "claims": [
            {
                "claim_id": "c1",
                "claim_type": "observed_fact",
                "epistemic_status": "supported",
                "statement": "板块相对强度提高",
                "thesis_effect": "supports",
                "confidence": "medium_high",
                "evidence": [_citation("fact")],
            },
            {
                "claim_id": "c2",
                "claim_type": "inference",
                "epistemic_status": "challenged",
                "statement": "上涨可能过度依赖少数龙头",
                "thesis_effect": "refutes",
                "confidence": "medium",
                "evidence": [_citation("counter")],
            },
        ],
        "mechanism_chain": (
            [
                {
                    "link_id": "m1",
                    "cause": "产业催化",
                    "mechanism": "盈利预期改善吸引增量资金",
                    "effect": "板块相对强度提高",
                    "status": "inferred",
                    "evidence": [_citation("source")],
                    "invalidation_condition": "催化落空或资金连续流出",
                },
                {
                    "link_id": "m2",
                    "cause": "增量资金进入",
                    "mechanism": "成交扩散到非龙头标的",
                    "effect": "行情持续性增强",
                    "status": "hypothesis",
                    "evidence": [_citation("breadth")],
                    "invalidation_condition": "宽度收缩且龙头集中度继续上升",
                },
            ]
            if deep else []
        ),
        "market_structure": (
            {
                "breadth": "上涨向板块内部扩散但尚未达到全面扩散。",
                "leadership_concentration": "龙头贡献较高，需要警惕集中度风险。",
                "volume_liquidity_confirmation": "量能提供部分确认。",
                "crowding_and_reversal_risk": "短期拥挤度上升，存在反转风险。",
                "persistence_assessment": "需要下一交易日继续确认。",
                "pricing_state": "partially_priced",
                "evidence": [_citation("structure1"), _citation("structure2")],
            }
            if deep else None
        ),
        "decision_boundary": (
            {
                "portfolio_relevance": "可进入组合候选池继续比较。",
                "candidate_expressions_for_portfolio_review": ["行业 ETF"],
                "actions_not_supported": ["追涨或直接满仓"],
                "sizing_constraints_for_portfolio_review": ["等待宽度和量能确认"],
                "monitoring_signals": ["相对强度", "成交宽度", "龙头集中度"],
            }
            if deep else None
        ),
        "forecasts": (
            [
                {
                    "forecast_id": "f1",
                    "subject_id": "sector:technology",
                    "metric": "relative_strength",
                    "expected_direction": "up",
                    "benchmark_subject_id": "index:all-a",
                    "baseline_value": 1.0,
                    "evaluation_start_at": (CUTOFF + timedelta(days=1)).isoformat(),
                    "evaluation_end_at": (CUTOFF + timedelta(days=5)).isoformat(),
                    "invalidation_condition": "相对强度跌破基线且宽度收缩",
                }
            ]
            if deep else []
        ),
        "invalidation_conditions": ["宽度收缩", "主力资金连续流出"],
        "confidence": {
            "overall": "medium_high",
            "evidence_quality": 0.8,
            "independent_confirmation": 0.7,
            "counterevidence_resilience": 0.6,
            "timing_clarity": 0.7,
            "rationale": "市场、历史和原文交叉确认，但反证仍然存在。",
        },
    }
    return CurrentResearchReportProposal.model_validate(
        {
            "base_report_revision_id": None,
            "proposed_report_revision_id": "report-rev-1",
            "run_id": "run-quality",
            "trigger_id": "trigger-quality",
            "trigger_slot": "intraday",
            "cutoff_at": CUTOFF.isoformat(),
            "source_frame_id": "frame-quality",
            "status": "updated",
            "report_summary": "科技相对强势获得部分确认，但必须尊重反证与决策边界。",
            "research_question": "科技强势是否具备可持续的产业和资金基础？",
            "data_quality_assessment": "关键快照已按截止时间对齐。",
            "hypotheses": _hypotheses(),
            "evidence_plan": revision["evidence_plan"],
            "counterevidence_summary": "龙头集中和拥挤上升构成直接反证，削弱无条件延续假设。",
            "active_views": [],
            "view_revisions": [revision],
            "observation_requirements": (
                [
                    {
                        "requirement_id": "o1",
                        "subject_id": "sector:technology",
                        "metric_or_event": "下一交易日宽度和相对强度",
                        "reason": "验证行情是否扩散并持续",
                        "due_at": (CUTOFF + timedelta(days=2)).isoformat(),
                        "source_preference": "database",
                        "related_view_id": view_id,
                        "related_forecast_id": "f1",
                    }
                ]
                if deep else []
            ),
        }
    )


def test_deep_research_passes_all_quality_gates() -> None:
    proposal = _proposal(deep=True)
    opened = {
        citation.reference
        for revision in proposal.view_revisions
        for claim in revision.claims
        for citation in claim.evidence
    }
    opened.update(
        reference
        for plan in proposal.view_revisions[0].evidence_plan
        for reference in plan.opened_references
    )
    opened.update(
        {
            "market:v1:source", "market:v1:breadth",
            "market:v1:structure1", "market:v1:structure2",
        }
    )
    evaluation = evaluate_research_quality(
        proposal,
        tool_names={
            "market_change_brief_open",
            "market_frame_open",
            "market_dimension_open",
            "market_sector_open",
            "market_instrument_history",
            "market_evidence_open",
            "external_web_read",
        },
        opened_evidence_refs=opened,
    )

    assert evaluation.passed is True
    assert proposal.active_views[0].revision_id == "view-rev-1"
    assert evaluation.overall_score >= 75
    assert evaluation.hard_failures == []
    require_publishable_quality(evaluation)


def test_challenge_event_is_state_degradation_not_a_new_forward_contract() -> None:
    payload = _proposal(deep=True).model_dump(mode="python")
    challenged = deepcopy(payload["view_revisions"][0])
    challenged.update(
        view_id="view-old",
        base_revision_id="view-old-rev-1",
        proposed_revision_id="view-old-rev-2",
        event="challenge",
        status="challenged",
        market_structure=None,
        decision_boundary=None,
        evidence_plan=[deepcopy(payload["view_revisions"][0]["evidence_plan"][0])],
        forecasts=[],
    )
    payload["view_revisions"].append(challenged)
    proposal = CurrentResearchReportProposal.model_validate(payload)
    opened = {
        citation.reference
        for revision in proposal.view_revisions
        for claim in revision.claims
        for citation in claim.evidence
    }
    opened.update(
        reference
        for plan in proposal.view_revisions[0].evidence_plan
        for reference in plan.opened_references
    )
    opened.update(
        {
            "market:v1:source", "market:v1:breadth",
            "market:v1:structure1", "market:v1:structure2",
        }
    )

    evaluation = evaluate_research_quality(
        proposal,
        tool_names={
            "market_change_brief_open",
            "market_frame_open",
            "market_dimension_open",
            "market_sector_open",
            "market_instrument_history",
            "market_evidence_open",
            "external_web_read",
        },
        opened_evidence_refs=opened,
    )

    assert "missing_market_structure_assessment" not in evaluation.advisory_findings
    assert "missing_portfolio_decision_boundary" not in evaluation.advisory_findings
    assert "primary_view_missing_own_multi_day_history" not in evaluation.advisory_findings


def test_challenge_only_update_does_not_trigger_impossible_forward_contract_gate() -> None:
    payload = _proposal(deep=True).model_dump(mode="python")
    challenged = deepcopy(payload["view_revisions"][0])
    challenged.update(
        view_id="view-old",
        base_revision_id="view-old-rev-1",
        proposed_revision_id="view-old-rev-2",
        event="challenge",
        status="challenged",
        market_structure=None,
        decision_boundary=None,
        forecasts=[],
    )
    payload["view_revisions"] = [challenged]
    proposal = CurrentResearchReportProposal.model_validate(payload)
    opened = {
        citation["reference"]
        for claim in challenged["claims"]
        for citation in claim["evidence"]
    }
    opened.update(
        reference
        for plan in challenged["evidence_plan"]
        for reference in plan["opened_references"]
    )

    evaluation = evaluate_research_quality(
        proposal,
        tool_names={
            "market_change_brief_open",
            "market_sector_open",
            "market_instrument_history",
            "market_evidence_open",
            "market_historical_analogue_open",
        },
        opened_evidence_refs=opened,
    )

    assert "missing_market_structure_assessment" not in evaluation.advisory_findings
    assert "missing_portfolio_decision_boundary" not in evaluation.advisory_findings
    assert "incomplete_mechanism_chain" not in evaluation.advisory_findings


def test_daily_history_binding_reads_market_bucket_not_evidence_dict_keys() -> None:
    proposal = _proposal(deep=True)
    payload = proposal.model_dump(mode="python")
    payload["view_revisions"][0]["evidence_plan"][1]["opened_references"] = []
    proposal = CurrentResearchReportProposal.model_validate(payload)
    locators = [
        encode_market_evidence_locator(
            MarketEvidenceIdentity(
                kind="snapshot",
                domain="market_snapshot",
                identity={"id": 200 + day, "trade_date": f"2026-08-{day:02d}"},
                data_type="ths_sector_daily",
                subject_id="ths:concept:886033",
                fact_time=f"2026-08-{day:02d}T15:00:00+08:00",
            )
        )
        for day in (8, 9, 10)
    ]
    now = datetime.now(UTC)
    aliases = {f"market_ref:M{index}": locator for index, locator in enumerate(locators, 1)}
    context = AgentRunContext(
        run_id="run-bind-history",
        session_id="session-bind-history",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
        evidence_aliases=aliases,
        tool_invocations=[
            ToolInvocation(
                name="market_instrument_history",
                call_id="history-1",
                arguments={
                    "code": "ths:concept:886033",
                    "data_type": "ths_sector_daily",
                },
                # Compaction replaces reversible locators with short model-facing aliases.
                result={
                    "window_evidence": {
                        "latest": {
                            "trade_date": "2026-08-10",
                            "evidence_locator": "market_ref:M3",
                        },
                        "5_bars": {
                            "baseline": {
                                "trade_date": "2026-08-08",
                                "evidence_locator": "market_ref:M1",
                            },
                            "close_high": {
                                "trade_date": "2026-08-09",
                                "evidence_locator": "market_ref:M2",
                            },
                        },
                    }
                },
                started_at=now,
                finished_at=now,
            )
        ],
    )
    proposal_payload = proposal.model_dump(mode="python")
    proposal_payload["view_revisions"][0]["scope"] = ["ths:concept:886033"]
    proposal = CurrentResearchReportProposal.model_validate(proposal_payload)

    bound = _bind_opened_daily_history_references(proposal, context)
    bound = prune_unopened_evidence_plan_references(bound, context)

    history_plan = bound.view_revisions[0].evidence_plan[1]
    assert history_plan.opened_references == list(reversed(locators))
    assert set(locators).issubset(collect_opened_evidence(context)["market"])


def test_shallow_market_summary_is_rejected_with_actionable_failures() -> None:
    evaluation = evaluate_research_quality(
        _proposal(deep=False),
        tool_names={"market_frame_open", "market_dimension_open"},
    )

    assert evaluation.passed is False
    assert "missing_narrative_source_evidence" not in evaluation.advisory_findings
    assert "missing_exact_market_evidence" not in evaluation.advisory_findings
    assert "missing_direct_counterevidence" not in evaluation.hard_failures
    assert "incomplete_mechanism_chain" in evaluation.advisory_findings


def test_non_trading_landscape_fallback_satisfies_market_coverage() -> None:
    evaluation = evaluate_research_quality(
        _proposal(deep=True),
        tool_names={
            "market_frame_open",
            "market_global_overview_open",
            "market_sector_overview",
            "market_evidence_open",
            "market_instrument_history",
        },
    )

    assert "missing_full_market_landscape" not in evaluation.advisory_findings


def test_no_change_without_auditable_claims_is_rejected() -> None:
    payload = _proposal(deep=True).model_dump(mode="python")
    payload.update(
        status="no_change",
        view_revisions=[],
        active_views=[],
        claims=[],
        no_change_reason="暂未达到观点修订门槛。",
    )
    proposal = CurrentResearchReportProposal.model_validate(payload)

    evaluation = evaluate_research_quality(
        proposal,
        tool_names={"market_change_brief_open", "market_dimension_open"},
    )

    assert evaluation.passed is True
    assert evaluation.evidence_reference_count == 0
    assert "missing_auditable_report_claims" in evaluation.advisory_findings
    assert "missing_formal_evidence_references" in evaluation.advisory_findings
    require_publishable_quality(evaluation)


def test_market_citation_for_another_explicit_subject_is_rejected() -> None:
    payload = _proposal(deep=True).model_dump(mode="python")
    wrong_reference = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": 1},
            data_type="ths_market_profile",
            subject_id="cn:a_share:ths_market_profile",
        )
    )
    payload["view_revisions"][0]["claims"][0]["statement"] = (
        "贵金属板块881169上涨"
    )
    payload["view_revisions"][0]["claims"][0]["evidence"][0]["reference"] = (
        wrong_reference
    )
    proposal = CurrentResearchReportProposal.model_validate(payload)
    refs = {
        citation.reference
        for revision in proposal.view_revisions
        for claim in revision.claims
        for citation in claim.evidence
    }
    evaluation = evaluate_research_quality(
        proposal,
        tool_names={
            "market_change_brief_open", "market_sector_open",
            "market_instrument_history", "market_evidence_open",
            "external_web_read",
        },
        opened_evidence_refs=refs,
    )

    assert evaluation.passed is False
    assert "market_citation_subject_mismatch" in evaluation.hard_failures


def test_research_language_rejects_unbounded_superlative_and_allocation_advice() -> None:
    payload = _proposal(deep=True).model_dump(mode="python")
    payload["report_summary"] = "科技是全市场最强且唯一主线。"
    payload["view_revisions"][0]["decision_boundary"]["portfolio_relevance"] = (
        "当前适合作为核心配置方向。"
    )
    proposal = CurrentResearchReportProposal.model_validate(payload)
    opened = {
        citation.reference
        for revision in proposal.view_revisions
        for claim in revision.claims
        for citation in claim.evidence
    }
    opened.update(
        reference
        for plan in proposal.view_revisions[0].evidence_plan
        for reference in plan.opened_references
    )

    evaluation = evaluate_research_quality(
        proposal,
        tool_names={
            "market_change_brief_open", "market_sector_open",
            "market_instrument_history", "market_evidence_open",
            "external_web_read",
        },
        opened_evidence_refs=opened,
    )

    assert evaluation.passed is False
    assert "research_language_overclaim" in evaluation.advisory_findings


def test_observed_fact_with_interpretation_is_rejected() -> None:
    payload = _proposal(deep=True).model_dump(mode="python")
    payload["view_revisions"][0]["claims"][0]["statement"] = (
        "创业板ETF连续三日震荡，反映反弹动能明显减弱。"
    )
    proposal = CurrentResearchReportProposal.model_validate(payload)
    opened = {
        citation.reference
        for revision in proposal.view_revisions
        for claim in revision.claims
        for citation in claim.evidence
    }
    opened.update(
        reference
        for plan in proposal.view_revisions[0].evidence_plan
        for reference in plan.opened_references
    )

    evaluation = evaluate_research_quality(
        proposal,
        tool_names={
            "market_change_brief_open", "market_sector_open",
            "market_instrument_history", "market_evidence_open",
            "external_web_read",
        },
        opened_evidence_refs=opened,
    )

    assert evaluation.passed is False
    assert "observed_fact_contains_inference" in evaluation.advisory_findings


def test_intraday_evidence_cannot_be_called_premarket() -> None:
    payload = _proposal(deep=True).model_dump(mode="python")
    reference = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": 501, "trade_date": "2026-08-11"},
            data_type="ths_sector_rotation",
            subject_id="ths:industry:881169:rise_rate",
            fact_time="2026-08-11T02:01:00+00:00",
        )
    )
    claim = payload["view_revisions"][0]["claims"][0]
    claim["statement"] = "贵金属盘前上涨比例为71.43%。"
    claim["evidence"][0]["reference"] = reference
    proposal = CurrentResearchReportProposal.model_validate(payload)

    assert _temporal_language_failures(proposal) == ["c1"]
