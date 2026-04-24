"""宏观指标规则 — ft_macro_indicators"""
from src.domain.extraction.services.l1b.rule_engine import RuleResult, SurpriseRule, ThresholdRule, TrendRule


class CPISurprise(SurpriseRule):
    """CPI 超预期 — |actual - expected| > 0.5σ"""
    rule_name = "cpi_surprise"
    data_source = "ft_macro_indicators"
    event_type = "macro_data"
    event_subtype = "cpi_surprise"
    description = "CPI 同比偏离预期超过 0.5σ"

    def evaluate(self, record, thresholds):
        if record.get("indicator") != "cpi":
            return []
        results = super().evaluate(record, thresholds)
        for r in results:
            if r.fired:
                actual = r.evidence.get("actual", 0)
                expected = r.evidence.get("expected", 0)
                diff = r.evidence.get("diff", 0)
                direction_text = "高于" if diff > 0 else "低于"
                r.title_template = f"CPI 同比 {actual:.1f}% {direction_text}预期"
                r.summary_template = f"CPI 同比 {actual:.1f}%，{direction_text}前值 {expected:.1f}% 达 {abs(diff):.1f} 个百分点"
        return results


class PMICross(ThresholdRule):
    """PMI 穿越 50 线 — 衰退/扩张信号"""
    rule_name = "pmi_cross_50"
    data_source = "ft_macro_indicators"
    event_type = "macro_data"
    event_subtype = "pmi_cross"
    description = "PMI 穿越荣枯线 50"

    def evaluate(self, record, thresholds):
        if record.get("indicator") != "pmi":
            return []
        value = record.get("value")
        prev_value = record.get("prev_value")
        if value is None or prev_value is None:
            return []

        crossed_up = prev_value < 50 <= value
        crossed_down = prev_value >= 50 > value

        if not crossed_up and not crossed_down:
            return []

        direction = "positive" if crossed_up else "negative"
        return [RuleResult(
            fired=True,
            direction=direction,
            strength=0.7,
            evidence={"value": value, "prev_value": prev_value, "cross": "up" if crossed_up else "down"},
            title_template=f"PMI {'重返扩张区间' if crossed_up else '跌破荣枯线'}：{value:.1f}",
            summary_template=f"PMI 从 {prev_value:.1f} {'升至' if crossed_up else '降至'} {value:.1f}，{'重归荣枯线上方' if crossed_up else '跌破 50 荣枯线'}",
        )]


class RateChange(TrendRule):
    """利率变动 — LPR/SHIBOR 连续 N 期同方向"""
    rule_name = "rate_change"
    data_source = "ft_macro_indicators"
    event_type = "policy"
    event_subtype = "rate_change"
    lookback = 3
    description = "LPR/SHIBOR 连续 3 期同方向变动"

    def evaluate(self, record, thresholds):
        indicator = record.get("indicator", "")
        if indicator not in ("lpr_1y", "lpr_5y", "shibor_on", "shibor_1w", "shibor_1m", "shibor_3m"):
            return []

        results = super().evaluate(record, thresholds)
        for r in results:
            if r.fired:
                period = record.get("period", "")
                value = record.get("value", 0)
                name_map = {
                    "lpr_1y": "1年期LPR", "lpr_5y": "5年期LPR",
                    "shibor_on": "隔夜Shibor", "shibor_1w": "1周Shibor",
                    "shibor_1m": "1月Shibor", "shibor_3m": "3月Shibor",
                }
                name = name_map.get(indicator, indicator)
                direction_text = "上行" if r.direction == "positive" else "下行"
                r.title_template = f"{name}连续{self.lookback}期{direction_text}：{value:.2f}%"
                r.summary_template = f"{name}({period})报 {value:.2f}%，已连续 {self.lookback} 期{direction_text}"
        return results


ALL_MACRO_RULES = [CPISurprise(), PMICross(), RateChange()]
