"""关键词前置分流 — 7 类事件的关键词映射"""
from __future__ import annotations

# event_type → 关键词列表
KEYWORD_MAP: dict[str, list[str]] = {
    "policy": [
        "央行", "人民银行", "PBOC", "发改委", "NDRC", "证监会", "CSRC", "银保监",
        "降准", "降息", "加息", "加息", "准备金", "LPR", "逆回购",
        "补贴", "试点", "规划", "纲要", "政策", "改革", "监管", "批复",
        "国务院", "财政部", "商务部", "工信部",
    ],
    "earnings": [
        "净利润", "营收", "业绩", "预告", "业绩快报", "年报", "季报", "半年报",
        "财报", "分红", "扭亏", "预增", "预减", "盈利", "亏损",
    ],
    "m&a": [
        "收购", "并购", "重组", "要约", "易主", "借壳", "私有化", "合并",
        "战略入股", "控股权", "股权收购",
    ],
    "announcement": [
        "股东减持", "增持", "回购", "股权激励", "诉讼", "仲裁",
        "退市", "停牌", "复牌", "质押", "解禁", "定增", "配股",
    ],
    "shock": [
        "爆炸", "事故", "制裁", "地震", "停产", "停产检修", "火灾",
        "疫情", "冲突", "战争", "恐怖", "黑天鹅",
    ],
    "industry": [
        "渗透率", "价格战", "新产品", "出货量", "产能", "供需",
        "涨价", "降价", "库存", "景气", "周期",
    ],
    "macro_data": [
        "CPI", "PPI", "PMI", "GDP", "社融", "M2", "信贷",
        "进出口", "贸易顺差", "贸易逆差", "外汇储备",
        "非农", "美联储", "FOMC", "通胀",
    ],
}

# 构建反向索引: keyword → event_type
_KEYWORD_INDEX: dict[str, str] = {}
for _etype, _keywords in KEYWORD_MAP.items():
    for _kw in _keywords:
        _KEYWORD_INDEX[_kw.lower()] = _etype


def pre_classify(title: str, summary: str = "") -> tuple[str | None, float]:
    """关键词前置分流

    Returns: (event_type | None, confidence)
    - 命中 → (event_type, 0.8)
    - 多类命中 → 取第一个
    - 未命中 → (None, 0.0)
    """
    text = f"{title} {summary}".lower()

    hit_types: dict[str, int] = {}
    for kw, etype in _KEYWORD_INDEX.items():
        if kw in text:
            hit_types[etype] = hit_types.get(etype, 0) + 1

    if not hit_types:
        return None, 0.0

    # 命中关键词最多的类别
    best_type = max(hit_types, key=hit_types.get)
    confidence = min(0.95, 0.6 + hit_types[best_type] * 0.1)
    return best_type, confidence
