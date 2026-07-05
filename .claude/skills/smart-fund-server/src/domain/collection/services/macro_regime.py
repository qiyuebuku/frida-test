"""宏观 Regime 信号合成引擎

读取 ft_macro_indicators 最新值 → 五维度加权打分 → 输出 regime + multiplier
写入 ft_macro_regime (每日 UPSERT)
"""
import logging
import math
from datetime import date, datetime, timedelta, timezone

from src.infrastructure.time_utils import app_today

logger = logging.getLogger(__name__)


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


# 每个指标的打分规则: (indicator, weight_within_dim, scoring_fn)
# scoring_fn 输入最新 row (含 value/yoy/mom/prev_value)，输出 [-1, +1]
_RULES: dict[str, list[tuple[str, float, object]]] = {
    "liquidity": [
        ("m2",                0.25, lambda r: _clip((r["yoy"] - 8) / 3) if r.get("yoy") is not None else 0),
        ("social_financing",  0.20, lambda r: _clip((r["yoy"] - 0) / 30) if r.get("yoy") is not None else 0),
        ("rmb_loan",          0.15, lambda r: _clip(r["yoy"] / 20) if r.get("yoy") is not None else 0),
        ("shibor_on",         0.15, lambda r: _clip((2.0 - r["value"]) / 1.0)),
        ("lpr_1y",            0.10, lambda r: _clip((3.45 - r["value"]) / 0.5)),
        ("omo_net",           0.15, lambda r: _clip(r["value"] / 3000)),
    ],
    "growth": [
        ("pmi",            0.25, lambda r: _clip((r["value"] - 50) / 2)),
        ("gdp",            0.20, lambda r: _clip((r["yoy"] - 5) / 2) if r.get("yoy") is not None else 0),
        ("industrial_va",  0.20, lambda r: _clip((r["yoy"] - 5) / 3) if r.get("yoy") is not None else 0),
        ("retail_sales",   0.15, lambda r: _clip((r["yoy"] - 5) / 3) if r.get("yoy") is not None else 0),
        ("fai",            0.20, lambda r: _clip((r["yoy"] - 4) / 3) if r.get("yoy") is not None else 0),
    ],
    "inflation": [
        ("cpi",            0.50, lambda r: _clip(1 - abs(r["yoy"] - 2.0) / 3) if r.get("yoy") is not None else 0),
        ("ppi",            0.50, lambda r: _clip(1 - abs(r["yoy"]) / 5) if r.get("yoy") is not None else 0),
    ],
    "external": [
        ("usdcny",         0.35, lambda r: _clip((7.3 - r["value"]) / 0.2)),
        ("cnus_spread_10y",0.25, lambda r: _clip(r["value"] / 2)),
        ("us_cpi_yoy",     0.15, lambda r: _clip((2.0 - r["value"]) / 2) if r.get("value") is not None else 0),
        ("forex_reserve",  0.15, lambda r: _clip(r["mom"] / 2) if r.get("mom") else 0),
        ("customs_export", 0.10, lambda r: _clip(r["yoy"] / 10) if r.get("yoy") else 0),
    ],
}

# 维度权重
_DIM_WEIGHTS = {
    "liquidity": 0.30,
    "growth":    0.25,
    "inflation": 0.15,
    "external":  0.15,
    "policy":    0.15,
}

# policy 维度关键词
_MACRO_POS_KW = ["降准", "降息", "减税", "刺激", "扩内需", "宽松", "再贷款", "定向", "支持"]
_MACRO_NEG_KW = ["加息", "从严", "整顿", "监管", "收紧", "反垄断", "限制", "处罚"]

# 公司级公告噪声模式（匹配到任一项则跳过，不参与政策打分）
_NOISE_PATTERNS = [
    "*ST", "ST", "退市", "涨停", "跌停",
    "净利同比预", "业绩公告", "季报", "年报",
    "控股股东", "股东拟减持", "股东减持",
    "授权许可协议",
    "药品注册", "临床试验批准",
]


