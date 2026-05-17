"""Tests for deterministic financial news source classification."""

from src.domain.knowledge_adapters.financial.source_classification import (
    classify_news_source_type,
)


def test_category_policy_is_policy_news() -> None:
    result = classify_news_source_type({"category": "policy", "title": "政策发布"})

    assert result.source_type == "policy_news"
    assert result.reason == "category_policy"
    assert "category:policy" in result.matched_rules


def test_category_company_is_news_articles() -> None:
    result = classify_news_source_type({"category": "company", "title": "公司订单增长"})

    assert result.source_type == "news_articles"
    assert result.reason == "category_company"


def test_official_source_is_policy_news_without_category() -> None:
    result = classify_news_source_type(
        {"source_name": "中国人民银行", "title": "公开市场业务交易公告"}
    )

    assert result.source_type == "policy_news"
    assert result.reason == "official_source"


def test_media_policy_title_can_be_policy_news_when_action_and_subject_match() -> None:
    result = classify_news_source_type(
        {
            "source_name": "同花顺",
            "title": "两部门联合发布促进资本市场改革若干措施",
            "category": "",
        }
    )

    assert result.source_type == "policy_news"
    assert result.reason == "policy_text_pattern"
    assert any(rule.startswith("policy_action:") for rule in result.matched_rules)


def test_media_market_topic_is_not_promoted_to_policy_news() -> None:
    result = classify_news_source_type(
        {
            "source_name": "证券日报",
            "title": "A股并购重组市场呈现三方面新变化",
            "category": "",
        }
    )

    assert result.source_type == "news_articles"
    assert result.reason == "news_source_default"


def test_geopolitical_news_defaults_to_news_articles() -> None:
    result = classify_news_source_type(
        {
            "source_name": "同花顺",
            "title": "俄就波法联合军演发出警告",
            "category": "",
        }
    )

    assert result.source_type == "news_articles"
    assert result.reason == "negative_news_pattern"
