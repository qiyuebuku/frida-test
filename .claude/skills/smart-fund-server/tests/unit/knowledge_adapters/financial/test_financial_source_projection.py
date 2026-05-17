"""Tests for financial Source Record projection helpers."""

from datetime import date, datetime, timezone

from src.domain.knowledge_adapters.financial.source_projection import (
    explain_projection_skip,
    project_ft_macro_indicator_row,
    project_ft_market_cache_row,
    project_ft_market_flow_row,
    project_ft_news_row,
    project_ft_sentiment_row,
)


def test_ft_news_projection_maps_trace_metadata_and_mentions() -> None:
    record = project_ft_news_row(
        {
            "id": 1,
            "title": "宁德时代技术发布会简析",
            "summary": "",
            "content": "",
            "source": "ths",
            "source_name": "同花顺",
            "source_reliability": 0.7,
            "category": "company",
            "url": "https://example.test",
            "tags": ["快充"],
            "related_stocks": ["300750"],
            "published_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "fingerprint": "fp",
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        },
        stock_names={"300750": "宁德时代"},
    )

    assert record is not None
    assert record["source_type"] == "news_articles"
    assert record["source_id"] == "ft_news:1"
    assert record["raw_text"] == "宁德时代技术发布会简析"
    assert record["metadata"]["source_table"] == "ft_news"
    assert record["metadata"]["source_type_reason"] == "category_company"
    assert record["payload"]["mentioned_entities"] == [
        {"type": "stock", "exchange": "SZ", "code": "300750", "name": "宁德时代", "confidence": 0.7}
    ]
    assert record["metadata"]["tags"] == ["快充"]
    assert record["metadata"]["weak_entity_hints"] == [
        {"kind": "tag", "value": "快充", "confidence": 0.25, "in_title": False, "in_text": False}
    ]


def test_ft_market_flow_projection_outputs_derived_signal() -> None:
    record = project_ft_market_flow_row(
        {
            "id": 2,
            "data_type": "stock_flow",
            "trade_date": date(2026, 5, 1),
            "data": {"code": "300750", "net_amount": 123.4},
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        }
    )

    assert record is not None
    assert record["source_type"] == "derived_signal"
    assert record["payload"]["target_ref"] == "stock:SZ:300750"
    assert record["payload"]["signal_type"] == "market_flow.stock_flow"
    assert record["payload"]["value"] == 123.4
    assert record["metadata"]["signal_source_type"] == "market_flow.stock_flow"


def test_ft_market_flow_projection_supports_dragon_tiger_net_amt() -> None:
    record = project_ft_market_flow_row(
        {
            "id": 22,
            "data_type": "dragon_tiger",
            "trade_date": date(2026, 5, 1),
            "data": {"code": "001267", "name": "汇绿生态", "net_amt": 263928288.77, "buy_amt": 826967246.62},
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        }
    )

    assert record is not None
    assert record["payload"]["target_ref"] == "stock:SZ:001267"
    assert record["payload"]["signal_type"] == "market_flow.dragon_tiger"
    assert record["payload"]["value"] == 263928288.77
    assert record["metadata"]["value_path"] == "net_amt"


def test_ft_market_cache_projection_keeps_snapshot_metadata() -> None:
    record = project_ft_market_cache_row(
        {
            "id": 3,
            "data_type": "index_quote",
            "data": {"name": "沪深300", "value": 3888.0},
            "expires_at": datetime(2026, 5, 1, 10, 0),
            "created_at": datetime(2026, 5, 1, 9, 30),
        }
    )

    assert record is not None
    assert record["source_id"] == "ft_market_cache:index_quote:3"
    assert record["metadata"]["expires_at"] == "2026-05-01T10:00:00"


def test_ft_market_cache_projection_supports_nested_sector_ranking() -> None:
    record = project_ft_market_cache_row(
        {
            "id": 33,
            "data_type": "sector_ranking",
            "data": {
                "status_code": 0,
                "data": {
                    "total": 175,
                    "topRise": [{"name": "固态电池", "changeRate": 4.9}],
                    "topFall": [{"name": "猪肉", "changeRate": -1.1}],
                },
            },
            "expires_at": datetime(2026, 5, 1, 10, 0),
            "created_at": datetime(2026, 5, 1, 9, 30),
        }
    )

    assert record is not None
    assert record["payload"]["target_ref"] == "macro_indicator:market_snapshot.sector_ranking"
    assert record["payload"]["value"] == 175
    assert record["metadata"]["value_path"] == "data.total"


def test_ft_sentiment_projection_outputs_derived_signal() -> None:
    record = project_ft_sentiment_row(
        {
            "id": 4,
            "data_type": "market_temperature",
            "trade_date": date(2026, 5, 1),
            "data": {"score": 72},
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        }
    )

    assert record is not None
    assert record["payload"]["signal_type"] == "sentiment.market_temperature"
    assert record["payload"]["value"] == 72


def test_ft_sentiment_projection_supports_root_list_hot_topics() -> None:
    record = project_ft_sentiment_row(
        {
            "id": 44,
            "data_type": "xueqiu_hot_topics",
            "trade_date": date(2026, 5, 1),
            "data": [{"tag": "AI", "discussions": 10}, {"tag": "固态电池", "discussions": 6}],
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        }
    )

    assert record is not None
    assert record["payload"]["target_ref"] == "macro_indicator:sentiment.xueqiu_hot_topics"
    assert record["payload"]["value"] == 16
    assert record["metadata"]["value_path"] == "[].discussions.sum"


def test_ft_macro_indicator_projection_outputs_macro_target() -> None:
    record = project_ft_macro_indicator_row(
        {
            "id": 5,
            "indicator": "pmi",
            "period": "2026-04",
            "value": 50.4,
            "unit": "%",
            "source": "stats",
            "published_at": date(2026, 5, 1),
            "dim_tag": "growth",
            "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
        }
    )

    assert record is not None
    assert record["source_id"] == "ft_macro_indicators:pmi:2026-04:stats"
    assert record["payload"]["target_ref"] == "macro_indicator:pmi"
    assert record["payload"]["unit"] == "%"


def test_structured_projection_returns_none_when_required_value_missing() -> None:
    assert project_ft_market_flow_row({"id": 1, "data_type": "stock_flow", "data": {}}) is None


def test_structured_projection_skip_reason_explains_missing_value() -> None:
    skip = explain_projection_skip(
        "ft_market_flow",
        {
            "id": 1,
            "data_type": "stock_flow",
            "trade_date": date(2026, 5, 1),
            "data": {"code": "300750"},
        },
    )

    assert skip["reason"] == "missing_value"
    assert skip["data_type"] == "stock_flow"
