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
