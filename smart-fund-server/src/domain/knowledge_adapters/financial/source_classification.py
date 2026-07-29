"""Deterministic source classification for financial news Source Records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

FinancialNewsSourceType = Literal["news_articles", "policy_news"]


@dataclass(frozen=True)
class SourceTypeClassification:
    source_type: FinancialNewsSourceType
    reason: str
    matched_rules: list[str] = field(default_factory=list)
    confidence: float = 0.5
    uncertain: bool = False
    candidates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_metadata(self) -> dict[str, Any]:
        metadata = {
            "source_type_reason": self.reason,
            "source_type_matched_rules": list(self.matched_rules),
            "source_type_confidence": self.confidence,
            "source_type_uncertain": self.uncertain,
            "source_type_candidates": list(self.candidates),
        }
        if self.warnings:
            metadata["source_type_warnings"] = list(self.warnings)
        return metadata


POLICY_CATEGORIES = {"policy", "macro"}
NEWS_CATEGORIES = {"company", "industry", "market", "global", "stock", "fund", "research"}

OFFICIAL_SOURCE_KEYWORDS = {
    "中共中央",
    "国务院",
    "国务院办公厅",
    "中央财经委员会",
    "中央金融委员会",
    "中央金融办",
    "全国人大",
    "全国政协",
    "发改委",
    "国家发展改革委",
    "财政部",
    "工信部",
    "工业和信息化部",
    "商务部",
    "科技部",
    "住建部",
    "交通运输部",
    "自然资源部",
    "生态环境部",
    "农业农村部",
    "教育部",
    "人社部",
    "卫健委",
    "应急管理部",
    "人民银行",
    "中国人民银行",
    "央行",
    "金融监管总局",
    "银保监会",
    "证监会",
    "外汇局",
    "交易商协会",
    "基金业协会",
    "证券业协会",
    "期货业协会",
    "上交所",
    "深交所",
    "北交所",
    "港交所",
    "中金所",
    "上期所",
    "郑商所",
    "大商所",
    "广期所",
    "中国结算",
    "国家统计局",
    "海关总署",
    "税务总局",
    "国家医保局",
    "国家能源局",
    "国家粮储局",
    "省政府",
    "市政府",
    "地方发改委",
    "地方工信厅",
    "地方金融监管局",
    "管委会",
    "美联储",
    "欧洲央行",
    "日本央行",
    "英国央行",
    "美国财政部",
    "美国商务部",
    "SEC",
    "CFTC",
    "IMF",
    "世界银行",
    "WTO",
    "OPEC",
    "IEA",
}

NEWS_SOURCE_KEYWORDS = {
    "同花顺",
    "东方财富",
    "财联社",
    "新浪财经",
    "新浪港股",
    "新浪基金",
    "新浪证券",
    "新浪科技",
    "雪球",
    "Wind",
    "Choice",
    "滚动播报",
    "市场资讯",
    "环球市场播报",
    "证券时报",
    "证券日报",
    "中国证券报",
    "上海证券报",
    "中证网",
    "每日经济新闻",
    "21世纪经济报道",
    "第一财经",
    "一财网",
    "界面",
    "澎湃",
    "蓝鲸财经",
    "财新",
    "财经网",
    "券商中国",
    "中国基金报",
    "金融一线",
    "智通财经",
    "新华社",
    "新华视点",
    "央视",
    "央视新闻",
    "央广财经",
    "中国新闻网",
    "中新经纬",
    "新京报",
    "环球网",
    "IT之家",
    "快科技",
    "科创板日报",
    "上海有色网",
    "期货日报",
    "爱集微",
    "盖世汽车",
}

BROKER_SOURCE_KEYWORDS = {
    "中信证券",
    "东吴证券",
    "华泰证券",
    "招商证券",
    "广发证券",
    "天风证券",
    "申万宏源",
    "国泰君安",
    "海通证券",
    "兴业证券",
    "国金证券",
    "国盛证券",
}

POLICY_ACTION_KEYWORDS = {
    "发布",
    "印发",
    "出台",
    "下发",
    "公布",
    "颁布",
    "施行",
    "实施",
    "修订",
    "废止",
    "审议通过",
    "批准",
    "决定",
    "部署",
    "明确",
    "要求",
    "规范",
    "加强",
    "支持",
    "推进",
    "征求意见",
    "公开征求意见",
    "征求意见稿",
    "反馈意见",
    "答记者问",
}

POLICY_DOCUMENT_KEYWORDS = {
    "通知",
    "意见",
    "办法",
    "规定",
    "条例",
    "细则",
    "指引",
    "指南",
    "规划",
    "方案",
    "行动计划",
    "工作方案",
    "实施方案",
    "若干措施",
    "管理办法",
    "监管规则",
    "业务规则",
    "标准",
    "目录",
    "清单",
    "白皮书",
    "公告",
    "决定",
}

POLICY_TOPIC_KEYWORDS = {
    "资本市场改革",
    "并购重组",
    "注册制",
    "退市制度",
    "减持规则",
    "分红监管",
    "融资融券",
    "再融资",
    "降准",
    "降息",
    "LPR",
    "MLF",
    "货币政策",
    "财政政策",
    "产业政策",
    "房地产政策",
    "出口管制",
    "反倾销",
    "制裁",
    "关税",
}

POLICY_SUBJECT_KEYWORDS = {
    "两部门",
    "三部门",
    "多部门",
    "有关部门",
    "监管部门",
    "国家",
    "中央",
}

NEGATIVE_NEWS_KEYWORDS = {
    "午评",
    "收评",
    "盘中",
    "开盘",
    "尾盘",
    "涨停",
    "跌停",
    "异动",
    "主力资金",
    "资金流向",
    "成交额",
    "业绩预告",
    "年报点评",
    "季报点评",
    "订单",
    "产能",
    "回购",
    "减持",
    "增持",
    "调研",
    "评级",
    "目标价",
    "点评",
    "简评",
    "深度",
    "策略",
    "周报",
    "月报",
    "展望",
    "看好",
    "建议关注",
    "冲突",
    "战争",
    "军演",
    "谈判",
    "选举",
    "外交表态",
    "事故",
    "自然灾害",
}

BAD_SOURCE_NAMES = {"", "BUG", "NONE", "NULL", "N/A", "-"}


def classify_news_source_type(row: dict[str, Any]) -> SourceTypeClassification:
    category = _norm(row.get("category")).lower()
    source_text = _source_text(row)
    body_text = _body_text(row)
    title_text = str(row.get("title") or "")
    warnings: list[str] = []

    if category in POLICY_CATEGORIES:
        return SourceTypeClassification(
            source_type="policy_news",
            reason=f"category_{category}",
            matched_rules=[f"category:{category}"],
            confidence=0.95,
        )
    if category in NEWS_CATEGORIES:
        return SourceTypeClassification(
            source_type="news_articles",
            reason=f"category_{category}",
            matched_rules=[f"category:{category}"],
            confidence=0.9,
        )

    if _norm(row.get("source_name")).upper() in BAD_SOURCE_NAMES and _norm(row.get("source")).upper() in BAD_SOURCE_NAMES:
        warnings.append("bad_source_name")

    official_hits = _hits(source_text, OFFICIAL_SOURCE_KEYWORDS)
    news_source_hits = _hits(source_text, NEWS_SOURCE_KEYWORDS)
    broker_hits = _hits(source_text, BROKER_SOURCE_KEYWORDS)
    policy_action_hits = _hits(title_text + body_text[:500], POLICY_ACTION_KEYWORDS)
    policy_doc_hits = _hits(title_text + body_text[:500], POLICY_DOCUMENT_KEYWORDS)
    policy_topic_hits = _hits(title_text + body_text[:500], POLICY_TOPIC_KEYWORDS)
    policy_subject_hits = _hits(title_text + body_text[:500], POLICY_SUBJECT_KEYWORDS)
    negative_hits = _hits(title_text, NEGATIVE_NEWS_KEYWORDS)

    if official_hits:
        return SourceTypeClassification(
            source_type="policy_news",
            reason="official_source",
            matched_rules=[f"official_source:{item}" for item in official_hits],
            confidence=0.88,
            warnings=warnings,
        )

    if broker_hits:
        return SourceTypeClassification(
            source_type="news_articles",
            reason="broker_source_default_news",
            matched_rules=[f"broker_source:{item}" for item in broker_hits],
            confidence=0.86,
            warnings=warnings,
        )

    policy_score = len(policy_action_hits) + len(policy_doc_hits) + len(policy_topic_hits) + len(policy_subject_hits)
    strong_policy_text = bool(policy_subject_hits and (policy_action_hits or policy_doc_hits))
    non_media_policy_text = bool(policy_action_hits and policy_doc_hits and not news_source_hits)
    if (strong_policy_text or non_media_policy_text) and not negative_hits:
        matched = (
            [f"policy_action:{item}" for item in policy_action_hits]
            + [f"policy_document:{item}" for item in policy_doc_hits]
            + [f"policy_topic:{item}" for item in policy_topic_hits]
            + [f"policy_subject:{item}" for item in policy_subject_hits]
        )
        return SourceTypeClassification(
            source_type="policy_news",
            reason="policy_text_pattern",
            matched_rules=matched,
            confidence=0.76 if news_source_hits else 0.82,
            uncertain=bool(news_source_hits),
            candidates=_candidates(policy_score=policy_score, negative_hits=negative_hits),
            warnings=warnings,
        )

    if negative_hits:
        return SourceTypeClassification(
            source_type="news_articles",
            reason="negative_news_pattern",
            matched_rules=[f"negative_news:{item}" for item in negative_hits],
            confidence=0.82,
            warnings=warnings,
        )

    if news_source_hits:
        return SourceTypeClassification(
            source_type="news_articles",
            reason="news_source_default",
            matched_rules=[f"news_source:{item}" for item in news_source_hits],
            confidence=0.72,
            uncertain=policy_score > 0,
            candidates=_candidates(policy_score=policy_score, negative_hits=negative_hits),
            warnings=warnings,
        )

    return SourceTypeClassification(
        source_type="news_articles",
        reason="uncertain_default_news",
        matched_rules=[],
        confidence=0.55,
        uncertain=True,
        candidates=_candidates(policy_score=policy_score, negative_hits=negative_hits),
        warnings=warnings,
    )


def _source_text(row: dict[str, Any]) -> str:
    return f"{row.get('source') or ''} {row.get('source_name') or ''}"


def _body_text(row: dict[str, Any]) -> str:
    return f"{row.get('summary') or ''}\n{row.get('content') or ''}"


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _hits(text: str, keywords: set[str]) -> list[str]:
    if not text:
        return []
    return sorted(keyword for keyword in keywords if keyword and keyword in text)


def _candidates(*, policy_score: int, negative_hits: list[str]) -> list[dict[str, Any]]:
    return [
        {"source_type": "policy_news", "score": policy_score},
        {"source_type": "news_articles", "score": len(negative_hits)},
    ]
