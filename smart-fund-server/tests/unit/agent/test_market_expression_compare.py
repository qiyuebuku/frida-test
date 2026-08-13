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
