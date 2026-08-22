from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.application.services.market_observation_service import (
    MarketObservationService,
    _sector_snapshot,
    _hot_sector_poll_interval_seconds,
)
from src.application.services.china_exchange_calendar_service import (
    ChinaExchangeCalendarService,
)
from src.application.services.market_observability_service import (
    MarketObservabilityService,
    _group_sector_rotation_periods,
)
from src.infrastructure.clients.ths import THSClient


@pytest.fixture(autouse=True)
def _legacy_http_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THS_NATIVE_COMMAND_STREAM_ENABLED", "0")


NOW = datetime(2026, 8, 2, 2, 30, tzinfo=timezone.utc)


def test_native_etf_home_rows_normalizes_sortable_card_fields() -> None:
    rows = THSClient._native_etf_home_rows({
        "data": {
            "dataDict": {
                "55": ["科创芯片ETF"],
                "4": ["588200"],
                "34338": ["17"],
                "33001": ["+6.76%"],
                "48": ["-0.12%"],
                "19": ["18.35亿"],
                "34307": ["245.08亿"],
            }
        }
    })

    assert len(rows) == 1
    assert rows[0] == {
        "name": "科创芯片ETF",
        "code": "588200",
        "market": "17",
        "change_pct": 6.76,
        "change_speed_pct": -0.12,
        "turnover_yuan": rows[0]["turnover_yuan"],
        "scale_yuan": 24_508_000_000.0,
        "display": {
            "change_pct": "+6.76%",
            "change_speed_pct": "-0.12%",
            "turnover": "18.35亿",
            "scale": "245.08亿",
        },
    }
    assert rows[0]["turnover_yuan"] == pytest.approx(1_835_000_000.0)


def _open_market_session() -> dict:
    return {
        "status": "ok",
        "data": {"is_trading_day": True, "market_session": "open"},
    }


@pytest.mark.asyncio
async def test_native_sector_ranking_maps_column_table() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    client._request_native_sector_quote_table = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "provider_sector_code": "885001",
                "sector_name": "AI应用",
                "market_code": "48",
                "indicators": {
                    "34818": 8.1,
                    "36251": 1.2,
                    "34311": 2.2,
                },
            },
            {
                "provider_sector_code": "885002",
                "sector_name": "算力租赁",
                "market_code": "48",
                "indicators": {
                    "34818": 7.64,
                    "36251": 0.8,
                    "34311": 1.7,
                },
            },
        ]
    )
    try:
        result = await client.get_native_sector_ranking("change", 50)
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["count"] == 2
    assert result["data"]["classification"] == "all"
    assert result["data"]["sectors"][0]["sector_name"] == "AI应用"
    assert result["data"]["sectors"][0]["metric_value"] == 8.1
    client._request_native_sector_quote_table.assert_awaited_once_with("all")


@pytest.mark.asyncio
async def test_native_sector_ranking_bundle_fetches_once_and_sorts_metrics() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    client._request_native_sector_quote_table = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "provider_sector_code": "885001",
                "sector_name": "AI应用",
                "market_code": "48",
                "indicators": {
                    "34818": "8.10%",
                    "36251": "0.20%",
                    "34311": "1.20",
                },
            },
            {
                "provider_sector_code": "885002",
                "sector_name": "算力租赁",
                "market_code": "48",
                "indicators": {
                    "34818": "7.64%",
                    "36251": "1.30%",
                    "34311": "2.40",
                },
            },
        ]
    )
    try:
        result = await client.get_native_sector_ranking_bundle("concept", 50)
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["source_row_count"] == 2
    assert result["data"]["rankings"]["change"][0]["sector_name"] == "AI应用"
    assert result["data"]["rankings"]["speed"][0]["sector_name"] == "算力租赁"
    assert result["data"]["rankings"]["volume_ratio"][0]["sector_name"] == "算力租赁"
    client._request_native_sector_quote_table.assert_awaited_once_with("concept")


@pytest.mark.asyncio
async def test_native_sector_quote_table_uses_explicit_query_client_list() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    request = AsyncMock(
        side_effect=[
            *[
                {
                    "success": True,
                    "data": {
                        "rows": [
                            {
                                "code": "885001",
                                "market": "48",
                                "name": "AI应用",
                                "indicators": {
                                    "up_down_limit_up_num": {"content": "18"}
                                },
                            }
                        ]
                    },
                }
                for _ in range(4)
            ],
            *[
                {
                    "success": True,
                    "data": {
                        "rows": [
                            {
                                "code": "885001",
                                "market": "48",
                                "name": "AI应用",
                                "indicators": {
                                    "34818": {"content": "8.10%"},
                                    "36251": {"content": "0.20%"},
                                    "34311": {"content": "1.20"},
                                },
                            }
                        ]
                    },
                }
                for _ in range(4)
            ],
        ]
    )
    client._request_native_sector_bridge = request  # type: ignore[method-assign]
    try:
        rows = await client._request_native_sector_quote_table("concept")
    finally:
        await client.close()

    assert rows[0]["sector_name"] == "AI应用"
    assert rows[0]["indicators"]["34818"] == "8.10%"
    assert rows[0]["indicators"]["up_down_limit_up_num"] == "18"
    listing_body = request.await_args_list[0].args[1]
    quote_body = request.await_args_list[4].args[1]
    assert listing_body["hurricane_ids"] == ["industry_l1"]
    assert quote_body["securities"] == [
        {"code": "885001", "market": "48", "name": "AI应用"}
    ]


