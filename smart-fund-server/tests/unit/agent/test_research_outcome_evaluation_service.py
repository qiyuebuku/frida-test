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


def test_relative_return_forecast_uses_subject_and_benchmark_daily_bars(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 15, 7, 0, tzinfo=UTC)
    forecast = Forecast(
        forecast_id="forecast-relative",
        subject_id="cn:concept:886033",
        metric="CPO概念相对上证指数累计超额收益方向",
        expected_direction="up",
        benchmark_subject_id="cn:index:000001",
        evaluation_start_at=datetime(2026, 8, 12, 7, 0, tzinfo=UTC),
        evaluation_end_at=now,
        invalidation_condition="相对收益转负",
    )
    service = ResearchOutcomeEvaluationService()
    monkeypatch.setattr(service._research, "list_due_forecasts", lambda **_: [forecast])

    def history(*, subject_id, data_type, **_):
        closes = (
            [
                ("2026-08-14", 5833.173, 4),
                ("2026-08-12", 5721.673, 3),
            ]
            if subject_id == "ths:concept:886033"
            else [
                ("2026-08-14", 3927.18, 2),
                ("2026-08-12", 3946.68, 1),
            ]
        )
        return [
            {
                "id": row_id,
                "subject_id": subject_id,
                "data_type": data_type,
                "trade_date": trade_date,
                "bucket_at": datetime.fromisoformat(f"{trade_date}T07:00:00+00:00"),
                "observed_at": datetime.fromisoformat(f"{trade_date}T07:00:00+00:00"),
                "fetched_at": datetime.fromisoformat(f"{trade_date}T07:01:00+00:00"),
                "data": {"close": close},
            }
            for trade_date, close, row_id in closes
        ]

    monkeypatch.setattr(service._market, "query_history", history)
    captured = []
    monkeypatch.setattr(
        service._research,
        "save_outcome_evaluations_batch",
        lambda items: captured.extend(items) or len(items),
    )

    result = service.evaluate_due(evaluated_at=now)

    assert result["evaluated_count"] == 1
    observation, evaluation = captured[0]
    assert observation.actual_value == 1.9487
    assert evaluation.direction_correct is True
    assert evaluation.status == "confirmed"
    assert len(observation.evidence) == 4
