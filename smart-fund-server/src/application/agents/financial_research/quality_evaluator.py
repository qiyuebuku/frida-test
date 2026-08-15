"""Deterministic, versioned quality evaluation for Research proposals."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Iterable, Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from src.application.agents.financial_research.schemas import (
    CurrentResearchReportProposal,
    ResearchContract,
    ResearchRunStatus,
)
from src.application.agents.financial_research.semantic_evaluator import (
    SemanticResearchEvaluation,
)
from src.application.services.market_evidence_locator import (
    LOCATOR_PREFIX,
    decode_market_evidence_locator,
)


QUALITY_EVALUATOR_VERSION = "research-quality-v3"


class ResearchQualityScores(ResearchContract):
    evidence_entailment: float = Field(ge=0, le=10)
    historical_calibration: float = Field(ge=0, le=10)
    counterevidence_directness: float = Field(ge=0, le=10)
    mechanism_and_source_quality: float = Field(ge=0, le=10)
    market_structure_and_pricing: float = Field(ge=0, le=10)
    portfolio_decision_value: float = Field(ge=0, le=10)
    exploration_depth: float = Field(ge=0, le=10)
    clarity_and_structure: float = Field(ge=0, le=10)


class ResearchQualityEvaluation(ResearchContract):
    evaluation_id: str = Field(min_length=1, max_length=220)
    run_id: str = Field(min_length=1, max_length=180)
    evaluator_version: Literal["research-quality-v3"] = QUALITY_EVALUATOR_VERSION
    evaluated_at: datetime
    overall_score: float = Field(ge=0, le=100)
    grade: Literal["excellent", "good", "needs_improvement", "rejected"]
    passed: bool
    scores: ResearchQualityScores
    hard_failures: list[str] = Field(default_factory=list, max_length=30)
    advisory_findings: list[str] = Field(default_factory=list, max_length=50)
    improvement_actions: list[str] = Field(default_factory=list, max_length=30)
    tool_coverage: list[str] = Field(default_factory=list, max_length=100)
    evidence_reference_count: int = Field(ge=0)
    outcome_adjusted_score: float | None = Field(default=None, ge=0, le=100)


def evaluate_research_quality(
    proposal: CurrentResearchReportProposal,
    *,
    tool_names: Iterable[str],
    opened_evidence_refs: Iterable[str] = (),
    evaluated_at: datetime | None = None,
) -> ResearchQualityEvaluation:
    """Score research substance from the proposal and actual tool trajectory."""

    tools = {str(item) for item in tool_names if str(item)}
    opened = {str(item) for item in opened_evidence_refs if str(item)}
    revisions = proposal.view_revisions
    # An invalidation revision records why an old view is closed.  It should
    # carry direct refutation evidence, but it is not a new investable thesis
    # and therefore does not need a fresh market-structure/portfolio-expression
    # package.  Score those forward-looking contracts on surviving views only.
    forward_revisions = [
        item for item in revisions
        if item.status in {"active", "challenged"}
        and item.event not in {"challenge", "invalidate"}
    ]
    report_claims = proposal.claims
    claims = [*report_claims, *[claim for revision in revisions for claim in revision.claims]]
    citations = [
        citation
        for claim in claims
        for citation in claim.evidence
    ]
    mechanism_citations = [
        citation
        for revision in revisions
        for link in revision.mechanism_chain
        for citation in link.evidence
    ]
    structure_citations = [
        citation
        for revision in revisions
        if revision.market_structure is not None
        for citation in revision.market_structure.evidence
    ]
    all_citations = [*citations, *mechanism_citations, *structure_citations]
    references = {item.reference for item in all_citations}

    supported_claims = sum(
        any(item.support == "supports" for item in claim.evidence)
        for claim in claims
    )
    factual_integrity = 10.0 if all(
        claim.claim_type not in {"observed_fact", "source_claim", "forecast"}
        or any(item.support == "supports" for item in claim.evidence)
        for claim in claims
    ) else 3.0
    evidence_coverage = _ratio_score(supported_claims, len(claims))
    evidence_independence = min(
        10.0,
        len(references) * 1.5 + len({item.kind for item in all_citations}) * 2,
    )
    if proposal.status == ResearchRunStatus.UPDATED:
        if forward_revisions:
            mechanism_score = min(
                10.0,
                sum(len(revision.mechanism_chain) for revision in forward_revisions) * 3.0
                + (2.0 if mechanism_citations else 0.0),
            )
            structure_score = 10.0 if all(
                revision.market_structure is not None
                and len(revision.market_structure.evidence) >= 2
                for revision in forward_revisions
            ) else 0.0
        else:
            # A challenge/invalidate-only revision records deterioration of an
            # existing view. It deliberately does not propose a new forward
            # expression, so forward-contract fields are not missing; they are
            # not applicable. Scoring them as zero made the later gate
            # impossible to satisfy, regardless of how often the model retried.
            mechanism_score = 10.0
            structure_score = 10.0
    else:
        completed_layers = {
            item.layer for item in proposal.evidence_plan
            if item.status == "completed" and item.opened_references
        }
        mechanism_score = min(10.0, 2.5 * len(completed_layers))
        structure_score = min(
            10.0,
            2.0 * sum(
                tool in tools
                for tool in (
                    "market_change_brief_open",
                    "market_sector_rankings",
                    "market_sector_open",
                    "market_sector_compare_open",
                    "market_instrument_history",
                    "market_evidence_open",
                )
            ),
        )
    forecast_count = sum(len(revision.forecasts) for revision in revisions)
    if proposal.status == ResearchRunStatus.UPDATED:
        falsifiability = min(
            10.0,
            forecast_count * 4.0
            + min(3.0, sum(len(item.invalidation_conditions) for item in revisions))
            + (3.0 if proposal.observation_requirements else 0.0),
        )
    else:
        falsifiability = min(
            10.0,
            2.0 * sum(bool(item.refuting_observations) for item in proposal.hypotheses)
            + 2.0 * sum(item.validation_deadline is not None for item in proposal.hypotheses)
            + (2.0 if proposal.observation_requirements else 0.0),
        )
    decision_score = (
        10.0 if proposal.status == ResearchRunStatus.UPDATED and (
            not forward_revisions
            or all(
                revision.decision_boundary is not None
                and revision.decision_boundary.actions_not_supported
                and revision.decision_boundary.monitoring_signals
                for revision in forward_revisions
            )
        )
        else min(
            10.0,
            (5.0 if proposal.no_change_reason else 0.0)
            + (5.0 if proposal.observation_requirements else 0.0),
        )
    )

    forecast_calibration = (
        8.0 if "market_historical_analogue_open" in tools
        else 5.0 if tools.intersection({"market_instrument_history", "market_sector_open"})
        else 3.0 if forecast_count else 6.0
    )
    scores = ResearchQualityScores(
        evidence_entailment=round((factual_integrity + evidence_coverage) / 2, 2),
        historical_calibration=forecast_calibration,
        counterevidence_directness=_counterevidence_score(proposal),
        mechanism_and_source_quality=round(
            (mechanism_score + evidence_independence) / 2, 2
        ),
        market_structure_and_pricing=structure_score,
        portfolio_decision_value=decision_score,
        exploration_depth=_exploration_score(tools, references),
        clarity_and_structure=_clarity_score(claims),
    )
    failures: list[str] = []
    actions: list[str] = []
    history_tools = {
        "market_instrument_history",
        "market_technical_state_open",
        "market_historical_analogue_open",
        "market_sector_open",
        "market_sector_compare_open",
    }
    # External narrative is optional.  Objective price/flow/history research
    # must not be pushed into inventing a causal story merely to satisfy a
    # structural gate.  When a report does use narrative sources, citation and
    # semantic evaluation still check their lineage and entailment.
    if not claims:
        failures.append("missing_auditable_report_claims")
        actions.append("把报告关键结论拆成逐条 Claim，并为事实和反证附上本次打开的证据")
    if references.difference(opened):
        failures.append("citation_not_present_in_opened_ledger")
        actions.append("删除未实际打开的引用，并从证据账本复制正式引用")
    semantic_mismatches = _market_citation_semantic_mismatches(claims)
    if semantic_mismatches:
        failures.append("market_citation_subject_mismatch")
        actions.append(
            "以下 Claim 引用了其他对象的市场记录："
            + "、".join(semantic_mismatches[:8])
            + "；请打开并引用与主张中标的或板块代码一致的记录"
        )
    if not references:
        failures.append("missing_formal_evidence_references")
        actions.append("至少打开并引用支持市场判断的记录级证据")
    if not _valid_data_quality_hypothesis(proposal):
        failures.append("invalid_data_quality_hypothesis")
        actions.append("数据质量假设只能检查覆盖、新鲜度、截止时间、口径或缺失，方向性市场解释应归入主假设或替代假设")
    if (
        proposal.status == ResearchRunStatus.NO_CHANGE
        and any(item.impact == "critical" for item in proposal.evidence_gaps)
    ):
        failures.append("no_change_has_critical_evidence_gap")
        actions.append("关键证据缺失时不能用 no_change 固化市场判断；应补齐证据或提交 insufficient_evidence")
    landscape_fallback = {
        "market_frame_open",
        "market_global_overview_open",
        "market_sector_overview",
    }
    if (
        "market_change_brief_open" not in tools
        and not landscape_fallback.issubset(tools)
    ):
        failures.append("missing_full_market_landscape")
        actions.append(
            "全市场复核应打开 overall 变化简报；非交易日简报没有可登记记录时，"
            "至少同时核对市场框架、全球概览和板块概览后再选择下钻方向"
        )
    has_record_level_market_evidence = any(
        reference.startswith(LOCATOR_PREFIX) for reference in references
    )
    if "market_evidence_open" not in tools and not has_record_level_market_evidence:
        failures.append("missing_exact_market_evidence")
        actions.append(
            "使用返回稳定 market:v1 定位符的专用工具，或通过 market_evidence_open "
            "打开记录级市场证据和关键字段"
        )
    if not tools.intersection(history_tools):
        failures.append("missing_history_or_object_drilldown")
        actions.append("下钻对象和历史，验证持续性、事件窗口与基线")
    if not any(
        claim.thesis_effect == "refutes"
        and any(item.support == "supports" for item in claim.evidence)
        for claim in claims
    ):
        failures.append("missing_direct_counterevidence")
        actions.append("至少引用一条直接反证，并说明它削弱哪条假设")
    if evidence_independence < 6:
        failures.append("insufficient_independent_evidence")
        actions.append("补充不同证据身份或不同证据类型，避免重复计算同一事实")
    if (
        proposal.status == ResearchRunStatus.UPDATED
        and forward_revisions
        and mechanism_score < 8
    ):
        failures.append("incomplete_mechanism_chain")
        actions.append("补齐至少两段带证据和失效条件的原因—机制—结果链")
    if (
        proposal.status != ResearchRunStatus.UPDATED or forward_revisions
    ) and structure_score < 8:
        failures.append("missing_market_structure_assessment")
        actions.append("补齐宽度、龙头集中、量能、拥挤、持续性和定价状态")
    if (
        proposal.status == ResearchRunStatus.UPDATED
        and forward_revisions
        and not proposal.observation_requirements
    ):
        failures.append("missing_testable_forecast")
        actions.append("声明可验证的后续观察要求；历史稳健性不足时不得强造方向预测")
    if proposal.status == ResearchRunStatus.UPDATED:
        history_failures = _primary_history_failures(proposal)
        if history_failures:
            failures.append("primary_view_missing_own_multi_day_history")
            actions.append(
                "正式观点必须用观点对象自身至少3个不同交易日的历史验证持续性，"
                "不得用无关指数或ETF历史替代：" + "、".join(history_failures)
            )
    if (
        proposal.status != ResearchRunStatus.UPDATED or forward_revisions
    ) and decision_score < 10:
        failures.append("missing_portfolio_decision_boundary")
        actions.append("明确组合层可继续评估什么、不能据此做什么和监控信号")
    if (
        proposal.status != ResearchRunStatus.UPDATED
        and re.search(r"(不存在|没有|缺乏|未形成).{0,12}(主线|方向)", (
            proposal.report_summary + "\n" + (proposal.no_change_reason or "")
        ))
        and "market_sector_compare_open" not in tools
    ):
        failures.append("missing_multi_candidate_comparison")
        actions.append(
            "声称市场没有可持续主线前，使用 market_sector_compare_open "
            "对两个至四个最重要候选做同口径比较，不能只下钻一个板块"
        )
    overclaim_failures = _language_boundary_failures(proposal)
    if overclaim_failures:
        failures.append("research_language_overclaim")
        actions.extend(overclaim_failures)
    fact_inference_failures = _observed_fact_inference_failures(proposal)
    if fact_inference_failures:
        failures.append("observed_fact_contains_inference")
        actions.append(
            "以下 observed_fact 混入了推断性结论，请拆成事实与 inference："
            + "、".join(fact_inference_failures[:8])
        )
    temporal_failures = _temporal_language_failures(proposal)
    if temporal_failures:
        failures.append("temporal_language_not_supported")
        actions.append(
            "以下 Claim 的盘前/连续性描述与证据时间不符："
            + "、".join(temporal_failures[:8])
        )
    overall = _overall(scores)
    if overall < 75:
        failures.append("overall_score_below_75")
        actions.append("根据分项得分补齐研究后重新提交")
    integrity_failure_codes = {
        "citation_not_present_in_opened_ledger",
        "market_citation_subject_mismatch",
    }
    advisory_findings = list(dict.fromkeys(
        item for item in failures if item not in integrity_failure_codes
    ))
    hard_failures = list(dict.fromkeys(
        item for item in failures if item in integrity_failure_codes
    ))
    return _evaluation(
        proposal,
        scores=scores,
        overall=overall,
        failures=hard_failures,
        advisory_findings=advisory_findings,
        actions=list(dict.fromkeys(actions)),
        tools=tools,
        evidence_count=len(references),
        evaluated_at=evaluated_at,
    )


def require_publishable_quality(evaluation: ResearchQualityEvaluation) -> None:
    if evaluation.hard_failures:
        raise ValueError(
            "Research quality gate failed: "
            f"score={evaluation.overall_score:.1f}; failures="
            + ", ".join(evaluation.hard_failures)
            + "; actions="
            + "；".join(evaluation.improvement_actions)
        )


def merge_semantic_quality(
    deterministic_scores: ResearchQualityScores,
    semantic: SemanticResearchEvaluation,
) -> tuple[ResearchQualityScores, float, str]:
    """Merge independent semantic judgment without changing integrity status."""

    semantic_scores = semantic.scores
    merged = ResearchQualityScores(
        evidence_entailment=semantic_scores.evidence_entailment,
        historical_calibration=semantic_scores.forecast_calibration,
        counterevidence_directness=semantic_scores.counterevidence_directness,
        mechanism_and_source_quality=round(
            (
                semantic_scores.mechanism_completeness
                + semantic_scores.source_independence
            ) / 2,
            2,
        ),
        market_structure_and_pricing=round(
            (
                deterministic_scores.market_structure_and_pricing
                + semantic_scores.narrative_selection_bias
            ) / 2,
            2,
        ),
        portfolio_decision_value=semantic_scores.decision_value,
        exploration_depth=deterministic_scores.exploration_depth,
        clarity_and_structure=deterministic_scores.clarity_and_structure,
    )
    overall = _overall(merged)
    key_floor = min(
        merged.evidence_entailment,
        merged.historical_calibration,
        merged.counterevidence_directness,
    )
    grade = (
        "excellent" if overall >= 90 and key_floor >= 8
        else "good" if overall >= 75
        else "needs_improvement"
    )
    return merged, overall, grade


def _exploration_score(tools: set[str], references: set[str]) -> float:
    layers = (
        {
            "market_frame_open",
            "market_change_brief_open",
            "research_data_catalog_open",
        },
        {
            "market_dimension_open",
            "market_change_brief_open",
            "market_topic_open",
            "market_domain_open",
        },
        {"market_sector_open", "market_sector_compare_open", "market_instrument_open", "kg_card_open"},
        {
            "market_instrument_history",
            "market_technical_state_open",
            "market_historical_analogue_open",
        },
        {"market_evidence_open", "kg_edge_open", "external_web_read", "external_content_read"},
    )
    covered = sum(2.0 for layer in layers if tools.intersection(layer))
    if (
        not tools.intersection(layers[-1])
        and any(reference.startswith(LOCATOR_PREFIX) for reference in references)
    ):
        covered += 2.0
    return covered


def _hypothesis_score(proposal: CurrentResearchReportProposal) -> float:
    groups = [proposal.hypotheses, *[item.hypotheses for item in proposal.view_revisions]]
    if not groups:
        return 0.0
    group_scores = []
    for hypotheses in groups:
        roles = {item.role for item in hypotheses}
        score = 6.0 if {"primary", "alternative", "data_quality"}.issubset(roles) else 0.0
        if all(item.refuting_observations for item in hypotheses):
            score += 2.0
        if any(item.status in {"challenged", "falsified", "inconclusive"} for item in hypotheses):
            score += 2.0
        group_scores.append(score)
    return sum(group_scores) / len(group_scores)


def _counterevidence_score(proposal) -> float:
    claims = [
        *proposal.claims,
        *[
        claim for revision in proposal.view_revisions for claim in revision.claims
        ],
    ]
    score = 4.0 if any(
        claim.thesis_effect == "refutes"
        and any(item.support == "supports" for item in claim.evidence)
        for claim in claims
    ) else 0.0
    if len(proposal.counterevidence_summary.strip()) >= 80:
        score += 3.0
    if any(item.role == "alternative" and item.status != "unverified" for item in proposal.hypotheses):
        score += 3.0
    return score


def _valid_data_quality_hypothesis(proposal: CurrentResearchReportProposal) -> bool:
    hypotheses = [
        *proposal.hypotheses,
        *[
            item
            for revision in proposal.view_revisions
            for item in revision.hypotheses
        ],
    ]
    quality_hypotheses = [item for item in hypotheses if item.role == "data_quality"]
    # 非更新型结论不必为了凑格式机械创建数据质量假设；但一旦声明该角色，
    # 内容必须真正在讨论数据质量。updated 的三角色完整性由 Schema 强制。
    if not quality_hypotheses:
        return proposal.status != ResearchRunStatus.UPDATED
    quality_terms = (
        "数据", "覆盖", "缺失", "新鲜", "截止", "时间", "口径", "快照",
        "延迟", "异常", "持仓", "返回", "核验", "data", "fresh", "cutoff", "missing", "coverage",
    )
    return all(
        any(term in item.statement.lower() for term in quality_terms)
        for item in quality_hypotheses
    )


def _market_citation_semantic_mismatches(claims) -> list[str]:
    """Reject the common failure mode of citing an unrelated opened object.

    Full natural-language entailment remains the Agent's job, but explicit
    six-digit instrument/sector codes give us a deterministic identity check.
    """

    mismatches: list[str] = []
    for claim in claims:
        explicit_codes = set(re.findall(r"(?<!\d)\d{6}(?!\d)", claim.statement))
        if not explicit_codes:
            continue
        market_citations = [
            item for item in claim.evidence
            if item.kind == "market" and item.reference.startswith(LOCATOR_PREFIX)
        ]
        if not market_citations:
            continue
        cited_subjects: list[str] = []
        for citation in market_citations:
            try:
                identity = decode_market_evidence_locator(citation.reference)
            except ValueError:
                continue
            cited_subjects.append(str(identity.subject_id or ""))
        if cited_subjects and not any(
            code in subject
            for code in explicit_codes
            for subject in cited_subjects
        ):
            mismatches.append(claim.claim_id)
    return mismatches


def _primary_history_failures(
    proposal: CurrentResearchReportProposal,
) -> list[str]:
    failures: list[str] = []
    for revision in proposal.view_revisions:
        if (
            revision.status not in {"active", "challenged"}
            or revision.event in {"challenge", "invalidate"}
        ):
            continue
        primary_ids = {
            item.hypothesis_id for item in revision.hypotheses
            if item.role == "primary"
        }
        explicit_codes = set(
            re.findall(
                r"(?<!\d)\d{6}(?!\d)",
                " ".join(
                    [revision.title, revision.thesis, *revision.scope]
                    + [
                        item.statement for item in revision.hypotheses
                        if item.role == "primary"
                    ]
                ),
            )
        )
        history_refs = {
            reference
            for plan in revision.evidence_plan
            if (
                plan.layer == "history"
                and plan.status == "completed"
                and primary_ids.intersection(plan.hypothesis_ids)
            )
            for reference in plan.opened_references
            if reference.startswith(LOCATOR_PREFIX)
        }
        dates: set[str] = set()
        matching_subject = not explicit_codes
        for reference in history_refs:
            try:
                identity = decode_market_evidence_locator(reference)
            except ValueError:
                continue
            subject = str(identity.subject_id or "")
            if not explicit_codes or any(code in subject for code in explicit_codes):
                matching_subject = True
                trade_date = identity.identity.get("trade_date")
                if trade_date or identity.fact_time:
                    dates.add(str(trade_date or identity.fact_time)[:10])
        if not matching_subject or len(dates) < 3:
            failures.append(revision.view_id)
    return failures


def _language_boundary_failures(
    proposal: CurrentResearchReportProposal,
) -> list[str]:
    texts = [proposal.report_summary]
    for revision in proposal.view_revisions:
        texts.extend(
            [
                revision.title,
                revision.thesis,
                (
                    revision.decision_boundary.portfolio_relevance
                    if revision.decision_boundary is not None else ""
                ),
            ]
        )
    joined = "\n".join(texts)
    failures: list[str] = []
    if re.search(r"全市场最强|唯一主线|唯一方向", joined):
        failures.append(
            "只比较有限候选时不得使用‘全市场最强’或‘唯一主线’，请改为有边界的相对描述"
        )
    if re.search(r"适合.{0,8}配置|应当.{0,8}配置|建议.{0,8}(买入|卖出|加仓|减仓|配置)", joined):
        failures.append(
            "Research 不得给出配置或交易建议，只能声明值得 Portfolio 继续评估的候选表达"
        )
    return failures


def _observed_fact_inference_failures(
    proposal: CurrentResearchReportProposal,
) -> list[str]:
    """Keep raw observations separate from interpretations and mechanisms."""

    inference_terms = re.compile(
        r"说明|表明|意味着|反映|可能|持续性.{0,4}(存疑|不足)|"
        r"动能.{0,4}(减弱|增强)|进入.{0,6}(阶段|状态)|形成.{0,6}主线"
    )
    claims = [
        *proposal.claims,
        *[
            claim
            for revision in proposal.view_revisions
            for claim in revision.claims
        ],
    ]
    return [
        claim.claim_id
        for claim in claims
        if claim.claim_type == "observed_fact"
        and inference_terms.search(claim.statement)
    ]


def _temporal_language_failures(
    proposal: CurrentResearchReportProposal,
) -> list[str]:
    claims = [
        *proposal.claims,
        *[
            claim
            for revision in proposal.view_revisions
            for claim in revision.claims
        ],
    ]
    failures: list[str] = []
    for claim in claims:
        identities = []
        for citation in claim.evidence:
            if citation.kind != "market" or not citation.reference.startswith(LOCATOR_PREFIX):
                continue
            try:
                identities.append(decode_market_evidence_locator(citation.reference))
            except ValueError:
                continue
        if "盘前" in claim.statement:
            for identity in identities:
                if not identity.fact_time:
                    continue
                try:
                    fact_time = datetime.fromisoformat(
                        str(identity.fact_time).replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
                if fact_time.tzinfo is not None:
                    fact_time = fact_time.astimezone(ZoneInfo("Asia/Shanghai"))
                if (9, 30) <= (fact_time.hour, fact_time.minute) <= (11, 30):
                    failures.append(claim.claim_id)
                    break
        if re.search(r"连续.{0,6}(稳居|排名)|持续.{0,6}(稳居|排名)", claim.statement):
            dates_by_series: dict[tuple[str, str], set[str]] = {}
            for identity in identities:
                key = (str(identity.data_type or ""), str(identity.subject_id or ""))
                trade_date = identity.identity.get("trade_date") or identity.fact_time
                if trade_date:
                    dates_by_series.setdefault(key, set()).add(str(trade_date)[:10])
            if not any(len(dates) >= 2 for dates in dates_by_series.values()):
                failures.append(claim.claim_id)
    return list(dict.fromkeys(failures))


def _ratio_score(numerator: int, denominator: int) -> float:
    return 10.0 if denominator == 0 else round(10.0 * numerator / denominator, 2)


def _overall(scores: ResearchQualityScores) -> float:
    weights = {
        "evidence_entailment": 0.20,
        "historical_calibration": 0.20,
        "counterevidence_directness": 0.15,
        "mechanism_and_source_quality": 0.15,
        "market_structure_and_pricing": 0.10,
        "portfolio_decision_value": 0.10,
        "exploration_depth": 0.05,
        "clarity_and_structure": 0.05,
    }
    return round(
        sum(getattr(scores, key) * weight for key, weight in weights.items()) * 10,
        2,
    )


def _evaluation(
    proposal,
    *,
    scores,
    overall,
    failures,
    advisory_findings,
    actions,
    tools,
    evidence_count,
    evaluated_at,
):
    passed = not failures
    key_floor = min(
        scores.evidence_entailment,
        scores.historical_calibration,
        scores.counterevidence_directness,
    )
    grade = (
        "rejected" if failures
        else "excellent" if overall >= 90 and key_floor >= 8
        else "good" if overall >= 75
        else "needs_improvement"
    )
    return ResearchQualityEvaluation(
        evaluation_id=f"quality:{proposal.run_id}:{QUALITY_EVALUATOR_VERSION}",
        run_id=proposal.run_id,
        evaluated_at=evaluated_at or datetime.now(UTC),
        overall_score=overall,
        grade=grade,
        passed=passed,
        scores=scores,
        hard_failures=failures,
        advisory_findings=advisory_findings,
        improvement_actions=actions,
        tool_coverage=sorted(tools),
        evidence_reference_count=evidence_count,
    )


def _clarity_score(claims) -> float:
    if not claims:
        return 0.0
    atomic = 0
    for claim in claims:
        statement = claim.statement
        if len(statement) > 180:
            continue
        separators = len(re.findall(r"[；;。]", statement))
        # A calibration result for one subject and one horizon is one auditable
        # proposition even when it compactly reports absolute/relative checks
        # plus a tail statistic.  Requiring the model to split that matrix
        # hides the relationship between its cells and rewards less complete
        # disclosure. Other prose keeps the strict atomicity rule.
        is_calibration_matrix = (
            (
                any(term in statement for term in ("历史类比", "时间留出"))
                and any(term in statement for term in ("3日", "5日", "前瞻"))
            )
            or (
                any(term in statement for term in ("绝对", "相对", "四格"))
                and all(term in statement for term in ("3日", "5日"))
            )
        )
        if separators <= 1 or is_calibration_matrix:
            atomic += 1
    return _ratio_score(atomic, len(claims))