@pytest.mark.asyncio
async def test_native_sector_limit_up_uses_classified_hurricane_query() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    request = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "rows": [
                    {
                        "code": "885001",
                        "name": "AI应用",
                        "market": 48,
                        "indicators": {
                            "up_down_limit_up_num": {"content": "18"},
                            "hq-fncdict-199112": {"content": "5.11"},
                        },
                    }
                ]
            },
        }
    )
    client._request_native_sector_bridge = request  # type: ignore[method-assign]
    try:
        result = await client.get_native_sector_ranking(
            "limit_up_count",
            50,
            "concept",
        )
    finally:
        await client.close()

    body = request.await_args.args[1]
    assert body["hurricane_ids"] == ["cn_concept"]
    assert result["data"]["sectors"][0]["limit_up_count"] == 18


@pytest.mark.asyncio
async def test_sector_fragment_collects_only_requested_slice(monkeypatch) -> None:
    from src.infrastructure import clients

    ranking = AsyncMock(
        return_value={
            "status": "ok",
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "data": {
                "count": 1,
                "sectors": [
                    {
                        "provider_sector_code": "881121",
                        "sector_name": "半导体",
                        "rank": 1,
                        "metric": "change",
                        "metric_value": 4.23,
                    }
                ],
            },
        }
    )
    monkeypatch.setattr(
        clients,
        "market_calendar",
        SimpleNamespace(get_market_session=AsyncMock(return_value=_open_market_session())),
    )
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(get_native_sector_ranking=ranking),
    )
    service = MarketObservationService()

    batch = await service._collect_ths_sector_fragment(
        "ranking",
        "industry",
        "change",
    )

    ranking.assert_awaited_once_with("change", 50, "industry")
    assert batch.fetched_count == 1
    assert batch.snapshots[0]["data_type"] == "ths_sector_ranking"
    assert batch.snapshots[0]["data"]["sector_name"] == "半导体"
    assert batch.projections[0][0] == "ths_sector_ranking_industry_change"


@pytest.mark.asyncio
async def test_hot_sector_fragment_collects_while_market_is_closed(
    monkeypatch,
) -> None:
    from src.infrastructure import clients

    market_session = AsyncMock(
        return_value={
            "status": "ok",
            "data": {
                "is_trading_day": True,
                "market_session": "closed",
            },
        }
    )
    hot = AsyncMock(
        return_value={
            "status": "ok",
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "data": {
                "count": 1,
                "sectors": [
                    {
                        "provider_sector_code": "881121",
                        "sector_name": "半导体",
                        "market_code": "48",
                        "sector_type": "industry",
                        "heat_rank": 1,
                        "heat_score": 70900,
                        "change_pct": 6.02,
                    }
                ],
            },
        }
    )
    monkeypatch.setattr(
        clients,
        "market_calendar",
        SimpleNamespace(get_market_session=market_session),
    )
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(get_native_hot_boards=hot),
    )

    repository = SimpleNamespace(list_latest=lambda **_kwargs: [])
    batch = await MarketObservationService(
        snapshot_repository=repository,  # type: ignore[arg-type]
    )._collect_ths_sector_fragment(
        "hot",
        "industry",
        None,
    )

    market_session.assert_not_awaited()
    hot.assert_awaited_once_with("industry", 50)
    assert batch.snapshots[0]["data"]["heat_score"] == 70900


def test_hot_sector_poll_interval_is_adaptive() -> None:
    def cn(hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 8, 5, hour, minute, tzinfo=timezone(timedelta(hours=8)))

    assert _hot_sector_poll_interval_seconds(cn(10)) == 60
    assert _hot_sector_poll_interval_seconds(cn(12)) == 300
    assert _hot_sector_poll_interval_seconds(cn(20)) == 300
    assert _hot_sector_poll_interval_seconds(cn(2)) == 1800


@pytest.mark.asyncio
async def test_sector_table_fragment_fans_out_three_rankings(monkeypatch) -> None:
    from src.infrastructure import clients

    bundle = AsyncMock(
        return_value={
            "status": "ok",
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "data": {
                "source_row_count": 1,
                "rankings": {
                    metric: [
                        {
                            "provider_sector_code": "881121",
                            "sector_name": "半导体",
                            "rank": 1,
                            "metric": metric,
                            "metric_value": value,
                        }
                    ]
                    for metric, value in {
                        "change": 4.23,
                        "speed": 0.31,
                        "volume_ratio": 1.8,
                    }.items()
                },
            },
        }
    )
    monkeypatch.setattr(
        clients,
        "market_calendar",
        SimpleNamespace(get_market_session=AsyncMock(return_value=_open_market_session())),
    )
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(get_native_sector_ranking_bundle=bundle),
    )

    batch = await MarketObservationService()._collect_ths_sector_fragment(
        "table",
        "industry",
        None,
    )

    bundle.assert_awaited_once_with("industry", 50)
    assert batch.fetched_count == 3
    assert {item[0] for item in batch.projections} == {
        "ths_sector_ranking_industry_change",
        "ths_sector_ranking_industry_speed",
        "ths_sector_ranking_industry_volume_ratio",
    }


