from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "app/src/main/java/com/yuyang/thshook/MainHook.java"
)


def test_order_executor_routes_shanghai_and_shenzhen_explicitly() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "tradeMarketRouteForCode" in source
    assert "prefix == '5' || prefix == '6'" in source
    assert 'return "11"; // 上海' in source
    assert "prefix == '0' || prefix == '1' || prefix == '2' || prefix == '3'" in source
    assert 'return "24"; // 深圳' in source
    assert '"36670", marketRoute' in source
    assert "unsupported or ambiguous security market" in source


def test_bse_route_comes_from_official_broker_prequery() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "queryOfficialTradeMarketRoute" in source
    assert "invokeTradeQuery(1804, 2682, params, true, true)" in source
    assert 'optString("36670"' in source
    assert 'return "27".equals(officialRoute) ? officialRoute : null' in source
