from __future__ import annotations

import base64
import importlib
import json
from datetime import date
from unittest.mock import AsyncMock
from pathlib import Path
from types import MethodType

import httpx
import pandas as pd
import pytest

from src.infrastructure.clients.eastmoney import EastmoneyClient
from src.infrastructure.clients.sina import SinaClient
from src.infrastructure.clients.ths import THSClient, _previous_year_same_day
from src.infrastructure.clients.tencent import TencentClient

SINA_MODULE = importlib.import_module("src.infrastructure.clients.sina")
THS_MODULE = importlib.import_module("src.infrastructure.clients.ths")
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "market_data"


class _Response:
    def __init__(self, *, content: bytes = b"", payload=None, text: str = ""):
        self.content = content
        self._payload = payload
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _HttpClient:
    def __init__(self, response: _Response):
        self.response = response
        self.calls = 0

    async def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.response


class _FallbackHttpClient(_HttpClient):
    async def get(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            raise httpx.RemoteProtocolError("realtime host disconnected")
        return self.response


class _ThsEtfFlowHttpClient:
    async def post(self, url: str, **_kwargs):
        if url.endswith("/quotation/data/query/v1/table"):
            return _Response(
                payload={
                    "status_code": 0,
                    "data": {
                        "data": [
                            {
                                "values": [
                                    {"value": "-300000000"}
                                ]
                            }
                        ]
                    },
                }
            )
        return _Response(
            payload={
                "status_code": 0,
                "data": {
                    "total": 456,
                    "itemList": [
                        [
                            "1583451900",
                            "36",
                            "159845",
                            "华夏中证1000ETF",
                            "36:159845",
                        ]
                    ],
                    "indexes": [
                        {
                            "type": "estimation_net_inflow_etf",
                            "desc": (
                                "根据深市数据计算\n"
                                "计算公式：（申购量-赎回量）*IOPV"
                            ),
                        },
                        {"type": "subMarket"},
                        {"type": "tradeCode"},
                        {"type": "simpleName"},
                        {"type": "code"},
                    ],
                },
            }
        )

    async def get(self, _url: str, **_kwargs):
        return _Response(
            payload={
                "status_code": 0,
                "data": {
                    "time_range": ["1785481140", "1785481200"],
                    "data": [
                        {
                            "values": [
                                {
                                    "values": [
                                        "-200000000",
                                        "-300000000",
                                    ]
                                }
                            ]
                        }
                    ],
                },
            }
        )


@pytest.mark.asyncio
async def test_sina_global_index_defaults_cover_major_markets() -> None:
    requested: list[str] = []

    async def fetch_hq(_self, symbols: str) -> str:
        requested.extend(symbols.split(","))
        return "\n".join(
            [
                'var hq_str_b_NKY="日经225指数,40000,100,0.25";',
                'var hq_str_b_KOSPI="韩国KOSPI指数,3000,30,1.00";',
                'var hq_str_b_KOSDAQ="韩国高斯达克指数,900,9,1.01";',
            ]
        )

    client = SinaClient()
    client._fetch_hq = MethodType(fetch_hq, client)
    try:
        result = await client.get_global_index()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert {"b_NKY", "b_KOSPI", "b_KOSDAQ"}.issubset(requested)
    assert [item["name"] for item in result["data"]["indices"]] == [
        "日经225指数",
        "韩国KOSPI指数",
        "韩国高斯达克指数",
    ]


@pytest.mark.asyncio
async def test_ths_etf_estimated_flow_keeps_app_methodology() -> None:
    client = THSClient()
    original_client = client._client
    client._client = _ThsEtfFlowHttpClient()
    await original_client.aclose()

    result = await client.get_etf_estimated_net_inflow()

    assert result["status"] == "ok"
    assert result["data"]["total_net_inflow_yuan"] == -300000000.0
    assert result["data"]["coverage_market"] == "szse_etf"
    assert result["data"]["top_inflow"] == {
        "code": "159845",
        "name": "华夏中证1000ETF",
        "market": "sz",
        "net_inflow_yuan": 1583451900.0,
    }
    assert result["provider_metadata"]["is_official_subscription"] is False
    assert result["trade_date"] == "2026-07-31"


def _native_unified_response(
    request: httpx.Request,
    online_id: str,
    data: dict,
) -> httpx.Response:
    encoded = base64.b64encode(
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return httpx.Response(
        200,
        request=request,
        json={
            "success": True,
            "onlineId": online_id,
            "response": {
                "head": {
                    "errorCode": 0,
                    "errorMsg": "",
                    "onlineId": online_id,
                    "type": "RES",
                },
                "body": {"data": encoded, "type": 5},
            },
        },
    )


@pytest.mark.asyncio
async def test_ths_native_market_anomalies_decode_unified_payloads() -> None:
    requests: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests[payload["onlineId"]] = payload
        if payload["onlineId"] == "dpydLine":
            return _native_unified_response(
                request,
                "dpydLine",
                {"content": {"10": [3800.0], "19": [100.0]}},
            )
        if payload["onlineId"] == "marketLabel":
            return _native_unified_response(
                request,
                "marketLabel",
                {"mobiledpyd": [{"title": "指数快速下探"}]},
            )
        if payload["onlineId"] == "ggList":
            return _native_unified_response(
                request,
                "ggList",
                {"dxjl": [{"stockcode": "600000", "time": 1785481200}]},
            )
        if payload["onlineId"] == "blockList":
            return _native_unified_response(
                request,
                "blockList",
                {"block_dxjl": [{"stockcode": "885700", "time": 1785481199}]},
            )
        return _native_unified_response(
            request,
            "largeOrderList",
            {"dbwt": [{"stockcode": "600001", "time": 1785481198}]},
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_market_anomalies()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["count"] == 5
    assert result["data"]["market_events"][0]["title"] == "指数快速下探"
    assert result["data"]["stock_events"][0]["stockcode"] == "600000"
    assert result["data"]["sector_events"][0]["stockcode"] == "885700"
    assert result["data"]["large_order_events"][0]["stockcode"] == "600001"
    assert "max_msg_num=500" in requests["ggList"]["requestDic"]
    assert "key=block_dxjl" in requests["blockList"]["requestDic"]
    assert "key=dbwt" in requests["largeOrderList"]["requestDic"]


@pytest.mark.asyncio
async def test_ths_native_market_anomalies_skip_detail_buffers_for_stream_mode() -> None:
    requested_online_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        online_id = payload["onlineId"]
        requested_online_ids.append(online_id)
        if online_id == "dpydLine":
            return _native_unified_response(
                request,
                online_id,
                {"content": {"10": [3800.0], "19": [100.0]}},
            )
        return _native_unified_response(
            request,
            online_id,
            {"mobiledpyd": [{"title": "指数快速下探"}]},
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_market_anomalies(
            include_detail_events=False
        )
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert requested_online_ids == ["dpydLine", "marketLabel"]
    assert result["data"]["stock_events"] == []
    assert result["provider_metadata"]["detail_event_mode"] == "persistent_stream"


@pytest.mark.asyncio
async def test_ths_native_call_auction_normalizes_app_sections() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["onlineId"] == "jjData":
            return _native_unified_response(
                request,
                "jjData",
                {
                    "dpjjyd_stock": [
                        {
                            "callAuctionStage": 2,
                            "isCallAuction": False,
                            "callAuctionHotStock": [{"stockCode": "600001"}],
                            "callAuctionHotStockNew": [],
                            "callAuctionLimitUpStock": [{"stockCode": "000001"}],
                            "callAuctionPlate": [{"plateName": "半导体"}],
                        }
                    ]
                },
            )
        return _native_unified_response(
            request,
            "jjLine",
            {"dpjjyd_cas_1A0001": [{"cas_position": 1}]},
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_call_auction()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["count"] == 4
    assert result["data"]["stage"] == 2
    assert result["data"]["hot_sectors"][0]["plateName"] == "半导体"
    assert result["data"]["line"][0]["cas_position"] == 1


@pytest.mark.asyncio
async def test_ths_native_stock_ranking_uses_verified_unified_fields() -> None:
    columns = {
        "4": ["301550", "688626"],
        "5": ["301550", "688626"],
        "55": ["斯菱智驱", "翔宇医疗"],
        "10": ["72.44", "42.80"],
        "34818": ["14.04%", "5.52%"],
        "48": ["2.69%", "2.08%"],
        "19": ["5.95亿", "1.36亿"],
        "34311": ["3.21", "2.18"],
        "34312": ["12.34%", "8.76%"],
        "34370": ["-0.19", "-0.14"],
        "34391": ["-2733.8万", "-919.1万"],
        "34819": ["16.25%", "9.11%"],
        "36072": ["汽车零部件", "医疗器械"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/native/unified"
        request_payload = json.loads(request.content)
        assert request_payload["protocolId"] == 1208
        assert request_payload["pageId"] == 2312
        assert request_payload["onlineId"] == "zhangsu"
        assert "rowcount=50" in request_payload["requestDic"]
        assert "sortid=48" in request_payload["requestDic"]
        bridge_payload = {
            "success": True,
            "response": {
                "head": {"errorCode": 0, "pageId": 2312, "protocolId": 1208},
                "body": {"dataDict": columns},
            },
        }
        return httpx.Response(200, request=request, json=bridge_payload)

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_stock_ranking("quick", 50)
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["provider"] == "ths_native"
    assert result["provider_metadata"]["channel"] == "android_native_unified_request"
    assert result["data"]["stocks"][:2] == [
        {
            "code": "301550",
            "name": "斯菱智驱",
            "latest": 72.44,
            "change_rate": 14.04,
            "speed": 2.69,
            "turnover": "5.95亿",
            "volume_ratio": 3.21,
            "turnover_rate": 12.34,
            "large_order_ratio": -0.19,
            "main_net_inflow": "-2733.8万",
            "amplitude": 16.25,
            "industry": "汽车零部件",
        },
        {
            "code": "688626",
            "name": "翔宇医疗",
            "latest": 42.8,
            "change_rate": 5.52,
            "speed": 2.08,
            "turnover": "1.36亿",
            "volume_ratio": 2.18,
            "turnover_rate": 8.76,
            "large_order_ratio": -0.14,
            "main_net_inflow": "-919.1万",
            "amplitude": 9.11,
            "industry": "医疗器械",
        },
    ]


@pytest.mark.asyncio
async def test_ths_native_volume_ratio_uses_unified_callback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/native/unified"
        request_payload = json.loads(request.content)
        assert request_payload["onlineId"] == "liangbi"
        assert "rowcount=50" in request_payload["requestDic"]
        assert "sortid=34311" in request_payload["requestDic"]
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "response": {
                    "head": {"errorCode": 0},
                    "body": {
                        "dataDict": {
                            "4": ["000001"],
                            "55": ["平安银行"],
                            "34311": ["3.21"],
                        }
                    },
                },
            },
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_stock_ranking("volume_ratio", 50)
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["provider_metadata"]["channel"] == "android_native_unified_request"
    assert result["data"]["stocks"][0]["volume_ratio"] == 3.21


@pytest.mark.asyncio
async def test_ths_stock_rankings_use_each_native_page_sort_request() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "response": {
                    "head": {"errorCode": 0},
                    "body": {
                        "dataDict": {
                            "4": ["000001", "000002", "000003"],
                            "55": ["甲", "乙", "丙"],
                            "34818": ["1.00%", "-2.00%", "3.00%"],
                            "19": ["1亿", "9000万", "2亿"],
                        }
                    },
                },
            },
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        rise = await client.get_native_stock_ranking("rise", 3)
        fall = await client.get_native_stock_ranking("fall", 3)
        turnover = await client.get_native_stock_ranking("turnover", 3)
    finally:
        await client.close()

    assert request_count == 3
    assert [row["code"] for row in rise["data"]["stocks"]] == [
        "000001",
        "000002",
        "000003",
    ]
    assert [row["code"] for row in fall["data"]["stocks"]] == [
        "000001",
        "000002",
        "000003",
    ]
    assert [row["code"] for row in turnover["data"]["stocks"]] == [
        "000001",
        "000002",
        "000003",
    ]
    assert rise["provider_metadata"]["request_mode"] == "native_rank_exact"


@pytest.mark.asyncio
async def test_ths_native_stock_dynamic_groups_keep_full_configuration() -> None:
    config = {
        "data": {
            "gegufeaturelist": [
                {
                    "title": "同花顺热榜",
                    "subtitle": "全市场关注度最高的股票",
                    "highlightTag": "热点",
                    "isShowRanking": "1",
                    "query": "热度最高的前100支股票",
                    "promptId": "prompt-hot",
                    "headers": [
                        {"indicatorId": "10"},
                        {"indicatorId": "34818"},
                    ],
                    "sortHeader": {"indicatorId": "", "sortOrder": ""},
                    "jumpUrl": "https://example.test/hot",
                    "subtitleJumpUrl": "https://example.test/hot",
                    "data_code": "rebanggegu1h",
                    "key": "rebanggegu1h",
                },
                {
                    "title": "低估值高成长",
                    "subtitle": "",
                    "highlightTag": "热点",
                    "isShowRanking": "0",
                    "query": "低估值高成长股票",
                    "promptId": "prompt-growth",
                    "headers": [
                        {"indicatorId": "10"},
                        {"indicatorId": "34818"},
                    ],
                    "sortHeader": {"indicatorId": "", "sortOrder": ""},
                    "jumpUrl": "https://example.test/growth",
                    "subtitleJumpUrl": "",
                    "data_code": "diguzhigaochengzhang",
                    "key": "diguzhigaochengzhang",
                }
            ]
        }
    }

    requested_counts: list[tuple[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/config_list"):
            return httpx.Response(200, request=request, json=config)
        if request.url.path == "/native/unified":
            return httpx.Response(
                200,
                request=request,
                json={
                    "success": True,
                    "response": {
                        "head": {"errorCode": 0},
                        "body": {
                            "dataDict": {
                                "4": ["688825"],
                                "55": ["C长鑫"],
                                "10": ["53.97"],
                                "34818": ["2.08%"],
                                "48": ["0.12%"],
                            }
                        },
                    },
                },
            )
        assert request.url.path == "/native/hurricane"
        request_payload = json.loads(request.content)
        requested_counts.append(
            (request_payload["hurricane_ids"][0], request_payload["count"])
        )
        assert request_payload["hurricane_type"] == "PROMPT_CODE"
        assert request_payload["hurricane_ids"][0] in {
            "prompt-hot",
            "prompt-growth",
        }
        assert request_payload["http_source_id"] == "securities-ranking-slider"
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "data": {
                    "total": 1,
                    "rows": [
                        {
                            "code": "688825",
                            "market": "17",
                            "name": "C长鑫",
                            "indicators": {
                                "10": {"content": "53.97"},
                                "34818": {"content": "2.08%"},
                            },
                        }
                    ],
                },
            },
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_stock_dynamic_groups(100)
        homepage_result = await client.get_native_stock_dynamic_groups(
            4,
            homepage_layout=True,
        )
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["provider_metadata"]["channel"] == "android_native_hurricane"
    group = result["data"]["groups"][0]
    assert group["data_code"] == "rebanggegu1h"
    assert group["is_show_ranking"] is True
    assert group["jump_url"] == "https://example.test/hot"
    assert group["stocks"][0]["name"] == "C长鑫"
    assert group["stocks"][0]["change_rate"] == 2.08
    assert requested_counts == [
        ("prompt-hot", 100),
        ("prompt-growth", 100),
        ("prompt-hot", 4),
        ("prompt-growth", 5),
    ]
    homepage_groups = homepage_result["data"]["groups"]
    assert homepage_groups[0]["requested_count"] == 4
    assert homepage_groups[0]["stocks"][0]["speed"] == 0.12
    assert homepage_groups[1]["requested_count"] == 5


@pytest.mark.asyncio
async def test_ths_native_realtime_indicator_names_chart_points() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "status": 0,
                "response_type": 0,
                "key": "sjdp_market_capital",
                "data": {
                    "name": "大盘资金",
                    "summary": {"tips": "测试"},
                    "point_key_list": [
                        "time",
                        "net_inflow",
                        "szzz",
                        "x_index",
                    ],
                    "point_list": [
                        ["202607310930", "62.22", "3833.54", "09:30"]
                    ],
                },
            },
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_realtime_indicator("market_capital")
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["points"] == [
        {
            "time": "202607310930",
            "net_inflow": 62.22,
            "szzz": 3833.54,
            "x_index": "09:30",
        }
    ]


@pytest.mark.asyncio
async def test_ths_northbound_capital_preserves_unavailable_direction() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "data": {
                    "name": "北向资金",
                    "point_key_list": [
                        "time",
                        "net_purchase",
                        "turnover",
                        "turnover_sh",
                        "turnover_sz",
                    ],
                    "point_list": [
                        ["202607311500", None, "3541.02", "1599.27", "1941.75"]
                    ],
                },
            },
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_realtime_indicator(
            "northbound_capital"
        )
    finally:
        await client.close()

    point = result["data"]["points"][0]
    assert point["net_purchase"] is None
    assert point["turnover"] == 3541.02
    assert point["turnover_sh"] == 1599.27
    assert point["turnover_sz"] == 1941.75


@pytest.mark.asyncio
async def test_ths_northbound_turnover_history_uses_daily_points() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sjdp_north_turnover.json")
        return httpx.Response(
            200,
            request=request,
            json={
                "status_code": 0,
                "status_msg": "ok",
                "data": {
                    "key": "sjdp_north_turnover",
                    "name": "北向成交额",
                    "point_key_list": ["date", "turnover", "szzz"],
                    "point_list": [
                        ["20260730", "4193.12", "3867.03"],
                        ["20260731", "3541.02", "3832.26"],
                    ],
                },
            },
        )

    client = THSClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_northbound_turnover_history()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["points"] == [
        {"date": "20260730", "turnover": 4193.12, "szzz": 3867.03},
        {"date": "20260731", "turnover": 3541.02, "szzz": 3832.26},
    ]
    assert result["provider_metadata"]["frequency"] == "daily"


@pytest.mark.asyncio
async def test_ths_market_valuation_thresholds_are_not_current_pe_pb() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "status_code": 0,
                "data": {
                    "key": "sjdp_valuation_hs",
                    "point_key_list": [
                        "date",
                        "szzz",
                        "szzz_risk_pe",
                        "szzz_chance_pe",
                    ],
                    "point_list": [
                        ["20260731", "3832.26", "16.20", "13.80"]
                    ],
                },
            },
        )

    client = THSClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_market_valuation_thresholds()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["points"][0]["szzz_risk_pe"] == 16.2
    assert result["provider_metadata"]["is_current_market_pe_pb"] is False


def test_previous_year_same_day_preserves_market_window() -> None:
    assert _previous_year_same_day(date(2026, 8, 1)) == date(2025, 8, 1)
    assert _previous_year_same_day(date(2024, 2, 29)) == date(2023, 2, 28)


@pytest.mark.asyncio
async def test_ths_native_bond_market_history_uses_page_price_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["protocolId"] == 1234
        assert payload["pageId"] == 2312
        assert "stockcode=T9999" in payload["requestDic"]
        return _native_unified_response(
            request,
            payload["onlineId"],
            {
                "content": {
                    "1": [20260730.0, 20260731.0],
                    "11": [109.36, 109.40],
                },
                "extDataDict": {"55": "十年国债主连"},
            },
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_bond_market_history("long")
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["items"][-1] == {
        "date": "20260731",
        "price": 109.4,
    }
    assert result["data"]["name"] == "十年国债主连"


@pytest.mark.asyncio
async def test_ths_native_bond_benchmark_uses_all_a_market() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "stockcode=883957" in payload["requestDic"]
        assert "marketcode=48" in payload["requestDic"]
        return _native_unified_response(
            request,
            payload["onlineId"],
            {
                "content": {
                    "1": [20260730.0, 20260731.0],
                    "11": [1692.74, 1736.671],
                },
                "extDataDict": {"55": "同花顺全A(沪深京)"},
            },
        )

    client = THSClient(
        native_bridge_url="http://native-bridge",
        native_command_stream_enabled=False,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_native_bond_market_history("benchmark")
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["market_code"] == "48"
    assert result["provider_metadata"]["instrument_type"] == (
        "broad_market_benchmark"
    )


@pytest.mark.asyncio
async def test_ths_index_sentiment_history_aligns_parallel_arrays() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "1B0016": {
                        "time": ["20260729", "20260730"],
                        "price": [2915.19, 2926.48],
                        "sentiment": [53.4, 52.81],
                    }
                }
            },
        )

    client = THSClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_index_sentiment_history("sh50")
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["trade_date"] == "2026-07-30"
    assert result["data"]["items"][-1] == {
        "date": "20260730",
        "price": 2926.48,
        "sentiment": 52.81,
    }


@pytest.mark.asyncio
async def test_sina_bond_futures_parses_financial_futures_layout() -> None:
    async def fetch_hq(_self, _symbols: str) -> str:
        values = ["0"] * 50
        values[0:7] = [
            "109.350",
            "109.425",
            "109.315",
            "109.400",
            "96023",
            "10504061.020",
            "376798",
        ]
        values[26] = "109.400"
        values[36] = "2026-07-31"
        values[37] = "15:15:00"
        values[49] = "10年期国债期货连续"
        return f'var hq_str_nf_T0="{",".join(values)}";'

    client = SinaClient()
    client._fetch_hq = MethodType(fetch_hq, client)
    try:
        result = await client.get_bond_futures()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["futures"][0]["name"] == "10年期国债期货连续"
    assert result["data"]["futures"][0]["price"] == 109.4
    assert result["data"]["futures"][0]["open_interest"] == 376798.0


@pytest.mark.asyncio
async def test_sina_sector_constituents_return_complete_membership() -> None:
    payload = [
        {
            "symbol": "sh600001",
            "code": "600001",
            "name": "示例公司",
            "trade": "12.30",
            "pricechange": 0.1,
            "changepercent": 0.82,
            "settlement": "12.20",
            "open": "12.25",
            "high": "12.40",
            "low": "12.10",
            "volume": 1000,
            "amount": 123000,
            "ticktime": "15:00:00",
            "per": 18.5,
            "pb": 2.1,
            "mktcap": 100000,
            "nmc": 80000,
            "turnoverratio": 1.5,
        }
    ]
    client = SinaClient()
    original_client = client._client
    client._client = _HttpClient(_Response(payload=payload))
    await original_client.aclose()

    result = await client.get_sector_constituents(
        "hangye_ZA01",
        sector_type="industry",
    )

    assert result["status"] == "ok"
    assert result["data"]["count"] == 1
    assert result["data"]["constituents"][0]["stock_code"] == "600001"
    assert result["data"]["constituents"][0]["weight"] is None
    assert result["provider_metadata"]["complete"] is True


@pytest.mark.asyncio
async def test_sina_sector_ranking_returns_complete_normalized_list() -> None:
    payload = json.loads((FIXTURE_ROOT / "sina_sector_ranking.json").read_text())
    body = (
        "var S_Finance_bankuai_industry = "
        + json.dumps(payload, ensure_ascii=False)
        + ";"
    ).encode("gbk")
    client = SinaClient()
    original_client = client._client
    client._client = _HttpClient(_Response(content=body))
    await original_client.aclose()

    result = await SinaClient.get_sector_ranking.__wrapped__(client, "industry", 5)

    assert result["status"] == "ok"
    assert result["data"]["total"] == 1
    sector = result["data"]["sectors"][0]
    assert sector["provider_sector_code"] == "hangye_ZA01"
    assert sector["sector_type"] == "industry"
    assert sector["lead_stock"]["code"] == "sh600001"


@pytest.mark.asyncio
async def test_sina_sector_ranking_reports_parse_error() -> None:
    client = SinaClient()
    original_client = client._client
    client._client = _HttpClient(_Response(content=b"schema changed"))
    await original_client.aclose()

    result = await SinaClient.get_sector_ranking.__wrapped__(client, "concept", 5)

    assert result["status"] == "parse_error"
    assert result["status_code"] == -1


@pytest.mark.asyncio
async def test_sina_kline_treats_null_payload_as_valid_empty_result() -> None:
    client = SinaClient()
    original_client = client._client
    client._client = _HttpClient(_Response(payload=None))
    await original_client.aclose()

    result = await client.get_kline("sh000001")

    assert result["status_code"] == 0
    assert result["data"]["count"] == 0
    assert result["data"]["bars"] == []


@pytest.mark.asyncio
async def test_ths_sector_kline_is_labeled_as_sector_index(monkeypatch) -> None:
    frame = pd.DataFrame(
        [
            {
                "日期": date(2026, 7, 29),
                "开盘价": 100,
                "最高价": 110,
                "最低价": 99,
                "收盘价": 108,
                "成交量": 123,
                "成交额": 456,
            }
        ]
    )
    monkeypatch.setattr(
        THS_MODULE.ak,
        "stock_board_industry_index_ths",
        lambda **_kwargs: frame,
    )
    client = THSClient()
    try:
        result = await client.get_sector_kline("半导体", "industry", "20260701", "20260730")
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["provider_metadata"]["is_sector_index"] is True
    assert result["data"]["bars"][0]["close"] == 108
    assert "lead_stock" not in result["data"]


@pytest.mark.asyncio
async def test_ths_fund_flow_trend_accepts_null_optional_payloads() -> None:
    async def get_scale_change(_self, _fund_code: str):
        return {"status_code": 0, "data": None}

    async def get_holder_ratio(_self, _fund_code: str):
        return {"status_code": 0, "data": None}

    client = THSClient()
    client.get_scale_change = MethodType(get_scale_change, client)
    client.get_holder_ratio = MethodType(get_holder_ratio, client)
    try:
        result = await client.get_fund_flow_trend("960015")
    finally:
        await client.close()

    assert result["status_code"] == 0
    assert result["data"]["quarters"] == []
    assert result["data"]["trend"] == "数据不足"
    assert result["data"]["orgData"] == []
    assert result["data"]["signals"] == []


@pytest.mark.asyncio
async def test_ths_market_limit_counts_do_not_generate_sentiment() -> None:
    async def get_limit_pool(_self, pool_type: str):
        return {
            "status_code": 0,
            "data": {
                "page": {"total": 12 if pool_type == "up" else 3},
                "info": [
                    {
                        "code": "600001",
                        "name": "示例公司",
                        "change_rate": 10 if pool_type == "up" else -10,
                    }
                ],
            },
        }

    client = THSClient()
    client.get_limit_pool = MethodType(get_limit_pool, client)
    try:
        result = await client.get_market_limit_counts()
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["limit_up_count"] == 12
    assert result["data"]["limit_down_count"] == 3
    assert "signal" not in result["data"]
    assert "sentiment" not in result["data"]


@pytest.mark.asyncio
async def test_eastmoney_market_breadth_uses_provider_aggregate_fields() -> None:
    payload = {
        "data": {
            "diff": [
                {
                    "f12": "000001",
                    "f14": "上证指数",
                    "f2": 3804.69,
                    "f3": -0.62,
                    "f4": -23.78,
                    "f5": 592298923,
                    "f6": 1106477266461.8,
                    "f104": 897,
                    "f105": 1396,
                    "f106": 58,
                    "f124": 1785399121,
                },
                {
                    "f12": "399001",
                    "f14": "深证成指",
                    "f2": 13285.8,
                    "f3": -2.73,
                    "f4": -372.64,
                    "f5": 686984750,
                    "f6": 1236331989082.34,
                    "f104": 786,
                    "f105": 2068,
                    "f106": 70,
                    "f124": 1785399121,
                },
                {
                    "f12": "399006",
                    "f14": "创业板指",
                    "f2": 3244.62,
                    "f3": -3.97,
                    "f4": -134.08,
                    "f5": 100,
                    "f6": 200,
                    "f104": 0,
                    "f105": 0,
                    "f106": 0,
                    "f124": 1785399121,
                },
                {
                    "f12": "000002",
                    "f14": "Ａ股指数",
                    "f2": 3989.07,
                    "f3": -0.62,
                    "f4": -25.08,
                    "f5": 591773917,
                    "f6": 1105422533933,
                    "f104": 871,
                    "f105": 1386,
                    "f106": 53,
                    "f124": 1785399121,
                },
                {
                    "f12": "399107",
                    "f14": "深证Ａ指",
                    "f2": 2482.69,
                    "f3": -2.46,
                    "f4": -62.72,
                    "f5": 686692068,
                    "f6": 1236225466163.88,
                    "f104": 765,
                    "f105": 2061,
                    "f106": 61,
                    "f124": 1785399121,
                },
                {
                    "f12": "899050",
                    "f14": "北证50",
                    "f2": 1049.07,
                    "f3": -1.43,
                    "f4": -15.22,
                    "f5": 8030178,
                    "f6": 15846647400,
                    "f104": 132,
                    "f105": 188,
                    "f106": 11,
                    "f124": 1785399121,
                },
            ]
        }
    }
    payload["data"]["diff"].extend(
        {
            "f12": code,
            "f14": name,
            "f2": 1000,
            "f3": 1.2,
            "f4": 12,
            "f5": 100,
            "f6": 200,
            "f104": 0,
            "f105": 0,
            "f106": 0,
            "f124": 1785399121,
        }
        for code, name in (
            ("000680", "科创综指"),
            ("000688", "科创50"),
            ("000510", "中证A500"),
            ("000300", "沪深300"),
            ("000852", "中证1000"),
            ("000016", "上证50"),
            ("000905", "中证500"),
            ("399330", "深证100"),
            ("000698", "科创100"),
        )
    )
    client = EastmoneyClient()
    original_client = client._client
    fake_client = _HttpClient(_Response(payload=payload))
    client._client = fake_client
    await original_client.aclose()

    result = await client.get_market_breadth()

    assert result["status"] == "ok"
    assert result["data"]["up_count"] == 1768
    assert result["data"]["down_count"] == 3635
    assert result["data"]["flat_count"] == 125
    assert result["data"]["covered_security_count"] == 5528
    assert result["data"]["turnover"] == pytest.approx(2358655902944.14)
    assert len(result["data"]["indices"]) == 13
    assert [item["name"] for item in result["data"]["indices"]] == [
        "上证指数",
        "深证成指",
        "创业板指",
        "北证50",
        "科创综指",
        "科创50",
        "中证A500",
        "沪深300",
        "中证1000",
        "上证50",
        "中证500",
        "深证100",
        "科创100",
    ]
    assert result["provider_metadata"]["single_request"] is True
    assert result["provider_metadata"]["freshness"] == "realtime"
    assert result["provider_metadata"]["delayed_fallback"] is False
    assert result["source_time"] == "2026-07-30T16:12:01+08:00"
    assert result["trade_date"] == "2026-07-30"
    assert result["data"]["indices"][0]["source_time"] == result["source_time"]
    assert fake_client.calls == 1

    fallback_client = EastmoneyClient()
    original_client = fallback_client._client
    fallback_http = _FallbackHttpClient(_Response(payload=payload))
    fallback_client._client = fallback_http
    await original_client.aclose()

    fallback_result = await fallback_client.get_market_breadth()

    assert fallback_result["status"] == "ok"
    assert fallback_result["provider_metadata"]["freshness"] == "delayed"
    assert fallback_result["provider_metadata"]["delayed_fallback"] is True
    assert fallback_http.calls == 2


@pytest.mark.asyncio
async def test_eastmoney_intraday_turnover_aligns_previous_trade_date() -> None:
    payload = {
        "data": {
            "name": "市场指数",
            "trends": [
                "2026-07-30 09:30,1,1,1,1,10,100.00,1",
                "2026-07-30 09:31,1,1,1,1,20,200.00,1",
                "2026-07-31 09:30,1,1,1,1,15,150.00,1",
                "2026-07-31 09:31,1,1,1,1,25,250.00,1",
            ],
        }
    }
    client = EastmoneyClient()
    original_client = client._client
    fake_client = _HttpClient(_Response(payload=payload))
    client._client = fake_client
    await original_client.aclose()

    result = await client.get_market_intraday_turnover_comparison()

    assert result["status"] == "ok"
    assert result["trade_date"] == "2026-07-31"
    assert result["data"]["previous_trade_date"] == "2026-07-30"
    assert result["data"]["comparison_time"] == "09:31"
    assert result["data"]["current_turnover"] == 1200
    assert result["data"]["previous_turnover"] == 900
    assert len(result["data"]["components"]) == 3
    assert fake_client.calls == 3


@pytest.mark.asyncio
async def test_sina_etf_catalog_keeps_quote_identity(monkeypatch) -> None:
    frame = pd.DataFrame(
        [
            {
                "代码": "sh510300",
                "名称": "沪深300ETF",
                "最新价": 4.5,
                "涨跌额": 0.1,
                "涨跌幅": 2.2,
                "昨收": 4.4,
                "今开": 4.45,
                "最高": 4.55,
                "最低": 4.42,
                "成交量": 1000,
                "成交额": 4500,
            }
        ]
    )
    monkeypatch.setattr(
        SINA_MODULE.ak,
        "fund_etf_category_sina",
        lambda **_kwargs: frame,
    )
    client = SinaClient()
    try:
        result = await client.get_etf_catalog()
    finally:
        await client.close()

    etf = result["data"]["etfs"][0]
    assert etf["code"] == "510300"
    assert etf["market"] == "sh"
    assert etf["trading_status"] == "unknown"


@pytest.mark.asyncio
async def test_eastmoney_sector_network_error_is_not_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked", request=request)

    client = EastmoneyClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_sector_list("industry")
    finally:
        await client.close()

    assert result["status"] == "upstream_error"
    assert result["status"] != "empty"


@pytest.mark.asyncio
async def test_eastmoney_sector_fixture_preserves_bk_code() -> None:
    payload = json.loads((FIXTURE_ROOT / "eastmoney_sector_list.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=payload)

    client = EastmoneyClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_sector_list("industry")
    finally:
        await client.close()

    sector = result["data"]["sectors"][0]
    assert result["status"] == "ok"
    assert sector["provider_sector_code"] == "BK0475"
    assert sector["sector_type"] == "industry"
    assert sector["lead_stock_code"] == "600036"


@pytest.mark.asyncio
async def test_eastmoney_sector_constituents_preserve_membership() -> None:
    payload = {
        "data": {
            "total": 1,
            "diff": [
                {
                    "f2": 12.3,
                    "f3": 1.2,
                    "f5": 100,
                    "f6": 123456,
                    "f8": 2.1,
                    "f9": 18.5,
                    "f12": "600001",
                    "f14": "示例公司",
                    "f15": 12.5,
                    "f16": 12.0,
                    "f17": 12.1,
                    "f18": 12.15,
                    "f20": 1000000000,
                    "f21": 800000000,
                }
            ],
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=payload)

    client = EastmoneyClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_sector_constituents("BK0475")
    finally:
        await client.close()

    constituent = result["data"]["constituents"][0]
    assert result["status"] == "ok"
    assert result["data"]["provider_sector_code"] == "BK0475"
    assert constituent["stock_code"] == "600001"
    assert constituent["weight"] is None
    assert result["provider_metadata"]["weight_available"] is False


@pytest.mark.asyncio
async def test_eastmoney_hot_board_sorts_normalized_sector_facts() -> None:
    async def get_sector_list(_self, sector_type: str):
        return {
            "status": "ok",
            "data": {
                "sectors": [
                    {
                        "provider_sector_code": "BK0001",
                        "sector_name": "板块一",
                        "change_pct": 1,
                        "main_net_inflow": 10,
                        "change_5d_pct": 2,
                    },
                    {
                        "provider_sector_code": "BK0002",
                        "sector_name": "板块二",
                        "change_pct": 3,
                        "main_net_inflow": 5,
                        "change_5d_pct": 1,
                    },
                ]
            },
        }

    client = EastmoneyClient()
    client.get_sector_list = MethodType(get_sector_list, client)
    try:
        result = await client.get_hot_board("industry", "rise", 1)
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["boards"][0]["code"] == "BK0002"
    assert result["provider"] == "eastmoney"


@pytest.mark.asyncio
async def test_eastmoney_minute_kline_keeps_legacy_alias() -> None:
    payload = {
        "rc": 0,
        "data": {
            "klines": ["2026-07-30 09:35,10,11,12,9,100,1000"],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=payload)

    client = EastmoneyClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_sector_minute_kline("BK0475", "2026-07-30")
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["bars"] == result["klines"]
    assert result["klines"][0]["close"] == 11


@pytest.mark.asyncio
async def test_ths_sector_intraday_parses_price_points() -> None:
    payload = {
        "bk_881121": {
            "name": "半导体",
            "pre": "14993.861",
            "date": "20260730",
            "tradeTime": ["0930-1130", "1300-1500"],
            "isTrading": 1,
            "data": (
                "0930,14815.421,3219093200,85.338,37721685;"
                "0931,14820.914,7990637900,81.118,100468075"
            ),
        }
    }
    body = f"callback({json.dumps(payload)});"
    client = THSClient()
    original_client = client._client
    client._client = _HttpClient(_Response(text=body))
    await original_client.aclose()

    result = await client.get_sector_intraday("881121")

    assert result["status"] == "ok"
    assert result["data"]["count"] == 2
    assert result["data"]["points"][0]["price"] == 14815.421
    assert result["data"]["points"][0]["turnover"] == 3219093200
    assert result["provider_metadata"]["has_true_ohlc"] is False


@pytest.mark.asyncio
async def test_ths_concept_intraday_resolves_quote_code() -> None:
    payload = {
        "bk_885908": {
            "name": "第三代半导体",
            "pre": "1000",
            "date": "20260730",
            "data": "0930,1010,200,10,20",
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "gn/detail/code/308700" in str(request.url):
            return httpx.Response(
                200,
                request=request,
                text='<input type="hidden" id="clid" value="885908">',
            )
        return httpx.Response(
            200,
            request=request,
            text=f"callback({json.dumps(payload)});",
        )

    client = THSClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_sector_intraday(
            "308700",
            sector_type="concept",
        )
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["provider_sector_code"] == "308700"
    assert result["data"]["sector_name"] == "第三代半导体"
    assert result["provider_metadata"]["provider_quote_code"] == "885908"


@pytest.mark.asyncio
async def test_eastmoney_sector_margin_returns_direct_industry_statistics() -> None:
    payload = {
        "result": {
            "count": 1,
            "data": [
                {
                    "BOARD_CODE": "1036",
                    "BOARD_NAME": "半导体",
                    "TRADE_DATE": "2026-07-29 00:00:00",
                    "FIN_BALANCE": 100,
                    "FIN_BUY_AMT": 20,
                    "FIN_REPAY_AMT": 10,
                    "FIN_NETBUY_AMT": 10,
                    "LOAN_BALANCE": 5,
                    "LOAN_BALANCE_VOL": 4,
                    "LOAN_SELL_VOL": 3,
                    "LOAN_REPAY_VOL": 2,
                    "FIN_NETSELL_AMT": 1,
                    "MARGIN_BALANCE": 105,
                    "FIN_BALANCE_RATIO": 2.5,
                    "NOTLIMITED_MARKETCAP_A": 4000,
                }
            ],
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["reportName"] == "RPTA_WEB_BKJYMXN"
        assert 'BOARD_TYPE_CODE="005"' in request.url.params["filter"]
        return httpx.Response(200, request=request, json=payload)

    client = EastmoneyClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_sector_margin("industry")
    finally:
        await client.close()

    sector = result["data"]["sectors"][0]
    assert result["status"] == "ok"
    assert sector["provider_sector_code"] == "BK1036"
    assert sector["financing_net_buy"] == 10
    assert sector["margin_balance"] == 105


@pytest.mark.asyncio
async def test_eastmoney_sector_kline_keeps_normalized_and_legacy_bars() -> None:
    payload = {
        "rc": 0,
        "data": {
            "name": "示例板块",
            "klines": ["2026-07-30,10,11,12,9,100,1000"],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=payload)

    client = EastmoneyClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_sector_kline("BK0475")
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["bars"] == result["data"]["klines"]
    assert result["data"]["bars"][0]["close"] == 11
    assert (
        result["provider_metadata"]["selected_host"]
        == EastmoneyClient.PUSH2_DELAY
    )


@pytest.mark.asyncio
async def test_sina_futures_term_structure_marks_open_interest_leader(
    monkeypatch,
) -> None:
    contract_frame = pd.DataFrame(
        [
            {
                "合约代码": "au2608",
                "上市日": date(2025, 8, 18),
                "到期日": date(2026, 8, 17),
                "最后交割日": date(2026, 8, 19),
            },
            {
                "合约代码": "au2610",
                "上市日": date(2025, 10, 16),
                "到期日": date(2026, 10, 15),
                "最后交割日": date(2026, 10, 19),
            },
        ]
    )

    def contract_info(*, date: str):
        assert date == "20260730"
        return contract_frame

    def quote(*, symbol: str, market: str, adjust: str):
        assert market == "CF"
        assert adjust == "0"
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "time": "150000",
                    "open": 880,
                    "high": 890,
                    "low": 875,
                    "current_price": 885,
                    "bid_price": 884.8,
                    "ask_price": 885.2,
                    "volume": 100 if symbol == "AU2608" else 200,
                    "hold": 1000 if symbol == "AU2608" else 2000,
                    "last_close": 882,
                    "last_settle_price": 883,
                }
            ]
        )

    monkeypatch.setattr(SINA_MODULE.ak, "futures_contract_info_shfe", contract_info)
    monkeypatch.setattr(SINA_MODULE.ak, "futures_zh_spot", quote)
    client = SinaClient()
    try:
        result = await client.get_futures_term_structure(
            "AU",
            exchange="SHFE",
            trade_date="20260730",
        )
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["count"] == 2
    assert result["data"]["main_contract_code"] == "AU2610"
    assert result["data"]["contracts"][0]["is_main_contract"] is False
    assert result["data"]["contracts"][1]["is_main_contract"] is True
    assert (
        result["provider_metadata"]["main_contract_method"]
        == "maximum_open_interest_in_returned_curve"
    )


@pytest.mark.asyncio
async def test_sina_dce_term_structure_uses_all_listed_contracts(
    monkeypatch,
) -> None:
    frame = pd.DataFrame(
        [
            {
                "symbol": "M0",
                "trade": 3000,
                "position": 999999,
                "tradedate": "2026-07-30",
            },
            {
                "symbol": "M2609",
                "trade": 3082,
                "position": 1000,
                "volume": 100,
                "ticktime": "15:00:00",
                "tradedate": "2026-07-30",
            },
            {
                "symbol": "M2701",
                "trade": 3142,
                "position": 2000,
                "volume": 200,
                "ticktime": "15:00:00",
                "tradedate": "2026-07-30",
            },
        ]
    )

    def realtime(*, symbol: str):
        assert symbol == "豆粕"
        return frame

    monkeypatch.setattr(SINA_MODULE.ak, "futures_zh_realtime", realtime)
    client = SinaClient()
    try:
        result = await client.get_futures_term_structure(
            "M",
            exchange="DCE",
            trade_date="20260730",
        )
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["count"] == 2
    assert result["data"]["main_contract_code"] == "M2701"
    assert result["data"]["contracts"][0]["contract_month"] == "2026-09"
    assert result["provider_metadata"]["exact_expiration_available"] is False


@pytest.mark.asyncio
async def test_sina_ine_contract_directory_falls_back_to_previous_trade_day(
    monkeypatch,
) -> None:
    payload = {
        "ContractBaseInfo": [
            {
                "INSTRUMENTID": "sc2609",
                "OPENDATE": "20250901",
                "EXPIREDATE": "20260831",
                "ENDDELIVDATE": "20260907",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "20260730" in str(request.url):
            return httpx.Response(404, request=request)
        return httpx.Response(200, request=request, json=payload)

    def quote(*, symbol: str, market: str, adjust: str):
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "time": "150000",
                    "current_price": 550,
                    "hold": 1000,
                    "volume": 100,
                }
            ]
        )

    monkeypatch.setattr(SINA_MODULE.ak, "futures_zh_spot", quote)
    client = SinaClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_futures_term_structure(
            "SC",
            exchange="INE",
            trade_date="20260730",
        )
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["trade_date"] == "2026-07-29"
    assert result["data"]["contracts"][0]["expires_at"] == "2026-08-31"
    assert result["provider_metadata"]["requested_trade_date"] == "2026-07-30"


def test_tencent_etf_market_prefix() -> None:
    assert TencentClient._stock_tencent("510300") == "sh510300"
    assert TencentClient._stock_tencent("159915") == "sz159915"


@pytest.mark.asyncio
async def test_eastmoney_index_daily_bars_normalizes_all_ohlcv_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = EastmoneyClient()
    monkeypatch.setattr(
        client,
        "get_index_kline",
        AsyncMock(
            return_value={
                "data": {
                    "name": "上证指数",
                    "klines": [
                        "2026-08-10,3900,3910,3920,3890,100,200000"
                    ],
                }
            }
        ),
    )
    try:
        result = await client.get_index_daily_bars("上证指数", limit=60)
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["data"]["symbol"] == "000001"
    assert result["data"]["bars"] == [
        {
            "date": "2026-08-10",
            "open": 3900.0,
            "close": 3910.0,
            "high": 3920.0,
            "low": 3890.0,
            "volume": 100.0,
            "turnover": 200000.0,
        }
    ]


@pytest.mark.asyncio
async def test_eastmoney_sector_kline_skips_empty_delay_host() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.host))
        if "push2delay" in str(request.url.host):
            payload = {"rc": 0, "data": {"name": "房地产", "klines": []}}
        else:
            payload = {
                "rc": 0,
                "data": {
                    "name": "房地产",
                    "klines": [
                        "2026-08-12,100,101,102,99,1000,2000"
                    ],
                },
            }
        return httpx.Response(200, request=request, json=payload)

    client = EastmoneyClient()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_sector_kline("BK1202", limit=60)
    finally:
        await client.close()

    assert len(calls) == 2
    assert result["status"] == "ok"
    assert result["data"]["bars"][-1]["date"] == "2026-08-12"
    assert result["provider_metadata"]["delayed_fallback"] is False


@pytest.mark.asyncio
async def test_ths_native_security_daily_bars_aligns_protocol_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = THSClient()
    monkeypatch.setattr(
        client,
        "_request_native_unified",
        AsyncMock(
            return_value={
                "data": {
                    "content": {
                        "1": [20260811.0, 20260812.0],
                        "7": [100.0, 101.0],
                        "8": [103.0, 104.0],
                        "9": [99.0, 100.0],
                        "11": [102.0, 103.0],
                        "13": [1000.0, 1200.0],
                    },
                    "extDataDict": {"55": "半导体"},
                }
            }
        ),
    )
    try:
        result = await client.get_native_security_daily_bars(
            "881121", "48", name="半导体", count=120
        )
    finally:
        await client.close()

    assert result["status"] == "ok"
    assert result["provider"] == "ths_native"
    assert result["trade_date"] == "2026-08-12"
    assert result["data"]["bars"][-1] == {
        "date": "2026-08-12",
        "open": 101.0,
        "high": 104.0,
        "low": 100.0,
        "close": 103.0,
        "volume": 1200.0,
    }
    request = client._request_native_unified.await_args.kwargs
    assert request["protocol_id"] == 1234
    assert "stockcode=881121" in request["request_dic"]
    assert "marketcode=48" in request["request_dic"]
    assert "klinecount=500" in request["request_dic"]