@pytest.mark.asyncio
async def test_sector_signal_fragment_collects_one_rotation_source(monkeypatch) -> None:
    from src.infrastructure import clients

    rotation = AsyncMock(
        return_value={
            "status": "ok",
            "provider": "ths_app_http",
            "market": "cn",
            "fetched_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "data": {
                "sector_type": "concept",
                "metric": "change",
                "periods": [
                    {
                        "date": "2026-08-01",
                        "block_list": [
                            {
                                "code": "886068",
                                "name": "AI视频",
                                "info": {"rate": 8.1},
                            }
                        ],
                    }
                ],
            },
        }
    )
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(get_native_sector_rotation=rotation),
    )
    service = MarketObservationService()

    batch = await service._collect_ths_sector_signal_fragment(
        "rotation",
        "concept",
        "change",
    )

    rotation.assert_awaited_once_with(
        sector_type="concept",
        metric="change",
        day_count=60,
        sector_count=10,
    )
    assert batch.fetched_count == 1
    assert batch.snapshots[0]["data_type"] == "ths_sector_rotation"
    assert batch.projections[0][0] == "ths_rotation_concept_change"


@pytest.mark.asyncio
async def test_native_hot_boards_enriches_exact_public_etf_mapping() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    client._request_native_sector_bridge = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "success": True,
            "data": {
                "rows": [
                    {
                        "code": "886068",
                        "name": "AI视频",
                        "market": 48,
                        "indicators": {
                            "ths-hot-data-minute-attention-rate": {
                                "content": "8541"
                            },
                            "34818": {"content": "8.10"},
                        },
                    }
                ]
            },
        }
    )
    client.get_hot_board = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "ok",
            "data": {
                "sectors": [
                    {
                        "provider_sector_code": "886068",
                        "representative_etf_code": "159869",
                        "representative_etf_name": "游戏ETF",
                    }
                ]
            },
        }
    )
    client._request_native_stock_quotes = AsyncMock(  # type: ignore[method-assign]
        return_value={}
    )
    try:
        result = await client.get_native_hot_boards("concept", 50)
    finally:
        await client.close()

    sector = result["data"]["sectors"][0]
    assert sector["market_code"] == 48
    assert sector["representative_etf_code"] == "159869"
    assert sector["representative_etf_name"] == "游戏ETF"


@pytest.mark.asyncio
async def test_native_hot_boards_hydrates_missing_name_and_change() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    client._request_native_sector_bridge = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "success": True,
            "data": {
                "rows": [
                    {
                        "code": "886033",
                        "name": "",
                        "market": 48,
                        "indicators": {
                            "ths-hot-data-minute-attention-rate": {
                                "content": "574162"
                            }
                        },
                    }
                ]
            },
        }
    )
    client.get_hot_board = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "ok", "data": {"sectors": []}}
    )
    client._request_native_stock_quotes = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "886033": {"name": "示例概念", "change_rate": 3.21}
        }
    )
    try:
        result = await client.get_native_hot_boards("concept", 50)
    finally:
        await client.close()

    sector = result["data"]["sectors"][0]
    assert sector["sector_name"] == "示例概念"
    assert sector["change_pct"] == 3.21
    client._request_native_stock_quotes.assert_awaited_once_with(
        [("886033", "48")]
    )


@pytest.mark.asyncio
async def test_native_index_hot_boards_use_index_sector_endpoint() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    request = AsyncMock(
        return_value={
            "status_code": 0,
            "data": {
                "total": 1,
                "indexes": [
                    {"idx": 0, "index_id": "security_name"},
                    {"idx": 1, "index_id": "price_change_ratio_pct"},
                    {
                        "idx": 2,
                        "index_id": "ths-hot-data-minute-attention-rate",
                    },
                ],
                "data": [
                    {
                        "code": "120:H30590",
                        "values": [
                            {"idx": 0, "value": "机器人"},
                            {"idx": 1, "value": 5.85},
                            {"idx": 2, "value": 1554},
                        ],
                    },
                ]
            },
        }
    )
    client._post = request  # type: ignore[method-assign]
    client._request_native_sector_bridge = AsyncMock()  # type: ignore[method-assign]
    client.get_hot_board = AsyncMock()  # type: ignore[method-assign]
    try:
        result = await client.get_native_hot_boards("index", 50)
    finally:
        await client.close()

    body = request.await_args.kwargs["json"]
    sector = result["data"]["sectors"][0]
    assert body["page_info"]["page_size"] == 50
    assert result["data"]["sector_type"] == "index"
    assert sector["provider_sector_code"] == "H30590"
    assert sector["sector_name"] == "机器人"
    assert sector["market_code"] == "120"
    assert sector["heat_rank"] == 1
    assert sector["heat_score"] == 1554
    assert sector["change_pct"] == 5.85
    assert sector["representative_etf_code"] is None
    client._request_native_sector_bridge.assert_not_awaited()
    client.get_hot_board.assert_not_awaited()


