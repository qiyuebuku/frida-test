"""Deterministic historical calibration and technical-state calculations."""

from __future__ import annotations

from math import isfinite
from statistics import median
from typing import Any


def technical_state(
    items: list[dict[str, Any]],
    *,
    subject_id: str,
    benchmark_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bars = _bars(items)
    if len(bars) < 2:
        raise ValueError("技术状态至少需要两个有效收盘价")
    latest = bars[-1]
    result: dict[str, Any] = {
        "subject_id": subject_id,
        "latest_trade_date": latest["trade_date"],
        "latest_close": _round(latest["close"]),
        "available_bars": len(bars),
        "windows": {},
    }
    for window in (20, 60, 120):
        if len(bars) < window:
            continue
        sample = bars[-window:]
        high = max(sample, key=lambda row: row["high"])
        low = min(sample, key=lambda row: row["low"])
        prior = sample[:-1]
        prior_high = max(row["high"] for row in prior)
        prior_low = min(row["low"] for row in prior)
        state = "inside_range"
        if latest["close"] > prior_high:
            state = "breakout"
        elif latest["close"] < prior_low:
            state = "breakdown"
        result["windows"][f"{window}_bars"] = {
            "high": _round(high["high"]),
            "high_trade_date": high["trade_date"],
            "low": _round(low["low"]),
            "low_trade_date": low["trade_date"],
            "distance_to_high_pct": _pct(latest["close"], high["high"]),
            "distance_from_low_pct": _pct(latest["close"], low["low"]),
            "return_pct": _pct(latest["close"], sample[0]["close"]),
            "position_state": state,
            "high_evidence_locator": high.get("evidence_locator"),
            "low_evidence_locator": low.get("evidence_locator"),
        }
    swing_high, swing_low = _recent_swings(bars)
    result["recent_swing"] = {
        "rule": "最近20根K线内、左右各2根K线确认的最后一个局部极值",
        "high": _swing_projection(swing_high, "high"),
        "low": _swing_projection(swing_low, "low"),
    }
    drawdown_high = max(bars, key=lambda row: row["high"])
    result["peak_drawdown_pct"] = _pct(latest["close"], drawdown_high["high"])
    prior_volumes = [
        row["volume"] for row in bars[-21:-1]
        if isinstance(row.get("volume"), (int, float)) and row["volume"] >= 0
    ]
    if isinstance(latest.get("volume"), (int, float)) and prior_volumes:
        baseline = median(prior_volumes)
        ratio = latest["volume"] / baseline if baseline else None
        result["volume_confirmation"] = {
            "latest_volume_raw": _round(latest["volume"]),
            "prior_20_median_volume_raw": _round(baseline),
            "latest_to_prior_median_ratio": _round(ratio) if ratio is not None else None,
            "state": (
                "above_prior_median" if ratio is not None and ratio > 1
                else "below_prior_median" if ratio is not None and ratio < 1
                else "at_prior_median"
            ),
            "semantics": "provider-native raw volume ratio; unit is not established",
            "latest_evidence_locator": latest.get("evidence_locator"),
        }
    if benchmark_items:
        benchmark = _bars(benchmark_items)
        aligned = _aligned_returns(bars, benchmark, 20)
        if aligned is not None:
            result["relative_strength"] = aligned
    result["evidence_locators"] = list(dict.fromkeys(
        locator for locator in (
            latest.get("evidence_locator"),
            drawdown_high.get("evidence_locator"),
            swing_high.get("evidence_locator") if swing_high else None,
            swing_low.get("evidence_locator") if swing_low else None,
        ) if locator
    ))
    return result


def historical_analogues(
    items: list[dict[str, Any]],
    *,
    subject_id: str,
    benchmark_items: list[dict[str, Any]] | None = None,
    forward_window: int = 3,
    min_samples: int = 8,
    match_distance_threshold: float = 2.5,
) -> dict[str, Any]:
    bars = _bars(items)
    if not 1 <= forward_window <= 20:
        raise ValueError("forward_window 必须在 1 到 20 之间")
    if not 1.0 <= match_distance_threshold <= 8.0:
        raise ValueError("match_distance_threshold 必须在 1.0 到 8.0 之间")
    if len(bars) < 25 + forward_window:
        raise ValueError("历史相似场景至少需要 25 根基准K线及完整前瞻窗口")
    benchmark = _bars(benchmark_items or [])
    benchmark_by_date = {row["trade_date"]: row for row in benchmark}
    current = _signal(bars, len(bars) - 1)
    candidates: list[tuple[float, int, dict[str, float]]] = []
    sensitivity_ceiling = min(8.0, match_distance_threshold * 1.3)
    for index in range(20, len(bars) - forward_window - 1):
        signal = _signal(bars, index)
        distance = _signal_distance(current, signal)
        if distance <= sensitivity_ceiling:
            candidates.append((distance, index, signal))
    candidates.sort(key=lambda row: row[0])
    selected = _select_non_overlapping_matches(
        [row for row in candidates if row[0] <= match_distance_threshold],
        forward_window=forward_window,
        limit=30,
    )
    observations: list[dict[str, Any]] = []
    for distance, index, signal in selected:
        start = bars[index]
        end = bars[index + forward_window]
        absolute_return = _raw_pct(end["close"], start["close"])
        relative_return = None
        if benchmark_by_date:
            b_start = benchmark_by_date.get(start["trade_date"])
            b_end = benchmark_by_date.get(end["trade_date"])
            if b_start and b_end:
                relative_return = absolute_return - _raw_pct(b_end["close"], b_start["close"])
        path = bars[index + 1 : index + forward_window + 1]
        observations.append({
            "signal_trade_date": start["trade_date"],
            "forward_end_trade_date": end["trade_date"],
            "match_distance": _round(distance),
            "forward_return_pct": _round(absolute_return),
            "relative_return_pct": _round(relative_return) if relative_return is not None else None,
            "max_favorable_excursion_pct": _round(max(_raw_pct(row["high"], start["close"]) for row in path)),
            "max_adverse_excursion_pct": _round(min(_raw_pct(row["low"], start["close"]) for row in path)),
            "evidence_locator": start.get("evidence_locator"),
        })
    returns = [row["forward_return_pct"] for row in observations]
    relatives = [row["relative_return_pct"] for row in observations if row["relative_return_pct"] is not None]
    enough = len(observations) >= min_samples
    strict_threshold = min(1.5, match_distance_threshold * 0.7)
    strict_observations = [
        row for row in observations if row["match_distance"] <= strict_threshold
    ]
    strict_returns = [row["forward_return_pct"] for row in strict_observations]
    trimmed_returns = (
        sorted(returns)[1:-1] if len(returns) >= 8 else returns
    )
    chronological = sorted(observations, key=lambda row: row["signal_trade_date"])
    split_index = len(chronological) // 2
    development = chronological[:split_index]
    holdout = chronological[split_index:]
    development_returns = [row["forward_return_pct"] for row in development]
    holdout_returns = [row["forward_return_pct"] for row in holdout]
    development_relative_returns = [
        row["relative_return_pct"]
        for row in development
        if row["relative_return_pct"] is not None
    ]
    holdout_relative_returns = [
        row["relative_return_pct"]
        for row in holdout
        if row["relative_return_pct"] is not None
    ]
    minimum_half_samples = max(3, min_samples // 2)
    temporal_validation = _temporal_validation(
        development_returns,
        holdout_returns,
        minimum_half_samples=minimum_half_samples,
    )
    relative_temporal_validation = _temporal_validation(
        development_relative_returns,
        holdout_relative_returns,
        minimum_half_samples=minimum_half_samples,
    )
    return {
        "subject_id": subject_id,
        "signal_definition": {
            "features": ["5日收益", "20日收益", "距20日高点", "20日区间位置"],
            "distance_rule": (
                "四项标准化绝对距离之和不超过"
                f"{match_distance_threshold:g}，最多返回最相似30例"
            ),
            "match_distance_threshold": match_distance_threshold,
            "current_signal": {key: _round(value) for key, value in current.items()},
        },
        "forward_window_bars": forward_window,
        "sample_count": len(observations),
        "minimum_sample_count": min_samples,
        "calibration_status": "calibrated" if enough else "insufficient_samples",
        "warning": None if enough else "样本不足，不得把正收益比例表述为可靠概率",
        "statistics": _distribution(returns, relatives) if observations else {},
        "robustness": {
            "leakage_controls": {
                "point_in_time_features_only": True,
                "future_bars_excluded_from_signal": True,
                "non_overlapping_forward_windows": True,
                "minimum_signal_gap_bars": forward_window + 1,
                "temporal_split": "chronological_older_vs_newer_half",
                "interpretation": (
                    "信号只使用各历史时点当时可见数据；相邻命中的前瞻窗口已去重，"
                    "避免同一段未来行情被重复计为多个样本。"
                ),
            },
            "threshold_sensitivity": _threshold_sensitivity(
                candidates,
                bars=bars,
                benchmark_by_date=benchmark_by_date,
                forward_window=forward_window,
                base_threshold=match_distance_threshold,
            ),
            "strict_distance_threshold": _round(strict_threshold),
            "strict_sample_count": len(strict_observations),
            "strict_statistics": (
                _distribution(strict_returns, []) if strict_returns else {}
            ),
            "trimmed_one_each_tail_statistics": (
                _distribution(trimmed_returns, []) if trimmed_returns else {}
            ),
            "wide_match_share": _round(
                sum(row["match_distance"] > strict_threshold for row in observations)
                / len(observations)
            ) if observations else None,
            "interpretation": (
                "宽阈值样本占比较高；方向只能作为低置信度条件证据，必须与严格子集、"
                "离群值敏感性和当前趋势共同解释"
                if observations and sum(row["match_distance"] > strict_threshold for row in observations) / len(observations) > 0.5
                else "样本主要来自较近似历史状态，仍需结合当前趋势和反证"
            ),
            "temporal_holdout": temporal_validation,
            "relative_temporal_holdout": relative_temporal_validation,
        },
        "samples": observations,
        "evidence_locators": [row["evidence_locator"] for row in observations if row["evidence_locator"]],
    }


def _select_non_overlapping_matches(
    candidates: list[tuple[float, int, dict[str, float]]],
    *,
    forward_window: int,
    limit: int,
) -> list[tuple[float, int, dict[str, float]]]:
    """Keep closest episodes without counting the same future path twice."""

    selected: list[tuple[float, int, dict[str, float]]] = []
    selected_indices: list[int] = []
    for candidate in candidates:
        index = candidate[1]
        if any(abs(index - prior) <= forward_window for prior in selected_indices):
            continue
        selected.append(candidate)
        selected_indices.append(index)
        if len(selected) >= limit:
            break
    return selected


def _threshold_sensitivity(
    candidates: list[tuple[float, int, dict[str, float]]],
    *,
    bars: list[dict[str, Any]],
    benchmark_by_date: dict[str, dict[str, Any]],
    forward_window: int,
    base_threshold: float,
) -> list[dict[str, Any]]:
    """Recompute the same statistic at narrower/base/wider match thresholds."""

    thresholds = sorted({
        max(1.0, base_threshold * 0.7),
        base_threshold,
        min(8.0, base_threshold * 1.3),
    })
    results: list[dict[str, Any]] = []
    for threshold in thresholds:
        eligible = [row for row in candidates if row[0] <= threshold]
        selected = _select_non_overlapping_matches(
            eligible,
            forward_window=forward_window,
            limit=30,
        )
        absolute_returns: list[float] = []
        relative_returns: list[float] = []
        for _, index, _ in selected:
            start = bars[index]
            end = bars[index + forward_window]
            absolute = _raw_pct(end["close"], start["close"])
            absolute_returns.append(absolute)
            benchmark_start = benchmark_by_date.get(start["trade_date"])
            benchmark_end = benchmark_by_date.get(end["trade_date"])
            if benchmark_start and benchmark_end:
                relative_returns.append(
                    absolute
                    - _raw_pct(benchmark_end["close"], benchmark_start["close"])
                )
        distribution = (
            _distribution(absolute_returns, relative_returns)
            if absolute_returns
            else {}
        )
        results.append({
            "match_distance_threshold": _round(threshold),
            "sample_count": len(selected),
            "median_return_pct": distribution.get("median_return_pct"),
            "positive_share": distribution.get("positive_share"),
            "median_relative_return_pct": distribution.get(
                "median_relative_return_pct"
            ),
            "positive_relative_share": distribution.get(
                "positive_relative_share"
            ),
        })
    return results


def _temporal_validation(
    development_returns: list[float],
    holdout_returns: list[float],
    *,
    minimum_half_samples: int,
) -> dict[str, Any]:
    """Compare older matches with a later untouched chronological holdout."""

    enough = (
        len(development_returns) >= minimum_half_samples
        and len(holdout_returns) >= minimum_half_samples
    )
    development = (
        _distribution(development_returns, []) if development_returns else {}
    )
    holdout = _distribution(holdout_returns, []) if holdout_returns else {}
    development_median = development.get("median_return_pct")
    holdout_median = holdout.get("median_return_pct")
    direction_consistent = (
        development_median is not None
        and holdout_median is not None
        and (
            (development_median > 0 and holdout_median > 0)
            or (development_median < 0 and holdout_median < 0)
            or (development_median == 0 and holdout_median == 0)
        )
    )
    return {
        "method": "按信号日期排序，较早一半为开发样本，较晚一半为留出样本",
        "minimum_half_samples": minimum_half_samples,
        "validation_status": "validated" if enough else "insufficient_samples",
        "development_sample_count": len(development_returns),
        "holdout_sample_count": len(holdout_returns),
        "development_statistics": development,
        "holdout_statistics": holdout,
        "median_direction_consistent": direction_consistent if enough else None,
        "interpretation": (
            "留出样本与较早样本的中位方向一致；仍需结合幅度、尾部与当前趋势判断"
            if enough and direction_consistent
            else "留出样本未确认较早样本方向，不得把完整样本的方向当作稳定规律"
            if enough
            else "两段样本至少一段不足，不能声称已通过时间外稳定性验证"
        ),
    }


def _bars(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        data = item.get("data") if isinstance(item, dict) else None
        if not isinstance(data, dict):
            continue
        try:
            close = float(data["close"])
            high = float(data.get("high", close))
            low = float(data.get("low", close))
        except (KeyError, TypeError, ValueError):
            continue
        if not all(isfinite(value) and value > 0 for value in (close, high, low)):
            continue
        volume = data.get("volume")
        try:
            volume = float(volume) if volume is not None else None
        except (TypeError, ValueError):
            volume = None
        rows.append({
            "trade_date": str(item.get("trade_date") or ""),
            "close": close,
            "high": high,
            "low": low,
            "volume": volume if volume is not None and isfinite(volume) and volume >= 0 else None,
            "evidence_locator": item.get("evidence_locator"),
        })
    rows.sort(key=lambda row: row["trade_date"])
    return rows


def _signal(bars: list[dict[str, Any]], index: int) -> dict[str, float]:
    sample = bars[index - 19 : index + 1]
    close = bars[index]["close"]
    high = max(row["high"] for row in sample)
    low = min(row["low"] for row in sample)
    spread = high - low
    return {
        "return_5_pct": _raw_pct(close, bars[index - 4]["close"]),
        "return_20_pct": _raw_pct(close, bars[index - 19]["close"]),
        "distance_to_20d_high_pct": _raw_pct(close, high),
        "range_position": (close - low) / spread if spread else 0.5,
    }


def _signal_distance(left: dict[str, float], right: dict[str, float]) -> float:
    scales = {"return_5_pct": 3.0, "return_20_pct": 8.0, "distance_to_20d_high_pct": 5.0, "range_position": 0.25}
    return sum(abs(left[key] - right[key]) / scales[key] for key in scales)


def _distribution(returns: list[float], relatives: list[float]) -> dict[str, Any]:
    ordered = sorted(returns)
    result = {
        "positive_share": _round(sum(value > 0 for value in returns) / len(returns)),
        "median_return_pct": _round(median(returns)),
        "lower_quartile_return_pct": _round(_quantile(ordered, 0.25)),
        "upper_quartile_return_pct": _round(_quantile(ordered, 0.75)),
        "lower_decile_return_pct": _round(_quantile(ordered, 0.10)),
        "upper_decile_return_pct": _round(_quantile(ordered, 0.90)),
        "minimum_return_pct": _round(ordered[0]),
        "maximum_return_pct": _round(ordered[-1]),
    }
    if relatives:
        ordered_relatives = sorted(relatives)
        result.update({
            "relative_positive_share": _round(sum(value > 0 for value in relatives) / len(relatives)),
            "median_relative_return_pct": _round(median(relatives)),
            "lower_quartile_relative_return_pct": _round(
                _quantile(ordered_relatives, 0.25)
            ),
            "upper_quartile_relative_return_pct": _round(
                _quantile(ordered_relatives, 0.75)
            ),
            "lower_decile_relative_return_pct": _round(
                _quantile(ordered_relatives, 0.10)
            ),
            "upper_decile_relative_return_pct": _round(
                _quantile(ordered_relatives, 0.90)
            ),
            "minimum_relative_return_pct": _round(ordered_relatives[0]),
            "maximum_relative_return_pct": _round(ordered_relatives[-1]),
        })
    return result


def _quantile(values: list[float], fraction: float) -> float:
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def _recent_swings(bars: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    sample = bars[-20:]
    highs = [sample[i] for i in range(2, len(sample) - 2) if sample[i]["high"] == max(row["high"] for row in sample[i-2:i+3])]
    lows = [sample[i] for i in range(2, len(sample) - 2) if sample[i]["low"] == min(row["low"] for row in sample[i-2:i+3])]
    return (highs[-1] if highs else None, lows[-1] if lows else None)


def _swing_projection(row: dict[str, Any] | None, field: str) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"value": _round(row[field]), "trade_date": row["trade_date"], "evidence_locator": row.get("evidence_locator")}


def _aligned_returns(subject: list[dict[str, Any]], benchmark: list[dict[str, Any]], window: int) -> dict[str, Any] | None:
    left = {row["trade_date"]: row for row in subject}
    right = {row["trade_date"]: row for row in benchmark}
    dates = sorted(set(left).intersection(right))
    if len(dates) < window:
        return None
    selected = dates[-window:]
    subject_return = _raw_pct(left[selected[-1]]["close"], left[selected[0]]["close"])
    benchmark_return = _raw_pct(right[selected[-1]]["close"], right[selected[0]]["close"])
    return {"window_bars": window, "subject_return_pct": _round(subject_return), "benchmark_return_pct": _round(benchmark_return), "excess_return_pct": _round(subject_return - benchmark_return)}


def _raw_pct(current: float, baseline: float) -> float:
    return (current / baseline - 1.0) * 100.0


def _pct(current: float, baseline: float) -> float:
    return _round(_raw_pct(current, baseline))


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None
