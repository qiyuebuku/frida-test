"""Deterministic evaluation of pre-declared Research Agent forecasts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.application.agents.financial_research.schemas import (
    Forecast,
    OutcomeEvaluation,
    OutcomeObservation,
)


@dataclass(frozen=True, slots=True)
class OutcomeAssessments:
    """Semantic assessments supplied only after opening outcome evidence.

    Price/range checks are deterministic.  Fact, mechanism, timing, expression,
    and pricing cannot be honestly inferred from a number, so callers must pass
    them explicitly or retain ``unknown``.
    """

    fact: str = "unknown"
    mechanism: str = "unknown"
    timing: str = "unknown"
    expression: str = "unknown"
    pricing: str = "unknown"
    summary: str = "仅完成预先声明指标的确定性验证；语义维度仍待证据评估。"


class ResearchOutcomeEvaluator:
    def evaluate(
        self,
        *,
        forecast: Forecast,
        observation: OutcomeObservation,
        evaluation_id: str,
        assessments: OutcomeAssessments | None = None,
        evaluated_at: datetime | None = None,
    ) -> OutcomeEvaluation:
        if observation.forecast_id != forecast.forecast_id:
            raise ValueError("Outcome observation does not belong to forecast")
        if observation.observed_at < forecast.evaluation_start_at:
            raise ValueError("Outcome was observed before the forecast window")
        if observation.observed_at > forecast.evaluation_end_at:
            raise ValueError("Outcome was observed after the forecast window")

        semantic = assessments or OutcomeAssessments()
        direction_correct = self._direction_correct(forecast, observation)
        range_correct = self._range_correct(forecast, observation)
        benchmark_outperformance = self._benchmark_outperformance(
            forecast,
            observation,
        )
        status = self._status(
            forecast=forecast,
            observation=observation,
            direction_correct=direction_correct,
            range_correct=range_correct,
        )

        return OutcomeEvaluation(
            evaluation_id=evaluation_id,
            forecast_id=forecast.forecast_id,
            observation_id=observation.observation_id,
            status=status,
            direction_correct=direction_correct,
            range_correct=range_correct,
            benchmark_outperformance=benchmark_outperformance,
            invalidation_condition_hit=observation.invalidation_condition_hit,
            fact_assessment=semantic.fact,
            mechanism_assessment=semantic.mechanism,
            timing_assessment=semantic.timing,
            expression_assessment=semantic.expression,
            pricing_assessment=semantic.pricing,
            summary=semantic.summary,
            evaluated_at=evaluated_at or datetime.now(UTC),
        )

    @staticmethod
    def _direction_correct(
        forecast: Forecast,
        observation: OutcomeObservation,
    ) -> bool | None:
        if (
            forecast.expected_direction in {"flat", "range", "non_price"}
            or forecast.baseline_value is None
            or observation.actual_value is None
        ):
            return None
        delta = observation.actual_value - forecast.baseline_value
        if forecast.expected_direction == "up":
            return delta > 0
        if forecast.expected_direction == "down":
            return delta < 0
        return None

    @staticmethod
    def _range_correct(
        forecast: Forecast,
        observation: OutcomeObservation,
    ) -> bool | None:
        if observation.actual_value is None:
            return None
        if (
            forecast.expected_min_value is None
            and forecast.expected_max_value is None
        ):
            return None
        above_min = (
            forecast.expected_min_value is None
            or observation.actual_value >= forecast.expected_min_value
        )
        below_max = (
            forecast.expected_max_value is None
            or observation.actual_value <= forecast.expected_max_value
        )
        return above_min and below_max

    @staticmethod
    def _benchmark_outperformance(
        forecast: Forecast,
        observation: OutcomeObservation,
    ) -> bool | None:
        if (
            forecast.benchmark_subject_id is None
            or forecast.baseline_value is None
            or observation.actual_value is None
            or observation.benchmark_value is None
        ):
            return None
        subject_change = observation.actual_value - forecast.baseline_value
        return subject_change > observation.benchmark_value

    @staticmethod
    def _status(
        *,
        forecast: Forecast,
        observation: OutcomeObservation,
        direction_correct: bool | None,
        range_correct: bool | None,
    ) -> str:
        if observation.invalidation_condition_hit:
            return "invalidated"
        checks = [
            value
            for value in (direction_correct, range_correct)
            if value is not None
        ]
        if not checks or forecast.expected_direction == "non_price":
            return "inconclusive"
        if all(checks):
            return "confirmed"
        if any(checks):
            return "partially_confirmed"
        return "not_confirmed"