@pytest.mark.asyncio
async def test_commodity_linkage_combines_all_three_app_tabs() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    client._get_native_sector_derived_table = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "ok",
            "data": {
                "items": [
                    {
                        "code": "lu2609",
                        "name": "低硫燃油2609",
                        "change_pct": 3.33,
                        "related_asset_mapping": (
                            '{"block":{"market":"49","code":"884020"},'
                            '"etf":{"market":"36","code":"159870"}}'
                        ),
                    }
                ]
            },
        }
    )
    client._get_public_commodity_linkage_items = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [
                {
                    "code": "S004455157",
                    "name": "丙烯酸异辛脂",
                    "increase": 41.8,
                    "block": {"market": "48", "code": "885829"},
                    "etf": None,
                }
            ],
            [
                {
                    "code": "S004450139",
                    "name": "油页岩:进口量",
                    "increase": 370045.83,
                    "block": {"market": "49", "code": "884016"},
                    "etf": None,
                }
            ],
        ]
    )
    client._get_direct_linked_asset_quotes = AsyncMock(  # type: ignore[method-assign]
        return_value={
            ("49", "884020"): {
                "market_code": "49",
                "security_code": "884020",
                "security_name": "石油加工",
                "change_pct": 0.85,
            },
            ("36", "159870"): {
                "market_code": "36",
                "security_code": "159870",
                "security_name": "化工ETF鹏华",
                "change_pct": -0.13,
            },
        }
    )
    try:
        result = await client.get_native_sector_commodity_linkage(500)
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["counts"] == {
        "futures": 1,
        "spot": 1,
        "industry": 1,
    }
    future = result["data"]["linkage_types"]["futures"][0]
    assert future["source_name"] == "低硫燃油2609"
    assert [asset["security_name"] for asset in future["linked_assets"]] == [
        "石油加工",
        "化工ETF鹏华",
    ]
    assert future["linked_assets"][1]["change_pct"] == -0.13
    assert result["data"]["linkage_types"]["spot"][0]["source_change_pct"] == 41.8


@pytest.mark.asyncio
async def test_commodity_linked_asset_quotes_use_direct_ths_http() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    responses = {
        "bk_885829": (
            'callback({"bk_885829":{"name":"丙烯酸","pre":"100",'
            '"data":"1459,108,0,0,0"}})'
        ),
        "hs_512400": (
            'callback({"hs_512400":{"name":"有色金属ETF南方",'
            '"pre":"1.25","data":"1459,1.20,0,0,0"}})'
        ),
    }

    async def request(url: str, **_kwargs):
        symbol = url.split("/time/", 1)[1].split("/", 1)[0]
        return SimpleNamespace(
            text=responses[symbol],
            raise_for_status=lambda: None,
        )

    client._client.get = AsyncMock(side_effect=request)
    try:
        result = await client._get_direct_linked_asset_quotes(
            [("48", "885829"), ("20", "512400")]
        )
    finally:
        await client.close()

    assert result[("48", "885829")]["security_name"] == "丙烯酸"
    assert result[("48", "885829")]["change_pct"] == pytest.approx(8.0)
    assert result[("20", "512400")]["change_pct"] == pytest.approx(-4.0)
    requested_urls = [call.args[0] for call in client._client.get.await_args_list]
    assert requested_urls == [
        "https://d.10jqka.com.cn/v6/time/bk_885829/last.js",
        "https://d.10jqka.com.cn/v6/time/hs_512400/last.js",
    ]


@pytest.mark.asyncio
async def test_commodity_linked_asset_quote_keeps_name_without_trade_points() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    client._client.get = AsyncMock(
        return_value=SimpleNamespace(
            text=(
                'callback({"bk_885647":{"name":"互联网彩票",'
                '"pre":"","data":""}})'
            ),
            raise_for_status=lambda: None,
        )
    )
    try:
        result = await client._get_direct_linked_asset_quotes([("48", "885647")])
    finally:
        await client.close()

    assert result[("48", "885647")]["security_name"] == "互联网彩票"
    assert result[("48", "885647")]["change_pct"] is None


@pytest.mark.asyncio
async def test_public_commodity_linkage_paginates_with_endpoint_limit() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    first_page = [{"code": f"S{index}"} for index in range(20)]
    second_page = [{"code": f"S{index}"} for index in range(20, 25)]
    request = AsyncMock(
        side_effect=[
            {
                "status_code": 0,
                "total": 25,
                "data": {"commodity_detail_list": first_page},
            },
            {
                "status_code": 0,
                "total": 25,
                "data": {"commodity_detail_list": second_page},
            },
        ]
    )
    client._get = request  # type: ignore[method-assign]
    try:
        result = await client._get_public_commodity_linkage_items("spot", 500)
    finally:
        await client.close()

    assert len(result) == 25
    assert request.await_args_list[0].args[0].endswith("/spot/start_row/desc/0/20")
    assert request.await_args_list[1].args[0].endswith("/spot/start_row/desc/20/20")


