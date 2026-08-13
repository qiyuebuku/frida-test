from __future__ import annotations

import io

import pandas as pd
import pytest

from src.infrastructure.clients.exchange_fund import ExchangeFundClient


class _Response:
    def __init__(self, *, payload=None, content: bytes = b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _RoutingHttpClient:
    def __init__(self, *, sse_payload: dict, szse_content: bytes):
        self.sse_payload = sse_payload
        self.szse_content = szse_content

    async def get(self, url: str, **_kwargs):
        if "sse.com.cn" in url:
            return _Response(payload=self.sse_payload)
        return _Response(content=self.szse_content)


def _szse_workbook() -> bytes:
    frame = pd.DataFrame(
        [
            {
                "日期": "2026-07-29",
                "基金代码": 159915,
                "基金简称": "创业板ETF",
                "基金规模(份)": 10_000_000,
            },
            {
                "日期": "2026-07-30",
                "基金代码": 159915,
                "基金简称": "创业板ETF",
                "基金规模(份)": 11_000_000,
            },
        ]
    )
    output = io.BytesIO()
    frame.to_excel(output, index=False, engine="openpyxl")
    return output.getvalue()


def _sse_payload() -> dict:
    return {
        "pageHelp": {"total": 1},
        "result": [
            {
                "STAT_DATE": "2026-07-30",
                "ETF_TYPE": "单市",
                "SEC_CODE": "510030",
                "NUM": "1",
                "SEC_NAME": "价值ETF",
                "TOT_VOL": "15422.98",
            }
        ],
    }


async def _client() -> ExchangeFundClient:
    client = ExchangeFundClient()
    original_client = client._client
    client._client = _RoutingHttpClient(
        sse_payload=_sse_payload(),
        szse_content=_szse_workbook(),
    )
    await original_client.aclose()
    return client


@pytest.mark.asyncio
async def test_sse_daily_etf_shares_normalizes_official_unit() -> None:
    client = await _client()

    result = await client.get_sse_etf_daily_shares("20260730")

    assert result["status"] == "ok"
    assert result["trade_date"] == "2026-07-30"
    assert result["data"]["count"] == 1
    item = result["data"]["items"][0]
    assert item["code"] == "510030"
    assert item["shares"] == 154_229_800
    assert item["share_unit"] == "share"
    assert result["provider_metadata"]["complete"] is True
    assert result["provider_metadata"]["net_subscription_available"] is False


@pytest.mark.asyncio
async def test_szse_daily_etf_shares_supports_date_range() -> None:
    client = await _client()

    result = await client.get_szse_etf_daily_shares("20260729", "20260730")

    assert result["status"] == "ok"
    assert result["trade_date"] == "2026-07-30"
    assert result["data"]["count"] == 2
    assert result["data"]["items"][0]["code"] == "159915"
    assert result["data"]["items"][1]["shares"] == 11_000_000
    assert result["provider_metadata"]["frequency"] == "daily"


@pytest.mark.asyncio
async def test_combined_daily_etf_shares_requires_both_exchanges() -> None:
    client = await _client()

    result = await client.get_etf_daily_shares("20260730")

    assert result["status"] == "ok"
    assert result["data"]["exchange_counts"] == {"sse": 1, "szse": 1}
    assert result["data"]["count"] == 2
    assert {item["exchange"] for item in result["data"]["items"]} == {
        "sse",
        "szse",
    }


@pytest.mark.asyncio
async def test_szse_daily_etf_shares_rejects_ranges_over_six_months() -> None:
    client = await _client()

    with pytest.raises(ValueError, match="cannot exceed 6 months"):
        await client.get_szse_etf_daily_shares("20260101", "20260731")
