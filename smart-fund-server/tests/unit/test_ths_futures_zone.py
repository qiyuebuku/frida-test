from __future__ import annotations

import pytest

from src.infrastructure.clients.ths import THSClient


@pytest.mark.asyncio
async def test_futures_zone_covers_all_ui_modules(monkeypatch) -> None:
    client = THSClient(timeout=1)

    async def fake_bridge(*args, **kwargs):
        return {
            "success": True,
            "data": {
                "rows": [
                    {
                        "code": "au9999",
                        "name": "沪金主连",
                        "indicators": {},
                    }
                ]
            },
        }

    async def fake_quotes(securities):
        return {
            code: {"name": code, "latest": 1.0, "change_rate": 0.1}
            for code, _ in securities
        }

    async def fake_unified(*, online_id, **kwargs):
        return {"data": {"dataDict": {"4": [online_id], "55": [online_id]}}}

    monkeypatch.setattr(client, "_request_native_sector_bridge", fake_bridge)
    monkeypatch.setattr(client, "_request_native_stock_quotes", fake_quotes)
    monkeypatch.setattr(client, "_request_native_unified", fake_unified)

    try:
        result = await client.get_native_futures_zone_snapshot()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["provider_metadata"]["complete"] is True
    assert result["data"]["hot_continuous_contracts"]["count"] == 1
    assert len(result["data"]["futures_indices"]) == 10
    assert len(result["data"]["main_contract_rankings"]) == 14
    assert set(result["data"]["commodity_fund_flow"]) == {"inflow", "outflow"}
    assert len(result["data"]["page_config"]["index_codes"]) == 10


@pytest.mark.asyncio
async def test_futures_zone_preserves_successful_modules_when_one_group_fails(
    monkeypatch,
) -> None:
    client = THSClient(timeout=1)

    async def fake_bridge(*args, **kwargs):
        return {"success": True, "data": {"rows": []}}

    async def fake_quotes(securities):
        return {code: {"name": code} for code, _ in securities}

    async def fake_unified(*, online_id, **kwargs):
        if online_id == "futures_rank_night":
            raise RuntimeError("temporary native failure")
        return {"data": {"dataDict": {"4": [online_id]}}}

    monkeypatch.setattr(client, "_request_native_sector_bridge", fake_bridge)
    monkeypatch.setattr(client, "_request_native_stock_quotes", fake_quotes)
    monkeypatch.setattr(client, "_request_native_unified", fake_unified)

    try:
        result = await client.get_native_futures_zone_snapshot()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["provider_metadata"]["complete"] is False
    assert result["provider_metadata"]["completed_modules"]["rankings"] is False
    assert "ranking:night" in result["provider_metadata"]["errors"]
    assert len(result["data"]["main_contract_rankings"]) == 13


@pytest.mark.asyncio
async def test_futures_ranking_fragment_returns_one_native_table(monkeypatch) -> None:
    client = THSClient(timeout=1)

    async def fake_unified(*, online_id, **kwargs):
        assert online_id == "futures_rank_precious"
        return {"data": {"dataDict": {"4": ["au9999"], "55": ["沪金主连"]}}}

    monkeypatch.setattr(client, "_request_native_unified", fake_unified)
    try:
        result = await client.get_native_futures_fragment("ranking", "precious")
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["kind"] == "ranking"
    assert result["data"]["group"] == "precious"
    assert result["data"]["native_table"]["dataDict"]["4"] == ["au9999"]