@pytest.mark.asyncio
async def test_public_commodity_linkage_fetches_remaining_pages_concurrently() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    active = 0
    max_active = 0

    async def request(url: str) -> dict:
        nonlocal active, max_active
        start = int(url.rstrip("/").split("/")[-2])
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        end = min(start + 20, 55)
        return {
            "status_code": 0,
            "total": 55,
            "data": {
                "commodity_detail_list": [
                    {"code": f"S{index}"} for index in range(start, end)
                ]
            },
        }

    client._get = request  # type: ignore[method-assign]
    try:
        result = await client._get_public_commodity_linkage_items("spot", 500)
    finally:
        await client.close()

    assert [item["code"] for item in result] == [f"S{index}" for index in range(55)]
    assert max_active == 2


@pytest.mark.asyncio
async def test_native_sector_flow_keeps_inflow_and_outflow_extremes() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    request = AsyncMock(
        return_value={
            "head": {"protocolId": 1348, "errorCode": 0},
            "data": {
                "dataDict": {
                    "4": ["881121", "881270", "881155", "881144"],
                    "55": ["半导体", "元件", "银行", "化学制药"],
                    "34391": ["139.51", "101.02", "-5.66", "-12.41"],
                }
            },
        }
    )
    client._request_native_unified = request  # type: ignore[method-assign]
    try:
        result = await client.get_native_sector_fund_flow("industry", 500)
    finally:
        await client.close()

    body = request.await_args.kwargs
    sectors = result["data"]["sectors"]
    assert body["protocol_id"] == 1348
    assert body["page_id"] == 2312
    assert "rowcount=500" in body["request_dic"]
    assert sectors[0]["flow_direction"] == "inflow"
    assert sectors[0]["direction_rank"] == 1
    assert sectors[2]["flow_direction"] == "outflow"
    assert sectors[2]["direction_rank"] == 2
    assert sectors[3]["flow_direction"] == "outflow"
    assert sectors[3]["direction_rank"] == 1


@pytest.mark.asyncio
async def test_native_sector_constituents_uses_board_selector_and_maps_rows() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")
    request = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "total": 54,
                "rows": [
                    {
                        "code": "000156",
                        "name": "华数传媒",
                        "market": 33,
                        "indicators": {
                            "last_price": {"content": "9.21"},
                            "hq-fncdict-199112": {"content": "3.14"},
                            "hq-fncdict-3934664": {"content": "0.25"},
                            "hq-fncdict-1968584": {"content": "2.11"},
                        },
                    }
                ],
            },
        }
    )
    client._request_native_sector_bridge = request  # type: ignore[method-assign]
    try:
        result = await client.get_native_sector_constituents(
            "886068", market_code="48", count=200
        )
    finally:
        await client.close()

    body = request.await_args.args[1]
    assert body["http_source_id"] == "sif-constituent-stock"
    assert body["_preserve_frame_id"] is True
    assert body["selectors"]["intersection"][0] == {
        "type": "HQ_BLOCK_CODE",
        "values": ["48:886068"],
    }
    assert body["sort_indicator_id"] == ""
    assert body["order"] == ""
    assert body["hurricane_type"] is None
    assert body["required_hurricane_indicator_ids"] == ["security_name"]
    assert body["completion_mode"] == "settled"
    assert body["timeout_ms"] == 8000
    assert result["data"]["total_count"] == 54
    assert result["data"]["constituents"][0]["security_name"] == "华数传媒"
    assert result["data"]["constituents"][0]["change_pct"] == 3.14


@pytest.mark.asyncio
async def test_native_sector_constituents_pages_past_upstream_100_row_limit() -> None:
    client = THSClient(native_bridge_url="http://native-bridge")

    def native_row(index: int) -> dict:
        return {
            "code": f"{index:06d}",
            "name": f"股票{index}",
            "market": 33,
            "indicators": {},
        }

    request = AsyncMock(
        side_effect=[
            {
                "success": True,
                "data": {
                    "total": 150,
                    "rows": [native_row(i) for i in range(start, min(start + 20, 150))],
                },
            }
            for start in range(0, 150, 20)
        ]
    )
    client._request_native_sector_bridge = request  # type: ignore[method-assign]
    try:
        result = await client.get_native_sector_constituents("885001", count=500)
    finally:
        await client.close()

    assert request.await_count == 8
    assert [call.args[1]["start"] for call in request.await_args_list] == list(
        range(0, 150, 20)
    )
    assert [call.args[1]["count"] for call in request.await_args_list] == [20] * 8
    assert result["data"]["count"] == 150
    assert result["data"]["total_count"] == 150


