from __future__ import annotations

import pytest

from src.infrastructure.clients.ths import THSClient


@pytest.mark.asyncio
async def test_native_security_quotes_preserve_request_order_and_metadata() -> None:
    client = object.__new__(THSClient)

    async def request(_securities):
        return {
            "600519": {
                "name": "贵州茅台",
                "latest": 1500.0,
                "change_rate": 1.2,
                "turnover_yuan": 1_000_000.0,
            },
            "000001": {
                "name": "平安银行",
                "latest": 12.0,
                "change_rate": -0.5,
                "turnover_yuan": 2_000_000.0,
            },
        }

    client._request_native_stock_quotes = request

    result = await client.get_native_security_quotes([
        ("600519", "17"),
        ("000001", "33"),
        ("600519", "17"),
    ])

    assert result["status"] == "ok"
    assert [row["code"] for row in result["data"]["securities"]] == [
        "600519", "000001",
    ]
    assert result["data"]["securities"][0]["market_code"] == "17"
    assert result["provider_metadata"]["protocol_id"] == 1264
    assert result["provider_metadata"]["page_id"] == 2312


@pytest.mark.asyncio
async def test_native_security_quotes_returns_explicit_empty_result() -> None:
    client = object.__new__(THSClient)

    async def request(_securities):
        return {}

    client._request_native_stock_quotes = request

    result = await client.get_native_security_quotes([("600519", "17")])

    assert result["status"] == "empty"
    assert result["data"]["securities"] == []
