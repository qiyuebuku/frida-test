"""资金流规则 — ft_market_flow"""
from src.domain.extraction.services.l1b.rule_engine import ThresholdRule


class NorthboundLargeInflow(ThresholdRule):
    """北向资金大额净流入 — net_flow > p95"""
    rule_name = "northbound_large_inflow"
    data_source = "ft_market_flow"
    event_type = "macro_data"
    event_subtype = "northbound_surge"
    metric_field = "net_flow"
    description = "北向资金净流入超过 90 日 95 分位"

    def evaluate(self, record, thresholds):
        if record.get("data_type") != "northbound":
            return []
        results = super().evaluate(record, thresholds)
        for r in results:
            if r.fired and r.direction == "positive":
                val = r.evidence.get("value", 0)
                r.title_template = f"北向资金大幅净流入 {val:.1f} 亿元"
                r.summary_template = f"北向资金今日净流入 {val:.1f} 亿元，超过近 90 日 95% 分位阈值"
            elif r.fired and r.direction == "negative":
                r.title_template = f"北向资金大幅净流出 {abs(r.evidence.get('value', 0)):.1f} 亿元"
                r.summary_template = f"北向资金今日净流出 {abs(r.evidence.get('value', 0)):.1f} 亿元，超过近 90 日 5% 分位阈值"
        return results


class SectorFundAbnormal(ThresholdRule):
    """板块资金异动 — 单板块净流入 z-score > 2"""
    rule_name = "sector_fund_abnormal"
    data_source = "ft_market_flow"
    event_type = "macro_data"
    event_subtype = "sector_fund_abnormal"
    metric_field = "net_amount"
    description = "板块资金净流入/流出超过 90 日 95 分位"

    def evaluate(self, record, thresholds):
        if record.get("data_type") != "sector_flow":
            return []
        results = super().evaluate(record, thresholds)
        data = record.get("data") or {}
        name = data.get("name", "未知板块")
        for r in results:
            if r.fired:
                val = r.evidence.get("value", 0)
                direction_text = "净流入" if val > 0 else "净流出"
                r.title_template = f"{name}板块资金异动：{direction_text} {abs(val):.1f} 亿元"
                r.summary_template = f"{name}板块今日资金{direction_text} {abs(val):.1f} 亿元，触发异常阈值"
        return results


class DragonTigerConcentrated(ThresholdRule):
    """龙虎榜集中交易 — net_amt 超过阈值"""
    rule_name = "dragon_tiger_concentrated"
    data_source = "ft_market_flow"
    event_type = "announcement"
    event_subtype = "dragon_tiger"
    metric_field = "net_amt"
    description = "龙虎榜个股净买入超过阈值"

    def evaluate(self, record, thresholds):
        if record.get("data_type") != "dragon_tiger":
            return []
        results = super().evaluate(record, thresholds)
        data = record.get("data") or {}
        code = data.get("code", "")
        name = data.get("name", "")
        reason = data.get("reason", "")
        for r in results:
            if r.fired:
                val = r.evidence.get("value", 0)
                r.title_template = f"龙虎榜：{name}({code}) 净买入 {val:.1f} 万元"
                r.summary_template = f"{name}({code}) 上榜龙虎榜，净买入 {val:.1f} 万元。原因：{reason}"
                r.evidence["code"] = code
                r.evidence["name"] = name
        return results


ALL_FUND_FLOW_RULES = [NorthboundLargeInflow(), SectorFundAbnormal(), DragonTigerConcentrated()]