def test_sector_snapshot_metric_suffix_prevents_same_bucket_collision() -> None:
    response = {
        "provider": "ths_native",
        "market": "cn",
        "fetched_at": NOW.isoformat(),
    }
    sector = {
        "provider_sector_code": "885001",
        "sector_name": "AI应用",
        "metric": "change",
        "rank": 1,
    }

    change = _sector_snapshot(
        response=response,
        sector=sector,
        sector_type="all",
        data_type="ths_sector_ranking",
        bucket_seconds=60,
        identity_suffix="change",
    )
    speed = _sector_snapshot(
        response=response,
        sector={**sector, "metric": "speed"},
        sector_type="all",
        data_type="ths_sector_ranking",
        bucket_seconds=60,
        identity_suffix="speed",
    )

    assert change["subject_id"] != speed["subject_id"]
    assert change["bucket_at"] == speed["bucket_at"]


class _SectorSnapshotRepository:
    def list_latest(self, **_kwargs):
        return [
            _snapshot(
                data_type="ths_sector_hot",
                subject_id="ths_native:concept:885001",
                data={
                    "provider_sector_code": "885001",
                    "sector_name": "AI应用",
                    "sector_type": "concept",
                    "heat_rank": 1,
                    "heat_score": 14011,
                    "change_pct": 5.11,
                },
            ),
            _snapshot(
                data_type="ths_sector_flow",
                subject_id="ths_native:industry:881121",
                data={
                    "provider_sector_code": "881121",
                    "sector_name": "半导体",
                    "sector_type": "industry",
                    "rank": 1,
                    "main_net_inflow": 139.5,
                },
            ),
            _snapshot(
                data_type="ths_sector_flow",
                subject_id="ths_native:industry:881144",
                data={
                    "provider_sector_code": "881144",
                    "sector_name": "化学制药",
                    "sector_type": "industry",
                    "rank": 90,
                    "direction_rank": 1,
                    "flow_direction": "outflow",
                    "main_net_inflow": -12.41,
                },
            ),
            _snapshot(
                data_type="ths_sector_hot",
                subject_id="ths_native:index:883902",
                data={
                    "provider_sector_code": "883902",
                    "sector_name": "昨日成交前十",
                    "sector_type": "index",
                    "heat_rank": 1,
                    "heat_score": None,
                    "change_pct": 2.55,
                },
            ),
            _snapshot(
                data_type="ths_sector_hot",
                subject_id="ths_app_http:index:H30590",
                provider="ths_app_http",
                data={
                    "provider_sector_code": "H30590",
                    "sector_name": "机器人",
                    "sector_type": "index",
                    "heat_rank": 1,
                    "heat_score": 1554,
                    "change_pct": 5.85,
                },
            ),
            _snapshot(
                data_type="ths_sector_commodity_linkage",
                subject_id="ths_native:commodity:legacy",
                data={
                    "provider_sector_code": "legacy",
                    "sector_name": "旧商品联动",
                },
            ),
            _snapshot(
                data_type="ths_sector_commodity_linkage",
                subject_id="ths_composite:commodity:futures:lu2609",
                provider="ths_composite",
                data={
                    "identity_version": 2,
                    "provider_sector_code": "lu2609",
                    "sector_name": "低硫燃油2609",
                    "linkage_type": "futures",
                    "rank": 1,
                },
            ),
            _snapshot(
                data_type="ths_sector_commodity_linkage",
                subject_id="ths_native:commodity:futures:legacy",
                data={
                    "provider_sector_code": "legacy-future",
                    "sector_name": "旧期货联动",
                    "linkage_type": "futures",
                    "rank": 1,
                },
            ),
            _snapshot(
                data_type="ths_sector_ranking",
                subject_id="ths_native:style:883900:limit_up_count",
                data={
                    "provider_sector_code": "883900",
                    "sector_name": "昨日涨停表现",
                    "sector_type": "style",
                    "metric": "limit_up_count",
                    "rank": 1,
                    "metric_value": 101,
                },
            ),
        ]

    def query_history(self, **_kwargs):
        return []


def test_sector_overview_reads_persisted_rows_only() -> None:
    service = MarketObservabilityService(
        snapshot_repository=_SectorSnapshotRepository(),  # type: ignore[arg-type]
    )

    result = service.sector_overview(limit_per_group=20)

    assert result["upstream_requested"] is False
    assert result["facts"]["hot"]["concept"][0]["sector_name"] == "AI应用"
    assert [
        item["sector_name"] for item in result["facts"]["hot"]["index"]
    ] == ["机器人"]
    assert result["facts"]["fund_flows"]["industry"][0][
        "main_net_inflow"
    ] == 139.5
    assert result["facts"]["fund_flows"]["industry"][-1][
        "main_net_inflow"
    ] == -12.41
    assert result["facts"]["rankings"]["style"]["limit_up_count"][0][
        "metric_value"
    ] == 101
    assert list(result["provider_signals"]["commodity_linkage"]) == ["futures"]
    assert result["provider_signals"]["commodity_linkage"]["futures"][0][
        "sector_name"
    ] == "低硫燃油2609"


