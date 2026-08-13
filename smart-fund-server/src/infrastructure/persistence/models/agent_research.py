"""ORM models for authoritative Research Agent state and immutable history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


class AgentResearchRun(Base):
    __tablename__ = "agent_research_runs"
    __table_args__ = (
        Index("ix_agent_research_runs_cutoff", "cutoff_at"),
        Index("ix_agent_research_runs_status", "status", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    trigger_id: Mapped[str] = mapped_column(String(180), nullable=False)
    trigger_slot: Mapped[str] = mapped_column(String(32), nullable=False)
    source_frame_id: Mapped[str] = mapped_column(String(180), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    publishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    proposal_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentCurrentResearchReport(Base):
    __tablename__ = "agent_current_research_reports"

    report_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    current_revision_id: Mapped[str | None] = mapped_column(String(180))
    current_cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_check_status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_no_change_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentResearchReportRevision(Base):
    __tablename__ = "agent_research_report_revisions"
    __table_args__ = (
        Index("ix_agent_research_report_revisions_report", "report_id", "cutoff_at"),
        Index("ix_agent_research_report_revisions_run", "run_id", unique=True),
    )

    revision_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(180), nullable=False)
    base_revision_id: Mapped[str | None] = mapped_column(String(180))
    run_id: Mapped[str] = mapped_column(String(180), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_frame_id: Mapped[str] = mapped_column(String(180), nullable=False)
    report_summary: Mapped[str] = mapped_column(Text, nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentInvestmentView(Base):
    __tablename__ = "agent_investment_views"
    __table_args__ = (
        Index("ix_agent_investment_views_status", "status", "updated_at"),
    )

    view_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    current_revision_id: Mapped[str] = mapped_column(String(180), nullable=False)
    current_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentInvestmentViewRevision(Base):
    __tablename__ = "agent_investment_view_revisions"
    __table_args__ = (
        Index("ix_agent_view_revisions_view", "view_id", "cutoff_at"),
        Index("ix_agent_view_revisions_run", "run_id"),
    )

    revision_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    view_id: Mapped[str] = mapped_column(String(180), nullable=False)
    base_revision_id: Mapped[str | None] = mapped_column(String(180))
    run_id: Mapped[str] = mapped_column(String(180), nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    thesis: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[list] = mapped_column(JSONB, nullable=False)
    hypotheses: Mapped[list] = mapped_column(JSONB, nullable=False)
    evidence_plan: Mapped[list] = mapped_column(JSONB, nullable=False)
    mechanism_chain: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    market_structure: Mapped[dict | None] = mapped_column(JSONB)
    decision_boundary: Mapped[dict | None] = mapped_column(JSONB)
    invalidation_conditions: Mapped[list] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentResearchClaim(Base):
    __tablename__ = "agent_research_claims"
    __table_args__ = (
        Index("ix_agent_research_claims_revision", "revision_id"),
        Index("ix_agent_research_claims_type_status", "claim_type", "epistemic_status"),
    )

    claim_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(180), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    epistemic_status: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    thesis_effect: Mapped[str] = mapped_column(String(32), nullable=False, default="context")
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentResearchForecast(Base):
    __tablename__ = "agent_research_forecasts"
    __table_args__ = (
        Index("ix_agent_research_forecasts_revision", "revision_id"),
        Index("ix_agent_research_forecasts_due", "evaluation_end_at"),
    )

    forecast_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(180), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(180), nullable=False)
    metric: Mapped[str] = mapped_column(String(300), nullable=False)
    expected_direction: Mapped[str] = mapped_column(String(32), nullable=False)
    benchmark_subject_id: Mapped[str | None] = mapped_column(String(180))
    baseline_value: Mapped[float | None] = mapped_column(Float)
    expected_min_value: Mapped[float | None] = mapped_column(Float)
    expected_max_value: Mapped[float | None] = mapped_column(Float)
    evaluation_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluation_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    invalidation_condition: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentResearchObservationRequirement(Base):
    __tablename__ = "agent_research_observation_requirements"
    __table_args__ = (
        Index("ix_agent_observation_requirements_due", "status", "due_at"),
        Index("ix_agent_observation_requirements_view", "related_view_id"),
    )

    requirement_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(180), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(180), nullable=False)
    metric_or_event: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_preference: Mapped[str] = mapped_column(String(32), nullable=False)
    related_view_id: Mapped[str | None] = mapped_column(String(180))
    related_forecast_id: Mapped[str | None] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentResearchOutcomeObservation(Base):
    __tablename__ = "agent_research_outcome_observations"
    __table_args__ = (
        Index("ix_agent_outcome_observations_forecast", "forecast_id", "observed_at"),
    )

    observation_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(String(180), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_value: Mapped[float | None] = mapped_column(Float)
    benchmark_value: Mapped[float | None] = mapped_column(Float)
    invalidation_condition_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentResearchOutcomeEvaluation(Base):
    __tablename__ = "agent_research_outcome_evaluations"
    __table_args__ = (
        Index("ix_agent_outcome_evaluations_forecast", "forecast_id", "evaluated_at"),
        Index("ix_agent_outcome_evaluations_observation", "observation_id", unique=True),
    )

    evaluation_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    forecast_id: Mapped[str] = mapped_column(String(180), nullable=False)
    observation_id: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    direction_correct: Mapped[bool | None] = mapped_column(Boolean)
    range_correct: Mapped[bool | None] = mapped_column(Boolean)
    benchmark_outperformance: Mapped[bool | None] = mapped_column(Boolean)
    invalidation_condition_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fact_assessment: Mapped[str] = mapped_column(String(32), nullable=False)
    mechanism_assessment: Mapped[str] = mapped_column(String(32), nullable=False)
    timing_assessment: Mapped[str] = mapped_column(String(32), nullable=False)
    expression_assessment: Mapped[str] = mapped_column(String(32), nullable=False)
    pricing_assessment: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentResearchQualityEvaluation(Base):
    __tablename__ = "agent_research_quality_evaluations"
    __table_args__ = (
        Index("ix_agent_research_quality_run", "run_id", unique=True),
        Index("ix_agent_research_quality_score", "passed", "overall_score"),
    )

    evaluation_id: Mapped[str] = mapped_column(String(220), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(180), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    grade: Mapped[str] = mapped_column(String(32), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    hard_failures: Mapped[list] = mapped_column(JSONB, nullable=False)
    advisory_findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    semantic_evaluation: Mapped[dict | None] = mapped_column(JSONB)
    semantic_evaluator_version: Mapped[str | None] = mapped_column(String(64))
    semantic_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    improvement_actions: Mapped[list] = mapped_column(JSONB, nullable=False)
    tool_coverage: Mapped[list] = mapped_column(JSONB, nullable=False)
    evidence_reference_count: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome_adjusted_score: Mapped[float | None] = mapped_column(Float)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentRoleMemoryItem(Base):
    __tablename__ = "agent_role_memory_items"
    __table_args__ = (
        Index(
            "ix_agent_role_memory_lookup",
            "role",
            "status",
            "valid_from",
            "expires_at",
        ),
    )

    memory_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    applicability: Mapped[str] = mapped_column(Text, nullable=False)
    counterexample: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_references: Mapped[list] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentRoleMemoryCase(Base):
    __tablename__ = "agent_role_memory_cases"
    __table_args__ = (
        Index("ix_agent_role_memory_cases_memory", "memory_id", "created_at"),
    )

    case_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    memory_id: Mapped[str] = mapped_column(String(180), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    outcome_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentRuntimeRun(Base):
    __tablename__ = "agent_runtime_runs"
    __table_args__ = (
        Index("ix_agent_runtime_runs_role_status", "role", "status"),
        Index("ix_agent_runtime_runs_cutoff", "cutoff_at"),
    )

    run_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    run_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    authorized_tools: Mapped[list] = mapped_column(JSONB, nullable=False)
    account_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    budget: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentToolInvocation(Base):
    __tablename__ = "agent_tool_invocations"
    __table_args__ = (
        Index("ix_agent_tool_invocations_run", "run_id", "called_at"),
        Index("ix_agent_tool_invocations_tool", "tool_name", "called_at"),
    )

    invocation_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(180), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    response_digest: Mapped[str | None] = mapped_column(String(64))
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(180))
    called_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
