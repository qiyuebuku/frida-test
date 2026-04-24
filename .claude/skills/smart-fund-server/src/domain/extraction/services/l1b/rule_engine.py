"""L1b 规则引擎基类 — 三种规则模式：阈值/差值/趋势"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class RuleResult:
    """规则评估结果"""
    fired: bool
    direction: str = "neutral"  # positive / negative / neutral
    strength: float = 0.0       # [0, 1]
    evidence: dict = field(default_factory=dict)
    title_template: str = ""
    summary_template: str = ""


class NumericRule(ABC):
    """规则基类 — 子类需实现 evaluate()"""

    rule_name: str = ""
    data_source: str = ""           # ft_market_flow / ft_macro_indicators / ...
    event_type: str = ""
    event_subtype: str = ""
    description: str = ""

    @abstractmethod
    def evaluate(self, record: dict, thresholds: dict) -> list[RuleResult]:
        """评估单条数据记录

        Args:
            record: 从数据源读取的一条记录（含 data JSONB 字段）
            thresholds: 从 ft_rule_thresholds 读取的该规则阈值

        Returns: 可能触发多个 RuleResult（一条数据可触发多个子条件）
        """
        ...

    def build_event(self, result: RuleResult, record: dict) -> dict:
        """从 RuleResult 构建 ft_events 入库字典"""
        return {
            "title": result.title_template,
            "summary": result.summary_template,
            "event_type": self.event_type,
            "event_subtype": self.event_subtype,
            "source_type": "numeric",
            "source_table": self.data_source,
            "direction": result.direction,
            "strength": result.strength,
            "sentiment": 0.5 if result.direction == "neutral" else (0.7 if result.direction == "positive" else 0.3),
            "scope": "market",
            "duration": "short",
            "novelty": 0.5,
            "certainty": 0.8,
            "evidence_refs": [{
                "table": self.data_source,
                "pk": record.get("id"),
                "excerpt": str(record.get("data", ""))[:500],
            }],
            "event_time": record.get("trade_date") or record.get("published_at") or datetime.now(),
        }


class ThresholdRule(NumericRule):
    """单值阈值规则 — 值超过 p95/p5 触发"""

    metric_field: str = ""       # JSONB data 中的字段名
    threshold_key: str = "percentile_95"

    def evaluate(self, record: dict, thresholds: dict) -> list[RuleResult]:
        data = record.get("data") or {}
        if isinstance(data, str):
            import json
            try:
                data = json.loads(data)
            except Exception:
                return []

        value = _extract_float(data, self.metric_field)
        if value is None:
            return []

        p95 = thresholds.get("percentile_95")
        p5 = thresholds.get("percentile_5") or (thresholds.get("threshold_config") or {}).get("percentile_5")

        results = []
        if p95 is not None and value > p95:
            results.append(RuleResult(
                fired=True,
                direction="positive",
                strength=min(1.0, value / p95 - 0.5) if p95 > 0 else 0.5,
                evidence={"value": value, "threshold": p95, "field": self.metric_field},
            ))
        if p5 is not None and value < p5:
            results.append(RuleResult(
                fired=True,
                direction="negative",
                strength=min(1.0, (p5 - value) / abs(p5) + 0.5) if p5 != 0 else 0.5,
                evidence={"value": value, "threshold": p5, "field": self.metric_field},
            ))

        return results


class SurpriseRule(NumericRule):
    """差值对比规则 — 实际值与预期值偏差 > N*σ"""

    actual_field: str = ""
    expected_field: str = ""

    def evaluate(self, record: dict, thresholds: dict) -> list[RuleResult]:
        sigma = thresholds.get("sigma_value") or 1.0
        config = thresholds.get("threshold_config") or {}
        n_sigma = config.get("n_sigma", 0.5)

        # record 级别字段（ft_macro_indicators 的 value/prev_value）
        actual = record.get("value") or _extract_float(record.get("data") or {}, self.actual_field)
        expected = record.get("prev_value") or _extract_float(record.get("data") or {}, self.expected_field)

        if actual is None or expected is None:
            return []

        diff = actual - expected
        if sigma > 0 and abs(diff) > n_sigma * sigma:
            return [RuleResult(
                fired=True,
                direction="positive" if diff > 0 else "negative",
                strength=min(1.0, abs(diff) / sigma * 0.3),
                evidence={"actual": actual, "expected": expected, "diff": diff, "sigma": sigma},
            )]

        return []


class TrendRule(NumericRule):
    """趋势识别规则 — 连续 N 期同方向变化"""

    lookback: int = 3

    def evaluate(self, record: dict, thresholds: dict) -> list[RuleResult]:
        # TrendRule 需要外部传入历史序列，单条 record 无法判断
        # 由 L1bDetector 在调用前组装序列后传入 record["_history"]
        history = record.get("_history") or []
        if len(history) < self.lookback:
            return []

        diffs = []
        for i in range(1, len(history)):
            prev = history[i - 1].get("value") or _extract_float(history[i - 1].get("data") or {}, "value")
            curr = history[i].get("value") or _extract_float(history[i].get("data") or {}, "value")
            if prev is not None and curr is not None:
                diffs.append(curr - prev)

        if len(diffs) < self.lookback - 1:
            return []

        last_diffs = diffs[-(self.lookback - 1):]
        if all(d > 0 for d in last_diffs):
            direction = "positive"
        elif all(d < 0 for d in last_diffs):
            direction = "negative"
        else:
            return []

        return [RuleResult(
            fired=True,
            direction=direction,
            strength=0.6,
            evidence={"consecutive": len(last_diffs), "diffs": last_diffs},
        )]


def _extract_float(data: dict, field: str) -> float | None:
    """从 dict 中安全提取浮点值"""
    if not field or not isinstance(data, dict):
        return None
    v = data.get(field)
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
