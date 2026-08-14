from datetime import date, timedelta

from src.application.services.market_history_analysis import (
    historical_analogues,
    technical_state,
)


def _series(count: int = 160, *, multiplier: float = 1.0) -> list[dict]:
    start = date(2026, 1, 1)
    rows = []
    for index in range(count):
        close = multiplier * (100 + index * 0.08 + (index % 10 - 5) * 0.35)
        rows.append({
            "id": index + 1,
            "trade_date": (start + timedelta(days=index)).isoformat(),
            "data": {"close": close, "high": close + 1, "low": close - 1},
            "evidence_locator": f"market:v1:{index}",
        })
    return list(reversed(rows))


def test_technical_state_uses_fixed_windows_and_optional_benchmark() -> None:
    result = technical_state(
        _series(),
        subject_id="ths:concept:886033",
        benchmark_items=_series(multiplier=0.8),
    )

    assert set(result["windows"]) == {"20_bars", "60_bars", "120_bars"}
    assert result["windows"]["120_bars"]["high_trade_date"]
    assert result["recent_swing"]["rule"]
    assert result["relative_strength"]["window_bars"] == 20
    assert result["evidence_locators"]


def test_historical_analogues_returns_distribution_and_sample_identity() -> None:
    result = historical_analogues(
        _series(),
        subject_id="ths:concept:886033",
        benchmark_items=_series(multiplier=0.9),
        forward_window=3,
        min_samples=3,
    )

    assert result["sample_count"] >= 3
    assert result["calibration_status"] == "calibrated"
    assert "median_return_pct" in result["statistics"]
    assert "lower_quartile_relative_return_pct" in result["statistics"]
    assert "upper_quartile_relative_return_pct" in result["statistics"]
    assert result["samples"][0]["evidence_locator"].startswith("market:v1:")
    holdout = result["robustness"]["temporal_holdout"]
    assert holdout["validation_status"] == "validated"
    assert holdout["development_sample_count"] >= 3
    assert holdout["holdout_sample_count"] >= 3
    relative_holdout = result["robustness"]["relative_temporal_holdout"]
    assert relative_holdout["validation_status"] == "validated"
    assert "median_return_pct" in relative_holdout["holdout_statistics"]
    controls = result["robustness"]["leakage_controls"]
    assert controls["point_in_time_features_only"] is True
    assert controls["non_overlapping_forward_windows"] is True
    signal_dates = [row["signal_trade_date"] for row in result["samples"]]
    assert len(signal_dates) == len(set(signal_dates))
    sensitivity = result["robustness"]["threshold_sensitivity"]
    assert len(sensitivity) == 3
    assert [row["match_distance_threshold"] for row in sensitivity] == sorted(
        row["match_distance_threshold"] for row in sensitivity
    )


def test_historical_analogues_warns_when_minimum_is_not_met() -> None:
    result = historical_analogues(
        _series(60),
        subject_id="cn:index:000001",
        min_samples=30,
    )

    assert result["calibration_status"] == "insufficient_samples"
    assert "不得" in result["warning"]
    assert (
        result["robustness"]["temporal_holdout"]["validation_status"]
        == "insufficient_samples"
    )


def test_historical_analogues_discloses_adjustable_match_threshold() -> None:
    result = historical_analogues(
        _series(),
        subject_id="ths:concept:886033",
        min_samples=8,
        match_distance_threshold=5.0,
    )

    assert result["signal_definition"]["match_distance_threshold"] == 5.0
    assert "不超过5" in result["signal_definition"]["distance_rule"]
    assert result["robustness"]["strict_distance_threshold"] < 5.0
