from src.application.services.realtime_instrument_research_service import (
    _expression_projection,
)


def test_expression_projection_keeps_comparable_fields_and_holdings() -> None:
    result = _expression_projection({
        "code": "588000",
        "quote": {"name": "科创50ETF", "latest": 1.2, "turnover_yuan": 9000000},
        "fund_overview": {"data": {"trackingIndex": "科创50", "scale": "500亿"}},
        "performance": {"data": {"maxDrawdown": -18.2, "yearReturn": 12.5}},
        "holdings": {"top10": [{"stockCode": "688981"}, {"stockCode": "688256"}]},
        "evidence_locator": "market:v1:test",
    })

    assert result["tracking_index"] == "科创50"
    assert result["max_drawdown"] == -18.2
    assert result["top_holding_codes"] == ["688981", "688256"]


def test_expression_projection_does_not_treat_parent_fund_as_holding() -> None:
    result = _expression_projection({
        "code": "159363",
        "holdings": {"data": {"stock": [{
            "code": "159363",
            "secCode": "300502",
            "secName": "新易盛",
            "fundNavRate": 15.16,
        }]}},
    })

    assert result["top_holding_codes"] == ["300502"]
    assert result["top_holdings"] == [
        {"code": "300502", "name": "新易盛", "weight_pct": 15.16}
    ]
