"""THSTradeClient + TradeAccountProjectionService 单元测试。

设备端契约见逆向交接说明 §3.8/3.11（MainHook 18900 端点）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from src.application.services.trade_account_projection_service import (
    TradeAccountProjectionService,
)
from src.infrastructure.clients.ths_trade import (
    THSTradeClient,
    THSTradeError,
)

CUTOFF = datetime(2026, 8, 17, tzinfo=UTC)

FUNDS_PAYLOAD = {
    "query": "proto_1807",
    "pageId": 2605,
    "ok": True,
    "elapsed_ms": 1798,
    "data": {
        "struct": "StuffTableStruct",
        "frames": 1,
        "fields": {
            "total_assets": "3139.80",
            "float_profit": "0.00",
            "available_amount": "3139.80",
            "total_market_value": "0.00",
            "withdrawable_amount": "3139.80",
        },
    },
}

POSITIONS_EMPTY_PAYLOAD = {
    "query": "proto_1891",
    "pageId": 2606,
    "ok": True,
    "elapsed_ms": 1865,
    "data": {
        "struct": "StuffTableStruct",
        "caption": "股票列表",
        "columns": ["名称", "盈亏", "市值", "盈亏率", "成本", "现价", "数量"],
        "rows": [],
    },
}

POSITIONS_HOLDING_PAYLOAD = {
    "query": "proto_1891",
    "ok": True,
    "elapsed_ms": 1900,
    "data": {
        "struct": "StuffTableStruct",
        "key_columns": ["名称", "盈亏", "市值", "盈亏率", "成本", "现价", "数量", "代码"],
        "records": [
            {
                "名称": "恒生科技ETF大成",
                "盈亏": "-3.00",
                "市值": "59.00",
                "盈亏率": "-4.84%",
                "成本": "0.620",
                "现价": "0.590",
                "数量": "100",
                "代码": "159740",
            }
        ],
    },
}


def make_client(handler, **kwargs) -> THSTradeClient:
    """构造走 httpx.MockTransport 的客户端（不触网）。"""
    client = THSTradeClient(base_url="http://device.test", **kwargs)
    transport = httpx.MockTransport(handler)
    client._client = httpx.Client(transport=transport, timeout=client._timeout)
    return client


# ----------------------------------------------------------------------
# THSTradeClient：只读 + 缓存
# ----------------------------------------------------------------------


def test_read_query_returns_payload_and_caches_ok_result() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=FUNDS_PAYLOAD)

    client = make_client(handler, read_cache_ttl=60)
    first = client.funds()
    second = client.funds()

    assert first["ok"] is True
    assert second.get("from_cache") is True
    assert len(calls) == 1
    assert calls[0] == "/stock/trade/query"


def test_read_cache_expires_after_ttl() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FUNDS_PAYLOAD)

    client = make_client(handler, read_cache_ttl=0)
    client.funds()
    # TTL=0 → 不缓存
    result = client.funds()
    assert "from_cache" not in result


def test_read_error_is_not_cached() -> None:
    state = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            return httpx.Response(
                200,
                json={
                    "query": "proto_1807",
                    "ok": False,
                    "error": "trade account not logged in after silent relogin attempts",
                },
            )
        return httpx.Response(200, json=FUNDS_PAYLOAD)

    client = make_client(handler, read_cache_ttl=60)
    with pytest.raises(THSTradeError) as exc_info:
        client.funds()
    assert exc_info.value.reason_code == "trade_account_not_logged_in"

    state["fail"] = False
    result = client.funds()
    assert result["ok"] is True


def test_unknown_query_name_rejected() -> None:
    client = make_client(lambda request: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        client._read_query("no_such_query")


def test_device_unreachable_maps_to_reason_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = make_client(handler)
    with pytest.raises(THSTradeError) as exc_info:
        client.positions()
    assert exc_info.value.reason_code == "trade_endpoint_unreachable"


# ----------------------------------------------------------------------
# THSTradeClient：写端点门控
# ----------------------------------------------------------------------


def test_submit_order_requires_confirm() -> None:
    client = make_client(lambda request: httpx.Response(200, json={}))
    with pytest.raises(THSTradeError) as exc_info:
        client.submit_order(action="buy", code="159740", price="0.588", qty="100")
    assert exc_info.value.reason_code == "trade_confirm_required"


def test_submit_order_rejects_unknown_action() -> None:
    client = make_client(lambda request: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        client.submit_order(
            action="reverse_split", code="159740", price="1", qty="1", confirm=True
        )


def test_submit_order_sends_confirmed_body() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"endpoint": "order", "ok": True, "business_ok": True},
        )

    client = make_client(handler)
    result = client.submit_order(
        action="buy", code="159740", price="0.588", qty="100", confirm=True
    )
    assert captured["path"] == "/stock/trade/order"
    assert captured["body"] == {
        "action": "buy",
        "code": "159740",
        "price": "0.588",
        "qty": "100",
        "confirm": "true",
    }
    assert result["ok"] is True


def test_cancel_order_requires_confirm_and_six_part_entry() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"endpoint": "cancel", "ok": True, "business_ok": True}
        )

    client = make_client(handler)
    with pytest.raises(THSTradeError):
        client.cancel_order(
            entrust_no="1404",
            stock_code="159740",
            stock_name="恒生科技ETF大成",
            market_code="1",
            shareholder_account="0926764077",
        )
    result = client.cancel_order(
        entrust_no="1404",
        stock_code="159740",
        stock_name="恒生科技ETF大成",
        market_code="1",
        shareholder_account="0926764077",
        withdrawable_qty="100",
        confirm=True,
    )
    assert captured["body"]["confirm"] == "true"
    assert captured["body"]["entrust_no"] == "1404"
    assert result["business_ok"] is True


def test_transfer_disabled_by_default() -> None:
    client = make_client(lambda request: httpx.Response(200, json={}))
    with pytest.raises(THSTradeError) as exc_info:
        client.transfer(amount="100", bank_password="x", confirm=True)
    assert exc_info.value.reason_code == "trade_transfer_disabled"


def test_business_error_payload_maps_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "endpoint": "order",
                "ok": False,
                "error": "[251005]可用数量不足",
            },
        )

    client = make_client(handler)
    with pytest.raises(THSTradeError) as exc_info:
        client.submit_order(
            action="sell", code="159740", price="0.6", qty="100", confirm=True
        )
    assert exc_info.value.reason_code == "trade_endpoint_error"
    assert "251005" in str(exc_info.value)


def test_serial_lock_covers_request_roundtrip() -> None:
    """全部请求经全局锁（并发下 handler 观察不到交错请求）。"""
    active = {"count": 0, "max": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        active["count"] += 1
        active["max"] = max(active["max"], active["count"])
        active["count"] -= 1
        return httpx.Response(200, json=FUNDS_PAYLOAD)

    client = make_client(handler, read_cache_ttl=0)
    import threading

    threads = [
        threading.Thread(target=client.funds) for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert active["max"] == 1


# ----------------------------------------------------------------------
# TradeAccountProjectionService
# ----------------------------------------------------------------------


def test_exposure_summary_available_with_holdings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("name")
        if name == "positions":
            return httpx.Response(200, json=POSITIONS_HOLDING_PAYLOAD)
        return httpx.Response(200, json=FUNDS_PAYLOAD)

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.exposure_summary(
        cutoff_at=CUTOFF, account_ids=("account-1",)
    )
    assert result["status"] == "available"
    assert result["position_count"] == 1
    assert result["funds"]["total_assets"] == 3139.80
    assert result["summary"]["total_market_value"] == 0.00
    assert result["summary"]["cash_ratio"] == 1.0
    assert result["source"]["provider"] == "ths_trade_sdk"


def test_exposure_summary_empty_positions_is_available() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("name")
        if name == "positions":
            return httpx.Response(200, json=POSITIONS_EMPTY_PAYLOAD)
        return httpx.Response(200, json=FUNDS_PAYLOAD)

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.exposure_summary(cutoff_at=CUTOFF, account_ids=())
    assert result["status"] == "available"
    assert result["position_count"] == 0
    assert result["positions"] == []


def test_exposure_summary_unavailable_when_not_logged_in() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": False,
                "error": "trade account not logged in after silent relogin attempts",
            },
        )

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.exposure_summary(
        cutoff_at=CUTOFF, account_ids=("account-1",)
    )
    assert result["status"] == "unavailable"
    assert result["reason_code"] == "trade_account_not_logged_in"
    assert "券商账户端点当前不可用" in result["reason"]


def test_exposure_summary_unavailable_when_device_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.exposure_summary(cutoff_at=CUTOFF, account_ids=())
    assert result["status"] == "unavailable"
    assert result["reason_code"] == "trade_device_unreachable"


def test_position_open_found_by_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("name")
        if name == "positions":
            return httpx.Response(200, json=POSITIONS_HOLDING_PAYLOAD)
        return httpx.Response(200, json=FUNDS_PAYLOAD)

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.position_open(
        cutoff_at=CUTOFF, account_ids=(), instrument_id="159740"
    )
    assert result["status"] == "available"
    assert result["positions"][0]["代码"] == "159740"


def test_position_open_not_found_without_holding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("name")
        if name == "positions":
            return httpx.Response(200, json=POSITIONS_EMPTY_PAYLOAD)
        return httpx.Response(200, json=FUNDS_PAYLOAD)

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.position_open(
        cutoff_at=CUTOFF, account_ids=(), instrument_id="510300"
    )
    assert result["status"] == "not_found"


def test_position_performance_extracts_broker_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("name")
        if name == "positions":
            return httpx.Response(200, json=POSITIONS_HOLDING_PAYLOAD)
        return httpx.Response(200, json=FUNDS_PAYLOAD)

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.position_performance(
        cutoff_at=CUTOFF, account_ids=(), instrument_id="159740"
    )
    assert result["status"] == "available"
    perf = result["performance"][0]
    assert perf["code"] == "159740"
    assert perf["market_value"] == 59.00
    assert perf["float_profit"] == -3.00
    assert perf["cost"] == 0.620
    assert perf["current_price"] == 0.590


def test_position_performance_unavailable_passthrough() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.position_performance(
        cutoff_at=CUTOFF, account_ids=(), instrument_id="159740"
    )
    assert result["status"] == "unavailable"
    assert result["reason_code"] == "trade_device_unreachable"
