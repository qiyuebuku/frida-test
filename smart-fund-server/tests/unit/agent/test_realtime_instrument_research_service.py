from __future__ import annotations

import pytest

from src.application.services.realtime_instrument_research_service import (
    RealtimeInstrumentResearchService,
)
from src.infrastructure import clients


class _FakeTHS:
    async def get_native_security_quotes(self, securities):
        assert securities == [("600519", "17"), ("518880", "17")]
        return {
            "status": "ok",
            "data": {
                "securities": [
                    {"code": "600519", "market_code": "17", "name": "贵州茅台", "latest": 1500.0, "speed": 0.1},
                    {"code": "518880", "market_code": "17", "name": "黄金ETF", "latest": 8.1, "speed": -0.2},
                ]
            },
        }

    async def get_etf_identity(self, code):
        return {"status": "ok", "data": {"code": code, "name": "黄金ETF"}}


@pytest.mark.asyncio
async def test_overview_queries_untracked_stock_and_etf_directly(monkeypatch) -> None:
    monkeypatch.setattr(clients, "ths", _FakeTHS())

    result = await RealtimeInstrumentResearchService().open(
        codes=["600519", "518880"],
    )

    assert result["read_path"] == "direct_ths_upstream"
    assert [item["instrument_type"] for item in result["items"]] == ["stock", "etf"]
    assert result["fields"] == ["quote", "identity"]
    assert "speed" not in result["items"][0]["quote"]
    assert all(item["evidence_locator"].startswith("market:v1:") for item in result["items"])


@pytest.mark.asyncio
async def test_more_fields_enforce_smaller_batch_limit(monkeypatch) -> None:
    monkeypatch.setattr(clients, "ths", _FakeTHS())
    with pytest.raises(ValueError, match="最多查询 2 个"):
        await RealtimeInstrumentResearchService().open(
            codes=["600519", "000001", "300750", "518880"],
            fields=["quote", "identity", "holdings"],
        )


@pytest.mark.asyncio
async def test_etf_returns_only_selected_modules(monkeypatch) -> None:
    fake = _FakeTHS()
    monkeypatch.setattr(clients, "ths", fake)

    async def fake_uncached(_client, method_name, *_args, **_kwargs):
        values = {
            "get_holding_overview": {"data": {"ratio": 80}},
            "get_top10_holdings": {"data": [{"code": "600519"}]},
            "get_fund_detail": {"data": {"managerInfo": [{"id": "M1", "name": "经理甲"}]}},
        }
        return values[method_name]

    monkeypatch.setattr(
        "src.application.services.realtime_instrument_research_service._uncached",
        fake_uncached,
    )
    result = await RealtimeInstrumentResearchService().open(
        codes=["518880"],
        instrument_type="etf",
        fields=["holdings", "manager"],
        item_limit=5,
    )

    item = result["items"][0]
    assert set(item) == {"code", "instrument_type", "holdings", "manager", "evidence_locator"}
    assert item["manager"] == [{"id": "M1", "name": "经理甲"}]


@pytest.mark.asyncio
async def test_etf_quote_falls_back_without_discarding_other_fields(monkeypatch) -> None:
    class _FailingQuoteTHS(_FakeTHS):
        async def get_native_security_quotes(self, securities):
            return {"status": "error", "error": "upstream_error"}

    monkeypatch.setattr(clients, "ths", _FailingQuoteTHS())

    async def fake_uncached(_client, method_name, *_args, **_kwargs):
        if method_name == "get_fund_info":
            return {
                "data": {
                    "name": "创新药ETF",
                    "net": "0.8664",
                    "date": "2026-08-11",
                    "nowtime": "2026-08-12 08:58:44",
                    "defaultMarketId": "USZJ",
                }
            }
        if method_name in {"get_performance_rank", "get_year_return", "get_max_drawdown"}:
            return {"data": {"source": method_name}}
        raise AssertionError(method_name)

    monkeypatch.setattr(
        "src.application.services.realtime_instrument_research_service._uncached",
        fake_uncached,
    )
    result = await RealtimeInstrumentResearchService().open(
        codes=["159748"],
        instrument_type="etf",
        fields=["quote", "performance"],
    )

    item = result["items"][0]
    assert item["quote"]["latest"] == 0.8664
    assert item["quote"]["quote_semantics"] == "latest_published_fund_value"
    assert item["performance"]
    assert "原生实时行情不可用" in item["field_warnings"]["quote"]
