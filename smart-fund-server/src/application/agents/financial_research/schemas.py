"""Authoritative contracts for one Research Agent review.

The models in this module deliberately separate current read models, immutable
view revisions, evidence state, forecasts, and outcome evaluation.  Markdown is
only a presentation field; it is never the sole carrier of research state.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


_MODEL_LOCAL_DATETIME_FIELDS = {
    "observed_at",
    "as_of",
    "validation_deadline",
    "evaluation_start_at",
    "evaluation_end_at",
    "due_at",
    "valid_until",
}
_CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


class ResearchContract(BaseModel):
    """Reject timezone-ambiguous datetimes at every business boundary."""

    @field_validator("*", mode="before")
    @classmethod
    def accept_model_facing_china_local_time(cls, value, info: ValidationInfo):
        """Bind the compact model timestamp contract to an aware business time."""

        if info.field_name not in _MODEL_LOCAL_DATETIME_FIELDS:
            return value
        if not isinstance(value, str):
            return value
        parsed = None
        for pattern in (
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
        ):
            try:
                parsed = datetime.strptime(value, pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            return value
        return parsed.replace(tzinfo=_CHINA_TIMEZONE)

    @field_validator("*", mode="after")
    @classmethod
    def require_aware_datetimes(cls, value):
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("research datetimes must include a timezone")
        return value


class ResearchTaskMode(StrEnum):
    RESEARCH_REVIEW = "research_review"


class ResearchRunMode(StrEnum):
    PRODUCTION = "production"
    SHADOW = "shadow"
    DEBUG = "debug"
    REPLAY = "replay"


class ResearchTriggerSlot(StrEnum):
    PREMARKET = "premarket"
    INTRADAY = "intraday"
    POSTMARKET = "postmarket"
    EVENT = "event"
    DEEP_RESEARCH = "deep_research"
    OUTCOME_DUE = "outcome_due"


class ResearchRunStatus(StrEnum):
    UPDATED = "updated"
    NO_CHANGE = "no_change"
    BLOCKED = "blocked"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INCOMPLETE = "incomplete"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    MEDIUM_HIGH = "medium_high"
    HIGH = "high"


class EvidenceCitation(ResearchContract):
    citation_id: str = Field(min_length=1, max_length=180)
    kind: Literal["card", "edge", "community", "external", "market"]
    reference: str = Field(
        min_length=1,
        max_length=512,
        description=(
            "Opened Card/Edge/Community ID, opened URL or external_content "
            "handle, or stable market evidence locator returned by a market "
            "read tool."
        ),
    )
    support: Literal["supports", "refutes", "context"] = Field(
        description=(
            "How this citation relates to this exact claim. Use 'supports' for "
            "evidence that directly establishes the claim, 'refutes' for "
            "counterevidence, and 'context' only for background. Every "
            "observed_fact claim must contain at least one citation whose "
            "support is exactly 'supports'."
        )
    )
    observed_at: datetime | None = None
    as_of: datetime | None = None


class DataQualityIssue(ResearchContract):
    issue_code: str = Field(min_length=1, max_length=96)
    severity: Literal["info", "warning", "critical"]
    dimension: str = Field(min_length=1, max_length=96)
    description: str = Field(min_length=1, max_length=1000)
    affected_handles: list[str] = Field(default_factory=list, max_length=20)


class MarketDimensionSummary(ResearchContract):
    dimension: str = Field(min_length=1, max_length=96)
    summary: str = Field(min_length=1, max_length=1600)
    state: Literal[
        "strengthening",
        "weakening",
        "mixed",
        "stable",
        "unknown",
    ]
    as_of: datetime
    evidence_handles: list[str] = Field(default_factory=list, max_length=12)


class MarketStateFrame(ResearchContract):
    """Bounded, time-aligned starting point; never a dashboard payload dump."""

    frame_id: str = Field(min_length=1, max_length=180)
    cutoff_at: datetime
    trade_date: date | None = None
    market_session: Literal[
        "pre_open",
        "opening_auction",
        "continuous",
        "midday_break",
        "closed",
        "non_trading_day",
        "unknown",
    ]
    overview: str = Field(min_length=1, max_length=4000)
    dimensions: list[MarketDimensionSummary] = Field(
        default_factory=list,
        max_length=12,
    )
    significant_changes: list[str] = Field(default_factory=list, max_length=20)
    quality_issues: list[DataQualityIssue] = Field(default_factory=list, max_length=20)
    drilldown_handles: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_time_boundary(self) -> "MarketStateFrame":
        future_dimensions = [
            item.dimension for item in self.dimensions if item.as_of > self.cutoff_at
        ]
        if future_dimensions:
            raise ValueError(
                "Market State Frame contains dimensions after cutoff_at: "
                + ", ".join(future_dimensions)
            )
        return self


class ResearchTriggerEnvelope(ResearchContract):
    trigger_id: str = Field(min_length=1, max_length=180)
    trigger_slot: ResearchTriggerSlot
    source: Literal[
        "schedule",
        "market_change",
        "research_request",
        "view_challenge",
        "observation_due",
        "human",
        "replay",
    ]
    reason: str = Field(min_length=1, max_length=2000)
    cutoff_at: datetime
    run_mode: ResearchRunMode = ResearchRunMode.SHADOW
    max_tool_calls: int = Field(default=40, ge=1, le=200)
    max_elapsed_seconds: int = Field(default=3600, ge=30, le=3600)


class ActiveViewSnapshot(ResearchContract):
    view_id: str = Field(min_length=1, max_length=180)
    revision_id: str = Field(min_length=1, max_length=180)
    title: str = Field(min_length=1, max_length=300)
    status: Literal["active", "challenged"]
    thesis: str = Field(min_length=1, max_length=3000)
    confidence: ConfidenceLevel
    valid_until: datetime | None = None


class ResearchMemoryItem(ResearchContract):
    memory_id: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=1600)
    applicability: str = Field(min_length=1, max_length=1000)
    counterexample: str = Field(min_length=1, max_length=1000)
    evidence_references: list[str] = Field(default_factory=list, max_length=12)
    confidence: ConfidenceLevel
    expires_at: datetime | None = None


class ResearchContextPack(ResearchContract):
    trigger: ResearchTriggerEnvelope
    market_state: MarketStateFrame
    current_report_revision_id: str | None = Field(default=None, max_length=180)
    active_views: list[ActiveViewSnapshot] = Field(default_factory=list, max_length=20)
    memory_items: list[ResearchMemoryItem] = Field(default_factory=list, max_length=12)
    research_question: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_shared_cutoff(self) -> "ResearchContextPack":
        if self.market_state.cutoff_at > self.trigger.cutoff_at:
            raise ValueError("Market State Frame is newer than trigger cutoff_at")
        return self


class CompetingHypothesis(ResearchContract):
    hypothesis_id: str = Field(min_length=1, max_length=180)
    statement: str = Field(min_length=1, max_length=1600)
    role: Literal["primary", "alternative", "data_quality"]
    expected_observations: list[str] = Field(min_length=1, max_length=12)
    refuting_observations: list[str] = Field(min_length=1, max_length=12)
    validation_deadline: datetime | None = None
    status: Literal[
        "unverified",
        "partially_supported",
        "supported",
        "challenged",
        "falsified",
        "inconclusive",
    ] = "unverified"


class EvidencePlanItem(ResearchContract):
    plan_item_id: str = Field(min_length=1, max_length=180)
    hypothesis_ids: list[str] = Field(min_length=1, max_length=12)
    question: str = Field(min_length=1, max_length=1200)
    required_evidence: str = Field(min_length=1, max_length=1200)
    layer: Literal["overview", "dimension", "object", "history", "source"]
    status: Literal["completed", "not_needed", "unavailable", "budget_exhausted"]
    opened_references: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("opened_references", mode="before")
    @classmethod
    def keep_representative_opened_references(cls, value):
        """Evidence Plan is an index, not a duplicate of the evidence ledger."""

        if not isinstance(value, list):
            return value
        return list(dict.fromkeys(str(item) for item in value if str(item)))[:20]


class ResearchClaim(ResearchContract):
    claim_id: str = Field(min_length=1, max_length=180)
    claim_type: Literal[
        "observed_fact",
        "source_claim",
        "inference",
        "hypothesis",
        "forecast",
        "unknown",
    ]
    epistemic_status: Literal[
        "unverified",
        "partially_supported",
        "supported",
        "challenged",
        "falsified",
        "inconclusive",
        "expired",
    ]
    statement: str = Field(min_length=1, max_length=2400)
    thesis_effect: Literal["supports", "refutes", "context"] = Field(
        default="context",
        description=(
            "How this established claim affects the investment thesis. This "
            "is separate from EvidenceCitation.support, which describes "
            "whether evidence establishes this exact claim."
        ),
    )
    confidence: ConfidenceLevel
    evidence: list[EvidenceCitation] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def validate_evidence_semantics(self) -> "ResearchClaim":
        if self.claim_type == "observed_fact" and not any(
            item.support == "supports" for item in self.evidence
        ):
            found = [item.support for item in self.evidence]
            raise ValueError(
                "observed_fact requires at least one evidence item with "
                f"support='supports'; found support values: {found}. "
                "Either change a directly supporting citation to "
                "support='supports', or change claim_type when the statement "
                "is not a directly observed fact."
            )
        return self


class Forecast(ResearchContract):
    forecast_id: str = Field(min_length=1, max_length=180)
    subject_id: str = Field(min_length=1, max_length=180)
    metric: str = Field(min_length=1, max_length=300)
    expected_direction: Literal["up", "down", "flat", "range", "non_price"]
    benchmark_subject_id: str | None = Field(default=None, max_length=180)
    baseline_value: float | None = None
    expected_min_value: float | None = None
    expected_max_value: float | None = None
    evaluation_start_at: datetime
    evaluation_end_at: datetime
    invalidation_condition: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def validate_window_and_range(self) -> "Forecast":
        if self.evaluation_end_at <= self.evaluation_start_at:
            raise ValueError("forecast evaluation_end_at must be after evaluation_start_at")
        if (
            self.expected_min_value is not None
            and self.expected_max_value is not None
            and self.expected_min_value > self.expected_max_value
        ):
            raise ValueError("forecast expected_min_value exceeds expected_max_value")
        if self.expected_direction == "range" and (
            self.expected_min_value is None or self.expected_max_value is None
        ):
            raise ValueError(
                "range forecast requires both expected_min_value and "
                "expected_max_value; use up/down/flat when only a directional "
                "forecast is supported"
            )
        return self


class ObservationRequirement(ResearchContract):
    requirement_id: str = Field(min_length=1, max_length=180)
    subject_id: str = Field(min_length=1, max_length=180)
    metric_or_event: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1200)
    due_at: datetime
    source_preference: Literal["database", "realtime", "external", "any"]
    related_view_id: str | None = Field(default=None, max_length=180)
    related_forecast_id: str | None = Field(default=None, max_length=180)


class EvidenceGap(ResearchContract):
    gap_id: str = Field(min_length=1, max_length=180)
    description: str = Field(min_length=1, max_length=1200)
    reason: Literal[
        "permission_denied",
        "upstream_unavailable",
        "source_unreachable",
        "not_yet_available",
        "not_publicly_observable",
        "budget_exhausted",
    ]
    impact: Literal["non_critical", "critical"]
    confidence_impact: str = Field(min_length=1, max_length=800)
    attempted_tools: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_public_observability_reason(cls, value):
        if value in {"not_publicly_available", "not_public_available"}:
            return "not_publicly_observable"
        return value


class ConfidenceAssessment(ResearchContract):
    overall: ConfidenceLevel
    evidence_quality: ConfidenceLevel
    independent_confirmation: ConfidenceLevel
    counterevidence_resilience: ConfidenceLevel
    timing_clarity: ConfidenceLevel
    rationale: str = Field(min_length=1, max_length=1600)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_numeric_levels(cls, value):
        """Read old numeric reports without asking new runs for fake precision."""

        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for field in (
            "evidence_quality",
            "independent_confirmation",
            "counterevidence_resilience",
            "timing_clarity",
        ):
            score = normalized.get(field)
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            if score >= 0.8:
                normalized[field] = "high"
            elif score >= 0.55:
                normalized[field] = "medium"
            else:
                normalized[field] = "low"
        return normalized


class MechanismLink(ResearchContract):
    link_id: str = Field(min_length=1, max_length=180)
    cause: str = Field(min_length=1, max_length=1200)
    mechanism: str = Field(min_length=1, max_length=1600)
    effect: str = Field(min_length=1, max_length=1200)
    status: Literal["observed", "inferred", "hypothesis"]
    evidence: list[EvidenceCitation] = Field(min_length=1, max_length=12)
    invalidation_condition: str = Field(min_length=1, max_length=1000)


class MarketStructureAssessment(ResearchContract):
    breadth: str = Field(min_length=1, max_length=1200)
    leadership_concentration: str = Field(min_length=1, max_length=1200)
    volume_liquidity_confirmation: str = Field(min_length=1, max_length=1200)
    crowding_and_reversal_risk: str = Field(min_length=1, max_length=1200)
    persistence_assessment: str = Field(min_length=1, max_length=1200)
    pricing_state: Literal[
        "not_priced",
        "partially_priced",
        "largely_priced",
        "overpriced",
        "unknown",
    ]
    evidence: list[EvidenceCitation] = Field(min_length=2, max_length=16)


class PortfolioDecisionBoundary(ResearchContract):
    portfolio_relevance: str = Field(min_length=1, max_length=1600)
    candidate_expressions_for_portfolio_review: list[str] = Field(
        min_length=1,
        max_length=12,
    )
    actions_not_supported: list[str] = Field(min_length=1, max_length=12)
    sizing_constraints_for_portfolio_review: list[str] = Field(
        min_length=1,
        max_length=12,
    )
    monitoring_signals: list[str] = Field(min_length=1, max_length=16)


class InvestmentViewRevisionProposal(ResearchContract):
    view_id: str = Field(min_length=1, max_length=180)
    base_revision_id: str | None = Field(default=None, max_length=180)
    proposed_revision_id: str = Field(min_length=1, max_length=180)
    event: Literal[
        "create",
        "strengthen",
        "weaken",
        "challenge",
        "invalidate",
        "extend",
    ]
    status: Literal["active", "challenged", "invalidated", "expired"]
    title: str = Field(min_length=1, max_length=300)
    thesis: str = Field(min_length=1, max_length=4000)
    scope: list[str] = Field(min_length=1, max_length=20)
    hypotheses: list[CompetingHypothesis] = Field(min_length=3, max_length=10)
    evidence_plan: list[EvidencePlanItem] = Field(min_length=1, max_length=30)
    claims: list[ResearchClaim] = Field(min_length=1, max_length=40)
    mechanism_chain: list[MechanismLink] = Field(default_factory=list, max_length=12)
    market_structure: MarketStructureAssessment | None = None
    decision_boundary: PortfolioDecisionBoundary | None = None
    forecasts: list[Forecast] = Field(default_factory=list, max_length=12)
    invalidation_conditions: list[str] = Field(min_length=1, max_length=12)
    confidence: ConfidenceAssessment
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def validate_revision_identity(self) -> "InvestmentViewRevisionProposal":
        hypothesis_ids = [item.hypothesis_id for item in self.hypotheses]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("view revision contains duplicate hypothesis_id")
        plan_ids = [item.plan_item_id for item in self.evidence_plan]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("view revision contains duplicate plan_item_id")
        roles = {item.role for item in self.hypotheses}
        required_roles = {"primary", "alternative", "data_quality"}
        if not required_roles.issubset(roles):
            raise ValueError(
                "view revision requires primary, alternative, and data_quality "
                "hypotheses"
            )
        if self.event == "create" and self.base_revision_id is not None:
            raise ValueError("create revision cannot have base_revision_id")
        if self.event != "create" and self.base_revision_id is None:
            raise ValueError("non-create revision requires base_revision_id")
        return self


class CurrentResearchReportProposal(ResearchContract):
    task_mode: Literal[ResearchTaskMode.RESEARCH_REVIEW] = (
        ResearchTaskMode.RESEARCH_REVIEW
    )
    report_id: Literal["research:current"] = "research:current"
    base_report_revision_id: str | None = Field(default=None, max_length=180)
    proposed_report_revision_id: str | None = Field(default=None, max_length=180)
    run_id: str = Field(min_length=1, max_length=180)
    trigger_id: str = Field(min_length=1, max_length=180)
    trigger_slot: ResearchTriggerSlot
    cutoff_at: datetime
    source_frame_id: str = Field(min_length=1, max_length=180)
    status: ResearchRunStatus
    report_summary: str = Field(min_length=1, max_length=6000)
    research_question: str = Field(min_length=1, max_length=2000)
    data_quality_assessment: str = Field(min_length=1, max_length=2000)
    hypotheses: list[CompetingHypothesis] = Field(default_factory=list, max_length=10)
    evidence_plan: list[EvidencePlanItem] = Field(default_factory=list, max_length=30)
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=40)
    counterevidence_summary: str = Field(min_length=1, max_length=2400)
    memory_application: list[str] = Field(default_factory=list, max_length=12)
    active_views: list[ActiveViewSnapshot] = Field(default_factory=list, max_length=20)
    view_revisions: list[InvestmentViewRevisionProposal] = Field(
        default_factory=list,
        max_length=10,
    )
    observation_requirements: list[ObservationRequirement] = Field(
        default_factory=list,
        max_length=30,
    )
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list, max_length=20)
    no_change_reason: str | None = Field(default=None, max_length=1600)

    @model_validator(mode="before")
    @classmethod
    def project_active_views_from_revisions(cls, value):
        """Derive the final active-view read model from immutable revisions.

        Requiring the model to copy title, thesis, status and confidence into a
        second object creates no new information and caused provider correction
        loops.  The deterministic boundary owns this projection instead.
        """

        if not isinstance(value, dict):
            return value
        projected = {
            str(item.get("view_id")): dict(item)
            for item in (value.get("active_views") or [])
            if isinstance(item, dict) and item.get("view_id")
        }
        for revision in value.get("view_revisions") or []:
            if not isinstance(revision, dict) or not revision.get("view_id"):
                continue
            view_id = str(revision["view_id"])
            if revision.get("status") not in {"active", "challenged"}:
                projected.pop(view_id, None)
                continue
            confidence = revision.get("confidence") or {}
            projected[view_id] = {
                "view_id": view_id,
                "revision_id": revision.get("proposed_revision_id"),
                "title": revision.get("title"),
                "status": revision.get("status"),
                "thesis": revision.get("thesis"),
                "confidence": (
                    confidence.get("overall")
                    if isinstance(confidence, dict)
                    else None
                ),
                "valid_until": revision.get("valid_until"),
            }
        normalized = dict(value)
        normalized["active_views"] = list(projected.values())
        return normalized

    @property
    def publishable(self) -> bool:
        return self.status in {
            ResearchRunStatus.UPDATED,
            ResearchRunStatus.NO_CHANGE,
        }

    @model_validator(mode="after")
    def validate_status_contract(self) -> "CurrentResearchReportProposal":
        hypothesis_ids = [item.hypothesis_id for item in self.hypotheses]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("report contains duplicate hypothesis_id")
        plan_ids = [item.plan_item_id for item in self.evidence_plan]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("report contains duplicate plan_item_id")
        if self.status == ResearchRunStatus.UPDATED:
            hypothesis_roles = {item.role for item in self.hypotheses}
            required_roles = {"primary", "alternative", "data_quality"}
            if not required_roles.issubset(hypothesis_roles):
                raise ValueError(
                    "updated report requires primary, alternative, and "
                    "data_quality hypotheses"
                )
            if not self.evidence_plan:
                raise ValueError("updated report requires an evidence plan")
            if not self.view_revisions:
                raise ValueError("updated report requires at least one view revision")
            if not self.proposed_report_revision_id:
                raise ValueError("updated report requires proposed_report_revision_id")
        if self.status == ResearchRunStatus.NO_CHANGE:
            if self.view_revisions:
                raise ValueError("no_change report cannot contain view revisions")
            if not self.no_change_reason:
                raise ValueError("no_change report requires no_change_reason")
            initializes_report = self.base_report_revision_id is None
            if initializes_report and self.proposed_report_revision_id is None:
                raise ValueError(
                    "initial no_change must create the baseline report revision"
                )
            if not initializes_report and self.proposed_report_revision_id is not None:
                raise ValueError(
                    "subsequent no_change does not create a report revision"
                )
        if self.status in {
            ResearchRunStatus.BLOCKED,
            ResearchRunStatus.INSUFFICIENT_EVIDENCE,
        } and not any(gap.impact == "critical" for gap in self.evidence_gaps):
            raise ValueError(f"{self.status.value} requires a critical evidence gap")
        if self.status == ResearchRunStatus.INCOMPLETE and not any(
            gap.reason == "budget_exhausted" for gap in self.evidence_gaps
        ):
            raise ValueError("incomplete report requires a budget_exhausted gap")
        if not self.publishable:
            if self.view_revisions:
                raise ValueError("non-publishable report cannot contain view revisions")
            if self.proposed_report_revision_id is not None:
                raise ValueError("non-publishable report cannot create a report revision")
        active_by_id = {item.view_id: item for item in self.active_views}
        if len(active_by_id) != len(self.active_views):
            raise ValueError("report contains duplicate active view_id")
        for revision in self.view_revisions:
            snapshot = active_by_id.get(revision.view_id)
            if revision.status in {"active", "challenged"}:
                if snapshot is None:
                    raise ValueError(
                        f"revised active view is missing from report: {revision.view_id}"
                    )
                if snapshot.revision_id != revision.proposed_revision_id:
                    raise ValueError(
                        f"active view does not point to proposed revision: {revision.view_id}"
                    )
                if (
                    snapshot.title != revision.title
                    or snapshot.thesis != revision.thesis
                    or snapshot.status != revision.status
                    or snapshot.confidence != revision.confidence.overall
                ):
                    raise ValueError(
                        f"active view snapshot conflicts with revision: {revision.view_id}"
                    )
            elif snapshot is not None:
                raise ValueError(
                    f"inactive revision remains in active views: {revision.view_id}"
                )
        for revision in self.view_revisions:
            for forecast in revision.forecasts:
                if forecast.evaluation_start_at <= self.cutoff_at:
                    raise ValueError(
                        f"forecast window must start after cutoff_at: {forecast.forecast_id}"
                    )
        for requirement in self.observation_requirements:
            if requirement.due_at <= self.cutoff_at:
                raise ValueError(
                    "Observation Requirement must refer to a future fact: "
                    f"{requirement.requirement_id}"
                )
        return self


class ResearchReportDraft(ResearchContract):
    """Model-facing research content without server-owned run metadata.

    A non-updating conclusion may be concise.  Only an actual investment-view
    revision has to carry the full competing-hypothesis and evidence plan.
    """

    status: ResearchRunStatus
    report_summary: str = Field(min_length=1, max_length=6000)
    research_question: str = Field(min_length=1, max_length=2000)
    data_quality_assessment: str = Field(min_length=1, max_length=2000)
    hypotheses: list[CompetingHypothesis] = Field(default_factory=list, max_length=10)
    evidence_plan: list[EvidencePlanItem] = Field(default_factory=list, max_length=30)
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=40)
    counterevidence_summary: str = Field(min_length=1, max_length=2400)
    memory_application: list[str] = Field(default_factory=list, max_length=12)
    view_revisions: list[InvestmentViewRevisionProposal] = Field(
        default_factory=list,
        max_length=10,
    )
    observation_requirements: list[ObservationRequirement] = Field(
        default_factory=list,
        max_length=30,
    )
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list, max_length=20)
    no_change_reason: str | None = Field(default=None, max_length=1600)

    @model_validator(mode="after")
    def validate_draft_shape(self) -> "ResearchReportDraft":
        if self.status == ResearchRunStatus.UPDATED:
            roles = {item.role for item in self.hypotheses}
            if not {"primary", "alternative", "data_quality"}.issubset(roles):
                raise ValueError(
                    "updated draft requires primary, alternative, and "
                    "data_quality hypotheses"
                )
            if not self.evidence_plan:
                raise ValueError("updated draft requires an evidence plan")
            if not self.view_revisions:
                raise ValueError("updated draft requires at least one view revision")
        elif self.view_revisions:
            raise ValueError("only updated drafts may contain view revisions")
        if self.status == ResearchRunStatus.NO_CHANGE and not self.no_change_reason:
            raise ValueError("no_change draft requires no_change_reason")
        if self.status in {
            ResearchRunStatus.BLOCKED,
            ResearchRunStatus.INSUFFICIENT_EVIDENCE,
        } and not any(item.impact == "critical" for item in self.evidence_gaps):
            raise ValueError(f"{self.status.value} requires a critical evidence gap")
        if self.status == ResearchRunStatus.INCOMPLETE and not any(
            item.reason == "budget_exhausted" for item in self.evidence_gaps
        ):
            raise ValueError("incomplete draft requires a budget_exhausted gap")
        if self.status != ResearchRunStatus.INCOMPLETE and any(
            item.reason == "budget_exhausted" for item in self.evidence_gaps
        ):
            raise ValueError(
                "budget_exhausted is a run status, not a research caveat; "
                "use incomplete when the actual tool/time budget is exhausted"
            )
        return self


class ResearchConclusionDraft(ResearchContract):
    """Small terminal contract when the current investment view is unchanged."""

    status: Literal[
        ResearchRunStatus.NO_CHANGE,
        ResearchRunStatus.BLOCKED,
        ResearchRunStatus.INSUFFICIENT_EVIDENCE,
        ResearchRunStatus.INCOMPLETE,
    ]
    report_summary: str = Field(min_length=1, max_length=6000)
    research_question: str = Field(min_length=1, max_length=2000)
    data_quality_assessment: str = Field(min_length=1, max_length=2000)
    counterevidence_summary: str = Field(min_length=1, max_length=2400)
    hypotheses: list[CompetingHypothesis] = Field(default_factory=list, max_length=10)
    evidence_plan: list[EvidencePlanItem] = Field(default_factory=list, max_length=30)
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=40)
    memory_application: list[str] = Field(default_factory=list, max_length=12)
    observation_requirements: list[ObservationRequirement] = Field(
        default_factory=list,
        max_length=30,
    )
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list, max_length=20)
    no_change_reason: str | None = Field(default=None, max_length=1600)

    @model_validator(mode="before")
    @classmethod
    def bind_program_owned_citation_fields(cls, value):
        return _bind_model_citations(value)

    @model_validator(mode="after")
    def validate_conclusion(self) -> "ResearchConclusionDraft":
        ResearchReportDraft.model_validate(self.model_dump(mode="python"))
        return self

    def to_research_report_draft(self) -> ResearchReportDraft:
        return ResearchReportDraft.model_validate(self.model_dump(mode="python"))


class InvestmentViewResearchDraft(ResearchContract):
    """Full terminal contract used only when an investment view is revised."""

    status: Literal[ResearchRunStatus.UPDATED] = ResearchRunStatus.UPDATED
    report_summary: str = Field(min_length=1, max_length=6000)
    research_question: str = Field(min_length=1, max_length=2000)
    data_quality_assessment: str = Field(min_length=1, max_length=2000)
    hypotheses: list[CompetingHypothesis] = Field(default_factory=list, max_length=10)
    evidence_plan: list[EvidencePlanItem] = Field(default_factory=list, max_length=30)
    counterevidence_summary: str = Field(min_length=1, max_length=2400)
    memory_application: list[str] = Field(default_factory=list, max_length=12)
    view_revisions: list[InvestmentViewRevisionProposal] = Field(
        min_length=1,
        max_length=10,
    )
    observation_requirements: list[ObservationRequirement] = Field(
        default_factory=list,
        max_length=30,
    )
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def project_report_research_from_revision(cls, value):
        """Keep one canonical hypothesis/plan copy for a single revised view."""

        if not isinstance(value, dict):
            return value
        normalized = _bind_model_citations(value)
        if not isinstance(normalized, dict):
            return normalized
        revisions = [
            dict(item) if isinstance(item, dict) else item
            for item in (normalized.get("view_revisions") or [])
        ]
        first = revisions[0] if revisions and isinstance(revisions[0], dict) else {}
        hypotheses = normalized.get("hypotheses") or first.get("hypotheses") or []
        evidence_plan = normalized.get("evidence_plan") or first.get("evidence_plan") or []
        normalized["hypotheses"] = hypotheses
        normalized["evidence_plan"] = evidence_plan
        # GLM occasionally emits the same research structure twice and loses a
        # role or a plan in the nested copy.  The report represents one revised
        # view, so the service owns this deterministic projection.
        for revision in revisions:
            if not isinstance(revision, dict):
                continue
            revision["hypotheses"] = hypotheses
            revision["evidence_plan"] = evidence_plan
        normalized["view_revisions"] = revisions
        return normalized

    @model_validator(mode="after")
    def validate_revision_draft(self) -> "InvestmentViewResearchDraft":
        ResearchReportDraft.model_validate(self.model_dump(mode="python"))
        return self

    def to_research_report_draft(self) -> ResearchReportDraft:
        return ResearchReportDraft.model_validate(self.model_dump(mode="python"))


def _bind_model_citations(value):
    """Expand model-facing {reference, support} into the business contract."""

    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    counter = 0

    def visit(item, parent_claim: str = ""):
        nonlocal counter
        if isinstance(item, list):
            return [visit(child, parent_claim) for child in item]
        if not isinstance(item, dict):
            return item
        current = dict(item)
        claim_text = str(
            current.get("statement")
            or current.get("effect")
            or current.get("thesis")
            or parent_claim
            or "该结构化判断"
        )
        if "reference" in current and "support" in current:
            counter += 1
            reference = str(current.get("reference") or "")
            current["citation_id"] = f"citation-{counter:03d}"
            current["kind"] = _citation_kind(reference)
            current.pop("observed_at", None)
            current.pop("as_of", None)
        for key, child in list(current.items()):
            current[key] = visit(child, claim_text)
        return current

    return visit(normalized)


def _citation_kind(reference: str) -> str:
    if reference.startswith(("market_ref:", "market:v1:")):
        return "market"
    if reference.startswith("kg_card_relation:"):
        return "edge"
    if reference.startswith("kgc:"):
        return "community"
    if reference.startswith("kg_cognitive_card:"):
        return "card"
    return "external"


class CurrentResearchReport(ResearchContract):
    """The single current read model plus its immutable published content."""

    report_id: Literal["research:current"] = "research:current"
    current_revision_id: str = Field(min_length=1, max_length=180)
    version: int = Field(ge=1)
    current_cutoff_at: datetime
    last_checked_at: datetime
    last_check_status: ResearchRunStatus
    last_no_change_reason: str | None = None
    content: CurrentResearchReportProposal

    @model_validator(mode="after")
    def validate_current_pointer(self) -> "CurrentResearchReport":
        if self.content.proposed_report_revision_id != self.current_revision_id:
            raise ValueError("Current Research Report content does not match pointer")
        if self.content.report_id != self.report_id:
            raise ValueError("Current Research Report identity does not match content")
        return self


class OutcomeObservation(ResearchContract):
    observation_id: str = Field(min_length=1, max_length=180)
    forecast_id: str = Field(min_length=1, max_length=180)
    observed_at: datetime
    actual_value: float | None = None
    benchmark_value: float | None = None
    invalidation_condition_hit: bool = False
    evidence: list[EvidenceCitation] = Field(min_length=1, max_length=12)


class OutcomeEvaluation(ResearchContract):
    evaluation_id: str = Field(min_length=1, max_length=180)
    forecast_id: str = Field(min_length=1, max_length=180)
    observation_id: str = Field(min_length=1, max_length=180)
    status: Literal[
        "confirmed",
        "partially_confirmed",
        "not_confirmed",
        "invalidated",
        "inconclusive",
    ]
    direction_correct: bool | None = None
    range_correct: bool | None = None
    benchmark_outperformance: bool | None = None
    invalidation_condition_hit: bool
    fact_assessment: Literal["correct", "incorrect", "unknown"]
    mechanism_assessment: Literal["correct", "incorrect", "unknown"]
    timing_assessment: Literal["correct", "early", "late", "wrong_window", "unknown"]
    expression_assessment: Literal["correct", "incorrect", "unknown"]
    pricing_assessment: Literal[
        "not_priced",
        "partially_priced",
        "already_priced",
        "unknown",
    ]
    summary: str = Field(min_length=1, max_length=2400)
    evaluated_at: datetime
