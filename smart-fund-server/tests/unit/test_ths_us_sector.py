from __future__ import annotations

import pytest

from src.infrastructure.clients.ths import THSClient


@pytest.mark.asyncio
async def test_us_sector_periods_use_native_returns_and_http_leaders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = THSClient(timeout=1)
    native_calls: list[str] = []

    async def fake_unified(*, request_dic: str, **_kwargs) -> dict:
        native_calls.append(request_dic)
        prefix = "行业" if "marketid=2029" in request_dic else "概念"
        return {
            "data": {
                "dataDict": {
                    "4": ["861001", "861002"],
                    "55": [f"{prefix}甲", f"{prefix}乙"],
                    "36103": ["89", "89"],
                    "34376": ["12.00%", "20.00%"],
                    "34377": ["31.00%", "13.00%"],
                    "34850": ["14.00%", "42.00%"],
                }
            }
        }

    async def fake_proxy(url: str, **_kwargs) -> dict:
        prefix = "行业" if "tag=industry" in url else "概念"
        return {
            "status_code": 0,
            "data": {
                "sector_list": [
                    {
                        "sector_code": "861001",
                        "five_day_head_stock_name": f"{prefix}领涨股",
                    }
                ]
            },
        }

    monkeypatch.setattr(client, "_request_native_unified", fake_unified)
    monkeypatch.setattr(client, "_request_app_proxy", fake_proxy)
    try:
        result = await client.get_native_us_sector_snapshot()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["provider_metadata"]["complete"] is True
    assert len(native_calls) == 2
    assert all("rowcount=500" in request for request in native_calls)
    sectors = result["data"]["sectors"]
    industry_five_day = sectors["industry_five_day"]["data"]["sector_list"]
    industry_one_month = sectors["industry_one_month"]["data"]["sector_list"]
    industry_three_month = sectors["industry_three_month"]["data"]["sector_list"]
    assert [row["sector_name"] for row in industry_five_day] == [
        "行业乙",
        "行业甲",
    ]
    assert industry_five_day[1]["five_day_sector_uplift"] == "12.00%"
    assert industry_five_day[1]["five_day_head_stock_name"] == "行业领涨股"
    assert industry_one_month[0]["sector_name"] == "行业甲"
    assert industry_one_month[0]["one_month_sector_uplift"] == "31.00%"
    assert industry_three_month[0]["sector_name"] == "行业乙"
    assert industry_three_month[0]["three_month_sector_uplift"] == "42.00%"