def test_sector_ranking_uses_one_latest_common_trade_date() -> None:
    class Repository:
        def list_latest(self, **_kwargs):
            current = _snapshot(
                data_type="ths_sector_ranking",
                subject_id="ths_native:industry:881129:change",
                data={
                    "provider_sector_code": "881129",
                    "sector_type": "industry",
                    "metric": "change",
                    "rank": 4,
                },
            )
            stale = _snapshot(
                data_type="ths_sector_ranking",
                subject_id="ths_native:industry:881999:change",
                data={
                    "provider_sector_code": "881999",
                    "sector_type": "industry",
                    "metric": "change",
                    "rank": 1,
                },
            )
            current["trade_date"] = date(2026, 8, 14)
            stale["trade_date"] = date(2026, 8, 3)
            return [stale, current]

    result = MarketObservabilityService(
        snapshot_repository=Repository(),  # type: ignore[arg-type]
    ).sector_ranking(
        data_type="ths_sector_ranking",
        metric="change",
        sector_type="industry",
    )

    assert result["trade_date"] == "2026-08-14"
    assert result["total"] == 1
    assert result["items"][0]["provider_sector_code"] == "881129"


def test_sector_rotation_groups_by_type_metric_date_and_rank() -> None:
    rows = [
        _snapshot(
            data_type="ths_sector_rotation",
            subject_id=f"ths_app_http:concept:{code}:change",
            data={
                "provider_sector_code": code,
                "sector_name": name,
                "sector_type": "concept",
                "metric": "change",
                "rank": rank,
                "source_date": source_date,
                "source_signal": {"zf": value},
            },
        )
        for source_date, rank, code, name, value in (
            ("2026-07-31", 1, "886068", "AI视频", "8.1041"),
            ("2026-07-31", 2, "885918", "快手概念", "7.6407"),
            ("2026-07-30", 1, "885640", "草甘膦", "1.9790"),
        )
    ]
    rows.append(
        {
            **_snapshot(
                data_type="ths_sector_rotation",
                subject_id="ths_app_http:concept:stale:change",
                data={
                    "provider_sector_code": "stale",
                    "sector_name": "旧批次残留",
                    "sector_type": "concept",
                    "metric": "change",
                    "rank": 1,
                    "source_date": "2026-07-31",
                    "source_signal": {"zf": "99.99"},
                },
            ),
            "fetched_at": NOW - timedelta(minutes=5),
        }
    )

    result = _group_sector_rotation_periods(
        rows,
        day_limit=10,
        rank_limit=4,
    )

    periods = result["concept"]["change"]
    assert [period["source_date"] for period in periods] == [
        "2026-07-31",
        "2026-07-30",
    ]
    assert [item["sector_name"] for item in periods[0]["items"]] == [
        "AI视频",
        "快手概念",
    ]


class _SectorDetailRepository:
    def __init__(self) -> None:
        self.history_data_types: list[str] = []

    def list_latest(self, **_kwargs):
        return [
            _snapshot(
                data_type="ths_sector_hot",
                subject_id="ths_native:concept:886068",
                data={
                    "provider_sector_code": "886068",
                    "sector_name": "AI视频",
                    "sector_type": "concept",
                    "representative_etf_code": "159869",
                    "representative_etf_name": "游戏ETF",
                },
            ),
            _snapshot(
                data_type="ths_sector_constituents",
                subject_id="ths_native:concept:886068",
                data={
                    "provider_sector_code": "886068",
                    "sector_name": "AI视频",
                    "sector_type": "concept",
                    "total_count": 54,
                    "constituents": [
                        {
                            "rank": 1,
                            "security_code": "000156",
                            "security_name": "华数传媒",
                        }
                    ],
                },
            ) | {"trade_date": NOW.astimezone().date()},
        ]

    def query_history(self, **kwargs):
        self.history_data_types.append(str(kwargs["data_type"]))
        return []


def test_sector_detail_returns_persisted_constituents_and_etf() -> None:
    repository = _SectorDetailRepository()
    service = MarketObservabilityService(
        snapshot_repository=repository,  # type: ignore[arg-type]
    )

    result = service.sector_detail(
        provider_sector_code="886068",
        sector_type="concept",
    )

    assert result["upstream_requested"] is False
    assert result["constituent_count"] == 54
    assert result["constituents"][0]["security_name"] == "华数传媒"
    assert result["etf_navigation_candidates"] == [{
        "code": "159869",
        "name": "游戏ETF",
    }]
    assert "不是稳定板块代理" in result["etf_navigation_note"]
    assert all(
        item["data_type"] != "ths_sector_constituents"
        for item in result["latest"]
    )
    assert repository.history_data_types == ["ths_sector_hot"]


def test_sector_detail_rejects_legacy_constituent_date_bucket_mismatch() -> None:
    repository = _SectorDetailRepository()
    original_list_latest = repository.list_latest

    def mismatched_list_latest(**kwargs):
        rows = original_list_latest(**kwargs)
        for row in rows:
            if row["data_type"] == "ths_sector_constituents":
                row["trade_date"] = date(2026, 8, 3)
                row["bucket_at"] = datetime(2026, 8, 2, tzinfo=timezone.utc)
        return rows

    repository.list_latest = mismatched_list_latest  # type: ignore[method-assign]
    service = MarketObservabilityService(
        snapshot_repository=repository,  # type: ignore[arg-type]
    )

    result = service.sector_detail(
        provider_sector_code="886068",
        sector_type="concept",
    )

    assert result["found"] is True
    assert result["constituent_count"] == 0
    assert result["constituents"] == []
    assert result["constituent_evidence"] is None


