"""市场快照规则 — ft_market_cache"""
from src.domain.extraction.services.l1b.rule_engine import RuleResult, ThresholdRule


class LimitUpSurge(ThresholdRule):
    """涨停潮 — market_overview 中 limit_up.total > p95"""
    rule_name = "limit_up_surge"
    data_source = "ft_market_cache"
    event_type = "macro_data"
    event_subtype = "limit_up_surge"
    metric_field = ""  # 从 data JSONB 深层提取
    description = "市场涨停数量超过阈值"

    def evaluate(self, record, thresholds):
        data = record.get("data") or {}
        limit_up = data.get("limit_up") or {}
        total = limit_up.get("total")
        if total is None:
            return []

        p95 = thresholds.get("percentile_95") or 80
        if total <= p95:
            return []

        return [RuleResult(
            fired=True,
            direction="positive",
            strength=min(1.0, total / max(p95, 1) * 0.5),
            evidence={"total": total, "p95": p95},
            title_template=f"市场情绪火热：{total} 只个股涨停",
            summary_template=f"今日共有 {total} 只个股涨停，超过阈值 {p95:.0f}",
        )]


class LimitDownSurge(ThresholdRule):
    """跌停潮 — market_overview 中 limit_down.total > p95"""
    rule_name = "limit_down_surge"
    data_source = "ft_market_cache"
    event_type = "macro_data"
    event_subtype = "limit_down_surge"
    metric_field = ""
    description = "市场跌停数量超过阈值"

    def evaluate(self, record, thresholds):
        data = record.get("data") or {}
        limit_down = data.get("limit_down") or {}
        total = limit_down.get("total")
        if total is None:
            return []

        p95 = thresholds.get("percentile_95") or 30
        if total <= p95:
            return []

        return [RuleResult(
            fired=True,
            direction="negative",
            strength=min(1.0, total / max(p95, 1) * 0.5),
            evidence={"total": total, "p95": p95},
            title_template=f"市场恐慌：{total} 只个股跌停",
            summary_template=f"今日共有 {total} 只个股跌停，超过阈值 {p95:.0f}",
        )]


ALL_MARKET_RULES = [LimitUpSurge(), LimitDownSurge()]
