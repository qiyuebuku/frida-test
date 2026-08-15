"""Scheduled deterministic evaluation of due Research forecasts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.application.agents.financial_research.outcome_evaluator import (
    ResearchOutcomeEvaluator,
)
from src.application.agents.financial_research.schemas import (
    EvidenceCitation,
    OutcomeObservation,
)
from src.application.services.market_evidence_locator import (
    MarketEvidenceIdentity,
    encode_market_evidence_locator,
)
from src.infrastructure.persistence.repositories.agent_research_repository import (
    AgentResearchRepository,
)
from src.infrastructure.persistence.repositories.market_observation_repository import (
    MarketSnapshotRepository,
)


class ResearchOutcomeEvaluationService:
    def __init__(self) -> None:
        self._research = AgentResearchRepository()
        self._market = MarketSnapshotRepository()
        self._evaluator = ResearchOutcomeEvaluator()

    def evaluate_due(self, *, evaluated_at: datetime | None = None, limit: int = 100) -> dict[str, Any]:
        now = evaluated_at or datetime.now(UTC)
        forecasts = self._research.list_due_forecasts(due_at=now, limit=limit)
        saved = []
        unresolved = []
        for forecast in forecasts:
            derived = self._resolve_return_forecast(forecast)
            if derived is not None:
                evaluated_forecast, observation = derived
                evaluation = self._evaluator.evaluate(
                    forecast=evaluated_forecast,
                    observation=observation,
                    evaluation_id=f"outcome-evaluation:{forecast.forecast_id}",
                    evaluated_at=now,
                )
                saved.append((observation, evaluation))
                continue
            rows = self._market.query_latest(
                subject_ids=[forecast.subject_id],
                cutoff_at=forecast.evaluation_end_at,
            )
            selected = _select_metric_row(rows, forecast.metric)
            if selected is None:
                unresolved.append({
                    "forecast_id": forecast.forecast_id,
                    "reason": "metric_not_available",
                    "metric": forecast.metric,
                })
                continue
            value = _find_numeric(selected.get("data"), forecast.metric)
            if value is None:
                unresolved.append({
                    "forecast_id": forecast.forecast_id,
                    "reason": "metric_not_numeric",
                    "metric": forecast.metric,
                })
                continue
            observed_at = selected.get("observed_at") or selected.get("fetched_at") or now
            reference = encode_market_evidence_locator(MarketEvidenceIdentity(
                kind="snapshot",
                domain="market_snapshot",
                identity={"id": selected["id"]},
                data_type=selected.get("data_type"),
                subject_id=forecast.subject_id,
                fact_time=str(selected.get("observed_at") or selected.get("trade_date") or ""),
            ))
            observation = OutcomeObservation(
                observation_id=f"outcome:{forecast.forecast_id}",
                forecast_id=forecast.forecast_id,
                observed_at=max(observed_at, forecast.evaluation_start_at),
                actual_value=value,
                benchmark_value=None,
                invalidation_condition_hit=False,
                evidence=[EvidenceCitation(
                    citation_id=f"outcome-citation:{forecast.forecast_id}",
                    kind="market",
                    reference=reference,
                    support="supports",
                    observed_at=selected.get("observed_at"),
                    as_of=selected.get("observed_at"),
                )],
            )
            evaluation = self._evaluator.evaluate(
                forecast=forecast,
                observation=observation,
                evaluation_id=f"outcome-evaluation:{forecast.forecast_id}",
                evaluated_at=now,
            )
            saved.append((observation, evaluation))
        saved_count = self._research.save_outcome_evaluations_batch(saved)
        return {
            "status": "completed",
            "due_count": len(forecasts),
            "evaluated_count": saved_count,
            "unresolved": unresolved,
            "evaluated_at": now.isoformat(),
        }

    def _resolve_return_forecast(
        self,
        forecast,
    ) -> tuple[Any, OutcomeObservation] | None:
        metric = forecast.metric.lower()
        is_relative = (
            "超额" in metric
            or ("相对" in metric and ("收益" in metric or "涨跌" in metric))
            or "relative_return" in metric
        )
        is_return = is_relative or any(
            token in metric for token in ("收益", "涨跌", "return")
        )
        if not is_return:
            return None
        subject_series = _daily_series_identity(forecast.subject_id)
        if subject_series is None:
            return None
        subject_rows = self._market.query_history(
            subject_id=subject_series[1],
            data_type=subject_series[0],
            date_start=forecast.evaluation_start_at.date(),
            date_end=forecast.evaluation_end_at.date(),
            cutoff_at=forecast.evaluation_end_at,
            limit=20,
        )
        subject_pair = _window_close_pair(subject_rows)
        if subject_pair is None:
            return None
        subject_return = _return_pct(subject_pair)
        benchmark_return = None
        evidence_rows = list(subject_pair)
        if is_relative:
            if not forecast.benchmark_subject_id:
                return None
            benchmark_series = _daily_series_identity(
                forecast.benchmark_subject_id
            )
            if benchmark_series is None:
                return None
            benchmark_rows = self._market.query_history(
                subject_id=benchmark_series[1],
                data_type=benchmark_series[0],
                date_start=forecast.evaluation_start_at.date(),
                date_end=forecast.evaluation_end_at.date(),
                cutoff_at=forecast.evaluation_end_at,
                limit=20,
            )
            benchmark_pair = _window_close_pair(benchmark_rows)
            if benchmark_pair is None:
                return None
            benchmark_return = _return_pct(benchmark_pair)
            evidence_rows.extend(benchmark_pair)
        actual_value = subject_return
        observed_at = max(
            row.get("observed_at") or row.get("fetched_at")
            for row in evidence_rows
        )
        observed_at = min(
            max(observed_at, forecast.evaluation_start_at),
            forecast.evaluation_end_at,
        )
        citations = [
            EvidenceCitation(
                citation_id=f"outcome-citation:{forecast.forecast_id}:{index}",
                kind="market",
                reference=_snapshot_reference(row),
                support="supports",
                observed_at=row.get("observed_at"),
                as_of=row.get("observed_at"),
            )
            for index, row in enumerate(evidence_rows, start=1)
        ]
        observation = OutcomeObservation(
            observation_id=f"outcome:{forecast.forecast_id}",
            forecast_id=forecast.forecast_id,
            observed_at=observed_at,
            actual_value=round(actual_value, 4),
            benchmark_value=(
                round(benchmark_return, 4)
                if benchmark_return is not None
                else None
            ),
            invalidation_condition_hit=False,
            evidence=citations,
        )
        return forecast.model_copy(update={"baseline_value": 0.0}), observation


def _select_metric_row(rows: list[dict[str, Any]], metric: str) -> dict[str, Any] | None:
    return next((row for row in rows if _find_numeric(row.get("data"), metric) is not None), None)


def _find_numeric(value: Any, metric: str) -> float | None:
    if isinstance(value, dict):
        direct = value.get(metric)
        if isinstance(direct, (int, float)) and not isinstance(direct, bool):
            return float(direct)
        for child in value.values():
            found = _find_numeric(child, metric)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_numeric(child, metric)
            if found is not None:
                return found
    return None


def _daily_series_identity(subject_id: str) -> tuple[str, str] | None:
    value = str(subject_id)
    if value.startswith("cn:index:"):
        return "ths_index_daily", value
    if value.startswith("cn:concept:"):
        return "ths_sector_daily", value.replace("cn:concept:", "ths:concept:", 1)
    if value.startswith("cn:industry:"):
        return "ths_sector_daily", value.replace("cn:industry:", "ths:industry:", 1)
    if value.startswith(("ths:concept:", "ths:industry:")):
        return "ths_sector_daily", value
    return None


def _window_close_pair(rows: list[dict[str, Any]]) -> tuple[dict, dict] | None:
    ordered = sorted(
        (row for row in rows if _find_numeric(row.get("data"), "close") is not None),
        key=lambda row: (row.get("trade_date"), row.get("bucket_at")),
    )
    if len(ordered) < 2:
        return None
    return ordered[0], ordered[-1]


def _return_pct(pair: tuple[dict, dict]) -> float:
    start = _find_numeric(pair[0].get("data"), "close")
    end = _find_numeric(pair[1].get("data"), "close")
    if start is None or end is None or start == 0:
        raise ValueError("daily close series cannot produce a return")
    return (end / start - 1.0) * 100.0


def _snapshot_reference(row: dict[str, Any]) -> str:
    return encode_market_evidence_locator(MarketEvidenceIdentity(
        kind="snapshot",
        domain="market_snapshot",
        identity={"id": row["id"]},
        data_type=row.get("data_type"),
        subject_id=row.get("subject_id"),
        fact_time=str(row.get("observed_at") or row.get("trade_date") or ""),
    ))