class _SectorReferenceRepository:
    def list_latest(self, *, data_types, **_kwargs):
        if data_types == ["ths_sector_constituents"]:
            return [
                {
                    **_snapshot(
                        data_type="ths_sector_constituents",
                        subject_id="ths_native:concept:886068",
                        data={
                            "provider_sector_code": "886068",
                            "sector_name": "AI瑙嗛",
                            "sector_type": "concept",
                            "constituents": [],
                        },
                    ),
                    "trade_date": date.today(),
                }
            ]
        return [
            _snapshot(
                data_type="ths_sector_hot",
                subject_id="ths_native:concept:886068",
                data={
                    "provider_sector_code": "886068",
                    "sector_name": "AI瑙嗛",
                    "sector_type": "concept",
                },
            )
        ]


@pytest.mark.asyncio
async def test_sector_reference_refreshes_etf_metadata_without_core_run(
    monkeypatch,
) -> None:
    from src.infrastructure import clients

    hot = AsyncMock(
        side_effect=[
            {
                "status": "ok",
                "data": {
                    "sectors": [
                        {
                            "provider_sector_code": "886068",
                            "sector_name": "AI瑙嗛",
                            "representative_etf_code": "159869",
                            "representative_etf_name": "娓告垙ETF",
                        }
                    ]
                },
            },
            {"status": "ok", "data": {"sectors": []}},
            {"status": "ok", "data": {"sectors": []}},
        ]
    )
    constituents = AsyncMock(
        return_value={
            "status": "ok",
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "data": {
                "provider_sector_code": "886068",
                "total_count": 1,
                "constituents": [],
            },
        }
    )
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(
            get_native_hot_boards=hot,
            get_native_sector_constituents=constituents,
        ),
    )
    service = MarketObservationService(
        snapshot_repository=_SectorReferenceRepository(),  # type: ignore[arg-type]
    )

    batch = await service._collect_ths_sector_references()

    assert batch.snapshots[0]["data"]["representative_etf_code"] == "159869"
    assert batch.snapshots[0]["data"]["representative_etf_name"] == "娓告垙ETF"
    assert batch.details["metadata_errors"] == []
    assert [call.args[0] for call in hot.await_args_list] == [
        "concept",
        "industry",
        "index",
    ]


@pytest.mark.asyncio
async def test_sector_reference_ignores_stale_trade_date_candidates(
    monkeypatch,
) -> None:
    from src.infrastructure import clients

    session = ChinaExchangeCalendarService().resolve(datetime.now(timezone.utc))
    quote_trade_date = (
        session.previous_trade_date
        if session.market_session == "pre_open"
        else session.trade_date
    )

    class Repository:
        def list_latest(self, *, data_types, **_kwargs):
            if data_types == ["ths_sector_constituents"]:
                return []
            return [
                {
                    **_snapshot(
                        data_type="ths_sector_hot",
                        subject_id="ths_native:index:CN5075",
                        data={
                            "provider_sector_code": "CN5075",
                            "sector_name": "陈旧指数板块",
                            "sector_type": "index",
                            "market_code": "110",
                        },
                    ),
                        "trade_date": quote_trade_date - timedelta(days=1),
                },
                {
                    **_snapshot(
                        data_type="ths_sector_hot",
                        subject_id="ths_native:concept:886068",
                        data={
                            "provider_sector_code": "886068",
                            "sector_name": "AI视频",
                            "sector_type": "concept",
                            "market_code": "48",
                        },
                    ),
                        "trade_date": quote_trade_date,
                },
            ]

    hot = AsyncMock(return_value={"status": "ok", "data": {"sectors": []}})
    constituents = AsyncMock(
        return_value={
            "status": "ok",
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "data": {
                "provider_sector_code": "886068",
                "total_count": 1,
                "constituents": [],
            },
        }
    )
    monkeypatch.setattr(
        clients,
        "ths",
        SimpleNamespace(
            get_native_hot_boards=hot,
            get_native_sector_constituents=constituents,
        ),
    )
    service = MarketObservationService(
        snapshot_repository=Repository(),  # type: ignore[arg-type]
    )

    batch = await service._collect_ths_sector_references()

    assert batch.details["requested_sector_codes"] == ["886068"]
    constituents.assert_awaited_once_with(
        "886068", market_code="48", count=1000, sector_name="AI视频"
    )


def _snapshot(
    *,
    data_type: str,
    subject_id: str,
    data: dict,
    provider: str = "ths_native",
) -> dict:
    return {
        "id": 1,
        "data_type": data_type,
        "subject_type": "sector",
        "subject_id": subject_id,
        "market": "cn",
        "provider": provider,
        "trade_date": date(2026, 8, 1),
        "observed_at": NOW,
        "fetched_at": NOW,
        "bucket_at": NOW,
        "freshness_status": "realtime",
        "source_latency_seconds": 0,
        "data": data,
    }
