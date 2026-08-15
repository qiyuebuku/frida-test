"""Cutoff-aware read models for Research state, outcomes, and role memory."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import case, or_, select

from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.agent_research import (
    AgentInvestmentViewRevision,
    AgentResearchClaim,
    AgentResearchForecast,
    AgentResearchOutcomeEvaluation,
    AgentResearchOutcomeObservation,
    AgentResearchQualityEvaluation,
    AgentResearchReportRevision,
    AgentRoleMemoryCase,
    AgentRoleMemoryItem,
)


class AgentResearchReadRepository:
    def __init__(self, *, target: str | None = None) -> None:
        self._target = target

    def current_report_at(self, *, cutoff_at: datetime) -> dict[str, Any] | None:
        with get_session(self._target) as session:
            row = session.scalar(
                select(AgentResearchReportRevision)
                .where(AgentResearchReportRevision.cutoff_at <= cutoff_at)
                .order_by(
                    AgentResearchReportRevision.cutoff_at.desc(),
                    AgentResearchReportRevision.created_at.desc(),
                )
                .limit(1)
            )
            if row is None:
                return None
            return {
                "report_id": row.report_id,
                "revision_id": row.revision_id,
                "base_revision_id": row.base_revision_id,
                "run_id": row.run_id,
                "cutoff_at": row.cutoff_at.isoformat(),
                "source_frame_id": row.source_frame_id,
                "research_question": row.research_question,
                "report_summary": row.report_summary,
                "content": row.payload,
            }

    def list_views_at(
        self,
        *,
        cutoff_at: datetime,
        statuses: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 100))
        with get_session(self._target) as session:
            rows = list(
                session.scalars(
                    select(AgentInvestmentViewRevision)
                    .where(
                        AgentInvestmentViewRevision.cutoff_at <= cutoff_at,
                        AgentInvestmentViewRevision.status.in_(statuses),
                        or_(
                            AgentInvestmentViewRevision.valid_until.is_(None),
                            AgentInvestmentViewRevision.valid_until > cutoff_at,
                        ),
                    )
                    .distinct(AgentInvestmentViewRevision.view_id)
                    .order_by(
                        AgentInvestmentViewRevision.view_id,
                        AgentInvestmentViewRevision.cutoff_at.desc(),
                        AgentInvestmentViewRevision.created_at.desc(),
                    )
                    .limit(normalized_limit)
                ).all()
            )
        rows.sort(key=lambda item: item.cutoff_at, reverse=True)
        return [_view_summary(row) for row in rows]

    def open_view_at(
        self,
        *,
        view_id: str,
        cutoff_at: datetime,
        revision_id: str | None = None,
    ) -> dict[str, Any] | None:
        filters = [
            AgentInvestmentViewRevision.view_id == view_id,
            AgentInvestmentViewRevision.cutoff_at <= cutoff_at,
        ]
        if revision_id:
            filters.append(
                AgentInvestmentViewRevision.revision_id == revision_id
            )
        with get_session(self._target) as session:
            row = session.scalar(
                select(AgentInvestmentViewRevision)
                .where(*filters)
                .order_by(AgentInvestmentViewRevision.cutoff_at.desc())
                .limit(1)
            )
            if row is None:
                return None
            claims = list(
                session.scalars(
                    select(AgentResearchClaim)
                    .where(AgentResearchClaim.revision_id == row.revision_id)
                    .order_by(AgentResearchClaim.claim_id)
                ).all()
            )
            forecasts = list(
                session.scalars(
                    select(AgentResearchForecast)
                    .where(AgentResearchForecast.revision_id == row.revision_id)
                    .order_by(AgentResearchForecast.forecast_id)
                ).all()
            )
        return {
            **_view_summary(row),
            "base_revision_id": row.base_revision_id,
            "run_id": row.run_id,
            "event": row.event,
            "scope": row.scope,
            "hypotheses": row.hypotheses,
            "evidence_plan": row.evidence_plan,
            "mechanism_chain": row.mechanism_chain,
            "market_structure": row.market_structure,
            "decision_boundary": row.decision_boundary,
            "invalidation_conditions": row.invalidation_conditions,
            "claims": [
                {
                    "claim_id": item.claim_id,
                    "claim_type": item.claim_type,
                    "epistemic_status": item.epistemic_status,
                    "statement": item.statement,
                    "thesis_effect": item.thesis_effect,
                    "confidence": item.confidence,
                    "evidence_refs": item.evidence_refs,
                }
                for item in claims
            ],
            "forecasts": [_forecast(item) for item in forecasts],
        }

    def search_outcomes(
        self,
        *,
        cutoff_at: datetime,
        subject_id: str = "",
        status: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        filters = [AgentResearchOutcomeEvaluation.evaluated_at <= cutoff_at]
        if status:
            filters.append(AgentResearchOutcomeEvaluation.status == status)
        if subject_id:
            filters.append(AgentResearchForecast.subject_id == subject_id)
        normalized_limit = max(1, min(int(limit), 100))
        with get_session(self._target) as session:
            rows = session.execute(
                select(
                    AgentResearchOutcomeEvaluation,
                    AgentResearchOutcomeObservation,
                    AgentResearchForecast,
                )
                .join(
                    AgentResearchOutcomeObservation,
                    AgentResearchOutcomeObservation.observation_id
                    == AgentResearchOutcomeEvaluation.observation_id,
                )
                .join(
                    AgentResearchForecast,
                    AgentResearchForecast.forecast_id
                    == AgentResearchOutcomeEvaluation.forecast_id,
                )
                .where(*filters)
                .order_by(AgentResearchOutcomeEvaluation.evaluated_at.desc())
                .limit(normalized_limit)
            ).all()
        return [
            _outcome_summary(evaluation, observation, forecast)
            for evaluation, observation, forecast in rows
        ]

    def list_quality_evaluations(
        self,
        *,
        cutoff_at: datetime,
        passed: bool | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        filters = [AgentResearchQualityEvaluation.evaluated_at <= cutoff_at]
        if passed is not None:
            filters.append(AgentResearchQualityEvaluation.passed.is_(passed))
        normalized_limit = max(1, min(int(limit), 100))
        with get_session(self._target) as session:
            rows = list(
                session.scalars(
                    select(AgentResearchQualityEvaluation)
                    .where(*filters)
                    .order_by(AgentResearchQualityEvaluation.evaluated_at.desc())
                    .limit(normalized_limit)
                ).all()
            )
        return [_quality(row, include_details=False) for row in rows]

    def open_latest_quality_evaluation_for_run_at(
        self,
        *,
        run_id: str,
        cutoff_at: datetime,
    ) -> dict[str, Any] | None:
        with get_session(self._target) as session:
            row = session.scalar(
                select(AgentResearchQualityEvaluation)
                .where(
                    AgentResearchQualityEvaluation.run_id == run_id,
                    AgentResearchQualityEvaluation.evaluated_at <= cutoff_at,
                )
                .order_by(AgentResearchQualityEvaluation.evaluated_at.desc())
                .limit(1)
            )
        return _quality(row, include_details=True) if row is not None else None

    def open_outcome_at(
        self,
        *,
        evaluation_id: str,
        cutoff_at: datetime,
    ) -> dict[str, Any] | None:
        with get_session(self._target) as session:
            row = session.execute(
                select(
                    AgentResearchOutcomeEvaluation,
                    AgentResearchOutcomeObservation,
                    AgentResearchForecast,
                )
                .join(
                    AgentResearchOutcomeObservation,
                    AgentResearchOutcomeObservation.observation_id
                    == AgentResearchOutcomeEvaluation.observation_id,
                )
                .join(
                    AgentResearchForecast,
                    AgentResearchForecast.forecast_id
                    == AgentResearchOutcomeEvaluation.forecast_id,
                )
                .where(
                    AgentResearchOutcomeEvaluation.evaluation_id
                    == evaluation_id,
                    AgentResearchOutcomeEvaluation.evaluated_at <= cutoff_at,
                )
            ).one_or_none()
        if row is None:
            return None
        evaluation, observation, forecast = row
        return {
            **_outcome_summary(evaluation, observation, forecast),
            "observation": {
                "observed_at": observation.observed_at.isoformat(),
                "actual_value": observation.actual_value,
                "benchmark_value": observation.benchmark_value,
                "invalidation_condition_hit": (
                    observation.invalidation_condition_hit
                ),
                "evidence_refs": observation.evidence_refs,
            },
            "evaluation": {
                "direction_correct": evaluation.direction_correct,
                "range_correct": evaluation.range_correct,
                "benchmark_outperformance": (
                    evaluation.benchmark_outperformance
                ),
                "fact_assessment": evaluation.fact_assessment,
                "mechanism_assessment": evaluation.mechanism_assessment,
                "timing_assessment": evaluation.timing_assessment,
                "expression_assessment": evaluation.expression_assessment,
                "pricing_assessment": evaluation.pricing_assessment,
                "summary": evaluation.summary,
            },
            "forecast": _forecast(forecast),
        }

    def search_memories(
        self,
        *,
        role: str,
        cutoff_at: datetime,
        query: str = "",
        subject_id: str = "",
        market_regime: str = "",
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        filters = [
            AgentRoleMemoryItem.role == role,
            AgentRoleMemoryItem.status == "promoted",
            AgentRoleMemoryItem.valid_from <= cutoff_at,
            or_(
                AgentRoleMemoryItem.expires_at.is_(None),
                AgentRoleMemoryItem.expires_at > cutoff_at,
            ),
        ]
        if subject_id:
            parts = subject_id.split(":")
            subject_family = ":".join(parts[:2]) if len(parts) >= 2 else subject_id
            filters.append(
                or_(
                    AgentRoleMemoryItem.scope["memory_type"].astext
                    == "process_quality",
                    AgentRoleMemoryItem.scope["subject_ids"].contains([subject_id]),
                    AgentRoleMemoryItem.scope["subject_families"].contains(
                        [subject_family]
                    ),
                )
            )
        if market_regime:
            filters.append(
                or_(
                    AgentRoleMemoryItem.scope["memory_type"].astext
                    == "process_quality",
                    AgentRoleMemoryItem.scope["market_regimes"].contains(
                        [market_regime]
                    ),
                )
            )
        normalized_limit = max(1, min(int(limit), 30))
        retrieval_limit = min(max(normalized_limit * 3, 30), 100)
        with get_session(self._target) as session:
            rows = list(
                session.scalars(
                    select(AgentRoleMemoryItem)
                    .where(*filters)
                    .order_by(
                        case(
                            (AgentRoleMemoryItem.confidence == "high", 0),
                            (AgentRoleMemoryItem.confidence == "medium", 1),
                            else_=2,
                        ),
                        AgentRoleMemoryItem.updated_at.desc(),
                    )
                    .limit(retrieval_limit)
                ).all()
            )
        if query.strip():
            rows.sort(
                key=lambda row: _memory_query_score(query, row),
                reverse=True,
            )
        return [_memory_summary(row) for row in rows[:normalized_limit]]

    def open_memory_at(
        self,
        *,
        role: str,
        memory_id: str,
        cutoff_at: datetime,
    ) -> dict[str, Any] | None:
        with get_session(self._target) as session:
            row = session.scalar(
                select(AgentRoleMemoryItem).where(
                    AgentRoleMemoryItem.memory_id == memory_id,
                    AgentRoleMemoryItem.role == role,
                    AgentRoleMemoryItem.status == "promoted",
                    AgentRoleMemoryItem.valid_from <= cutoff_at,
                    or_(
                        AgentRoleMemoryItem.expires_at.is_(None),
                        AgentRoleMemoryItem.expires_at > cutoff_at,
                    ),
                )
            )
        if row is None:
            return None
        return {
            **_memory_summary(row),
            "evidence_references": row.evidence_references,
            "scope": row.scope,
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }

    def open_memory_cases(
        self,
        *,
        role: str,
        memory_id: str,
        cutoff_at: datetime,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 50))
        with get_session(self._target) as session:
            rows = list(
                session.scalars(
                    select(AgentRoleMemoryCase)
                    .where(
                        AgentRoleMemoryCase.role == role,
                        AgentRoleMemoryCase.memory_id == memory_id,
                        AgentRoleMemoryCase.created_at <= cutoff_at,
                    )
                    .order_by(AgentRoleMemoryCase.created_at.desc())
                    .limit(normalized_limit)
                ).all()
            )
        return [
            {
                "case_id": row.case_id,
                "memory_id": row.memory_id,
                "decision_ref": row.decision_ref,
                "outcome_refs": row.outcome_refs,
                "context": row.context,
                "result": row.result,
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ]


def _view_summary(row: AgentInvestmentViewRevision) -> dict[str, Any]:
    return {
        "view_id": row.view_id,
        "revision_id": row.revision_id,
        "title": row.title,
        "status": row.status,
        "cutoff_at": row.cutoff_at.isoformat(),
        "thesis": row.thesis,
        "confidence": row.confidence,
        "valid_until": _iso(row.valid_until),
    }


def _forecast(row: AgentResearchForecast) -> dict[str, Any]:
    return {
        "forecast_id": row.forecast_id,
        "subject_id": row.subject_id,
        "metric": row.metric,
        "expected_direction": row.expected_direction,
        "benchmark_subject_id": row.benchmark_subject_id,
        "baseline_value": row.baseline_value,
        "expected_min_value": row.expected_min_value,
        "expected_max_value": row.expected_max_value,
        "evaluation_start_at": row.evaluation_start_at.isoformat(),
        "evaluation_end_at": row.evaluation_end_at.isoformat(),
        "invalidation_condition": row.invalidation_condition,
        "status": row.status,
    }


def _outcome_summary(evaluation, observation, forecast) -> dict[str, Any]:
    return {
        "evaluation_id": evaluation.evaluation_id,
        "forecast_id": evaluation.forecast_id,
        "observation_id": evaluation.observation_id,
        "subject_id": forecast.subject_id,
        "metric": forecast.metric,
        "status": evaluation.status,
        "evaluated_at": evaluation.evaluated_at.isoformat(),
        "observed_at": observation.observed_at.isoformat(),
        "summary": evaluation.summary,
    }


def _memory_summary(row: AgentRoleMemoryItem) -> dict[str, Any]:
    return {
        "memory_id": row.memory_id,
        "role": row.role,
        "summary": row.summary,
        "applicability": row.applicability,
        "counterexample": row.counterexample,
        "confidence": row.confidence,
        "valid_from": row.valid_from.isoformat(),
        "expires_at": _iso(row.expires_at),
        "version": row.version,
    }


def _memory_query_score(query: str, row: AgentRoleMemoryItem) -> float:
    query_terms = _text_terms(query)
    memory_terms = _text_terms(" ".join((
        row.summary,
        row.applicability,
        row.counterexample,
    )))
    if not query_terms or not memory_terms:
        return 0.0
    overlap = len(query_terms & memory_terms)
    return overlap / max(len(query_terms), 1)


def _text_terms(value: str) -> set[str]:
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value.lower())
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[index:index + 2] for index in range(len(normalized) - 1)}


def _quality(row: AgentResearchQualityEvaluation, *, include_details: bool) -> dict[str, Any]:
    result = {
        "evaluation_id": row.evaluation_id,
        "run_id": row.run_id,
        "evaluator_version": row.evaluator_version,
        "overall_score": row.overall_score,
        "outcome_adjusted_score": row.outcome_adjusted_score,
        "grade": row.grade,
        "passed": row.passed,
        "hard_failures": row.hard_failures,
        "advisory_findings": row.advisory_findings,
        "semantic_evaluator_version": row.semantic_evaluator_version,
        "semantic_evaluated_at": _iso(row.semantic_evaluated_at),
        "evaluated_at": row.evaluated_at.isoformat(),
    }
    if include_details:
        result.update(
            {
                "scores": row.scores,
                "semantic_evaluation": row.semantic_evaluation,
                "improvement_actions": row.improvement_actions,
                "tool_coverage": row.tool_coverage,
                "evidence_reference_count": row.evidence_reference_count,
            }
        )
    return result


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