class MacroRegimeEngine:
    def recompute(self) -> dict:
        from src.infrastructure.persistence.repositories import MacroRepositoryImpl
        repo = MacroRepositoryImpl()

        latest = {row["indicator"]: row for row in repo.latest_per_indicator()}
        contributors: dict[str, list] = {}
        dim_scores: dict[str, float] = {}

        for dim, rules in _RULES.items():
            total_w, weighted_sum = 0.0, 0.0
            items = []
            for ind, w, fn in rules:
                row = latest.get(ind)
                if not row or row.get("value") is None:
                    continue
                try:
                    s = float(fn(row))
                except Exception:
                    continue
                s = max(-1.0, min(1.0, s))
                weighted_sum += s * w
                total_w += w
                items.append({
                    "indicator": ind,
                    "value": None if isinstance(row["value"], float) and math.isnan(row["value"]) else row["value"],
                    "yoy": None if isinstance(row.get("yoy"), float) and math.isnan(row.get("yoy")) else row.get("yoy"),
                    "score": round(s, 3),
                    "weight": w,
                })
            dim_scores[dim] = (weighted_sum / total_w) if total_w > 0 else 0.0
            contributors[dim] = items

        # policy 维度
        dim_scores["policy"], contributors["policy"] = self._policy_score()

        overall = sum(dim_scores.get(d, 0) * _DIM_WEIGHTS[d] for d in _DIM_WEIGHTS)
        overall = max(-1.0, min(1.0, overall))

        if overall >= 0.3:
            regime = "risk_on"
        elif overall <= -0.3:
            regime = "risk_off"
        else:
            regime = "neutral"

        # [-1, +1] → [0.6, 1.2]
        multiplier = round(0.9 + overall * 0.3, 3)
        multiplier = max(0.6, min(1.2, multiplier))

        snapshot = {
            "snapshot_date": app_today(),
            "regime": regime,
            "overall_score": round(overall, 3),
            "multiplier": multiplier,
            "liquidity_score": round(dim_scores.get("liquidity", 0), 3),
            "growth_score": round(dim_scores.get("growth", 0), 3),
            "inflation_score": round(dim_scores.get("inflation", 0), 3),
            "external_score": round(dim_scores.get("external", 0), 3),
            "policy_score": round(dim_scores.get("policy", 0), 3),
            "contributors": contributors,
        }
        repo.upsert_regime(snapshot)
        logger.info(
            "regime recomputed: %s score=%.3f mul=%.3f",
            regime, overall, multiplier,
        )
        return snapshot

    def _policy_score(self) -> tuple[float, list]:
        """从 ft_news 近 7 天 macro/policy 类新闻计算政策方向分

        v1: 关键词统计 + 公司公告噪声过滤。
        TODO: 后续可替换为 Knowledge / Cognitive Card 的政策方向信号。
        """
        try:
            from src.infrastructure.persistence.repositories import NewsRepositoryImpl
            repo = NewsRepositoryImpl()
            since = datetime.now(timezone.utc) - timedelta(days=7)
            news = repo.query_by_category(["macro", "policy"], since)
        except Exception:
            return 0.0, []

        pos = neg = 0
        items = []
        for n in news:
            title = (n.get("title") or "") + " " + (n.get("summary") or "")
            # 跳过公司级公告噪声
            if any(p in title for p in _NOISE_PATTERNS):
                continue
            p = sum(1 for kw in _MACRO_POS_KW if kw in title)
            q = sum(1 for kw in _MACRO_NEG_KW if kw in title)
            if p > q:
                pos += 1
                items.append({"title": n["title"], "dir": "+", "score": 1})
            elif q > p:
                neg += 1
                items.append({"title": n["title"], "dir": "-", "score": -1})

        raw = (pos - neg) / 5.0
        score = max(-1.0, min(1.0, raw))
        return score, items[:20]
