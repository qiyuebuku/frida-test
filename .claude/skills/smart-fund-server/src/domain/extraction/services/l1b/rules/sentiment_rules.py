"""情绪规则 — ft_sentiment"""
from src.domain.extraction.services.l1b.rule_engine import RuleResult, ThresholdRule


class LimitPoolSurge(ThresholdRule):
    """涨停/跌停潮 — 涨停/跌停数量超过 p95"""
    rule_name = "limit_pool_surge"
    data_source = "ft_sentiment"
    event_type = "macro_data"
    event_subtype = "limit_pool_surge"
    metric_field = "total"
    description = "涨停或跌停数量超过 90 日 95 分位"

    def evaluate(self, record, thresholds):
        if record.get("data_type") != "limit_pool":
            return []
        data = record.get("data") or {}
        info = data.get("info") or []
        total = data.get("total") or len(info)
        if total == 0:
            return []

        p95 = thresholds.get("percentile_95") or 50
        if total <= p95:
            return []

        # 判断涨/跌停方向
        up_count = sum(1 for item in info if (item.get("change") or 0) > 0)
        down_count = total - up_count
        is_limit_up = up_count > down_count

        direction = "positive" if is_limit_up else "negative"
        return [RuleResult(
            fired=True,
            direction=direction,
            strength=min(1.0, total / max(p95, 1) * 0.6),
            evidence={"total": total, "up": up_count, "down": down_count, "p95": p95},
            title_template=f"{'涨停' if is_limit_up else '跌停'}潮：今日 {total} 只个股{'涨停' if is_limit_up else '跌停'}",
            summary_template=f"今日共 {total} 只个股{'涨停' if is_limit_up else '跌停'}（{'涨' if is_limit_up else '跌'} {up_count if is_limit_up else down_count} 只），超过 90 日 95% 分位",
        )]


ALL_SENTIMENT_RULES = [LimitPoolSurge()]
