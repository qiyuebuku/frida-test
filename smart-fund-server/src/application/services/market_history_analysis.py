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
    for index in range(20, len(bars) - forward_window - 1):
        signal = _signal(bars, index)
        distance = _signal_distance(current, signal)
        if distance <= match_distance_threshold:
            candidates.append((distance, index, signal))
    candidates.sort(key=lambda row: row[0])
    selected = candidates[:30]
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
        "samples": observations,
        "evidence_locators": [row["evidence_locator"] for row in observations if row["evidence_locator"]],
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
        rows.append({
            "trade_date": str(item.get("trade_date") or ""),
            "close": close,
            "high": high,
            "low": low,
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
    }
    if relatives:
        result.update({
            "relative_positive_share": _round(sum(value > 0 for value in relatives) / len(relatives)),
            "median_relative_return_pct": _round(median(relatives)),
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
