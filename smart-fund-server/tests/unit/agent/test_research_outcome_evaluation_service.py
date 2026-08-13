from datetime import UTC, datetime, timedelta

from src.application.agents.financial_research.schemas import Forecast
from src.application.services.research_outcome_evaluation_service import (
    ResearchOutcomeEvaluationService,
)


def test_due_forecast_is_evaluated_from_matching_market_metric(monkeypatch) -> None:
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    forecast = Forecast(
        forecast_id="forecast-1",
        subject_id="cn:index:000001",
        metric="close",
        expected_direction="up",
        baseline_value=3900,
        evaluation_start_at=now - timedelta(days=3),
        evaluation_end_at=now,
        invalidation_condition="跌破基线",
    )
    service = ResearchOutcomeEvaluationService()
    monkeypatch.setattr(service._research, "list_due_forecasts", lambda **_: [forecast])
    monkeypatch.setattr(service._market, "query_latest", lambda **_: [{
        "id": 1,
        "subject_id": forecast.subject_id,
        "data_type": "ths_index_daily",
        "trade_date": "2026-08-12",
        "observed_at": now,
        "fetched_at": now,
        "data": {"close": 3950},
    }])
    captured = []
    monkeypatch.setattr(
        service._research,
        "save_outcome_evaluations_batch",
        lambda items: captured.extend(items) or len(items),
    )

    result = service.evaluate_due(evaluated_at=now)

    assert result["evaluated_count"] == 1
    assert captured[0][1].direction_correct is True
    assert captured[0][1].status == "confirmed"


def test_due_forecast_without_metric_remains_pending(monkeypatch) -> None:
    now = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    forecast = Forecast(
        forecast_id="forecast-2",
        subject_id="cn:index:000001",
        metric="relative_strength",
        expected_direction="up",
        evaluation_start_at=now - timedelta(days=3),
        evaluation_end_at=now,
        invalidation_condition="相对强度转弱",
    )
    service = ResearchOutcomeEvaluationService()
    monkeypatch.setattr(service._research, "list_due_forecasts", lambda **_: [forecast])
    monkeypatch.setattr(service._market, "query_latest", lambda **_: [])
    monkeypatch.setattr(service._research, "save_outcome_evaluations_batch", lambda items: len(items))

    result = service.evaluate_due(evaluated_at=now)

    assert result["evaluated_count"] == 0
    assert result["unresolved"][0]["reason"] == "metric_not_available"
