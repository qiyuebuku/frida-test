"""同花顺客户端 - 所有使用 *.10jqka.com.cn 域名的方法"""

import asyncio
import base64
import importlib.util
import json
import logging
import math
import os
import re
import shutil
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx
import akshare as ak

from src.infrastructure.clients.base import BaseClient, cached
from src.infrastructure.clients.market_contracts import (
    MarketDataStatus,
    market_error,
    market_result,
)
from src.infrastructure.clients.ths_native_stream import THSNativeCommandClient


logger = logging.getLogger(__name__)


def _native_number(value: object) -> float | None:
    if value in (None, "", "--"):
        return None
    text = str(value).strip().replace(",", "").rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def _native_kline_number(value: object) -> float | None:
    number = _native_number(value)
    if number is None or number <= -2_147_483_648:
        return None
    return number


def _native_kline_date(value: object) -> str | None:
    number = _native_number(value)
    if number is None:
        return None
    text = str(int(number))
    if len(text) != 8:
        return None
    try:
        return date.fromisoformat(
            f"{text[:4]}-{text[4:6]}-{text[6:]}"
        ).isoformat()
    except ValueError:
        return None


def _native_amount_number(value: object) -> float | None:
    """Normalize THS display amounts so differently suffixed values can sort."""

    if value in (None, "", "--"):
        return None
    text = str(value).strip().replace(",", "")
    units = (
        ("万亿", 100_000_000_000_000.0),
        ("亿", 100_000_000.0),
        ("万", 10_000.0),
    )
    multiplier = 1.0
    for suffix, factor in units:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            multiplier = factor
            break
    try:
        return float(text) * multiplier
    except ValueError:
        return None


ETF_TRACKING_INDEX_UNIQUE_TYPE = "tackMainIndexThscode"


def _previous_year_same_day(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


class THSClient(BaseClient):
    """同花顺数据客户端"""

    BASE_URL = "https://fund.10jqka.com.cn"
    DQ_BASE_URL = "https://dq.10jqka.com.cn"
    THS_DATA = "https://data.10jqka.com.cn"
    THS_LHB_URL = "http://data.10jqka.com.cn/market/longhu/"
    THS_LHB_AJAX = "http://data.10jqka.com.cn/ifmarket/lhbtable"
    HOT_LIST_BASE = "https://eq.10jqka.com.cn/open"
    HOT_TOPIC_BASE = "https://t.10jqka.com.cn"
    HOT_BOND_BASE = "https://dq.10jqka.com.cn"
    NEWS_BASE = "https://news.10jqka.com.cn"
    QUOTE_BASE = "https://d.10jqka.com.cn"
    ETF_ESTIMATED_FLOW_CODE = "48:883957"
    ETF_ESTIMATED_FLOW_INDEX = "estimation_net_inflow_ths_all_a"
    ETF_ESTIMATED_FLOW_POOL = "cd2bda06-22d9-3db5-95d3-456d77b1f82f"
    DEFAULT_NATIVE_BRIDGE_URL = "http://127.0.0.1:49350"
    NATIVE_BRIDGE_LANES = {
        "default",
        "events",
        "hurricane",
        "ranking",
        "realtime",
        "sector_table",
    }
    NATIVE_REALTIME_INDICATORS = {
        "market_capital": "sjdp_market_capital",
        "market_temperature": "sjdp_temperature_hs",
        "northbound_capital": "sjdp_north_capital",
        "ftse_a50": "sjdp_ftse_a50",
        "dow_futures": "sjdp_us_dog",
        "reverse_repo": "sjdp_reverse_repurchase",
        "usd_cny": "sjdp_dollar_rmb",
    }
    INDEX_SENTIMENTS = {
        "sh50": ("1B0016", "上证50"),
        "growth": ("399296", "创成长"),
    }
    BOND_MARKET_INSTRUMENTS = {
        "long": ("T9999", "128", "十年国债主连"),
        "short": ("TS9999", "128", "二年国债主连"),
        "benchmark": ("883957", "48", "同花顺全A(沪深京)"),
    }
    MARKET_VALUATION_URL = (
        "https://eq.10jqka.com.cn/open/api/dapan_v3/chart/"
        "sjdp_valuation_hs.json"
    )
    NORTHBOUND_TURNOVER_URL = (
        "https://eq.10jqka.com.cn/open/api/dapan_v3/chart/"
        "sjdp_north_turnover.json"
    )
    INDEX_SECTOR_URL = (
        "https://dq.10jqka.com.cn/fuyao/fund_fe_tools/fund/v1/index_sector"
    )
    STOCK_DYNAMIC_GROUP_CONFIG_URL = (
        "https://eq.10jqka.com.cn/open/api/dynamic_configuration/v1/"
        "config_list?key=gegufeaturelist"
    )
    COMMODITY_LINKAGE_BASE_URL = (
        "https://eq.10jqka.com.cn/open/api/block_quote/v1/commodity/list"
    )
    SHORT_SPIRIT_MAX_EVENTS = 500
    SHORT_SPIRIT_STOCK_DATA_IDS = (
        "1074269398,1074269399,1074269404,1074269405,"
        "592572,592574,527739,527735,1073744628,1073744629"
    )

    # 公告分类 catId 映射
    ANNOUNCEMENT_CATEGORIES = {
        "all": "0",
        "report": "003001",       # 业绩
        "dividend": "003004",     # 分红
        "change": "003007",       # 变更
        "operation": "003003,003002",  # 运作
        "other": "other",         # 其他
    }

    # 异动类型编码 → 中文名
    CHANGE_TYPES = {
        8201: "火箭发射", 8202: "快速反弹", 8203: "高台跳水", 8204: "加速下跌",
        8207: "竞价上涨", 8208: "竞价下跌", 8209: "高开5日线", 8210: "低开5日线",
        8211: "向上缺口", 8212: "向下缺口", 8213: "60日新高", 8214: "60日新低",
        8215: "60日大幅上涨", 8216: "60日大幅下跌",
        8193: "大笔买入", 8194: "大笔卖出", 64: "有大买盘", 128: "有大卖盘",
        4: "封涨停板", 8: "封跌停板", 16: "打开涨停板", 32: "打开跌停板",
    }
    # 中文名 → 编码（用于用户输入）
    CHANGE_TYPE_ALIAS = {v: k for k, v in CHANGE_TYPES.items()}
    # 预设分组
    CHANGE_TYPE_GROUPS = {
        "all": list(CHANGE_TYPES.keys()),
        "竞价": [8207, 8208],
        "拉升": [8201, 8202],
        "跳水": [8203, 8204],
        "大单": [8193, 8194, 64, 128],
        "涨停": [4, 16],
        "跌停": [8, 32],
        "缺口": [8211, 8212],
        "新高新低": [8213, 8214],
        "大幅": [8215, 8216],
    }

    def __init__(
        self,
        timeout: float = 10.0,
        native_bridge_url: str | None = None,
        app_http_bridge_url: str | None = None,
        native_command_stream_enabled: bool | None = None,
        native_command_host: str | None = None,
        native_command_port: int | None = None,
    ):
        self._native_bridge_url = (
            native_bridge_url
            or os.getenv("THS_NATIVE_BRIDGE_URL")
            or self.DEFAULT_NATIVE_BRIDGE_URL
        ).rstrip("/")
        super().__init__(timeout)
        self._native_request_locks: dict[str, asyncio.Lock] = {}
        self._native_app_transport_lock = asyncio.Lock()
        self._app_http_endpoint_locks: dict[str, asyncio.Lock] = {}
        self._native_load_balanced = os.getenv(
            "THS_NATIVE_LOAD_BALANCED", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._native_command_stream_enabled = (
            native_command_stream_enabled
            if native_command_stream_enabled is not None
            else os.getenv("THS_NATIVE_COMMAND_STREAM_ENABLED", "1") == "1"
        )
        self._native_command_host = native_command_host or os.getenv(
            "THS_NATIVE_COMMAND_HOST", "127.0.0.1"
        )
        self._native_command_port = native_command_port or int(
            os.getenv("THS_NATIVE_COMMAND_PORT", "49302")
        )
        self._native_command_client = THSNativeCommandClient(
            host=self._native_command_host,
            port=self._native_command_port,
        )
        self._native_sector_quote_tables_lock = asyncio.Lock()
        self._native_sector_quote_tables: dict[str, list[dict]] = {}
        self._native_sector_quote_tables_deadline = 0.0
        self._native_stock_quote_table_lock = asyncio.Lock()
        self._native_stock_quote_table: list[dict] = []
        self._native_stock_quote_table_head: dict = {}
        self._native_stock_quote_table_deadline = 0.0
        self._etf_tracking_index_cache: dict[str, dict[str, str | None]] = {}
        self._etf_tracking_index_cache_deadline = 0.0
        self._etf_tracking_index_cache_lock = asyncio.Lock()
        self._us_etf_sector_config_cache: dict = {}
        self._us_etf_sector_config_deadline = 0.0
        self._app_http_bridge_url = (
            app_http_bridge_url
            or os.getenv("THS_APP_HTTP_BRIDGE_URL")
            or self._native_bridge_url
        ).rstrip("/")

    def _native_bridge_for(self, lane: str) -> str:
        if lane not in self.NATIVE_BRIDGE_LANES:
            raise ValueError(f"unknown THS native bridge lane: {lane}")
        return self._native_bridge_url

    def _native_lock_for(self, lane: str) -> asyncio.Lock:
        self._native_bridge_for(lane)
        if self._native_load_balanced:
            return asyncio.Lock()
        # Lanes are logical scheduling labels, not independent transports.
        # A non-load-balanced bridge targets one App process whose native
        # Ranking/Hurricane/Unified calls share global frame state.
        return self._native_app_transport_lock

    def _native_unified_lock(self, protocol_id: int, page_id: int) -> asyncio.Lock:
        if self._native_load_balanced:
            # Each backend proxy enforces single-flight inside one App process.
            # A fresh client-side lock lets independent App processes execute
            # identical protocol/page requests concurrently.
            return asyncio.Lock()
        return self._native_app_transport_lock

    async def _request_native_command(
        self,
        *,
        route: str,
        payload: dict,
        timeout_seconds: float,
    ) -> dict:
        timeout = max(1.0, min(float(timeout_seconds), 180.0))
        return await self._native_command_client.request(
            route=route,
            payload=payload,
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._native_command_client.close()
        await super().close()

    async def get_native_market_anomalies(
        self,
        *,
        include_detail_events: bool = True,
    ) -> dict:
        """获取同花顺 App 口径的大盘和个股异动事件。"""

        try:
            async def detail_payloads() -> tuple[dict | None, dict | None, dict | None]:
                if not include_detail_events:
                    return None, None, None
                stock_payload = await self._request_native_unified(
                    lane="events",
                    online_id="ggList",
                    protocol_id=1004,
                    page_id=6002,
                    request_dic=(
                        "id=1004\r\naction=subscribe\r\nkey=dxjl_free\r\n"
                        f"data_id_list={self.SHORT_SPIRIT_STOCK_DATA_IDS}\r\n"
                        f"max_msg_num={self.SHORT_SPIRIT_MAX_EVENTS}\r\n"
                        "stock_list=all"
                    ),
                    cancel_request_dic=(
                        "id=1004\r\naction=unsubscribe\r\nkey=dxjl_free\r\n"
                        f"data_id_list={self.SHORT_SPIRIT_STOCK_DATA_IDS}\r\n"
                        f"max_msg_num={self.SHORT_SPIRIT_MAX_EVENTS}\r\n"
                        "stock_list=all"
                    ),
                    timeout_seconds=6,
                )
                sector_payload = await self._request_native_unified(
                    lane="events",
                    online_id="blockList",
                    protocol_id=1004,
                    page_id=6002,
                    request_dic=(
                        "action=subscribe\r\nkey=block_dxjl\r\n"
                        "data_id_list=1,2,3,4\r\n"
                        f"max_msg_num={self.SHORT_SPIRIT_MAX_EVENTS}\r\n"
                        "stock_list=all"
                    ),
                    cancel_request_dic=(
                        "action=unsubscribe\r\nkey=block_dxjl\r\n"
                        "data_id_list=1,2,3,4\r\n"
                        f"max_msg_num={self.SHORT_SPIRIT_MAX_EVENTS}\r\n"
                        "stock_list=all"
                    ),
                    timeout_seconds=6,
                )
                large_order_payload = await self._request_native_unified(
                    lane="events",
                    online_id="largeOrderList",
                    protocol_id=1004,
                    page_id=6002,
                    request_dic=(
                        "action=subscribe\r\nkey=dbwt\r\n"
                        "data_id_list=133990,133991\r\n"
                        f"max_msg_num={self.SHORT_SPIRIT_MAX_EVENTS}\r\n"
                        "stock_list=all"
                    ),
                    cancel_request_dic=(
                        "action=unsubscribe\r\nkey=dbwt\r\n"
                        "data_id_list=133990,133991\r\n"
                        f"max_msg_num={self.SHORT_SPIRIT_MAX_EVENTS}\r\n"
                        "stock_list=all"
                    ),
                    timeout_seconds=6,
                )
                return stock_payload, sector_payload, large_order_payload

            line_payload, market_payload, detail_values = await asyncio.gather(
                self._request_native_unified(
                    lane="events", online_id="dpydLine", protocol_id=1229,
                    page_id=2312, timeout_seconds=6,
                    request_dic=(
                        "fstrend=1\r\nstockcode=1A0001\r\nmarketcode=16"
                    ),
                ),
                self._request_native_unified(
                    lane="events", online_id="marketLabel", protocol_id=1002,
                    page_id=6000, timeout_seconds=6,
                    request_dic=(
                        "marketcode=16\r\naction=subscribe\r\n"
                        "key=mobiledpyd\r\nstockcode=1A0001"
                    ),
                    cancel_request_dic=(
                        "marketcode=16\r\naction=unsubscribe\r\n"
                        "key=mobiledpyd\r\nstockcode=1A0001"
                    ),
                ),
                detail_payloads(),
            )
            stock_payload, sector_payload, large_order_payload = detail_values
            market_events = self._normalize_market_anomaly_labels(
                market_payload["data"].get("mobiledpyd") or []
            )
            stock_events = (
                (stock_payload or {}).get("data", {}).get("dxjl")
                or (stock_payload or {}).get("data", {}).get("dxjl_free")
                or []
            )
            sector_events = (
                (sector_payload or {}).get("data", {}).get("block_dxjl") or []
            )
            large_order_events = (
                (large_order_payload or {}).get("data", {}).get("dbwt") or []
            )
            curve_payload = line_payload["data"].get("content") or {}
            if not isinstance(curve_payload, dict):
                curve_payload = {}
            index_values = curve_payload.get("10") or []
            turnover_values = curve_payload.get("19") or []
            time_keys = curve_payload.get("1") or []
            ext_data = (
                line_payload["data"].get("extDataDict")
                or line_payload["data"].get("exDataDict")
                or {}
            )
            if isinstance(ext_data, str):
                ext_data = json.loads(ext_data)
            center = _native_number(ext_data.get("6"))
            low = _native_number(ext_data.get("8"))
            high = _native_number(ext_data.get("9"))
            radius = (
                max(abs(high - center), abs(low - center))
                if center is not None and low is not None and high is not None
                else None
            )
            curve = [
                {
                    "position": position,
                    "time_key": (
                        time_keys[position]
                        if position < len(time_keys)
                        else None
                    ),
                    "index_value": value,
                    "turnover": (
                        turnover_values[position]
                        if position < len(turnover_values)
                        else None
                    ),
                }
                for position, value in enumerate(index_values)
            ]
            source_timestamp = max(
                (
                    int(item.get("time"))
                    for item in [
                        *market_events,
                        *stock_events,
                        *sector_events,
                        *large_order_events,
                    ]
                    if str(item.get("time") or "").isdigit()
                ),
                default=None,
            )
            observed_at = (
                datetime.fromtimestamp(source_timestamp, tz=timezone.utc)
                if source_timestamp is not None
                else None
            )

            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "count": (
                        len(market_events)
                        + len(stock_events)
                        + len(sector_events)
                        + len(large_order_events)
                        + len(curve)
                    ),
                    "market_events": market_events,
                    "stock_events": stock_events,
                    "sector_events": sector_events,
                    "large_order_events": large_order_events,
                    "curve": curve,
                    "axis": {
                        "center": center,
                        "min": center - radius if radius is not None else None,
                        "max": center + radius if radius is not None else None,
                        "percent_min": -radius / center * 100 if radius and center else None,
                        "percent_max": radius / center * 100 if radius and center else None,
                    },
                },
                observed_at=observed_at,
                source_time=(observed_at.isoformat() if observed_at else None),
                trade_date=(
                    observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
                    if observed_at
                    else None
                ),
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "channel": "android_native_unified_request",
                    "line_response_head": line_payload["head"],
                    "market_response_head": market_payload["head"],
                    "stock_response_head": (stock_payload or {}).get("head"),
                    "sector_response_head": (sector_payload or {}).get("head"),
                    "large_order_response_head": (large_order_payload or {}).get(
                        "head"
                    ),
                    "detail_event_mode": (
                        "one_shot" if include_detail_events else "persistent_stream"
                    ),
                    "source_time_available": observed_at is not None,
                    "short_spirit_max_events": self.SHORT_SPIRIT_MAX_EVENTS,
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_native",
                market="cn",
                error=exc,
                provider_metadata={
                    "channel": "android_native_unified_request",
                    "capability": "market_anomalies",
                },
            )

    @staticmethod
    def _normalize_market_anomaly_labels(messages: list[dict]) -> list[dict]:
        """Flatten mobiledpyd envelopes into drawable THS anomaly labels."""

        labels: list[dict] = []
        for message in messages:
            if (
                isinstance(message, dict)
                and "value" not in message
                and "data" not in message
                and (message.get("title") or message.get("time"))
            ):
                labels.append(message)
                continue
            payload: object = message
            if isinstance(message, dict) and message.get("value"):
                payload = message["value"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            events = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, dict) or not event.get("isdraw"):
                    continue
                event_time = event.get("ctime")
                for detail in event.get("info") or []:
                    if not isinstance(detail, dict) or not detail.get("isdraw", 1):
                        continue
                    timestamp = detail.get("time") or event_time
                    position = None
                    if str(timestamp or "").isdigit():
                        point_time = datetime.fromtimestamp(
                            int(timestamp), tz=timezone.utc
                        ).astimezone(ZoneInfo("Asia/Shanghai"))
                        minutes = point_time.hour * 60 + point_time.minute
                        position = (
                            minutes - (9 * 60 + 30)
                            if minutes <= 11 * 60 + 30
                            else 121 + minutes - 13 * 60
                        )
                        position = max(0, min(240, position))
                    labels.append(
                        {
                            **detail,
                            "time": timestamp,
                            "ctime": event_time,
                            "position": position,
                            "title": detail.get("title") or "大盘异动",
                            "reason": detail.get("analysisContent"),
                            "sector_name": detail.get("bkname"),
                        }
                    )
        return labels

    async def get_native_call_auction(self) -> dict:
        """获取同花顺 App 口径的集合竞价热点、板块和竞价轨迹。"""

        try:
            snapshot_payload = await self._request_native_unified(
                lane="events",
                online_id="jjData",
                protocol_id=1004,
                page_id=6002,
                request_dic=(
                    "id=1004\r\n"
                    "action=subscribe\r\n"
                    "key=dpjjyd_stock\r\n"
                    "data_id_list=5\r\n"
                    "max_msg_num=100\r\n"
                    "stock_list=all"
                ),
                cancel_request_dic=(
                    "id=1004\r\n"
                    "action=unsubscribe\r\n"
                    "key=dpjjyd_stock\r\n"
                    "data_id_list=5\r\n"
                    "max_msg_num=100\r\n"
                    "stock_list=all"
                ),
            )
            line_payload = await self._request_native_unified(
                lane="events",
                online_id="jjLine",
                protocol_id=1004,
                page_id=6002,
                request_dic=(
                    "action=subscribe\r\n"
                    "key=dpjjyd_cas_1A0001\r\n"
                    "data_id_list=5\r\n"
                    "max_msg_num=100\r\n"
                    "stock_list=all"
                ),
                cancel_request_dic=(
                    "action=unsubscribe\r\n"
                    "key=dpjjyd_cas_1A0001\r\n"
                    "data_id_list=5\r\n"
                    "max_msg_num=100\r\n"
                    "stock_list=all"
                ),
            )
            snapshots = snapshot_payload["data"].get("dpjjyd_stock") or []
            snapshot = snapshots[0] if snapshots else {}
            line = line_payload["data"].get("dpjjyd_cas_1A0001") or []
            hot_stocks = snapshot.get("callAuctionHotStock") or []
            new_hot_stocks = snapshot.get("callAuctionHotStockNew") or []
            limit_up_stocks = snapshot.get("callAuctionLimitUpStock") or []
            hot_sectors = snapshot.get("callAuctionPlate") or []
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "count": (
                        len(hot_stocks)
                        + len(new_hot_stocks)
                        + len(limit_up_stocks)
                        + len(hot_sectors)
                        + len(line)
                    ),
                    "stage": snapshot.get("callAuctionStage"),
                    "is_call_auction": snapshot.get("isCallAuction"),
                    "hot_stocks": hot_stocks,
                    "new_hot_stocks": new_hot_stocks,
                    "limit_up_stocks": limit_up_stocks,
                    "hot_sectors": hot_sectors,
                    "line": line,
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "channel": "android_native_unified_request",
                    "snapshot_response_head": snapshot_payload["head"],
                    "line_response_head": line_payload["head"],
                    "source_time_available": False,
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_native",
                market="cn",
                error=exc,
                provider_metadata={
                    "channel": "android_native_unified_request",
                    "capability": "call_auction",
                },
            )

    async def _request_native_stock_quote_table(self) -> tuple[list[dict], dict]:
        """Fetch one complete A-share table shared by all nine rankings."""

        async with self._native_stock_quote_table_lock:
            now = asyncio.get_running_loop().time()
            if (
                self._native_stock_quote_table
                and now < self._native_stock_quote_table_deadline
            ):
                return (
                    list(self._native_stock_quote_table),
                    dict(self._native_stock_quote_table_head),
                )

            unified_timeout = max(
                5,
                min(
                    int(os.getenv("THS_STOCK_RANKING_TIMEOUT_SECONDS", "20")),
                    30,
                ),
            )
            payload = await self._request_native_unified(
                lane="ranking",
                online_id="stockRankingCompleteTable",
                protocol_id=1208,
                page_id=2312,
                request_dic=(
                    "startrow=0\r\n"
                    "rowcount=6000\r\n"
                    "marketId=0\r\n"
                    "sortorder=0\r\n"
                    "sortid=34818"
                ),
                timeout_seconds=unified_timeout,
            )
            columns = payload["data"].get("dataDict") or {}
            if not isinstance(columns, dict):
                columns = {}
            row_count = max(
                (
                    len(value)
                    for value in columns.values()
                    if isinstance(value, list)
                ),
                default=0,
            )

            def get_value(key: str, index: int) -> object:
                values = columns.get(key)
                if isinstance(values, list) and index < len(values):
                    return values[index]
                return None

            rows: list[dict] = []
            for index in range(row_count):
                code = get_value("4", index) or get_value("5", index)
                name = get_value("55", index)
                if not code or not name:
                    continue
                turnover = get_value("19", index)
                main_net_inflow = get_value("34391", index)
                rows.append(
                    {
                        "code": code,
                        "name": name,
                        "latest": _native_number(get_value("10", index)),
                        "change_rate": _native_number(
                            get_value("34818", index)
                        ),
                        "speed": _native_number(get_value("48", index)),
                        "turnover": turnover,
                        "volume_ratio": _native_number(
                            get_value("34311", index)
                        ),
                        "turnover_rate": _native_number(
                            get_value("34312", index)
                        ),
                        "large_order_ratio": _native_number(
                            get_value("34370", index)
                        ),
                        "main_net_inflow": main_net_inflow,
                        "amplitude": _native_number(
                            get_value("34819", index)
                        ),
                        "industry": get_value("36072", index),
                        "_turnover_sort": _native_amount_number(turnover),
                        "_main_net_inflow_sort": _native_amount_number(
                            main_net_inflow
                        ),
                    }
                )
            if not rows:
                raise ValueError("THS complete stock quote table is empty")

            self._native_stock_quote_table = rows
            self._native_stock_quote_table_head = dict(payload["head"])
            self._native_stock_quote_table_deadline = now + 10.0
            return list(rows), dict(payload["head"])

    async def get_native_stock_ranking(
        self,
        sort: str = "rise",
        count: int = 20,
    ) -> dict:
        """获取与同花顺 App 股票排行一致的字段和排序口径。"""

        sort_config = {
            "rise": ("zhangfu", 34818, 0),
            "fall": ("diefu", 34818, 1),
            "quick": ("zhangsu", 48, 0),
            "turnover": ("chengjiaoe", 19, 0),
            "large_order": ("dadanjingliang", 34370, 0),
            "volume_ratio": ("liangbi", 34311, 0),
            "turnover_rate": ("huanshoulv", 34312, 0),
            "main_net_inflow": ("zhulijingliuru", 34391, 0),
            "amplitude": ("zhenfu", 34819, 0),
        }
        if sort not in sort_config:
            raise ValueError(
                "sort must be rise, fall, quick, turnover, large_order, "
                "volume_ratio, turnover_rate, main_net_inflow or amplitude"
            )
        online_id, sort_id, sort_order = sort_config[sort]
        normalized_count = max(1, min(int(count), 50))
        channel = "android_native_unified_request"
        try:
            payload = await self._request_native_unified(
                lane="ranking",
                online_id=online_id,
                protocol_id=1208,
                page_id=2312,
                request_dic=(
                    "startrow=0\r\n"
                    f"rowcount={normalized_count}\r\n"
                    f"sortorder={sort_order}\r\n"
                    f"sortid={sort_id}\r\n"
                    "newrealtime=0\r\n"
                    "selfstockcustom=1"
                ),
            )
            columns = payload["data"].get("dataDict") or {}
            row_count = max(
                (len(value) for value in columns.values() if isinstance(value, list)),
                default=0,
            )

            def value(key: str, index: int) -> object:
                values = columns.get(key)
                return values[index] if isinstance(values, list) and index < len(values) else None

            ranked_rows = []
            for index in range(row_count):
                code = value("4", index) or value("5", index)
                name = value("55", index)
                if not code or not name:
                    continue
                ranked_rows.append({
                    "code": code,
                    "name": name,
                    "latest": _native_number(value("10", index)),
                    "change_rate": _native_number(value("34818", index)),
                    "speed": _native_number(value("48", index)),
                    "turnover": value("19", index),
                    "volume_ratio": _native_number(value("34311", index)),
                    "turnover_rate": _native_number(value("34312", index)),
                    "large_order_ratio": _native_number(value("34370", index)),
                    "main_net_inflow": value("34391", index),
                    "amplitude": _native_number(value("34819", index)),
                    "industry": value("36072", index),
                })
            stocks = [
                {
                    key: value
                    for key, value in row.items()
                    if not key.startswith("_")
                }
                for row in ranked_rows
            ]
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "sort": sort,
                    "count": len(stocks),
                    "stocks": stocks,
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "channel": channel,
                    "response_head": payload["head"],
                    "request_mode": "native_rank_exact",
                    "source_row_count": row_count,
                    "sort_id": sort_id,
                    "sort_order": sort_order,
                    "market_id": 0,
                    "ranking_protocol_id": 1208,
                    "ranking_page_id": 2312,
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_native",
                market="cn",
                error=exc,
                provider_metadata={
                    "channel": channel,
                    "capability": "stock_ranking",
                    "sort": sort,
                },
            )

    async def get_native_market_profile(self) -> dict:
        """Read the three comparison cards with the exact THS market-page semantics."""

        try:
            yesterday = await self._request_native_unified(
                online_id="profile_yester",
                protocol_id=4052,
                page_id=2312,
                request_dic="startrow=0\r\nrowcount=1\r\nadddata=1",
            )
            cap = await self._request_native_unified(
                online_id="profile_dxp",
                protocol_id=1264,
                page_id=2312,
                request_dic=(
                    "startrow=0\r\nsortid=-1\r\nrowcount=2\r\nnewrealtime=0\r\n"
                    "selfstockcustom=1\r\nupdate=1\r\ncolumnorder=55|4|34338|34818\r\n"
                    "marketlist=16|16\r\nstocklist=1B0300|1B0852"
                ),
            )
            y = yesterday["data"].get("dataDict") or {}
            c = cap["data"].get("dataDict") or {}
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "yesterday_limit": {
                        "title": (y.get("55") or [None])[0],
                        "change_rate": _native_number((y.get("34818") or [None])[0]),
                        "leader_name": (y.get("35284") or [None])[0],
                        "leader_change_rate": _native_number((y.get("35286") or [None])[0]),
                    },
                    "cap_comparison": {
                        "largeCap": {
                            "name": (c.get("55") or [None, None])[0],
                            "code": (c.get("4") or [None, None])[0],
                            "changeRate": _native_number((c.get("34818") or [None, None])[0]),
                        },
                        "smallCap": {
                            "name": (c.get("55") or [None, None])[1],
                            "code": (c.get("4") or [None, None])[1],
                            "changeRate": _native_number((c.get("34818") or [None, None])[1]),
                        },
                    },
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={"channel": "android_native_unified_request"},
            )
        except Exception as exc:
            return market_error(provider="ths_native", market="cn", error=exc)

    async def get_native_limit_comparison(self) -> dict:
        """Read THS hqMarketZdt, whose last point is the page's limit ratio."""

        try:
            response = await self._client.post(
                f"{self._native_bridge_url}/jsbridge",
                json={"handler": "hqMarketZdt", "data": {}},
                timeout=30,
            )
            response.raise_for_status()
            payload: object = response.json()
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict) or not payload.get("success"):
                raise RuntimeError("hqMarketZdt bridge request failed")
            data = payload.get("data")
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                raise ValueError("hqMarketZdt response is not an object")
            all_data = (data.get("all") or {}).get("data") or {}
            zt = (data.get("zt") or [None])[-1] or all_data.get("zt")
            dt = (data.get("dt") or [None])[-1]
            if dt is None:
                dt = all_data.get("dt")
            return market_result(
                provider="ths_native",
                market="cn",
                data={"limit_up": int(zt), "limit_down": int(dt)},
                timezone_name="Asia/Shanghai",
                provider_metadata={"channel": "android_jsbridge_hqMarketZdt"},
            )
        except Exception as exc:
            return market_error(provider="ths_native", market="cn", error=exc)

    async def get_native_stock_dynamic_groups(
        self,
        count: int = 100,
        *,
        homepage_layout: bool = False,
    ) -> dict:
        """获取个股页动态分组配置及每组完整的原生股票列表。"""

        normalized_count = max(1, min(int(count), 100))
        try:
            response = await self._client.get(
                self.STOCK_DYNAMIC_GROUP_CONFIG_URL,
                headers={"Accept": "application/json"},
                timeout=20,
            )
            response.raise_for_status()
            config_payload = response.json()
            groups = (
                (config_payload.get("data") or {}).get("gegufeaturelist")
                or []
            )
            if not isinstance(groups, list) or not groups:
                raise ValueError("THS stock dynamic group configuration is empty")

            results = []
            for display_order, group in enumerate(groups):
                if not isinstance(group, dict):
                    continue
                prompt_id = str(group.get("promptId") or "").strip()
                if not prompt_id:
                    continue
                data_code = str(
                    group.get("data_code") or group.get("key") or prompt_id
                )
                request_count = (
                    5
                    if homepage_layout
                    and not str(group.get("subtitle") or "").strip()
                    else normalized_count
                )
                indicator_ids = ["55"]
                for header in group.get("headers") or []:
                    indicator_id = str(
                        (header or {}).get("indicatorId") or ""
                    ).strip()
                    if indicator_id and indicator_id not in indicator_ids:
                        indicator_ids.append(indicator_id)
                sort_header = group.get("sortHeader") or {}
                sort_indicator_id = str(
                    sort_header.get("indicatorId") or ""
                ).strip() or next(
                    (item for item in indicator_ids if item != "55"),
                    "34818",
                )
                order = (
                    "ASCENDING"
                    if str(sort_header.get("sortOrder") or "") == "1"
                    else "DESCENDING"
                )
                native = await self._request_native_sector_bridge(
                    "/native/hurricane",
                    {
                        "frame_id": 2312,
                        "start": 0,
                        "count": request_count,
                        "hurricane_type": "PROMPT_CODE",
                        "hurricane_ids": [prompt_id],
                        "hurricane_indicator_ids": [],
                        "mobile_indicator_ids": indicator_ids,
                        "sort_indicator_id": sort_indicator_id,
                        "order": order,
                        "http_source_id": "securities-ranking-slider",
                        "timeout_ms": 5000,
                    },
                    lane="hurricane",
                )
                native_data = native.get("data") or {}
                stocks = self._parse_native_dynamic_group_rows(
                    native_data.get("rows") or []
                )
                results.append(
                    {
                        "display_order": display_order,
                        "data_code": data_code,
                        "key": group.get("key"),
                        "title": group.get("title"),
                        "subtitle": group.get("subtitle"),
                        "highlight_tag": group.get("highlightTag"),
                        "is_show_ranking": (
                            str(group.get("isShowRanking") or "") == "1"
                        ),
                        "jump_url": group.get("jumpUrl"),
                        "subtitle_jump_url": group.get("subtitleJumpUrl"),
                        "query": group.get("query"),
                        "prompt_id": prompt_id,
                        "sort_indicator_id": sort_indicator_id,
                        "sort_order": order,
                        "total": native_data.get("total"),
                        "requested_count": request_count,
                        "count": len(stocks),
                        "stocks": stocks,
                    }
                )
            if not results:
                raise ValueError("THS stock dynamic groups returned no definitions")
            if homepage_layout:
                securities = []
                for result in results:
                    for stock in result.get("stocks") or []:
                        if stock.get("code") and stock.get("market_code"):
                            securities.append(
                                (str(stock["code"]), str(stock["market_code"]))
                            )
                quotes = {}
                if securities:
                    try:
                        quotes = await self._request_native_stock_quotes(securities)
                    except Exception as quote_exc:
                        logger.warning(
                            "THS dynamic group quote hydration failed; keeping source quotes: %s",
                            quote_exc,
                        )
                for result in results:
                    for stock in result.get("stocks") or []:
                        quote = quotes.get(str(stock.get("code") or ""))
                        if quote:
                            stock.update(quote)
            return market_result(
                provider="ths_native",
                market="cn",
                data={"count": len(results), "groups": results},
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "channel": "android_native_hurricane",
                    "config_url": self.STOCK_DYNAMIC_GROUP_CONFIG_URL,
                    "requested_count_per_group": normalized_count,
                    "homepage_layout": homepage_layout,
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_native",
                market="cn",
                error=exc,
                provider_metadata={
                    "channel": "android_native_hurricane",
                    "capability": "stock_dynamic_groups",
                },
            )

    @staticmethod
    def _parse_native_dynamic_group_rows(rows: list[dict]) -> list[dict]:
        stocks = []
        for rank, row in enumerate(rows, 1):
            indicators = row.get("indicators") or {}

            def value(indicator_id: str) -> object:
                cell = indicators.get(indicator_id)
                return cell.get("content") if isinstance(cell, dict) else cell

            stocks.append(
                {
                    "rank": rank,
                    "code": row.get("code"),
                    "market_code": row.get("market"),
                    "name": row.get("name") or value("55"),
                    "latest": _native_number(value("10")),
                    "change_rate": _native_number(value("34818")),
                    "speed": _native_number(value("48")),
                    "indicators": {
                        str(indicator_id): (
                            cell.get("content")
                            if isinstance(cell, dict)
                            else cell
                        )
                        for indicator_id, cell in indicators.items()
                    },
                }
            )
        return stocks

    @staticmethod
    def _iwencai_field(row: dict, *prefixes: str) -> object:
        for prefix in prefixes:
            for key, value in row.items():
                if str(key).startswith(prefix) and value not in (None, "", "--"):
                    return value
        return None

    @classmethod
    def _parse_signed_iwencai_rows(cls, payload: dict, limit: int) -> list[dict]:
        rows: list[dict] = []
        for answer in (payload.get("data") or {}).get("answer") or []:
            for text_item in answer.get("txt") or []:
                content = text_item.get("content") or {}
                if not isinstance(content, dict):
                    continue
                for component in content.get("components") or []:
                    data_rows = ((component.get("data") or {}).get("datas") or [])
                    for raw in data_rows:
                        raw_code = str(raw.get("股票代码") or "").strip().upper()
                        name = str(raw.get("股票简称") or "").strip()
                        if not raw_code or not name:
                            continue
                        code, _, suffix = raw_code.partition(".")
                        market_code = {"SH": "17", "SZ": "33", "BJ": "151"}.get(
                            suffix
                        )
                        rows.append(
                            {
                                "rank": len(rows) + 1,
                                "code": code,
                                "market_code": market_code,
                                "exchange": suffix or None,
                                "name": name,
                                "latest": _native_number(
                                    cls._iwencai_field(raw, "最新价", "收盘价")
                                ),
                                "change_rate": _native_number(
                                    cls._iwencai_field(raw, "涨跌幅", "最新涨跌幅")
                                ),
                                "speed": _native_number(
                                    cls._iwencai_field(raw, "涨速")
                                ),
                                "indicators": raw,
                            }
                        )
                        if len(rows) >= limit:
                            return rows
        return rows

    @staticmethod
    def _iwencai_node_path() -> str:
        node = shutil.which("node")
        if node:
            return node
        try:
            import playwright

            bundled = Path(playwright.__file__).parent / "driver" / "node"
            if bundled.is_file():
                return str(bundled)
        except ImportError:
            pass
        raise RuntimeError("Node.js runtime is unavailable for iwencai signing")

    async def _generate_iwencai_hexin_v(self) -> str:
        spec = importlib.util.find_spec("pywencai")
        if spec is None or spec.origin is None:
            raise RuntimeError("pywencai signing bundle is not installed")
        bundle = Path(spec.origin).parent / "hexin-v.bundle.js"
        if not bundle.is_file():
            raise RuntimeError("pywencai signing bundle is incomplete")
        process = await asyncio.create_subprocess_exec(
            self._iwencai_node_path(),
            str(bundle),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError("iwencai token generation timed out")
        token = stdout.decode().strip().splitlines()[-1] if stdout else ""
        if process.returncode != 0 or len(token) < 20:
            detail = stderr.decode(errors="replace").strip()[-500:]
            raise RuntimeError(f"iwencai token generation failed: {detail}")
        return token

    async def _request_signed_iwencai_stocks(
        self,
        question: str,
        limit: int,
    ) -> list[dict]:
        lock = getattr(self, "_signed_iwencai_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._signed_iwencai_lock = lock
        async with lock:
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    rows = await self._request_signed_iwencai_stocks_once(
                        question,
                        limit,
                    )
                    if rows:
                        return rows
                    last_error = RuntimeError("signed iwencai returned no stock rows")
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
            raise last_error or RuntimeError("signed iwencai request failed")

    async def _request_signed_iwencai_stocks_once(
        self,
        question: str,
        limit: int,
    ) -> list[dict]:
        token = await self._generate_iwencai_hexin_v()
        response = await self._client.post(
            "https://www.iwencai.com/customized/chart/get-robot-data",
            json={
                "question": question,
                "perpage": limit,
                "page": 1,
                "source": "Ths_iwencai_Xuangu",
                "version": "2.0",
                "secondary_intent": "stock",
                "add_info": (
                    '{"urp":{"scene":1,"company":1,"business":1},'
                    '"content_type":"stock","search_cat":"stock"}'
                ),
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Cookie": f"v={token}",
                "Referer": "https://www.iwencai.com/unifiedwap/home/index",
                "hexin-v": token,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") == -2 or (payload.get("data") or {}).get("captcha_url"):
            raise RuntimeError("signed iwencai request was challenged by captcha")
        return self._parse_signed_iwencai_rows(payload, limit)

    async def _request_native_stock_quotes(
        self,
        securities: list[tuple[str, str]],
    ) -> dict[str, dict]:
        """Hydrate prompt-code rows, which only contain identities, via quote 1264."""

        unique = list(dict.fromkeys(securities))
        quotes: dict[str, dict] = {}
        for offset in range(0, len(unique), 100):
            chunk = unique[offset : offset + 100]
            payload = await self._request_native_unified(
                online_id=f"dynamic_group_quotes_{offset}",
                protocol_id=1264,
                page_id=2312,
                request_dic=(
                    "startrow=0\r\nsortid=-1\r\n"
                    f"rowcount={len(chunk)}\r\n"
                    "newrealtime=0\r\nselfstockcustom=1\r\nupdate=1\r\n"
                    "columnorder=55|4|34338|10|34818|48|19|34311|34312|"
                    "34370|34391|34819|36072\r\n"
                    f"marketlist={'|'.join(market for _, market in chunk)}\r\n"
                    f"stocklist={'|'.join(code for code, _ in chunk)}"
                ),
            )
            columns = payload["data"].get("dataDict") or {}
            codes = columns.get("4") or []
            for index, code in enumerate(codes):
                def cell(field: str) -> object:
                    values = columns.get(field) or []
                    return values[index] if index < len(values) else None

                quotes[str(code)] = {
                    "name": cell("55"),
                    "latest": _native_number(cell("10")),
                    "change_rate": _native_number(cell("34818")),
                    "speed": _native_number(cell("48")),
                    "turnover": cell("19"),
                    "turnover_yuan": _native_amount_number(cell("19")),
                    "volume_ratio": _native_number(cell("34311")),
                    "turnover_rate": _native_number(cell("34312")),
                    "large_order_ratio": _native_number(cell("34370")),
                    "main_net_inflow": cell("34391"),
                    "main_net_inflow_yuan": _native_amount_number(cell("34391")),
                    "amplitude": _native_number(cell("34819")),
                    "industry": cell("36072"),
                }
        return quotes

    async def get_native_security_quotes(
        self,
        securities: list[tuple[str, str]],
    ) -> dict:
        """Read current quotes for explicit THS security code/market pairs."""

        try:
            quotes = await self._request_native_stock_quotes(securities)
            rows = []
            for code, market in dict.fromkeys(securities):
                quote = quotes.get(str(code))
                if quote:
                    rows.append({
                        "code": str(code),
                        "market_code": str(market),
                        **quote,
                    })
            if not rows:
                return market_result(
                    provider="ths_native",
                    market="cn",
                    data={"securities": []},
                    status=MarketDataStatus.EMPTY,
                    provider_metadata={
                        "channel": "android_native_unified_request",
                        "protocol_id": 1264,
                        "page_id": 2312,
                    },
                )
            return market_result(
                provider="ths_native",
                market="cn",
                data={"securities": rows},
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "channel": "android_native_unified_request",
                    "protocol_id": 1264,
                    "page_id": 2312,
                    "requested_count": len(securities),
                    "returned_count": len(rows),
                    "source_time_available": False,
                    "time_semantics": "app_callback_receive_time",
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_native",
                market="cn",
                error=exc,
                provider_metadata={
                    "channel": "android_native_unified_request",
                    "protocol_id": 1264,
                    "page_id": 2312,
                },
            )

    async def get_native_realtime_indicator(self, indicator: str) -> dict:
        """读取已确认的同花顺 App 客观市场实时指标。"""

        key = self.NATIVE_REALTIME_INDICATORS.get(indicator)
        if key is None:
            raise ValueError(
                f"unsupported native realtime indicator: {indicator}"
            )
        try:
            payload = await self._request_native_realtime(key)
            points = self._normalize_native_chart_points(payload)
            source_time = None
            if points:
                source_time = str(
                    points[-1].get("time")
                    or points[-1].get("date")
                    or ""
                ) or None
            return market_result(
                provider="ths_native",
                market=(
                    "cn" if indicator not in {"dow_futures"} else "global"
                ),
                data={
                    "indicator": indicator,
                    "indicator_key": key,
                    "name": payload.get("name"),
                    "count": len(points),
                    "points": points,
                    "lines": payload.get("lines") or [],
                    "summary": payload.get("summary") or {},
                },
                source_time=source_time,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "channel": "android_native_realtime_data",
                    "source_timezone_assumption": "Asia/Shanghai",
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_native",
                market="cn" if indicator != "dow_futures" else "global",
                error=exc,
                provider_metadata={
                    "channel": "android_native_realtime_data",
                    "capability": indicator,
                },
            )

    async def get_northbound_turnover_history(self) -> dict:
        """获取同花顺北向成交额日级历史。

        App 的 ``sjdp_north_capital`` 实时响应会在每个分钟点重复当日
        汇总成交额，不能作为成交额分钟曲线。页面实际展示的历史曲线
        来自这个独立的日级接口。
        """

        try:
            response = await self._client.get(
                self.NORTHBOUND_TURNOVER_URL,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                    "Referer": "https://eq.10jqka.com.cn/",
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status_code") != 0:
                raise ValueError(
                    payload.get("status_msg")
                    or "northbound turnover history request failed"
                )
            raw = payload.get("data") or {}
            points = self._normalize_native_chart_points(raw)
            return market_result(
                provider="ths_public",
                market="cn",
                data={
                    "indicator": "northbound_turnover",
                    "indicator_key": raw.get("key"),
                    "name": raw.get("name"),
                    "count": len(points),
                    "points": points,
                    "lines": raw.get("lines") or [],
                    "summary": raw.get("summary") or {},
                },
                source_time=(
                    str(points[-1].get("date")) if points else None
                ),
                trade_date=(
                    str(points[-1].get("date")) if points else None
                ),
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "frequency": "daily",
                    "value_semantics": "northbound_turnover",
                    "is_intraday_series": False,
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_public",
                market="cn",
                error=exc,
                provider_metadata={
                    "capability": "northbound_turnover_history",
                },
            )

    async def get_index_sentiment_history(self, index: str) -> dict:
        """获取同花顺上证50或创成长日级情绪历史。"""

        identity = self.INDEX_SENTIMENTS.get(index)
        if identity is None:
            raise ValueError(f"unsupported index sentiment: {index}")
        code, name = identity
        try:
            response = await self._client.get(
                "https://eq.10jqka.com.cn/open/api/etf_sentiment/v1/"
                "sentiment_index",
                params={"code": code},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                    "Referer": "https://eq.10jqka.com.cn/",
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            raw = ((payload.get("data") or {}).get(code) or {})
            dates = raw.get("time") or []
            prices = raw.get("price") or []
            sentiments = raw.get("sentiment") or []
            rows = [
                {
                    "date": str(observed_date),
                    "price": prices[position],
                    "sentiment": sentiments[position],
                }
                for position, observed_date in enumerate(dates)
                if position < len(prices) and position < len(sentiments)
            ]
            return market_result(
                provider="ths_public",
                market="cn",
                data={
                    "index": index,
                    "index_code": code,
                    "index_name": name,
                    "count": len(rows),
                    "items": rows,
                },
                source_time=rows[-1]["date"] if rows else None,
                trade_date=rows[-1]["date"] if rows else None,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "frequency": "daily",
                    "sentiment_methodology_available": False,
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_public",
                market="cn",
                error=exc,
                provider_metadata={
                    "capability": "index_sentiment",
                    "index": index,
                    "index_code": code,
                },
            )

    async def get_market_valuation_thresholds(self) -> dict:
        """获取同花顺页面使用的 A 股估值风险线和机会线。

        这些数值是页面阈值，不是当日真实 PE/PB；调用方必须与现有
        市场 PE/PB 序列分开保存和展示。
        """

        try:
            response = await self._client.get(
                self.MARKET_VALUATION_URL,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36"
                    ),
                    "Referer": "https://eq.10jqka.com.cn/",
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status_code") != 0:
                raise ValueError(
                    payload.get("status_msg")
                    or "market valuation threshold request failed"
                )
            raw = payload.get("data") or {}
            points = self._normalize_native_chart_points(raw)
            return market_result(
                provider="ths_public",
                market="cn",
                data={
                    "indicator": "market_valuation_threshold",
                    "indicator_key": raw.get("key"),
                    "name": raw.get("name"),
                    "count": len(points),
                    "points": points,
                    "lines": raw.get("lines") or [],
                    "summary": raw.get("summary") or {},
                },
                source_time=(
                    str(points[-1].get("date")) if points else None
                ),
                trade_date=(
                    str(points[-1].get("date")) if points else None
                ),
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "frequency": "weekly",
                    "value_semantics": "risk_and_opportunity_thresholds",
                    "is_current_market_pe_pb": False,
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_public",
                market="cn",
                error=exc,
                provider_metadata={
                    "capability": "market_valuation_thresholds",
                },
            )

    async def get_native_bond_market_history(self, tenor: str) -> dict:
        """获取同花顺债市风向卡片使用的国债期货主连历史价格。"""

        instrument = self.BOND_MARKET_INSTRUMENTS.get(tenor)
        if instrument is None:
            raise ValueError(f"unsupported bond market tenor: {tenor}")
        stock_code, market_code, name = instrument
        begin_date = _previous_year_same_day(
            datetime.now(ZoneInfo("Asia/Shanghai")).date()
        ).strftime("%Y%m%d")
        request_dic = (
            f"stockcode={stock_code}\r\n"
            f"marketcode={market_code}\r\n"
            "klineperiod=5\r\n"
            f"klinebegintime={begin_date}\r\n"
            "klinecount=300\r\n"
            "nopush=1"
        )
        try:
            payload = await self._request_native_unified(
                lane="sector_table",
                online_id=f"bond_{tenor}_date",
                protocol_id=1234,
                page_id=2312,
                request_dic=request_dic,
            )
            data = payload.get("data") or {}
            content = data.get("content") or {}
            dates = content.get("1") or []
            prices = content.get("11") or []
            items = []
            for position, raw_date in enumerate(dates):
                if position >= len(prices):
                    break
                price = self._optional_float(prices[position])
                date_text = str(int(float(raw_date)))
                if price is None or len(date_text) != 8:
                    continue
                items.append({"date": date_text, "price": price})
            ext = data.get("extDataDict") or data.get("exDataDict") or {}
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "tenor": tenor,
                    "code": stock_code,
                    "name": str(ext.get("55") or name),
                    "market_code": market_code,
                    "count": len(items),
                    "items": items,
                },
                source_time=items[-1]["date"] if items else None,
                trade_date=items[-1]["date"] if items else None,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "channel": "android_native_unified_request",
                    "protocol_id": 1234,
                    "page_id": 2312,
                    "price_field_id": "11",
                    "instrument_type": (
                        "broad_market_benchmark"
                        if tenor == "benchmark"
                        else "continuous_bond_futures"
                    ),
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_native",
                market="cn",
                error=exc,
                provider_metadata={
                    "capability": "bond_market_history",
                    "tenor": tenor,
                    "stock_code": stock_code,
                },
            )

    async def get_native_security_daily_bars(
        self,
        code: str,
        market_code: str,
        *,
        name: str = "",
        begin_date: str | None = None,
        count: int = 120,
    ) -> dict:
        """读取同花顺 App 原生指数或板块日 K 线。

        协议 ``1234/2312`` 的数组字段为：1 日期、7 开盘、8 最高、
        9 最低、11 收盘、13 成交量。不同字段必须按数组位置对齐。
        """

        normalized_code = str(code or "").strip()
        normalized_market = str(market_code or "").strip()
        if not normalized_code or not normalized_market:
            raise ValueError("code and market_code are required")
        normalized_count = max(2, min(int(count), 500))
        normalized_begin = begin_date or (
            datetime.now(ZoneInfo("Asia/Shanghai")).date()
            - timedelta(days=normalized_count * 2 + 30)
        ).strftime("%Y%m%d")
        request_dic = (
            f"stockcode={normalized_code}\r\n"
            f"marketcode={normalized_market}\r\n"
            "klineperiod=5\r\n"
            f"klinebegintime={normalized_begin}\r\n"
            "klinecount=500\r\n"
            "nopush=1"
        )
        try:
            payload = await self._request_native_unified(
                lane="sector_table",
                online_id=(
                    f"security_daily_{normalized_market}_{normalized_code}"
                ),
                protocol_id=1234,
                page_id=2312,
                request_dic=request_dic,
            )
            data = payload.get("data") or {}
            content = data.get("content") or {}
            ext = data.get("extDataDict") or data.get("exDataDict") or {}
            columns = {
                "date": content.get("1") or [],
                "open": content.get("7") or [],
                "high": content.get("8") or [],
                "low": content.get("9") or [],
                "close": content.get("11") or [],
                "volume": content.get("13") or [],
            }
            row_count = min((len(values) for values in columns.values()), default=0)
            bars = []
            for position in range(row_count):
                date_value = _native_kline_date(columns["date"][position])
                if date_value is None:
                    continue
                bars.append(
                    {
                        "date": date_value,
                        "open": _native_kline_number(columns["open"][position]),
                        "high": _native_kline_number(columns["high"][position]),
                        "low": _native_kline_number(columns["low"][position]),
                        "close": _native_kline_number(columns["close"][position]),
                        "volume": _native_kline_number(columns["volume"][position]),
                    }
                )
            bars = bars[-normalized_count:]
            if not bars:
                return market_result(
                    provider="ths_native",
                    market="cn",
                    data={
                        "code": normalized_code,
                        "market_code": normalized_market,
                        "name": str(ext.get("55") or name),
                        "interval": "1d",
                        "bars": [],
                    },
                    status=MarketDataStatus.EMPTY,
                    provider_metadata={
                        "channel": "android_native_unified_request",
                        "protocol_id": 1234,
                        "page_id": 2312,
                    },
                )
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "code": normalized_code,
                    "market_code": normalized_market,
                    "name": str(ext.get("55") or name),
                    "interval": "1d",
                    "count": len(bars),
                    "bars": bars,
                },
                source_time=bars[-1]["date"],
                trade_date=bars[-1]["date"],
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "channel": "android_native_unified_request",
                    "protocol_id": 1234,
                    "page_id": 2312,
                    "field_ids": {
                        "date": "1", "open": "7", "high": "8",
                        "low": "9", "close": "11", "volume": "13",
                    },
                    "complete": len(bars) == row_count,
                    "value_semantics": "native_security_index_kline",
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_native",
                market="cn",
                error=exc,
                provider_metadata={
                    "capability": "native_security_daily_bars",
                    "code": normalized_code,
                    "market_code": normalized_market,
                    "protocol_id": 1234,
                    "page_id": 2312,
                },
            )

    async def _request_native_realtime(self, key: str) -> dict:
        last_error: Exception | None = None
        lane = "realtime"
        async with self._native_lock_for(lane):
            for attempt in range(2):
                try:
                    request_payload = {
                        "key": key,
                        "requestParam": f"{key} data",
                        "requestChannel": f"{key}_channel",
                    }
                    if self._native_command_stream_enabled:
                        payload = await self._request_native_command(
                            route="realtime",
                            payload=request_payload,
                            timeout_seconds=65,
                        )
                    else:
                        response = await self._client.post(
                            f"{self._native_bridge_for(lane)}/native/realtime",
                            json=request_payload,
                            timeout=65,
                        )
                        response.raise_for_status()
                        payload = response.json()
                    if not payload.get("success"):
                        raise RuntimeError(
                            payload.get("error")
                            or f"native realtime request failed: {key}"
                        )
                    data = payload.get("data")
                    if isinstance(data, str):
                        data = json.loads(data)
                    if not isinstance(data, dict):
                        raise ValueError(
                            f"native realtime response is not an object: {key}"
                        )
                    return data
                except (httpx.HTTPError, RuntimeError) as exc:
                    last_error = exc
                    if attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    raise
        raise last_error or RuntimeError(
            f"native realtime request failed: {key}"
        )

    @staticmethod
    def _normalize_native_chart_points(payload: dict) -> list[dict]:
        keys = [str(item) for item in (payload.get("point_key_list") or [])]
        rows = payload.get("point_list") or []
        points: list[dict] = []
        for row in rows:
            if not isinstance(row, list):
                continue
            point = {
                key: THSClient._native_chart_value(
                    row[position] if position < len(row) else None,
                    key=key,
                )
                for position, key in enumerate(keys)
            }
            points.append(point)
        return points

    @staticmethod
    def _native_chart_value(value: object, *, key: str) -> object:
        if value in (None, ""):
            return None
        text = str(value)
        if key in {"time", "date", "x_index"}:
            return text
        try:
            return float(text)
        except ValueError:
            return text

    async def _request_native_unified(
        self,
        *,
        lane: str = "default",
        online_id: str,
        protocol_id: int,
        page_id: int,
        request_dic: str,
        cancel_request_dic: str = "",
        timeout_seconds: int = 25,
    ) -> dict:
        async with self._native_unified_lock(protocol_id, page_id):
            return await self._request_native_unified_locked(
                lane=lane,
                online_id=online_id,
                protocol_id=protocol_id,
                page_id=page_id,
                request_dic=request_dic,
                cancel_request_dic=cancel_request_dic,
                timeout_seconds=timeout_seconds,
            )

    async def _request_native_unified_locked(
        self,
        *,
        lane: str,
        online_id: str,
        protocol_id: int,
        page_id: int,
        request_dic: str,
        cancel_request_dic: str = "",
        timeout_seconds: int = 25,
    ) -> dict:
        normalized_timeout = max(1, min(int(timeout_seconds), 60))
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                request_payload = {
                        "onlineId": online_id,
                        "protocolId": protocol_id,
                        "pageId": page_id,
                        "requestType": 262144,
                        "requestDic": request_dic,
                        "cancelRequestDic": cancel_request_dic,
                        "timeoutSeconds": normalized_timeout,
                }
                if self._native_command_stream_enabled:
                    payload = await self._request_native_command(
                        route="unified",
                        payload=request_payload,
                        # Queue time belongs to the broker's global Unified
                        # scheduler.  The App response deadline remains the
                        # payload's timeoutSeconds; do not misclassify queueing
                        # behind another page as an upstream request timeout.
                        timeout_seconds=max(120, normalized_timeout + 15),
                    )
                else:
                    response = await self._client.post(
                        f"{self._native_bridge_for(lane)}/native/unified",
                        json=request_payload,
                        timeout=max(15, normalized_timeout + 15),
                    )
                    response.raise_for_status()
                    payload = response.json()
                if not payload.get("success"):
                    raise RuntimeError(
                        payload.get("error") or "native unified request failed"
                    )
                native_response = payload.get("response") or {}
                head = native_response.get("head") or {}
                if head.get("errorCode") not in (None, 0):
                    raise ValueError(
                        head.get("errorMsg")
                        or f"native unified request error {head.get('errorCode')}"
                    )
                return {
                    "head": head,
                    "data": self._decode_native_unified_body(
                        native_response.get("body")
                    ),
                }
            except (
                httpx.HTTPError,
                OSError,
                TimeoutError,
                json.JSONDecodeError,
                RuntimeError,
            ) as exc:
                last_error = exc
                if attempt == 0:
                    # The Hook returns only after removeRequest() has released
                    # the callback and online-frame registrations, so another
                    # fixed drain delay here only adds latency.
                    continue
                raise
        raise last_error or RuntimeError("native unified request failed")

    @staticmethod
    def _decode_native_unified_body(body: object) -> dict:
        if not isinstance(body, dict):
            return {}
        encoded = body.get("data")
        if isinstance(encoded, str):
            raw = base64.b64decode(encoded)
            decoded = None
            for encoding in ("utf-8", "gb18030"):
                try:
                    decoded = raw.decode(encoding).rstrip("\x00\r\n")
                    break
                except UnicodeDecodeError:
                    continue
            if decoded is None:
                raise UnicodeDecodeError(
                    "utf-8", raw, 0, len(raw),
                    "native body is neither UTF-8 nor GB18030",
                )
            value = json.loads(decoded)
            return value if isinstance(value, dict) else {"items": value}

        decoded_body = dict(body)
        for key in ("type", "content", "dataDict", "extDataDict"):
            value = decoded_body.get(key)
            if not isinstance(value, str):
                continue
            try:
                decoded_body[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
        return decoded_body

    # ========== 基金详情 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_fund_detail(self, fund_code: str) -> dict:
        """基金综合详情（含净值、涨幅、基金经理、交易规则等）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/detail/data/{fund_code}/123"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_fund_base(self, fund_code: str) -> dict:
        """基金基础信息（评分、风险等级、风格、基金经理）"""
        return await self._get(
            f"{self.BASE_URL}/quotation/fund_detail/v2/base/{fund_code}"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_fund_info(self, fund_code: str) -> dict:
        """基金行情信息（净值、涨幅、规模、交易状态）"""
        return await self._get(
            f"{self.BASE_URL}/quotation/fund_detail/get",
            params={"fundCode": fund_code},
        )

    async def get_etf_identity(self, fund_code: str) -> dict:
        """获取 ETF 身份、基金分类和跟踪指数。"""
        try:
            raw = await THSClient.get_fund_base.__wrapped__(self, fund_code)
            data = raw.get("data") or {}
            trade_status = data.get("tradeStatus") or {}
            if trade_status.get("tag") != "ETF":
                return market_result(
                    provider="ths",
                    market="cn",
                    data=None,
                    status=MarketDataStatus.EMPTY,
                    provider_metadata={
                        "fund_code": fund_code,
                        "reason": "not_etf",
                    },
                )
            related_index = data.get("relatedIndexInfo") or {}
            industry = data.get("industry") or {}
            identity = {
                "code": fund_code,
                "name": data.get("simpleName"),
                "market": "sh" if fund_code.startswith(("5", "6")) else "sz",
                "fund_type": data.get("secFundTypeName") or data.get("fundTypeName"),
                "tracking_index_code": (
                    related_index.get("indexCode") or industry.get("themeCode")
                ),
                "tracking_index_name": (
                    related_index.get("indexName") or industry.get("themeName")
                ),
                "risk_level": data.get("riskLevel"),
                "established_at": (data.get("handicap") or {}).get("establishmentDate"),
                "trading_status": "trading" if trade_status.get("buyStatus") == "1" else "unknown",
            }
            return market_result(
                provider="ths",
                market="cn",
                data=identity,
                source_time=(data.get("handicap") or {}).get("latestDate"),
                trade_date=(data.get("handicap") or {}).get("latestDate"),
                timezone_name="Asia/Shanghai",
                provider_metadata={"classification": "ths_fund_classification"},
            )
        except Exception as exc:
            return market_error(provider="ths", market="cn", error=exc)

    async def get_etf_share_history(self, fund_code: str) -> dict:
        """获取 ETF 季度份额、资产规模和申赎数据。"""
        identity = await self.get_etf_identity(fund_code)
        if identity.get("status") != "ok":
            return identity
        try:
            raw = await THSClient.get_scale_change.__wrapped__(self, fund_code)
            changes = ((raw.get("data") or {}).get("gmbd") or {})
            items = []
            for observed_date, row in changes.items():
                items.append(
                    {
                        "date": row.get("date") or observed_date,
                        "net_assets": self._optional_float(row.get("F001N_FUND353")),
                        "shares": self._optional_float(row.get("F002")),
                        "subscriptions": self._optional_float(row.get("F004")),
                        "redemptions": self._optional_float(row.get("F005")),
                        "share_change": self._optional_float(row.get("zfebd")),
                        "share_change_pct": self._optional_float(row.get("zfebdl")),
                        "currency": "CNY",
                        "share_unit": "share",
                    }
                )
            items.sort(key=lambda item: item["date"])
            return market_result(
                provider="ths",
                market="cn",
                data={
                    "code": fund_code,
                    "count": len(items),
                    "frequency": "quarterly",
                    "items": items,
                },
                trade_date=items[-1]["date"] if items else None,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "asset_unit": "yuan",
                    "subscriptions_unit": "yuan",
                    "redemptions_unit": "yuan",
                },
            )
        except Exception as exc:
            return market_error(provider="ths", market="cn", error=exc)

    async def get_etf_estimated_net_inflow(self) -> dict:
        """获取同花顺 ETF 盘中预估申购净流入。

        同花顺公开口径按深市申购量、赎回量和 IOPV 估算，不是交易所
        最终确认的真实净申购。总额和分时序列覆盖深市 ETF；榜首采用
        同花顺合作 ETF 池，与 App 当前展示口径保持一致。
        """

        request_headers = {
            "Origin": "https://eq.10jqka.com.cn",
            "Referer": "https://eq.10jqka.com.cn/",
            "Content-Type": "application/json",
        }
        minute_epoch = int(
            datetime.now(timezone.utc).timestamp() // 60 * 60
        )
        snapshot_body = {
            "code_selectors": {
                "include": [
                    {
                        "type": "stock_code",
                        "values": [self.ETF_ESTIMATED_FLOW_CODE],
                    }
                ]
            },
            "indexes": [
                {
                    "index_id": self.ETF_ESTIMATED_FLOW_INDEX,
                    "timestamp": "0",
                    "time_type": "SNAPSHOT",
                }
            ],
        }
        ranking_body = {
            "businessPoolKey": self.ETF_ESTIMATED_FLOW_POOL,
            "custom": {
                "fieldList": [
                    "estimation_net_inflow_etf",
                    "subMarket",
                ],
                "offset": 0,
                "limit": 1,
                "sort": "DESC",
                "sortType": "estimation_net_inflow_etf",
                "filterList": [
                    {
                        "type": "isCooperateEtf",
                        "relation": "AND",
                        "filterTypeQueryList": [
                            {"cond": "eq", "value": "1"}
                        ],
                    }
                ],
            },
        }
        try:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            flow_results = await asyncio.gather(
                    self._client.post(
                        f"{self.BASE_URL}/quotation/data/query/v1/table",
                        headers=request_headers,
                        json=snapshot_body,
                    ),
                    self._client.get(
                        (
                            f"{self.BASE_URL}/quotation/data/query/gateway/"
                            f"cache/v1/line/{self.ETF_ESTIMATED_FLOW_CODE.replace(':', '%3A')}/"
                            f"{self.ETF_ESTIMATED_FLOW_INDEX}/TREND/"
                            f"{minute_epoch}/-240"
                        ),
                        headers=request_headers,
                    ),
                    self._client.post(
                        f"{self.BASE_URL}/quotation/fund_pool/v2/query",
                        headers=request_headers,
                        json=ranking_body,
                    ),
                    self._request_app_proxy(
                        "https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline",
                        method="POST",
                        body={
                            "code_list": [{"codes": ["1A0001"], "market": "16"}],
                            "trade_class": "intraday",
                            "time_period": "min_1",
                            "trade_date": -1,
                            "begin_time": now_ms - 86_400_000,
                            "end_time": now_ms,
                            "adjust_type": "forward",
                            "gpid": 0,
                        },
                    ),
                    return_exceptions=True,
                )
            snapshot_response, trend_response, ranking_response, benchmark_result = flow_results
            for result in (snapshot_response, trend_response, ranking_response):
                if isinstance(result, BaseException):
                    raise result
            benchmark_raw = (
                benchmark_result
                if isinstance(benchmark_result, dict)
                else {}
            )
            for response in (
                snapshot_response,
                trend_response,
                ranking_response,
            ):
                response.raise_for_status()
            snapshot_raw = snapshot_response.json()
            trend_raw = trend_response.json()
            ranking_raw = ranking_response.json()
            for label, payload in (
                ("snapshot", snapshot_raw),
                ("trend", trend_raw),
                ("ranking", ranking_raw),
            ):
                if payload.get("status_code") != 0:
                    raise ValueError(
                        f"ETF estimated flow {label} failed: "
                        f"{payload.get('status_msg') or 'unknown error'}"
                    )

            total_value = _ths_snapshot_value(snapshot_raw)
            trend = _ths_trend_values(trend_raw)
            top_inflow, ranking_count, ranking_methodology = (
                _ths_top_etf_inflow(ranking_raw)
            )
            if total_value is None or not trend:
                return market_result(
                    provider="ths",
                    market="cn",
                    data=None,
                    status=MarketDataStatus.EMPTY,
                    provider_metadata={
                        "metric": self.ETF_ESTIMATED_FLOW_INDEX,
                        "reason": "empty_estimated_flow",
                    },
                )

            latest_epoch = int(trend[-1]["timestamp"])
            latest_at = datetime.fromtimestamp(
                latest_epoch,
                tz=timezone.utc,
            )
            latest_cn = latest_at.astimezone(ZoneInfo("Asia/Shanghai"))
            benchmark_trend: list[dict[str, float | int]] = []
            quote_rows = ((benchmark_raw.get("data") or {}).get("quote_data") or [])
            if quote_rows:
                quote = quote_rows[0]
                fields = [str(item) for item in quote.get("data_fields") or []]
                timestamp_pos = fields.index("1") if "1" in fields else -1
                close_pos = fields.index("11") if "11" in fields else -1
                if timestamp_pos >= 0 and close_pos >= 0:
                    for values in quote.get("value") or []:
                        if len(values) <= max(timestamp_pos, close_pos):
                            continue
                        timestamp = int(float(values[timestamp_pos]) / 1000)
                        point_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                        if point_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != latest_cn.date():
                            continue
                        benchmark_trend.append({
                            "timestamp": timestamp,
                            "index_value": float(values[close_pos]),
                        })
            return market_result(
                provider="ths",
                market="cn",
                data={
                    "metric": self.ETF_ESTIMATED_FLOW_INDEX,
                    "value_type": "estimated",
                    "coverage_market": "szse_etf",
                    "total_net_inflow_yuan": total_value,
                    "trend": trend,
                    "benchmark": {"code": "1A0001", "name": "上证指数"},
                    "benchmark_trend": benchmark_trend,
                    "top_inflow": top_inflow,
                    "ranking_scope": "ths_cooperative_etf_pool",
                    "ranking_fund_count": ranking_count,
                    "methodology": (
                        ranking_methodology
                        or "（申购量-赎回量）*IOPV"
                    ),
                },
                observed_at=latest_at,
                source_time=latest_at.isoformat(),
                trade_date=latest_cn.date(),
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "quality": "estimated",
                    "coverage": "szse_etf",
                    "is_official_subscription": False,
                    "source_metric_id": self.ETF_ESTIMATED_FLOW_INDEX,
                    "ranking_pool_key": self.ETF_ESTIMATED_FLOW_POOL,
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths",
                market="cn",
                error=exc,
                provider_metadata={
                    "metric": self.ETF_ESTIMATED_FLOW_INDEX,
                },
            )

    @staticmethod
    def _optional_float(value):
        if value in (None, "", "--"):
            return None
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_product_detail(self, fund_code: str) -> dict:
        """产品详情页（基本信息、投资理念、业绩基准、风险特征、分红等）"""
        resp = await self._client.get(
            f"{self.BASE_URL}/mobile/{fund_code}/newcpxq20171115.html"
        )
        resp.raise_for_status()
        raw = resp.content
        # 页面是 GBK 编码
        try:
            html = raw.decode("gbk")
        except Exception:
            html = raw.decode("utf-8", errors="replace")

        result = {}
        # 提取表格中的 key-value
        for m in re.finditer(r'<td class="u-t_th">(.*?)</td>\s*<td class="f-tr">(.*?)</td>', html, re.S):
            key = m.group(1).strip()
            val = m.group(2).strip()
            result[key] = val
        # 按 section 提取标题和内容
        for m in re.finditer(r'<h3 class="u-title f-b_1px">(.*?)</h3>(.*?)</section>', html, re.S):
            raw_title = m.group(1)
            body = m.group(2)
            # 先去掉 <em> 及其后面的所有子标签内容（tooltip），再去 HTML 标签
            clean_title = re.sub(r'<em.*', '', raw_title, flags=re.S)
            # 检查标题中是否含 span（分红统计 无 / 拆分详情 无）
            span = re.search(r'<span[^>]*>(.*?)</span>', raw_title)
            # 去掉 span 标签再提取纯文本标题
            no_span = re.sub(r'<span.*?</span>', '', clean_title, flags=re.S)
            title = re.sub(r'<.*?>', '', no_span).strip()
            if span:
                result[title] = span.group(1).strip()
                continue
            # 提取 body 中的 <p> 内容（去重，跳过注释中的重复）
            ps = re.findall(r'<p(?:\s[^>]*)?>(.*?)</p>', body, re.S)
            seen = set()
            contents = []
            for p in ps:
                text = re.sub(r'<.*?>', '', p).replace('\u3000', '').strip()
                if text and text not in seen:
                    seen.add(text)
                    contents.append(text)
            if contents:
                result[title] = contents[0]
        return {"status_code": 0, "data": result}

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_fund_flag(self, fund_code: str) -> dict:
        """基金标志（是否LOF/退市、二级分类）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/detail/over/{fund_code}_flag"
        )

    # ========== 净值走势 ==========

    async def get_nav_trend(self, fund_code: str, period: str = "year") -> dict:
        """净值走势图数据
        period: year(近一年) / month(近一月) / nowyear(今年以来)
        """
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/detail/flashnew/{fund_code}/{period}"
        )

    async def get_realtime_trend(self, fund_code: str) -> dict:
        """实时估值分时走势（每分钟更新）"""
        return await self._get(
            f"{self.BASE_URL}/quotation/fund/detail/holder/v2/stock_trend",
            params={"fundCode": fund_code},
        )

    # ========== 业绩表现 ==========

    async def get_performance_rank(self, fund_code: str) -> dict:
        """阶段涨幅及同类排名（近一周/月/季/半年/1-5年）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_rate",
            params={"fundCode": fund_code, "type": "range"},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_year_return(self, fund_code: str) -> dict:
        """年度收益率及同类排名"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_rate",
            params={"fundCode": fund_code, "type": "year"},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_max_drawdown(self, fund_code: str) -> dict:
        """最大回撤（近半年/近一年/近三年/成立以来）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_drawdown",
            params={"fundCode": fund_code, "type": "range"},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_periodic_rate(self, fund_code: str, group_type: str = "dayPeriodicRate") -> dict:
        """定期收益率（收益稳定度）
        group_type: dayPeriodicRate / weekPeriodicRate / monthPeriodicRate / quarterPeriodicRate / yearPeriodicRate
        """
        return await self._post(
            f"{self.BASE_URL}/quotation/fund_detail/v2/periodic_rate",
            json={"groupType": group_type, "tradeCode": fund_code, "limit": 200},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_profit_contribution(self, fund_code: str, time_type: str = "threeMonth") -> dict:
        """收益贡献分析
        time_type: threeMonth / halfYear / year
        """
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/profit_contribution",
            params={"fundCode": fund_code, "timeType": time_type},
        )

    # ========== 持仓信息 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_top10_holdings(self, fund_code: str) -> dict:
        """前十大持仓"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/ten_asset_info",
            params={"fundCode": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_holding_overview(self, fund_code: str) -> dict:
        """持仓概览"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_hold_head",
            params={"fundCode": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_asset_allocation(self, fund_code: str, manager_id: str = "") -> dict:
        """资产配置"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_asset_config",
            params={"fundCode": fund_code, "managerId": manager_id},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_style_preference(self, fund_code: str) -> dict:
        """投资风格偏好"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/query_type_prefer",
            params={"fundCode": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_position_dates(self, fund_code: str) -> dict:
        """持仓回顾 - 获取可用的季度日期列表及行业概要"""
        return await self._get(
            f"{self.BASE_URL}/bff-server/v1/fund/position_rank",
            params={"fund_code": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_position_detail(self, fund_code: str, end_date: str = "") -> dict:
        """持仓回顾 - 获取指定季度的前十大持仓明细"""
        return await self._get(
            f"{self.BASE_URL}/bff-server/v1/fund/position_detail",
            params={"fund_code": fund_code, "end_date": end_date},
        )

    # ========== 基金经理 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_manager_info(self, fund_code: str, manager_id: str) -> dict:
        """基金经理详细信息"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/single_fund/detail/manager_label_info",
            params={"fundManagerList": manager_id, "fundCode": fund_code},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_manager_profile(self, manager_id: str) -> dict:
        """基金经理完整档案（个人简历、雷达图、管理基金列表等）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/fundmanager/info/{manager_id}/0"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_manager_invest_history(self, manager_id: str) -> dict:
        """基金经理投资历史（管理的所有基金业绩、重仓股）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/static/fundmanager/investhistory/{manager_id}"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_manager_diagnose(self, manager_id: str) -> dict:
        """基金经理诊断评分（历史规模、回撤、年化收益）"""
        return await self._get(
            f"{self.BASE_URL}/feQuotation/manager/diagnose/detail",
            params={"id": manager_id},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_manager_industry_prefer(self, manager_id: str) -> dict:
        """基金经理行业偏好"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/manager/investment/get_fund_manager_industry_prefer",
            params={"fundManagerId": manager_id},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_manager_represent_fund(self, manager_id: str) -> dict:
        """基金经理代表基金"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/manager/investment/get_represent_fund",
            params={"fundManagerId": manager_id},
        )

    # ========== 交易规则与费率 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_trade_rule(self, fund_code: str) -> dict:
        """交易规则与费率（申购/赎回费率、管理费、托管费、服务费、交易确认时间）"""
        return await self._get(
            f"{self.BASE_URL}/interface/fund/tradeRule/{fund_code}"
        )

    # ========== 规模与持有人 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_scale_change(self, fund_code: str) -> dict:
        """规模变动历史（季度净资产、申购赎回金额、份额变动、持有人结构）"""
        return await self._get(
            f"{self.BASE_URL}/interface/fund/detail/{fund_code}_gmbd"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_holder_ratio(self, fund_code: str) -> dict:
        """机构持仓比例历史（半年度机构持有占比变化）"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund/detail/holder/const/{fund_code}"
        )

    # ========== 分红历史 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_dividend_history(self, fund_code: str) -> dict:
        """分红历史（从产品详情页 HTML 解析分红和拆分记录）"""
        resp = await self._client.get(
            f"{self.BASE_URL}/mobile/{fund_code}/newcpxq20171115.html"
        )
        resp.raise_for_status()
        try:
            html = resp.content.decode("gbk")
        except Exception:
            html = resp.content.decode("utf-8", errors="replace")

        result = {"dividends": [], "splits": [], "summary": ""}

        # 提取分红 section
        for m in re.finditer(r'分红统计(.*?)</section>', html, re.S):
            section = m.group(1)
            # 检查是否"无"
            span = re.search(r'<span[^>]*>(.*?)</span>', section)
            if span and span.group(1).strip() == "无":
                result["summary"] = "无分红记录"
                break
            # 提取累计分红摘要
            summary_m = re.search(r'累计分红(\d+)次.*?([\d.]+)元', section, re.S)
            if summary_m:
                result["summary"] = f"累计分红{summary_m.group(1)}次，{summary_m.group(2)}元/份"
            # 提取分红明细表
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', section, re.S)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
                cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                if len(cells) >= 3 and re.match(r'\d{4}', cells[0]):
                    result["dividends"].append({
                        "payDate": cells[0],
                        "recordDate": cells[1],
                        "perShare": cells[2],
                    })

        # 提取拆分 section
        for m in re.finditer(r'拆分详情(.*?)</section>', html, re.S):
            section = m.group(1)
            span = re.search(r'<span[^>]*>(.*?)</span>', section)
            if span and span.group(1).strip() == "无":
                break
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', section, re.S)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
                cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                if len(cells) >= 2 and re.match(r'\d{4}', cells[0]):
                    result["splits"].append({
                        "date": cells[0],
                        "detail": cells[1] if len(cells) > 1 else "",
                    })

        return {"status_code": 0, "data": result}

    # ========== 净值技术面 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_nav_technical(self, fund_code: str) -> dict:
        """基于近一年日净值计算技术面指标（RSI14/MA5/MA20/MA60/偏离度/信号）"""
        raw = await self.get_nav_trend(fund_code, "year")
        data_str = raw.get("data", "")
        if not data_str:
            return {"status_code": -1, "data": {}, "msg": "无净值数据"}

        # 解析净值序列：格式 "日期;x;净值;涨幅|..."，数据从新到旧排列，需反转为正序
        records = []
        for line in data_str.split("|"):
            parts = line.split(";")
            if len(parts) >= 3:
                try:
                    records.append({"date": parts[0], "nav": float(parts[2])})
                except (ValueError, IndexError):
                    continue
        records.reverse()  # 反转为时间正序（最早在前，最新在后）
        if len(records) < 15:
            return {"status_code": -1, "data": {}, "msg": f"净值数据不足({len(records)}条)"}

        navs = [r["nav"] for r in records]
        latest = records[-1]

        # RSI(14) - Wilder 平滑
        changes = [navs[i] - navs[i - 1] for i in range(1, len(navs))]
        period = 14
        gains = [max(c, 0) for c in changes[:period]]
        losses = [abs(min(c, 0)) for c in changes[:period]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        for c in changes[period:]:
            avg_gain = (avg_gain * (period - 1) + max(c, 0)) / period
            avg_loss = (avg_loss * (period - 1) + abs(min(c, 0))) / period
        rsi14 = round(100 * avg_gain / (avg_gain + avg_loss), 1) if (avg_gain + avg_loss) > 0 else 50.0

        # MA
        def _ma(n):
            if len(navs) < n:
                return None
            return round(sum(navs[-n:]) / n, 4)

        ma5 = _ma(5)
        ma20 = _ma(20)
        ma60 = _ma(60)
        cur = latest["nav"]

        def _dev(ma_val):
            if ma_val is None or ma_val == 0:
                return None
            return round((cur - ma_val) / ma_val * 100, 2)

        dev5 = _dev(ma5)
        dev20 = _dev(ma20)
        dev60 = _dev(ma60)

        # 信号判断
        signals = []
        if rsi14 > 70:
            signals.append("RSI超买(>70)，短期回调风险")
        elif rsi14 < 30:
            signals.append("RSI超卖(<30)，可能反弹")
        else:
            signals.append("RSI适中，未超买超卖")

        if ma5 and ma20 and ma60:
            if cur > ma5 and cur > ma20 and cur > ma60:
                signals.append("净值在所有均线之上，短期强势")
            if cur < ma20:
                signals.append("跌破20日均线，短期趋势转弱")
            if ma5 > ma20 > ma60 and cur > ma60:
                signals.append("多头排列（MA5>MA20>MA60），中期趋势向上")
            elif ma5 < ma20 < ma60:
                signals.append("空头排列（MA5<MA20<MA60），中期趋势向下")
            # 检查MA5与MA20交叉（近3日）
            if len(navs) >= 22:
                ma5_prev = sum(navs[-6:-1]) / 5
                ma20_prev = sum(navs[-21:-1]) / 20
                if ma5_prev >= ma20_prev and ma5 < ma20:
                    signals.append("短期均线死叉（MA5下穿MA20），注意风险")
                elif ma5_prev <= ma20_prev and ma5 > ma20:
                    signals.append("短期均线金叉（MA5上穿MA20），关注机会")

        return {
            "status_code": 0,
            "data": {
                "nav": cur, "date": latest["date"],
                "rsi14": rsi14,
                "ma5": ma5, "ma20": ma20, "ma60": ma60,
                "devMa5": dev5, "devMa20": dev20, "devMa60": dev60,
                "signals": signals,
            },
        }

    # ========== 基金申赎资金流趋势 ==========

    async def get_fund_flow_trend(self, fund_code: str) -> dict:
        """基于规模变动 + 机构持仓比例，分析申赎资金流趋势"""
        scale_raw, holder_raw = await asyncio.gather(
            self.get_scale_change(fund_code),
            self.get_holder_ratio(fund_code),
            return_exceptions=True,
        )

        # 解析规模变动
        quarters = []
        if isinstance(scale_raw, dict):
            scale_data = scale_raw.get("data") or {}
            gmbd = (
                scale_data.get("gmbd") or {}
                if isinstance(scale_data, dict)
                else {}
            )
            dates = sorted(gmbd.keys(), reverse=True)
            prev_nav = None
            def _safe_float(v, default=0.0):
                try:
                    return float(v) if v not in (None, "") else default
                except (ValueError, TypeError):
                    return default

            for date in dates:
                info = gmbd[date]
                jzc = _safe_float(info.get("jzc"))
                qjsg = _safe_float(info.get("qjsg"))
                qjsh = _safe_float(info.get("qjsh"))
                net_flow = round(qjsg - qjsh, 2)

                # 净申赎率：净申赎 / 上期净资产
                net_flow_rate = None
                if prev_nav and prev_nav > 0:
                    net_flow_rate = round(net_flow / prev_nav * 100, 2)
                prev_nav = jzc

                quarters.append({
                    "date": date,
                    "nav": jzc,
                    "subscribe": qjsg,
                    "redeem": qjsh,
                    "netFlow": net_flow,
                    "netFlowRate": net_flow_rate,
                })
            # 修正：dates是倒序的，prev_nav的赋值逻辑需要调整
            # 重新计算：净申赎率 = 净申赎 / 本期净资产（因为上期净资产在更早的日期）
            # 用相邻季度：当前季度的上一季度的净资产
            for i, q in enumerate(quarters):
                if i + 1 < len(quarters):
                    prev = quarters[i + 1]["nav"]
                    if prev > 0:
                        q["netFlowRate"] = round(q["netFlow"] / prev * 100, 2)
                    else:
                        q["netFlowRate"] = None
                else:
                    q["netFlowRate"] = None

        # 判断趋势
        trend = "数据不足"
        if len(quarters) >= 2:
            recent_flows = [q["netFlow"] for q in quarters[:4] if q["netFlow"] != 0]
            neg_count = sum(1 for f in recent_flows if f < 0)
            pos_count = sum(1 for f in recent_flows if f > 0)
            if neg_count >= 3:
                trend = "持续净赎回，资金在撤离"
            elif pos_count >= 3:
                trend = "持续净申购，资金在流入"
            elif neg_count >= 2 and pos_count >= 2:
                trend = "申赎交替，短线资金博弈"
            elif len(recent_flows) >= 1 and recent_flows[0] < 0:
                trend = "最近一季净赎回"
            elif len(recent_flows) >= 1 and recent_flows[0] > 0:
                trend = "最近一季净申购"

        # 解析机构占比
        org_trend = ""
        org_data = []
        if isinstance(holder_raw, dict):
            items = holder_raw.get("data") or []
            for item in items:
                date = item.get("date", "")
                org_rate = item.get("orgRate")
                if org_rate not in (None, ""):
                    try:
                        org_data.append({"date": date, "orgRate": round(float(org_rate), 2)})
                    except (ValueError, TypeError):
                        continue

        signals = []
        if org_data and len(org_data) >= 2:
            latest_org = org_data[0]["orgRate"]
            # 取较早的一期做对比（倒序排列，索引越大越早）
            earlier_idx = min(3, len(org_data) - 1)
            earlier_org = org_data[earlier_idx]["orgRate"]
            if earlier_org > 0 and latest_org < earlier_org * 0.5:
                org_trend = "机构占比大幅下降"
                signals.append(f"机构占比从{earlier_org}%降至{latest_org}%，聪明钱已撤退")
            elif earlier_org > 0 and latest_org > earlier_org * 1.5:
                org_trend = "机构占比大幅上升"
                signals.append(f"机构占比从{earlier_org}%升至{latest_org}%，机构在加仓")
            elif latest_org > earlier_org:
                org_trend = "机构占比小幅上升"
            elif latest_org < earlier_org:
                org_trend = "机构占比小幅下降"
            else:
                org_trend = "机构占比持平"

        # 添加资金流信号
        if "持续净赎回" in trend:
            signals.insert(0, "近期持续净赎回，资金在撤离")
        elif "持续净申购" in trend:
            signals.insert(0, "近期持续净申购，资金在流入")
        elif "交替" in trend:
            signals.insert(0, "近期申赎交替，无明确方向，短线资金博弈")

        return {
            "status_code": 0,
            "data": {
                "quarters": quarters[:8],
                "trend": trend,
                "orgRatioTrend": org_trend,
                "orgData": org_data[:6],
                "signals": signals,
            },
        }

    # ========== 指标与追踪 ==========

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_rsi_indicator(self, fund_code: str) -> dict:
        """RSI 买卖指标"""
        return await self._get(
            f"{self.DQ_BASE_URL}/fuyao/fund/default/v1/fund/indic",
            params={"tradeCodeList": fund_code, "typeList": "rsiBestLimitDown,rsiBestLimitUp"},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_fund_track(self, fund_code: str) -> dict:
        """基金追踪"""
        return await self._get(
            f"{self.BASE_URL}/hqapi/fund_track/query/{fund_code}"
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_announcements(self, fund_code: str, category: str = "all",
                                page: int = 1, page_size: int = 15) -> dict:
        """基金公告
        category: all/report/dividend/change/operation/other
        """
        cat_id = self.ANNOUNCEMENT_CATEGORIES.get(category, "0")
        return await self._get(
            f"{self.BASE_URL}/interface/net/pubnote2/{cat_id}_{fund_code}_{page}_{page_size}"
        )

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=1209600)
    async def get_news(self, fund_code: str, limit: int = 10) -> dict:
        """基金相关资讯"""
        # 需要先获取 hqcode
        info = await self.get_fund_info(fund_code)
        hqcode = info.get("data", {}).get("hqcode", "")
        if not hqcode:
            return {"status_code": -1, "data": {"contentList": []}, "status_msg": "未找到 hqcode"}
        return await self._get(
            f"{self.BASE_URL}/quotation/fund_content/v2/query",
            params={"code": hqcode, "marketId": "32", "limit": limit},
        )

    # ========== 基金排行与筛选（同花顺原生API） ==========

    # 默认查询字段列表
    _RANKING_FIELDS = [
        "unitNav", "chgpctDate", "chgpct", "week", "month", "tmonth",
        "hyear", "year", "twoyear", "tyear", "fyear", "nowyear", "now",
        "sharpeYear", "automaticYear", "maxDrawDownYear", "fundScale",
        "fundTags", "simpleName", "showType", "heavyRate", "rsi", "insPosition",
    ]

    # 默认过滤条件：规模>1000万、场外基金、可申购、不限大额赎回
    _DEFAULT_FILTERS = [
        {"filterField": "fundScale", "filterTypeList": [{"filterValue": "10000000", "filterSymbol": "GREATER"}], "isRankConfig": True},
        {"filterField": "otcFund", "innerJoinType": "OR", "filterTypeList": [{"filterValue": "1", "filterSymbol": "EQUAL"}]},
        {"filterField": "buyStatus", "innerJoinType": "OR", "filterTypeList": [{"filterValue": "1", "filterSymbol": "EQUAL"}]},
        {"filterField": "largeRedemptionNow", "innerJoinType": "OR", "filterTypeList": [{"filterValue": "0", "filterSymbol": "EQUAL"}]},
    ]

    # 本地策略筛选映射（基于已验证可用的 API 过滤字段）
    _STRATEGY_FILTERS = {
        "fund0001": {
            "name": "年年正收益",
            "desc": "连续5年正收益，成立超5年",
            "sort_type": "year",
            "sort": "DESC",
            "filters": [
                {"filterField": "yearPeriodicUpStreak", "filterTypeList": [{"filterValue": "5", "filterSymbol": "GREATER_EQUAL"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1825", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0002": {
            "name": "三年翻倍",
            "desc": "近3年涨幅超100%，成立超3年",
            "sort_type": "tyear",
            "sort": "DESC",
            "filters": [
                {"filterField": "tyear", "filterTypeList": [{"filterValue": "100", "filterSymbol": "GREATER_EQUAL"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1095", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0003": {
            "name": "十年十倍",
            "desc": "成立以来涨幅超1000%，成立超10年",
            "sort_type": "now",
            "sort": "DESC",
            "filters": [
                {"filterField": "now", "filterTypeList": [{"filterValue": "1000", "filterSymbol": "GREATER_EQUAL"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "3650", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0004": {
            "name": "十年绩优",
            "desc": "成立超10年，近3年收益排名前1/3",
            "sort_type": "now",
            "sort": "DESC",
            "filters": [
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "3650", "filterSymbol": "GREATER_EQUAL"}]},
                {"filterField": "rateRankPercentTyear", "filterTypeList": [{"filterValue": "33", "filterSymbol": "LESS_EQUAL"}]},
            ],
        },
        "fund0005": {
            "name": "低回撤率",
            "desc": "近3年最大回撤<5%，成立超3年",
            "sort_type": "maxDrawDownYear",
            "sort": "ASC",
            "filters": [
                {"filterField": "maxDrawDownTyear", "filterTypeList": [{"filterValue": "5", "filterSymbol": "LESS"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1095", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0007": {
            "name": "高性价比",
            "desc": "近3年夏普比率排名前10%，成立超3年",
            "sort_type": "sharpeYear",
            "sort": "DESC",
            "filters": [
                {"filterField": "sharpeRankPercentTyear", "filterTypeList": [{"filterValue": "10", "filterSymbol": "LESS_EQUAL"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1095", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0010": {
            "name": "能涨抗跌",
            "desc": "近3年收益前1/3，近3年回撤<5%，成立超3年",
            "sort_type": "tyear",
            "sort": "DESC",
            "filters": [
                {"filterField": "rateRankPercentTyear", "filterTypeList": [{"filterValue": "33", "filterSymbol": "LESS_EQUAL"}]},
                {"filterField": "maxDrawDownTyear", "filterTypeList": [{"filterValue": "5", "filterSymbol": "LESS"}]},
                {"filterField": "nowDayAmount", "filterTypeList": [{"filterValue": "1095", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0011": {
            "name": "机构偏爱",
            "desc": "机构持仓占比超80%",
            "sort_type": "year",
            "sort": "DESC",
            "filters": [
                {"filterField": "insPosition", "filterTypeList": [{"filterValue": "80", "filterSymbol": "GREATER_EQUAL"}]},
            ],
        },
        "fund0012": {
            "name": "小规模大潜力",
            "desc": "规模2-30亿，近1年夏普排名前10%",
            "sort_type": "sharpeYear",
            "sort": "DESC",
            "filters": [
                {"filterField": "fundScale", "filterTypeList": [{"filterValue": "200000000,3000000000", "filterSymbol": "BETWEEN"}]},
                {"filterField": "sharpeRankPercentYear", "filterTypeList": [{"filterValue": "10", "filterSymbol": "LESS_EQUAL"}]},
            ],
        },
    }

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_fund_ranking(self, sort_type: str = "year", sort: str = "DESC",
                               limit: int = 30, offset: int = 0,
                               fund_type: str = None, fund_company: str = None,
                               min_scale: float = None,
                               strategy: str = None,
                               extra_filters: list = None) -> dict:
        """同花顺基金排行（原生API）
        sort_type: year/hyear/tmonth/month/week/nowyear/tyear/fyear/now/sharpeYear/maxDrawDownYear 等
        sort: DESC/ASC
        fund_type: 基金类型代码（如 282001001=股票型）
        fund_company: 基金公司 orgid
        min_scale: 最小规模（元），默认1000万
        strategy: 预设策略key（fund0001=年年正收益 等），会覆盖 sort_type/sort 并追加策略过滤条件
        extra_filters: 自定义 filterList，直接追加
        """
        # 策略筛选：查找本地映射，覆盖排序并追加过滤条件
        if strategy and strategy in self._STRATEGY_FILTERS:
            cfg = self._STRATEGY_FILTERS[strategy]
            sort_type = cfg["sort_type"]
            sort = cfg["sort"]
            extra_filters = list(extra_filters or []) + cfg["filters"]

        filter_list = list(self._DEFAULT_FILTERS)

        # 自定义最小规模
        if min_scale is not None:
            filter_list = [f for f in filter_list if f.get("filterField") != "fundScale"]
            filter_list.append({
                "filterField": "fundScale",
                "filterTypeList": [{"filterValue": str(int(min_scale)), "filterSymbol": "GREATER"}],
                "isRankConfig": True,
            })

        # 基金类型过滤（使用 l2code 字段，支持多个代码逗号分隔）
        if fund_type:
            # 如果包含逗号，使用 IN 匹配多个类型
            if "," in fund_type:
                filter_list.append({
                    "filterField": "l2code",
                    "filterTypeList": [{"filterValue": fund_type, "filterSymbol": "IN"}],
                })
            else:
                filter_list.append({
                    "filterField": "l2code",
                    "filterTypeList": [{"filterValue": fund_type, "filterSymbol": "EQUAL"}],
                })

        # 基金公司过滤
        if fund_company:
            filter_list.append({
                "filterField": "orgid",
                "innerJoinType": "OR",
                "filterTypeList": [{"filterValue": fund_company, "filterSymbol": "EQUAL"}],
            })

        # 追加自定义过滤条件
        if extra_filters:
            filter_list.extend(extra_filters)

        ext = {
            "total": 0,
            "page": 0,
            "offset": offset,
            "limit": limit,
            "sort": sort,
            "sortType": sort_type,
            "outerJoinType": "AND",
            "filterList": filter_list,
            "fieldList": self._RANKING_FIELDS,
        }

        body = {
            "cardList": [{
                "cardModuleTypeEnum": "FUND",
                "cardEnum": "SORT_FILTER_V1",
                "ext": ext,
            }],
        }

        return await self._post(
            f"{self.BASE_URL}/quotation/common/v1/list/card/info",
            json=body,
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_rank_board_config(self) -> dict:
        """获取排行榜配置（涨幅榜/反弹榜/人气榜/加仓榜/超额榜）"""
        resp = await self._get(
            f"{self.BASE_URL}/marketing/activity_redis/v1/get/fund_rank_list_v1"
        )
        # data 可能是 JSON 字符串，需要二次解析
        data = resp.get("data")
        if isinstance(data, str):
            resp["data"] = json.loads(data)
        return resp

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_rank_filter_config(self) -> dict:
        """获取筛选策略配置（年年正收益/三年翻倍/机构偏爱/十年十倍等）"""
        resp = await self._get(
            f"{self.BASE_URL}/marketing/activity_redis/v1/get/fund_rank_filter_v1"
        )
        data = resp.get("data")
        if isinstance(data, str):
            resp["data"] = json.loads(data)
        return resp

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_rank_distribution(self, indic_list: list = None) -> dict:
        """获取收益率分布统计（各周期的收益率分布：max/min/每个百分点的基金数量）"""
        if indic_list is None:
            indic_list = [
                "month", "tmonth", "hyear", "year", "tyear", "fyear",
                "nowyear", "maxDrawDownYear",
            ]
        return await self._post(
            f"{self.BASE_URL}/quotation/rank/filter/v1/count/info",
            json={"indicList": indic_list},
        )

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_fund_company_list(self) -> list:
        """获取基金公司列表（使用独立请求避免 session 限流）"""
        async with httpx.AsyncClient(timeout=10.0) as tmp_client:
            resp = await tmp_client.get(
                f"{self.BASE_URL}/mInterface/jjgs.txt",
                headers={
                    "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                    "Referer": "https://fund.10jqka.com.cn/",
                },
            )
            resp.raise_for_status()
            return resp.json()

    # ========== 相似基金与对比 ==========

    @staticmethod
    def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
        """计算两个行业分布向量的余弦相似度"""
        keys = set(vec_a) | set(vec_b)
        dot = sum(vec_a.get(k, 0) * vec_b.get(k, 0) for k in keys)
        mag_a = math.sqrt(sum(v ** 2 for v in vec_a.values()))
        mag_b = math.sqrt(sum(v ** 2 for v in vec_b.values()))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    @staticmethod
    def _extract_industry_vector(style_data: dict) -> dict[str, float]:
        """从 style_preference 响应中提取最新一期的行业分布向量"""
        rate_list = style_data.get("data", {}).get("rateList", [])
        if not rate_list:
            return {}
        latest = rate_list[-1]  # 最新一期
        keys = ["kjRate", "zzRate", "xfRate", "zqRate", "jrRate", "ylRate", "jjRate"]
        return {k: latest.get(k, 0) for k in keys}

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def find_similar_funds(self, fund_code: str, top_n: int = 5) -> list[dict]:
        """自动发现同赛道基金
        1. 获取目标基金的行业分布
        2. 从同花顺获取基金排行（按近一年收益，取前100）
        3. 逐批查询候选基金的行业分布，筛选相似度高的
        返回: [{"code", "name", "similarity", "return_1y"}, ...]
        """
        # 1. 获取目标基金的行业向量
        target_style = await self.get_style_preference(fund_code)
        target_vec = self._extract_industry_vector(target_style)
        if not target_vec or all(v == 0 for v in target_vec.values()):
            return []

        # 2. 获取候选池（同花顺原生排行）
        ranking_resp = await self.get_fund_ranking(sort_type="year", sort="DESC", limit=100)
        # 从返回结构中提取基金列表: data[0].list
        data_list = ranking_resp.get("data", [])
        fund_list = data_list[0].get("list", []) if data_list else []
        # 排除目标基金自身
        candidates = [f for f in fund_list if f.get("tradeCode") != fund_code]

        # 3. 分批并发查询行业分布（每批10只，最多查30只）
        scored = []
        batch_size = 10
        max_query = 30
        for i in range(0, min(len(candidates), max_query), batch_size):
            batch = candidates[i:i + batch_size]
            tasks = [self.get_style_preference(f["tradeCode"]) for f in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for fund_info, style_result in zip(batch, results):
                if isinstance(style_result, Exception):
                    continue
                vec = self._extract_industry_vector(style_result)
                if not vec or all(v == 0 for v in vec.values()):
                    continue
                sim = self._cosine_similarity(target_vec, vec)
                if sim >= 0.5:  # 相似度阈值
                    year_return = fund_info.get("year")
                    scored.append({
                        "code": fund_info["tradeCode"],
                        "name": fund_info.get("simpleName", ""),
                        "similarity": round(sim, 4),
                        "return_1y": float(year_return) if year_return else None,
                    })

        # 4. 按相似度排序，取 top_n
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_n]

    @cached(source="ths", source_name="同花顺", domain="market", market="fund", ttl=1209600)
    async def get_fund_compare_data(self, fund_codes: list[str]) -> list[dict]:
        """并发获取多只基金的对比数据
        返回: [{"code", "name", "manager", "scale", "establish_date",
                "return_1y", "return_3y", "return_since", "annual_return",
                "max_drawdown_1y", "sharpe_1y",
                "top_industry", "industry_concentration", "org_ratio",
                "top10_stocks"}, ...]
        """

        async def _fetch_one(code: str) -> dict:
            detail_t = self.get_fund_detail(code)
            base_t = self.get_fund_base(code)
            rank_t = self.get_performance_rank(code)
            drawdown_t = self.get_max_drawdown(code)
            style_t = self.get_style_preference(code)
            overview_t = self.get_holding_overview(code)
            holder_t = self.get_holder_ratio(code)
            holdings_t = self.get_top10_holdings(code)

            results = await asyncio.gather(
                detail_t, base_t, rank_t, drawdown_t,
                style_t, overview_t, holder_t, holdings_t,
                return_exceptions=True,
            )
            detail, base, rank, drawdown, style, overview, holder, holdings = results

            # 解析 detail
            info = detail.get("data", {}) if isinstance(detail, dict) else {}
            managers = info.get("managerInfo", [])
            manager_name = managers[0].get("name", "") if managers else ""

            # 解析 base
            hc = {}
            if isinstance(base, dict):
                hc = base.get("data", {}).get("handicap", {})
            scale_raw = hc.get("fundScale")
            scale = round(float(scale_raw) / 1e8, 2) if scale_raw else None
            establish = hc.get("establishmentDate", "")
            sharpe = round(float(hc["sharpeYear"]), 4) if hc.get("sharpeYear") else None
            max_dd_year = round(float(hc["maxDrawDownYear"]), 2) if hc.get("maxDrawDownYear") else None
            annual = round(float(hc["nowAnnual"]), 2) if hc.get("nowAnnual") else None

            # 解析 rank
            rank_map = {}
            if isinstance(rank, dict):
                for item in rank.get("data", []):
                    t = item.get("time", "")
                    y = item.get("yield")
                    if y is not None:
                        rank_map[t] = round(float(y), 2)

            # 解析 drawdown
            dd_map = {}
            if isinstance(drawdown, dict):
                for item in drawdown.get("data", []):
                    t = item.get("time", "")
                    d = item.get("drawdown")
                    if d is not None:
                        dd_map[t] = round(float(d), 2)

            # 解析 style（最新一期的主要行业）
            label_map = {"kjRate": "科技", "zzRate": "制造", "xfRate": "消费",
                         "zqRate": "周期", "jrRate": "金融", "ylRate": "医疗", "jjRate": "军工"}
            top_industry = ""
            industry_pct = 0
            if isinstance(style, dict):
                vec = self._extract_industry_vector(style)
                if vec:
                    top_key = max(vec, key=vec.get)
                    top_industry = label_map.get(top_key, top_key)
                    industry_pct = round(vec[top_key] * 100, 1)

            # 解析 overview（集中度）
            concentration = None
            if isinstance(overview, dict):
                ov = overview.get("data", {}).get(code, {})
                c = ov.get("fundStockConcentration")
                if c:
                    concentration = round(float(c) * 100, 1)

            # 解析 holder_ratio（最新一期机构占比）
            org_ratio = None
            if isinstance(holder, dict):
                items = holder.get("data", [])
                if items:
                    r = items[0].get("orgRate")
                    if r:
                        org_ratio = round(float(r), 1)

            # 解析 top10 holdings（股票代码列表）
            top10 = []
            if isinstance(holdings, dict):
                for s in holdings.get("data", {}).get("stock", []):
                    top10.append(s.get("secCode", ""))

            return {
                "code": code,
                "name": info.get("name", code),
                "manager": manager_name,
                "scale": scale,
                "establish_date": establish,
                "return_1y": rank_map.get("近一年"),
                "return_3y": rank_map.get("近三年"),
                "return_since": rank_map.get("成立以来"),
                "annual_return": annual,
                "max_drawdown_1y": max_dd_year,
                "sharpe_1y": sharpe,
                "top_industry": f"{top_industry}({industry_pct}%)" if top_industry else "",
                "concentration": concentration,
                "org_ratio": org_ratio,
                "top10_stocks": top10,
            }

        tasks = [_fetch_one(code) for code in fund_codes]
        return await asyncio.gather(*tasks)

    # ========== 同花顺游资龙虎榜 ==========

    async def get_ths_dragon_tiger(self, tab: str = "youzi", count: int = 30) -> dict:
        """同花顺龙虎榜 - 带游资/机构标签

        tab: "youzi"=游资(含一线/知名), "jigou"=机构专用, "all"=全部
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "http://data.10jqka.com.cn/",
        }

        resp = await self._client.get(self.THS_LHB_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="replace")

        # 解析报告日期
        date_match = re.search(r'report="(\d{4}-\d{2}-\d{2})"', text)
        report_date = date_match.group(1) if date_match else ""

        # ---- 解析左侧股票列表 ----
        left_rows = re.findall(
            r'<tr[^>]*>\s*'
            r'<td[^>]*>\s*(?:<label[^>]*>(\d+日)</label>)?\s*</td>\s*'
            r'<td[^>]*>(\d{6})</td>\s*'
            r'<td[^>]*><a[^>]*stockcode="(\d+)"[^>]*rid="([^"]+)"[^>]*class="stock">([^<]+)</a></td>\s*'
            r'<td[^>]*class="[^"]*tr[^"]*">([^<]+)</td>\s*'
            r'<td[^>]*class="[^"]*tr[^"]*">([^<]+)</td>\s*'
            r'<td[^>]*class="[^"]*tr[^"]*">([^<]+)</td>\s*'
            r'<td[^>]*class="[^"]*tr[^"]*">([^<]+)</td>',
            text,
        )

        # ---- 解析右侧席位明细 ----
        stockcont_starts = [(m.start(), m.group(1))
                            for m in re.finditer(r"<div class=\"stockcont\"[^>]*rid='([^']+)'", text)]

        stock_details = {}
        for i, (start, rid) in enumerate(stockcont_starts):
            end = stockcont_starts[i + 1][0] if i + 1 < len(stockcont_starts) else len(text)
            section = text[start:end]

            # 汇总行
            summary = re.search(
                r'净额：<span[^>]*>([^<]+)</span>万元', section,
            )
            net_total = summary.group(1).replace(",", "") if summary else "0"

            # 买入/卖出席位
            buy_part = section.split("卖出金额最大的前5名")[0] if "卖出金额最大的前5名" in section else section
            sell_part = section[section.find("卖出金额最大的前5名"):] if "卖出金额最大的前5名" in section else ""

            def _parse_seats(html):
                seats = []
                for dept, label, buy, sell, net in re.findall(
                    r'title="([^"]+)">[^<]+</a>\s*'
                    r'(?:<label class="label[^"]*">([^<]+)</label>)?\s*</td>\s*'
                    r'<td[^>]*>([^<]*)</td>\s*'
                    r'<td[^>]*>([^<]*)</td>\s*'
                    r'<td[^>]*>([^<]*)</td>',
                    html,
                ):
                    # "机构专用" 没有 label 标签，直接作为 dept 名出现
                    effective_label = label
                    if not effective_label and dept == "机构专用":
                        effective_label = "机构专用"
                    seats.append({
                        "dept": dept,
                        "label": effective_label,
                        "buy": buy.strip(),
                        "sell": sell.strip(),
                        "net": net.strip(),
                    })
                return seats

            buy_seats = _parse_seats(buy_part)
            sell_seats = _parse_seats(sell_part)

            all_labels = set()
            for s in buy_seats + sell_seats:
                if s["label"]:
                    all_labels.add(s["label"])

            stock_details[rid] = {
                "netTotal": net_total,
                "buySeats": buy_seats,
                "sellSeats": sell_seats,
                "labels": all_labels,
            }

        # ---- 合并 & 分类 ----
        youzi_labels = {"一线游资", "知名游资"}
        jigou_label = "机构专用"
        all_items = []

        for row in left_rows:
            days, code, _, rid, name, price, chg, total_amt, net_amt = row
            detail = stock_details.get(rid, {})
            labels = detail.get("labels", set())

            # 判断类别
            has_youzi = bool(labels & youzi_labels)
            has_jigou = jigou_label in labels
            has_gansidui = "敢死队" in labels
            has_gfgs = "跟风高手" in labels

            if tab == "youzi" and not has_youzi:
                continue
            if tab == "jigou" and not has_jigou:
                continue
            if tab == "gansidui" and not has_gansidui:
                continue
            if tab == "gfgs" and not has_gfgs:
                continue

            # 收集参与的游资/机构席位信息
            tagged_seats = []
            for side, seats in [("买", detail.get("buySeats", [])), ("卖", detail.get("sellSeats", []))]:
                for s in seats:
                    if not s["label"]:
                        continue
                    if tab == "youzi" and s["label"] not in youzi_labels:
                        continue
                    if tab == "jigou" and s["label"] != jigou_label:
                        continue
                    tagged_seats.append({
                        "side": side,
                        "dept": s["dept"],
                        "label": s["label"],
                        "buy": s["buy"],
                        "sell": s["sell"],
                        "net": s["net"],
                    })

            all_items.append({
                "code": code,
                "name": name,
                "price": price,
                "chg": chg,
                "totalAmt": total_amt,
                "netAmt": net_amt,
                "days": days or "",
                "labels": sorted(labels),
                "seats": tagged_seats,
            })
            if len(all_items) >= count:
                break

        return {
            "status_code": 0,
            "data": {
                "tab": tab,
                "date": report_date,
                "total": len(all_items),
                "items": all_items,
            },
        }

    # ========== 市场热榜 (eq/t/dq.10jqka.com.cn) ==========

    async def _request_native_sector_bridge(
        self,
        path: str,
        payload: dict,
        *,
        lane: str = "sector_table",
        require_success: bool = True,
    ) -> dict:
        """Run one native query under the App-wide single-flight boundary."""

        bridge_payload = dict(payload)
        preserve_frame_id = bool(bridge_payload.pop("_preserve_frame_id", False))
        if path.startswith("/native/ranking-debug"):
            family = "ranking"
        elif path.startswith("/native/indicator-list") or (
            path.startswith("/native/hurricane") and payload.get("securities")
        ) or (
            path.startswith("/native/hurricane") and preserve_frame_id
        ):
            family = "indicator"
        elif path.startswith("/native/hurricane"):
            family = "hurricane"
        else:
            family = "other"

        async def execute() -> dict:
            last_error: Exception | None = None
            max_attempts = 1 if family == "hurricane" else 2
            bridge_timeout = min(
                75.0,
                max(3.0, float(bridge_payload.get("timeout_ms") or 72000) / 1000 + 3),
            )
            for attempt in range(max_attempts):
                try:
                    request_payload = dict(bridge_payload)
                    if family == "hurricane":
                        # Pure Hurricane clients are not bound to the board page
                        # lifecycle, so an isolated frame ID still prevents stale
                        # callback reuse even though requests are now serialized.
                        request_payload["frame_id"] = 100_000 + (
                            int.from_bytes(os.urandom(4), "big")
                            % 2_000_000_000
                        )
                    if self._native_command_stream_enabled:
                        route = (
                            "ranking"
                            if path.startswith("/native/ranking-debug")
                            else "hurricane"
                        )
                        result = await self._request_native_command(
                            route=route,
                            payload=request_payload,
                            timeout_seconds=bridge_timeout,
                        )
                    else:
                        response = await self._client.post(
                            f"{self._native_bridge_for(lane)}{path}",
                            json=request_payload,
                            timeout=bridge_timeout,
                        )
                        response.raise_for_status()
                        try:
                            result = response.json()
                        except json.JSONDecodeError:
                            # Older Hook builds escaped quotes and slashes but leaked
                            # control characters from native error text.
                            result = json.loads(response.text, strict=False)
                    if not isinstance(result, dict):
                        raise ValueError("THS native response is not an object")
                    if require_success and not result.get("success"):
                        raise RuntimeError(
                            str(result.get("error") or "THS native request failed")
                        )
                    return result
                except (
                    httpx.HTTPError,
                    OSError,
                    TimeoutError,
                    json.JSONDecodeError,
                    RuntimeError,
                    ValueError,
                ) as exc:
                    last_error = exc
                    if isinstance(exc, RuntimeError) and str(exc).startswith(
                        (
                            "timeout_incomplete_rows",
                            "incomplete_required_rows",
                            "callback_error_incomplete_rows",
                        )
                    ):
                        # Row-completion errors are deterministic for the same
                        # board/query snapshot. Repeating the full native wait
                        # only doubles latency and cannot create missing rows.
                        break
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(1.5 if attempt == 0 else 0.9)
            raise last_error or RuntimeError("THS native request failed")

        # All native families eventually share the App's single transport.
        # Production stress tests show that even isolated Hurricane frame IDs
        # can terminate the App when they overlap Ranking or Unified requests.
        # Keep this boundary deterministic; throughput improvements must come
        # from batching and persistent subscriptions rather than unsafe overlap.
        async with self._native_lock_for(lane):
            return await execute()

    @staticmethod
    def _native_table_rows(payload: dict) -> list[dict]:
        body = payload.get("data")
        if not isinstance(body, dict):
            response = payload.get("protocolResponse") or payload.get("response") or {}
            body = response.get("body") if isinstance(response, dict) else {}
        if not isinstance(body, dict):
            body = {}
        columns = body.get("dataDict") or {}
        if not isinstance(columns, dict):
            raise ValueError("THS native table dataDict is missing")
        row_count = max(
            (len(values) for values in columns.values() if isinstance(values, list)),
            default=0,
        )
        rows: list[dict] = []
        for index in range(row_count):
            indicators = {
                str(indicator_id): values[index]
                for indicator_id, values in columns.items()
                if isinstance(values, list) and index < len(values)
            }
            rows.append(
                {
                    "provider_sector_code": indicators.get("4")
                    or indicators.get("5"),
                    "sector_name": indicators.get("55"),
                    "market_code": indicators.get("36103"),
                    "indicators": indicators,
                }
            )
        return rows

    @staticmethod
    def _native_etf_home_rows(payload: dict) -> list[dict]:
        """Normalize the four fields rendered by the ETF home cards."""

        rows = THSClient._native_table_rows(payload)
        normalized: list[dict] = []
        for row in rows:
            indicators = row.get("indicators") or {}
            code = str(indicators.get("4") or "").strip()
            name = str(indicators.get("55") or "").strip()
            if not code or not name or name == "--":
                continue
            normalized.append({
                "name": name,
                "code": code,
                "market": str(indicators.get("34338") or "").strip(),
                "change_pct": _native_number(indicators.get("33001")),
                "change_speed_pct": _native_number(indicators.get("48")),
                "turnover_yuan": _native_amount_number(indicators.get("19")),
                "scale_yuan": _native_amount_number(indicators.get("34307")),
                "display": {
                    "change_pct": indicators.get("33001"),
                    "change_speed_pct": indicators.get("48"),
                    "turnover": indicators.get("19"),
                    "scale": indicators.get("34307"),
                },
            })
        return normalized

    async def _get_index_hot_boards(self, count: int) -> dict:
        """Return the App hot-list page's real index-sector ranking."""

        normalized_count = max(1, min(int(count), 500))
        try:
            payload = await self._post(
                self.INDEX_SECTOR_URL,
                json={
                    "page_info": {
                        "page_begin": 0,
                        "page_size": normalized_count,
                    }
                },
            )
            if payload.get("status_code") not in (None, 0):
                raise RuntimeError(
                    str(payload.get("status_msg") or "index sector request failed")
                )
            data = payload.get("data") or {}
            indicator_indexes = {
                str(item.get("index_id")): int(item.get("idx"))
                for item in (data.get("indexes") or [])
                if item.get("index_id") is not None and item.get("idx") is not None
            }

            def value_of(values: list, indicator_id: str):
                index = indicator_indexes.get(indicator_id)
                if index is None:
                    return None
                for item in values:
                    if isinstance(item, dict) and item.get("idx") == index:
                        return item.get("value")
                if index >= len(values):
                    return None
                fallback = values[index]
                return fallback.get("value") if isinstance(fallback, dict) else fallback

            sectors = []
            for rank, row in enumerate(data.get("data") or [], start=1):
                values = row.get("values") or []
                raw_code = str(row.get("code") or "")
                market_code, _, provider_code = raw_code.partition(":")
                sectors.append(
                    {
                        "provider_sector_code": provider_code or raw_code,
                        "sector_name": value_of(values, "security_name"),
                        "market_code": market_code or None,
                        "sector_type": "index",
                        "heat_rank": rank,
                        "heat_score": _native_number(
                            value_of(
                                values,
                                "ths-hot-data-minute-attention-rate",
                            )
                        ),
                        "change_pct": _native_number(
                            value_of(values, "price_change_ratio_pct")
                        ),
                        "representative_etf_code": None,
                        "representative_etf_name": None,
                    }
                )
            return market_result(
                provider="ths_app_http",
                market="cn",
                data={
                    "sector_type": "index",
                    "metric": "source_heat",
                    "count": len(sectors),
                    "total": data.get("total"),
                    "sectors": sectors,
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "ths-hot-list/index_sector",
                    "signal_class": "provider_derived",
                    "app_runtime_required": False,
                },
            )
        except Exception as exc:
            return market_error(provider="ths_app_http", market="cn", error=exc)

    async def get_native_hot_boards(
        self,
        board_type: str = "concept",
        count: int = 10,
    ) -> dict:
        """Return the AStockSector page's Hurricane heat ranking."""

        if board_type == "index":
            return await self._get_index_hot_boards(count)

        hurricane_ids = {
            "concept": "cn_concept",
            "industry": "industry_l1",
        }
        if board_type not in hurricane_ids:
            raise ValueError("board_type must be concept, industry or index")
        public_hot_task: asyncio.Task | None = None
        try:
            if board_type in {"concept", "industry"}:
                public_hot_task = asyncio.create_task(
                    self.get_hot_board(board_type, count=max(20, int(count)))
                )
            payload = await self._request_native_sector_bridge(
                "/native/hurricane",
                {
                    "frame_id": 2312,
                    "start": 0,
                    "count": max(1, min(int(count), 100)),
                    "sort_indicator_id": (
                        "ths-hot-data-minute-attention-rate"
                    ),
                    "order": "DESCENDING",
                    "http_source_id": "sif-quoter-dataapi-sector-statistics",
                    "source_header_id": "sif-quoter-dataapi-sector-statistics",
                    "hurricane_ids": [hurricane_ids[board_type]],
                    "hurricane_indicator_ids": [
                        "ths-hot-data-minute-attention-rate"
                    ],
                    "mobile_indicator_ids": ["34818"],
                },
                lane="hurricane",
            )
            public_by_code: dict[str, dict] = {}
            if public_hot_task is not None:
                try:
                    public_hot = await public_hot_task
                    public_by_code = {
                        str(item.get("provider_sector_code")): item
                        for item in (
                            (public_hot.get("data") or {}).get("sectors") or []
                        )
                        if item.get("provider_sector_code")
                    }
                except Exception:
                    # The native ranking remains useful when ETF enrichment is
                    # unavailable. Index boards have no public-list equivalent.
                    pass
            rows = ((payload.get("data") or {}).get("rows") or [])
            missing_quote_fields = [
                (str(row.get("code")), str(row.get("market")))
                for row in rows
                if row.get("code")
                and row.get("market") is not None
                and (
                    not row.get("name")
                    or not (row.get("indicators") or {}).get("34818")
                )
            ]
            quote_by_code: dict[str, dict] = {}
            if missing_quote_fields:
                try:
                    quote_by_code = await self._request_native_stock_quotes(
                        missing_quote_fields
                    )
                except Exception:
                    # Preserve the heat ranking if the short quote hydration
                    # request is temporarily unavailable.
                    pass
            sectors = []
            for rank, row in enumerate(rows, start=1):
                indicators = row.get("indicators") or {}
                public_item = public_by_code.get(str(row.get("code"))) or {}
                quote = quote_by_code.get(str(row.get("code"))) or {}
                heat = (
                    indicators.get("ths-hot-data-minute-attention-rate") or {}
                ).get("content")
                change = (indicators.get("34818") or {}).get("content")
                sectors.append(
                    {
                        "provider_sector_code": row.get("code"),
                        "sector_name": row.get("name") or quote.get("name"),
                        "market_code": row.get("market"),
                        "sector_type": board_type,
                        "heat_rank": rank,
                        "heat_score": _native_number(heat),
                        "change_pct": (
                            _native_number(change)
                            if _native_number(change) is not None
                            else quote.get("change_rate")
                        ),
                        "representative_etf_code": public_item.get(
                            "representative_etf_code"
                        ),
                        "representative_etf_name": public_item.get(
                            "representative_etf_name"
                        ),
                    }
                )
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "sector_type": board_type,
                    "metric": "source_heat",
                    "count": len(sectors),
                    "sectors": sectors,
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "AStockSector",
                    "signal_class": "provider_derived",
                    "app_runtime_required": True,
                },
            )
        except Exception as exc:
            if public_hot_task is not None and not public_hot_task.done():
                public_hot_task.cancel()
            return market_error(provider="ths_native", market="cn", error=exc)

    async def get_native_sector_constituents(
        self,
        sector_code: str,
        *,
        market_code: str = "48",
        count: int = 100,
        sector_name: str | None = None,
    ) -> dict:
        """Return a board's constituent stocks using the native THS board query."""

        normalized_code = str(sector_code).strip()
        normalized_market = str(market_code).strip()
        if not normalized_code:
            raise ValueError("sector_code is required")
        if not normalized_market:
            raise ValueError("market_code is required")
        requested_count = max(1, min(int(count), 1000))
        indicator_ids = [
            "security_name",
            "last_price",
            "hq-fncdict-199112",
            "hq-fncdict-3475914",
            "total_market_value",
            "hq-fncdict-3934664",
            "hq-fncdict-1968584",
            "turnover",
        ]
        try:
            rows: list[dict] = []
            total_count: int | None = None
            while len(rows) < requested_count:
                # The App callback emits at most 20 complete constituent rows
                # per Hurricane frame. Asking for 100 makes the Hook correctly
                # report callback_error_incomplete_rows even though those 20
                # rows are valid. Page at the native frame capacity instead.
                page_size = min(20, requested_count - len(rows))
                payload = await self._request_native_sector_bridge(
                    "/native/hurricane",
                    {
                        "frame_id": 2267,
                        "_preserve_frame_id": True,
                        "start": len(rows),
                        "count": page_size,
                        "sort_indicator_id": "",
                        "order": "",
                        "hurricane_type": None,
                        "http_source_id": "sif-constituent-stock",
                        "source_header_id": "sif-constituent-stock",
                        "hurricane_ids": [],
                        "hurricane_indicator_ids": indicator_ids,
                        # Constituents are reference data: identity/name is the
                        # required field. After market close THS may issue
                        # callback error code 20 before every quote indicator
                        # is populated, even though the constituent page is
                        # already usable. Let the bridge settle and persist the
                        # available quote fields instead of rejecting the page.
                        "required_hurricane_indicator_ids": ["security_name"],
                        "mobile_indicator_ids": [],
                        "completion_mode": "settled",
                        "timeout_ms": 8000,
                        "selectors": {
                            "intersection": [
                                {
                                    "type": "HQ_BLOCK_CODE",
                                    "values": [
                                        f"{normalized_market}:{normalized_code}"
                                    ],
                                }
                            ]
                        },
                    },
                    lane="hurricane",
                )
                native_data = payload.get("data") or {}
                page_rows = native_data.get("rows") or []
                if total_count is None and native_data.get("total") is not None:
                    total_count = int(native_data["total"])
                rows.extend(page_rows)
                if (
                    not page_rows
                    or len(page_rows) < page_size
                    or (total_count is not None and len(rows) >= total_count)
                ):
                    break
            constituents = []
            for rank, row in enumerate(rows, start=1):
                indicators = row.get("indicators") or {}

                def value(indicator_id: str) -> object:
                    raw = indicators.get(indicator_id)
                    return raw.get("content") if isinstance(raw, dict) else raw

                constituents.append(
                    {
                        "rank": rank,
                        "security_code": row.get("code"),
                        "security_name": row.get("name") or value("security_name"),
                        "market_code": row.get("market"),
                        "latest": _native_number(value("last_price")),
                        "change_pct": _native_number(value("hq-fncdict-199112")),
                        "float_market_value": value("hq-fncdict-3475914"),
                        "total_market_value": value("total_market_value"),
                        "speed_pct": _native_number(value("hq-fncdict-3934664")),
                        "turnover_rate": _native_number(value("hq-fncdict-1968584")),
                        "turnover": value("turnover"),
                    }
                )
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "provider_sector_code": normalized_code,
                    "market_code": normalized_market,
                    "count": len(constituents),
                    "total_count": total_count,
                    "constituents": constituents,
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "SecurityTableViewModel",
                    "http_source_id": "sif-constituent-stock",
                    "selector_type": "HQ_BLOCK_CODE",
                    "app_runtime_required": True,
                },
            )
        except Exception as exc:
            normalized_name = str(sector_name or "").strip()
            if normalized_name:
                try:
                    stocks = await self._request_signed_iwencai_stocks(
                        f"{normalized_name}板块成分股",
                        requested_count,
                    )
                    constituents = [
                        {
                            "rank": rank,
                            "security_code": row.get("code"),
                            "security_name": row.get("name"),
                            "market_code": row.get("market_code"),
                            "latest": row.get("latest"),
                            "change_pct": row.get("change_rate"),
                            "float_market_value": None,
                            "total_market_value": None,
                            "speed_pct": row.get("speed"),
                            "turnover_rate": None,
                            "turnover": None,
                        }
                        for rank, row in enumerate(stocks, start=1)
                    ]
                    return market_result(
                        provider="ths_iwencai",
                        market="cn",
                        data={
                            "provider_sector_code": normalized_code,
                            "market_code": normalized_market,
                            "count": len(constituents),
                            "total_count": len(constituents),
                            "constituents": constituents,
                        },
                        timezone_name="Asia/Shanghai",
                        provider_metadata={
                            "source_component": "signed_iwencai",
                            "fallback_from": "sif-constituent-stock",
                            "native_error": str(exc),
                            "app_runtime_required": False,
                        },
                    )
                except Exception as fallback_exc:
                    exc = RuntimeError(
                        f"native={exc}; signed_iwencai={fallback_exc}"
                    )
            return market_error(
                provider="ths_native",
                market="cn",
                error=exc,
                provider_metadata={
                    "capability": "sector_constituents",
                    "provider_sector_code": normalized_code,
                },
            )

    async def _request_native_sector_table(
        self,
        *,
        page_id: int,
        request_text: str,
    ) -> tuple[dict, list[dict]]:
        payload = await self._request_native_unified(
            lane="sector_table",
            online_id="",
            protocol_id=page_id,
            page_id=2312,
            request_dic=request_text,
            timeout_seconds=5,
        )
        return payload, self._native_table_rows(payload)

    async def _request_complete_native_sector_table(
        self,
        *,
        page_id: int,
        sort_id: str = "34818",
    ) -> list[dict]:
        """Fetch all rows for one native sector classification."""

        page_size = 500
        rows: list[dict] = []
        seen: set[tuple[str, str]] = set()
        while True:
            _, page_rows = await self._request_native_sector_table(
                page_id=page_id,
                request_text=(
                    f"rowcount={page_size}\r\n"
                    f"startrow={len(rows)}\r\n"
                    "adddata=1\r\nsortorder=0\r\n"
                    f"sortid={sort_id}"
                ),
            )
            added = 0
            for row in page_rows:
                identity = (
                    str(row.get("market_code") or ""),
                    str(row.get("provider_sector_code") or ""),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append(row)
                added += 1
            if len(page_rows) < page_size or added == 0:
                break
        return rows

    async def _request_native_sector_quote_table(
        self,
        classification: str,
    ) -> list[dict]:
        """Fetch all board universes, then run one MobileHQ quote query.

        A QueryClient is attached to the App board page's frame 2312. One
        process therefore has one callback slot, regardless of how many HTTP
        connections reach the Hook. Concurrent callers share this short-lived
        snapshot so five classification jobs cause one MobileHQ request rather
        than racing five requests on the same frame.
        """

        hurricane_ids = {
            "industry": ["industry_l1"],
            "concept": ["cn_concept"],
            "style": ["tszs"],
            "region": ["region"],
        }
        if classification not in {"all", *hurricane_ids}:
            raise ValueError(
                "classification must be all, industry, concept, style or region"
            )

        async with self._native_sector_quote_tables_lock:
            now = asyncio.get_running_loop().time()
            if (
                self._native_sector_quote_tables
                and now < self._native_sector_quote_tables_deadline
            ):
                return list(self._native_sector_quote_tables[classification])

            async def fetch_listing(name: str, ids: list[str]) -> tuple[str, list[dict]]:
                payload = await self._request_native_sector_bridge(
                    "/native/hurricane",
                    {
                        "frame_id": 2312,
                        "start": 0,
                        "count": 1000,
                        "sort_indicator_id": "up_down_limit_up_num",
                        "order": "DESCENDING",
                        "http_source_id": "sif-quoter-dataapi-sector-statistics",
                        "hurricane_ids": ids,
                        "hurricane_indicator_ids": [
                            "security_name",
                            "up_down_limit_up_num",
                        ],
                        "mobile_indicator_ids": [],
                        "settle_ms": 500,
                    },
                    lane="hurricane",
                )
                return name, ((payload.get("data") or {}).get("rows") or [])

            listing_results = await asyncio.gather(
                *(
                    fetch_listing(name, ids)
                    for name, ids in hurricane_ids.items()
                )
            )
            listings: dict[str, dict[tuple[str, str], dict]] = {}
            all_rows: dict[tuple[str, str], dict] = {}
            for name, rows in listing_results:
                category_rows: dict[tuple[str, str], dict] = {}
                for row in rows:
                    code = str(row.get("code") or "")
                    market = str(row.get("market") or "48")
                    if not code:
                        continue
                    key = (market, code)
                    category_rows.setdefault(key, row)
                    all_rows.setdefault(key, row)
                listings[name] = category_rows
            if not all_rows:
                return []

            def content(value: object) -> object:
                return value.get("content") if isinstance(value, dict) else value

            quotes_by_category: dict[
                str, dict[tuple[str, str], dict]
            ] = {}
            for name, category_rows in listings.items():
                securities = [
                    {
                        "code": code,
                        "market": market,
                        "name": str(row.get("name") or ""),
                    }
                    for (market, code), row in category_rows.items()
                ]
                if not securities:
                    quotes_by_category[name] = {}
                    continue
                quote_rows: list[dict] = []
                for offset in range(0, len(securities), 100):
                    security_chunk = securities[offset : offset + 100]
                    quotes = await self._request_native_sector_bridge(
                        "/native/hurricane",
                        {
                            "frame_id": 2312,
                            "start": 0,
                            "count": len(security_chunk),
                            "sort_indicator_id": "",
                            "order": "",
                            "hurricane_type": "TAG",
                            "hurricane_ids": [],
                            "hurricane_indicator_ids": [],
                            "mobile_indicator_ids": [
                                "55",
                                "10",
                                "34818",
                                "36251",
                                "34311",
                                "275",
                                "35284",
                                "35286",
                            ],
                            "required_mobile_indicator_ids": [
                                "55",
                                "10",
                                "34818",
                                "34311",
                            ],
                            "securities": security_chunk,
                            "settle_ms": 1000,
                        },
                        lane="hurricane",
                    )
                    quote_rows.extend(
                        ((quotes.get("data") or {}).get("rows") or [])
                    )
                quotes_by_category[name] = {
                    (
                        str(row.get("market") or "48"),
                        str(row.get("code") or ""),
                    ): row
                    for row in quote_rows
                    if row.get("code")
                }

            def build_rows(
                source: dict[tuple[str, str], dict],
                quotes_by_key: dict[tuple[str, str], dict],
            ) -> list[dict]:
                result: list[dict] = []
                for key, listing_row in source.items():
                    quote_row = quotes_by_key.get(key) or {}
                    listing_indicators = listing_row.get("indicators") or {}
                    quote_indicators = quote_row.get("indicators") or {}
                    indicators = {
                        indicator_id: content(value)
                        for indicator_id, value in quote_indicators.items()
                    }
                    indicators["up_down_limit_up_num"] = content(
                        listing_indicators.get("up_down_limit_up_num")
                    )
                    result.append(
                        {
                            "provider_sector_code": key[1],
                            "market_code": key[0],
                            "sector_name": (
                                quote_row.get("name")
                                or content(quote_indicators.get("55"))
                                or listing_row.get("name")
                                or content(
                                    listing_indicators.get("security_name")
                                )
                            ),
                            "indicators": indicators,
                        }
                    )
                return result

            self._native_sector_quote_tables = {
                name: build_rows(rows, quotes_by_category[name])
                for name, rows in listings.items()
            }
            all_table: dict[tuple[str, str], dict] = {}
            # The App's “all” tab combines tradeable industry and concept
            # boards. Style/region have their own tabs and must not pollute
            # the headline ranking.
            for category in ("industry", "concept"):
                rows = self._native_sector_quote_tables.get(category, [])
                for row in rows:
                    key = (
                        str(row.get("market_code") or "48"),
                        str(row.get("provider_sector_code") or ""),
                    )
                    all_table.setdefault(key, row)
            self._native_sector_quote_tables["all"] = list(all_table.values())
            self._native_sector_quote_tables_deadline = (
                asyncio.get_running_loop().time() + 5.0
            )
            return list(self._native_sector_quote_tables[classification])

    async def get_native_sector_ranking_bundle(
        self,
        classification: str,
        count: int = 50,
    ) -> dict:
        """Fetch one complete table and derive its three sortable rankings."""

        page_ids = {
            "all": 1358,
            "industry": 1209,
            "concept": 1297,
            "style": 4046,
            "region": 1337,
        }
        metric_ids = {
            "change": "34818",
            "speed": "36251",
            "volume_ratio": "34311",
        }
        if classification not in page_ids:
            raise ValueError(
                "classification must be all, industry, concept, style or region"
            )
        try:
            rows = await self._request_native_sector_quote_table(classification)
            limit = max(1, min(int(count), 500))
            rankings: dict[str, list[dict]] = {}
            for metric, indicator_id in metric_ids.items():
                def sort_key(row: dict) -> tuple[bool, float]:
                    value = _native_number(
                        (row.get("indicators") or {}).get(indicator_id)
                    )
                    return (
                        value is not None,
                        value if value is not None else float("-inf"),
                    )

                ranked_rows = sorted(
                    rows,
                    key=sort_key,
                    reverse=True,
                )[:limit]
                sectors = []
                for rank, row in enumerate(ranked_rows, start=1):
                    indicators = row.get("indicators") or {}
                    sectors.append(
                        {
                            **row,
                            "sector_type": classification,
                            "rank": rank,
                            "metric": metric,
                            "metric_value": _native_number(
                                indicators.get(indicator_id)
                            ),
                            "change_pct": _native_number(
                                indicators.get("34818")
                            ),
                            "speed_pct": _native_number(
                                indicators.get("36251")
                            ),
                            "volume_ratio": _native_number(
                                indicators.get("34311")
                            ),
                            "main_net_inflow": _native_number(
                                indicators.get("34391")
                            ),
                            "lead_stock_code": indicators.get("275"),
                            "lead_stock_name": indicators.get("35284"),
                            "lead_stock_change_pct": _native_number(
                                indicators.get("35286")
                            ),
                        }
                    )
                rankings[metric] = sectors
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "classification": classification,
                    "source_row_count": len(rows),
                    "count": sum(len(items) for items in rankings.values()),
                    "rankings": rankings,
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "AStockSector.PlateCard",
                    "signal_class": "market_fact",
                    "classification": classification,
                    "request_mode": "complete_table_fan_out",
                },
            )
        except Exception as exc:
            return market_error(provider="ths_native", market="cn", error=exc)

    async def get_native_sector_ranking(
        self,
        metric: str = "change",
        count: int = 50,
        classification: str = "all",
    ) -> dict:
        metric_ids = {
            "change": "34818",
            "speed": "36251",
            "volume_ratio": "34311",
            "limit_up_count": "up_down_limit_up_num",
        }
        page_ids = {
            "all": 1358,
            "industry": 1209,
            "concept": 1297,
            "style": 4046,
            "region": 1337,
        }
        hurricane_ids = {
            "all": ["cn_concept", "industry_l1", "region", "tszs"],
            "industry": ["industry_l1"],
            "concept": ["cn_concept"],
            "style": ["tszs"],
            "region": ["region"],
        }
        if metric not in metric_ids:
            raise ValueError(
                "metric must be change, speed, volume_ratio or limit_up_count"
            )
        if classification not in page_ids:
            raise ValueError(
                "classification must be all, industry, concept, style or region"
            )
        try:
            if metric == "limit_up_count":
                payload = await self._request_native_sector_bridge(
                    "/native/hurricane",
                    {
                        "frame_id": 2312,
                        "start": 0,
                        "count": max(1, min(int(count), 100)),
                        "sort_indicator_id": metric_ids[metric],
                        "order": "DESCENDING",
                        "http_source_id": (
                            "sif-quoter-dataapi-sector-statistics"
                        ),
                        "hurricane_ids": hurricane_ids[classification],
                        "hurricane_indicator_ids": [
                            "security_name",
                            metric_ids[metric],
                            "hq-fncdict-199112",
                        ],
                        "mobile_indicator_ids": [],
                    },
                    lane="hurricane",
                )
                native_rows = ((payload.get("data") or {}).get("rows") or [])
                try:
                    quotes = await self._request_native_stock_quotes(
                        [
                            (str(row.get("code")), str(row.get("market")))
                            for row in native_rows
                            if row.get("code") and row.get("market") is not None
                        ]
                    )
                except Exception:
                    quotes = {}
                sectors = []
                for rank, native_row in enumerate(native_rows, start=1):
                    indicators = native_row.get("indicators") or {}
                    quote = quotes.get(str(native_row.get("code"))) or {}

                    def hurricane_value(indicator_id: str) -> object:
                        value = indicators.get(indicator_id)
                        return value.get("content") if isinstance(value, dict) else value

                    sectors.append(
                        {
                            "provider_sector_code": native_row.get("code"),
                            "sector_name": native_row.get("name")
                            or hurricane_value("security_name")
                            or quote.get("name"),
                            "market_code": native_row.get("market"),
                            "sector_type": classification,
                            "rank": rank,
                            "metric": metric,
                            "metric_value": _native_number(
                                hurricane_value(metric_ids[metric])
                            ),
                            "limit_up_count": _native_number(
                                hurricane_value(metric_ids[metric])
                            ),
                            "change_pct": _native_number(
                                hurricane_value("hq-fncdict-199112")
                            ) or quote.get("change_rate"),
                        }
                    )
            else:
                rows = await self._request_native_sector_quote_table(classification)
                indicator_id = metric_ids[metric]

                def sort_key(row: dict) -> tuple[bool, float]:
                    value = _native_number(
                        (row.get("indicators") or {}).get(indicator_id)
                    )
                    return (
                        value is not None,
                        value if value is not None else float("-inf"),
                    )

                rows = sorted(rows, key=sort_key, reverse=True)[
                    : max(1, min(int(count), 500))
                ]
                sectors = []
                for rank, row in enumerate(rows, start=1):
                    indicators = row["indicators"]
                    sectors.append(
                        {
                            **row,
                            "sector_type": classification,
                            "rank": rank,
                            "metric": metric,
                            "metric_value": _native_number(
                                indicators.get(metric_ids[metric])
                            ),
                            "change_pct": _native_number(indicators.get("34818")),
                            "speed_pct": _native_number(indicators.get("36251")),
                            "volume_ratio": _native_number(indicators.get("34311")),
                            "main_net_inflow": _native_number(
                                indicators.get("34391")
                            ),
                            "lead_stock_code": indicators.get("275"),
                            "lead_stock_name": indicators.get("35284"),
                            "lead_stock_change_pct": _native_number(
                                indicators.get("35286")
                            ),
                        }
                    )
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "classification": classification,
                    "metric": metric,
                    "count": len(sectors),
                    "sectors": sectors,
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "AStockSector.PlateCard",
                    "signal_class": "market_fact",
                    "classification": classification,
                },
            )
        except Exception as exc:
            return market_error(provider="ths_native", market="cn", error=exc)

    async def get_native_sector_fund_flow(
        self,
        sector_type: str = "industry",
        count: int = 50,
    ) -> dict:
        page_ids = {"industry": 1348, "concept": 1349, "region": 1362}
        if sector_type not in page_ids:
            raise ValueError("sector_type must be industry, concept or region")
        try:
            _, rows = await self._request_native_sector_table(
                page_id=page_ids[sector_type],
                request_text=(
                    f"rowcount={max(1, min(int(count), 500))}\r\n"
                    "startrow=0\r\nsortid=34391\r\nsortorder=0"
                ),
            )
            sectors = []
            for rank, row in enumerate(rows, start=1):
                indicators = row["indicators"]
                sectors.append(
                    {
                        **row,
                        "sector_type": sector_type,
                        "rank": rank,
                        "main_net_inflow": _native_number(indicators.get("34391")),
                        "main_net_inflow_unit": "100_million_cny",
                    }
                )
            inflow_rank = 0
            for sector in sectors:
                value = sector.get("main_net_inflow")
                if value is None:
                    sector["flow_direction"] = "unknown"
                elif value > 0:
                    inflow_rank += 1
                    sector["flow_direction"] = "inflow"
                    sector["direction_rank"] = inflow_rank
                elif value < 0:
                    sector["flow_direction"] = "outflow"
                else:
                    sector["flow_direction"] = "flat"
            outflows = sorted(
                (
                    sector
                    for sector in sectors
                    if sector.get("flow_direction") == "outflow"
                ),
                key=lambda sector: sector.get("main_net_inflow") or 0,
            )
            for direction_rank, sector in enumerate(outflows, start=1):
                sector["direction_rank"] = direction_rank
            return market_result(
                provider="ths_native",
                market="cn",
                data={
                    "sector_type": sector_type,
                    "metric": "main_net_inflow",
                    "count": len(sectors),
                    "sectors": sectors,
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "AStockSector.SectorMainFlow",
                    "signal_class": "market_fact",
                    "unit": "100_million_cny",
                    "contains_both_directions": True,
                },
            )
        except Exception as exc:
            return market_error(provider="ths_native", market="cn", error=exc)

    async def _request_app_proxy(
        self,
        url: str,
        *,
        method: str = "GET",
        body: dict | list | str | None = None,
        content_type: str = "application/json",
    ) -> dict:
        request_body = body
        if body is not None and not isinstance(body, str):
            request_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        parsed_url = urlsplit(url)
        endpoint_key = f"{method.upper()}:{parsed_url.netloc}{parsed_url.path}"
        endpoint_lock = self._app_http_endpoint_locks.setdefault(
            endpoint_key,
            asyncio.Lock(),
        )
        # 同一个 App HTTP 接口共享回调槽。即使请求体不同，并发发送也可能让
        # 后返回的响应覆盖先前请求，表现为 status=ok 但 data={}。不同接口仍
        # 可并行，只有完全相同的 method + host + path 在客户端串行。
        async with endpoint_lock:
            response = await self._client.post(
                f"{self._app_http_bridge_url}/proxy",
                json={
                    "url": url,
                    "method": method.upper(),
                    "body": request_body or "",
                    "content_type": content_type,
                },
                timeout=30,
            )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("THS app HTTP response is not an object")
        return result

    async def _get_etf_tracking_index_map(
        self,
        codes: list[str],
    ) -> dict[str, dict[str, str | None]]:
        now = datetime.now(timezone.utc).timestamp()
        if self._etf_tracking_index_cache and now < self._etf_tracking_index_cache_deadline:
            return self._etf_tracking_index_cache
        async with self._etf_tracking_index_cache_lock:
            now = datetime.now(timezone.utc).timestamp()
            if self._etf_tracking_index_cache and now < self._etf_tracking_index_cache_deadline:
                return self._etf_tracking_index_cache
            semaphore = asyncio.Semaphore(32)

            async def load(code: str) -> tuple[str, dict[str, str | None]]:
                async with semaphore:
                    try:
                        raw = await self.get_fund_base(code)
                        data = raw.get("data") or {}
                        related = data.get("relatedIndexInfo") or {}
                        return code, {
                            "index_code": related.get("indexCode"),
                            "index_name": related.get("indexName"),
                        }
                    except Exception:
                        return code, {"index_code": None, "index_name": None}

            pairs = await asyncio.gather(*(load(code) for code in sorted(set(codes))))
            self._etf_tracking_index_cache = dict(pairs)
            self._etf_tracking_index_cache_deadline = now + 86_400
            return self._etf_tracking_index_cache

    async def get_native_etf_zone_snapshot(self) -> dict:
        """采集同花顺 App ETF 专区的完整数据模型。

        页面配置、赛道树、六类热门卡片、市场总览及 ETF 全量排行池均保留
        原始口径。排行明细一次读取全池，前端展示的涨幅、涨速、成交额等
        排序可由同一份字段完备的快照重建。
        """
        base = "https://fund.10jqka.com.cn"
        overview_body = {
            "code_selectors": {
                "include": [{"type": "stock_code", "values": ["48:883957"]}]
            },
            "indexes": [
                {"index_id": name}
                for name in (
                    "etfUpCount", "etfDownCount", "etfEqCount",
                    "etfTotalTurnoverMoney", "etfTotalTurnoverMoneyChgPreDay",
                )
            ],
        }
        pool_body = {
            "businessKey": "etf-ranking",
            "businessPoolKey": "347c9f28-8a67-48a7-8c05-380ff8e595c7",
            "custom": {
                # ETF 首页卡片本身需要名称、涨幅、涨速、成交额和规模；不能只取
                # subMarket 后再假定行情聚合接口能够补齐全部基金字段。
                "fieldList": [
                    "subMarket", "simpleName", "last_price",
                    "hq_price_change_ratio_pct",
                    "inr-price_change_ratio_pct-sum-4m",
                    "turnover", "total_market_value", "fund_share",
                ],
                "limit": 10000,
                "offset": 0,
            },
        }
        track_filtered_pool_body = {
            **pool_body,
            "custom": {
                **pool_body["custom"],
                "uniqueType": "etf_third_level_track_list",
            },
        }
        tracking_index_filtered_pool_body = {
            **pool_body,
            "custom": {
                **pool_body["custom"],
                # App“跟踪指数过滤”的原生服务端去重口径。tack 是上游
                # 字段的实际拼写，不能修正为 track。
                "uniqueType": ETF_TRACKING_INDEX_UNIQUE_TYPE,
            },
        }
        hot_urls = {
            "etf": "https://eq.10jqka.com.cn/open/api/etf_rank/v1/hot.txt?limit=100",
            **{
                kind: f"{base}/quotation/etf_tab/etf_rank/v1/hot?type={kind}&limit=100"
                for kind in ("a", "concept", "industry", "hk", "usa")
            },
        }
        scale_url = "https://eq.10jqka.com.cn/open/api/etf_tab/hq_tab/v1/scale"
        t0_url = (
            "https://eq.10jqka.com.cn/open/api/etf_adviser/"
            "v1/fund/tag/t_tag.txt"
        )
        try:
            (
                overview,
                rank_config,
                track_tree,
                pool,
                track_filtered_pool,
                tracking_index_filtered_pool,
                scale_payload,
                t0_payload,
                *hot_payloads,
            ) = await asyncio.gather(
                self._request_app_proxy(
                    f"{base}/quotation/data/query/v1/table",
                    method="POST",
                    body=overview_body,
                ),
                self._request_app_proxy(
                    f"{base}/marketing/operation/config/module/v1/key/etfzonerank"
                ),
                self._request_app_proxy(
                    f"{base}/quotation/etf_tab/hq_tab/v1/track/tree"
                ),
                self._request_app_proxy(
                    f"{base}/quotation/fund_pool/v2/query",
                    method="POST",
                    body=pool_body,
                ),
                self._request_app_proxy(
                    f"{base}/quotation/fund_pool/v2/query",
                    method="POST",
                    body=track_filtered_pool_body,
                ),
                self._request_app_proxy(
                    f"{base}/quotation/fund_pool/v2/query",
                    method="POST",
                    body=tracking_index_filtered_pool_body,
                ),
                self._request_app_proxy(scale_url),
                self._request_app_proxy(t0_url),
                *(self._request_app_proxy(url) for url in hot_urls.values()),
            )
            pool_data = pool.get("data") or {}
            track_filtered_pool_data = track_filtered_pool.get("data") or {}
            tracking_index_filtered_pool_data = (
                tracking_index_filtered_pool.get("data") or {}
            )
            scale_data = scale_payload.get("data") or {}
            if not isinstance(scale_data, dict):
                raise ValueError("THS ETF scale payload is not an object")
            indexes = pool_data.get("indexes") or []
            index_names = [str(item.get("type") or "") for item in indexes]
            market_pos = index_names.index("subMarket") if "subMarket" in index_names else 0
            code_pos = next(
                (idx for idx, name in enumerate(index_names) if name in {"tradeCode", "code"}),
                1,
            )
            codes = []
            for row in pool_data.get("itemList") or []:
                if not isinstance(row, list) or len(row) <= max(market_pos, code_pos):
                    continue
                market, code = str(row[market_pos]), str(row[code_pos])
                if market and code:
                    codes.append(f"{market}:{code}")
            config_data = rank_config.get("data") or []
            if isinstance(config_data, str):
                config_data = json.loads(config_data)
            rank_definitions = (
                (config_data[0] if config_data else {}).get("rankConfig") or []
            )
            indicator_ids = ["security_name"]
            for definition in rank_definitions:
                for indicator_id in str(definition.get("indicCodes") or "").split(","):
                    if indicator_id and indicator_id not in indicator_ids:
                        indicator_ids.append(indicator_id)
            code_groups: dict[str, list[str]] = {}
            for market_code in codes:
                market, code = market_code.split(":", 1)
                code_groups.setdefault(market, []).append(code)
            # multi_last_snapshot 对单个 market 下的代码数也有限制。旧实现按市场
            # 分批，沪深两个市场会一次塞入 100+ 个代码，服务端返回 fail_params
            # 且 quote_data 为空。这里按每市场 40 只切片。
            quote_requests = []
            for market, market_codes in code_groups.items():
                for start in range(0, len(market_codes), 40):
                    quote_requests.append(
                        self._request_app_proxy(
                        "https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/multi_last_snapshot",
                        method="POST",
                        body={
                            "code_list": [{
                                "market": market,
                                "codes": market_codes[start:start + 40],
                            }],
                            "trade_class": "intraday",
                            # 最新、涨幅、成交额、涨速；与 ETF 专区卡片请求一致。
                            "data_fields": ["security_name", "10", "199112", "19", "264648"],
                            "lang": "zh_cn",
                            "gpid": 0,
                        },
                        )
                    )
            quote_payloads = await asyncio.gather(*quote_requests)
            quote_row_count = sum(
                len(((item.get("data") or {}).get("quote_data") or []))
                for item in quote_payloads
            )
            hot = dict(zip(hot_urls, hot_payloads, strict=True))
            t0_data = t0_payload.get("data") or {}
            t0_list = t0_data.get("list") or [] if isinstance(t0_data, dict) else []
            t0_codes = sorted({str(item) for item in t0_list if item})
            identity_by_market_code: dict[str, dict[str, str]] = {}
            for row in pool_data.get("itemList") or []:
                if not isinstance(row, list) or len(row) <= max(market_pos, code_pos):
                    continue
                market, code = str(row[market_pos]), str(row[code_pos])
                name = str(row[index_names.index("simpleName")]) if "simpleName" in index_names else code
                identity_by_market_code[f"{market}:{code}"] = {
                    "market": market, "code": code, "name": name,
                }
            filtered_indexes = track_filtered_pool_data.get("indexes") or []
            filtered_types = [str(item.get("type") or "") for item in filtered_indexes]
            filtered_market_pos = filtered_types.index("subMarket")
            filtered_code_pos = next(
                idx for idx, name in enumerate(filtered_types)
                if name in {"tradeCode", "code"}
            )
            track_filtered_members = {
                f"{row[filtered_market_pos]}:{row[filtered_code_pos]}"
                for row in (track_filtered_pool_data.get("itemList") or [])
                if isinstance(row, list)
                and len(row) > max(filtered_market_pos, filtered_code_pos)
            }
            tracking_filtered_indexes = (
                tracking_index_filtered_pool_data.get("indexes") or []
            )
            tracking_filtered_types = [
                str(item.get("type") or "") for item in tracking_filtered_indexes
            ]
            tracking_filtered_market_pos = tracking_filtered_types.index("subMarket")
            tracking_filtered_code_pos = next(
                idx for idx, name in enumerate(tracking_filtered_types)
                if name in {"tradeCode", "code"}
            )
            tracking_index_filtered_members = {
                f"{row[tracking_filtered_market_pos]}:{row[tracking_filtered_code_pos]}"
                for row in (tracking_index_filtered_pool_data.get("itemList") or [])
                if isinstance(row, list)
                and len(row) > max(
                    tracking_filtered_market_pos,
                    tracking_filtered_code_pos,
                )
            }
            t0_members = set(t0_codes)
            t0_rows: list[dict] = []
            full_ranking_rows: list[dict] = []
            track_filtered_rows: list[dict] = []
            tracking_index_filtered_rows: list[dict] = []
            for quote_payload in quote_payloads:
                for quote in ((quote_payload.get("data") or {}).get("quote_data") or []):
                    fields = [str(item) for item in quote.get("data_fields") or []]
                    values = (quote.get("value") or [[]])[0]
                    cells = {
                        field: values[index] if index < len(values) else None
                        for index, field in enumerate(fields)
                    }
                    market = str(quote.get("market") or "")
                    code = str(quote.get("code") or "")
                    identity = identity_by_market_code.get(f"{market}:{code}") or {
                        "market": market,
                        "code": code,
                        "name": str(cells.get("security_name") or code),
                    }
                    scale = scale_data.get(identity["code"])
                    normalized_row = {
                        **identity,
                        "latest": _native_number(cells.get("10")),
                        "change_pct": _native_number(cells.get("199112")),
                        "change_speed_pct": _native_number(cells.get("264648")),
                        "turnover_yuan": _native_amount_number(cells.get("19")),
                        "scale_yuan": _native_amount_number(scale),
                        "display": {
                            "change_pct": cells.get("199112"),
                            "change_speed_pct": cells.get("264648"),
                            "turnover": cells.get("19"),
                            "scale": scale,
                        },
                    }
                    full_ranking_rows.append(normalized_row)
                    member_key = f"{identity['market']}:{identity['code']}"
                    if member_key in track_filtered_members:
                        track_filtered_rows.append(normalized_row)
                    if member_key in tracking_index_filtered_members:
                        tracking_index_filtered_rows.append(normalized_row)
                    if member_key in t0_members and member_key in track_filtered_members:
                        t0_rows.append(normalized_row)
            tracking_index_map = await self._get_etf_tracking_index_map(
                [item["code"] for item in identity_by_market_code.values()]
            )
            tracking_index_rows: list[dict] = []
            for row in tracking_index_filtered_rows:
                identity = tracking_index_map.get(row["code"]) or {}
                tracking_index_rows.append({
                    **row,
                    "tracking_index_code": identity.get("index_code"),
                    "tracking_index_name": identity.get("index_name"),
                })
            us_index_pattern = re.compile(
                r"纳指|纳斯达克|标普(?:500)?|道琼斯|罗素(?:1000|2000)|MSCI美国|美国(?:50|科技|消费|生物)"
            )
            non_us_pattern = re.compile(r"中国A股|港股通|港股")
            fund_name_by_code = {
                item["code"]: item.get("name") or ""
                for item in identity_by_market_code.values()
            }
            us_cross_border_codes = {
                code
                for code, identity in tracking_index_map.items()
                if (
                    us_index_pattern.search(" ".join((
                        str(identity.get("index_name") or ""),
                        str(fund_name_by_code.get(code) or ""),
                    )))
                    and not non_us_pattern.search(str(fund_name_by_code.get(code) or ""))
                )
            }
            # The US home card uses the ETF popularity endpoint's names/heat,
            # but limits membership to A-share ETFs tracking US indices.
            hot_etf_data = (hot.get("etf") or {}).get("data") or {}
            hot_etf_rows = (
                hot_etf_data.get("list") or []
                if isinstance(hot_etf_data, dict)
                else []
            )
            full_by_code = {row["code"]: row for row in full_ranking_rows}
            cross_border_popularity = []
            for hot_row in hot_etf_rows:
                code = str(hot_row.get("code") or "")
                if code not in us_cross_border_codes:
                    continue
                quote = full_by_code.get(code) or {}
                cross_border_popularity.append({
                    **quote,
                    "code": code,
                    "name": hot_row.get("name") or quote.get("name"),
                    "heat": _native_number(hot_row.get("rate")),
                    "heat_date": hot_row.get("sdate"),
                    "heat_hour": hot_row.get("stime"),
                    "t0": f"{quote.get('market')}:{code}" in t0_members,
                })
            cross_border_appreciation = sorted(
                (
                    {
                        **row,
                        "t0": f"{row.get('market')}:{row['code']}" in t0_members,
                    }
                    for row in full_ranking_rows
                    if row["code"] in us_cross_border_codes
                ),
                key=lambda row: row.get("change_pct")
                if row.get("change_pct") is not None else -math.inf,
                reverse=True,
            )
            t0_rows.sort(
                key=lambda item: item.get("change_pct")
                if item.get("change_pct") is not None else -math.inf,
                reverse=True,
            )
            fetched_at = datetime.now(timezone.utc)
            return market_result(
                provider="ths_app_http",
                market="cn",
                data={
                    "market_overview": overview,
                    "hot_rankings": hot,
                    "operation_config": config_data,
                    "track_tree": track_tree.get("data"),
                    "rank_definitions": rank_definitions,
                    "etf_universe": pool_data,
                    "etf_quotes": quote_payloads,
                    "etf_scales": scale_data,
                    "full_ranking": {
                        "count": len(full_ranking_rows),
                        "rows": full_ranking_rows,
                        "membership_source": scale_url,
                        "quote_source": "multi_last_snapshot",
                    },
                    "track_filtered_ranking": {
                        "count": len(track_filtered_rows),
                        "rows": track_filtered_rows,
                        "unique_type": "etf_third_level_track_list",
                        "quote_source": "multi_last_snapshot",
                    },
                    "tracking_index_filtered_ranking": {
                        "count": len(tracking_index_rows),
                        "rows": tracking_index_rows,
                        "unique_type": ETF_TRACKING_INDEX_UNIQUE_TYPE,
                        "membership_source": "quotation/fund_pool/v2/query",
                        "identity_source": "quotation/fund_detail/v2/base",
                    },
                    "us_cross_border_etf": {
                        "popularity": cross_border_popularity,
                        "appreciation": cross_border_appreciation,
                        "count": len(us_cross_border_codes),
                        "membership_source": "relatedIndexInfo.indexName",
                        "heat_source": hot_urls["etf"],
                        "quote_source": "multi_last_snapshot",
                    },
                    "native_t0_codes": t0_codes,
                    "t0_fallback_ranking": {
                        "category": "t0",
                        "count": len(t0_rows),
                        "rows": t0_rows,
                        "membership_source": t0_url,
                        "quote_source": "multi_last_snapshot",
                    },
                    "etf_count": len(codes),
                },
                source_time=fetched_at.isoformat(),
                trade_date=fetched_at.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "thsjj-jj-fe-etf-zone",
                    "complete": quote_row_count == len(codes) and bool(scale_data),
                    "hot_categories": list(hot_urls),
                    "ranking_field_count": len(indicator_ids),
                    "quote_fields": ["10", "199112", "19", "264648"],
                    "quote_row_count": quote_row_count,
                    "scale_count": len(scale_data),
                    "native_t0_count": len(t0_codes),
                    "home_rankings_transport": "ths_native_stream_push",
                },
            )
        except Exception as exc:
            return market_error(provider="ths_app_http", market="cn", error=exc)

    async def get_native_futures_zone_snapshot(self) -> dict:
        """采集同花顺 App FuturesSynthesis 页面全部行情模块。"""
        ranking_groups = {
            "all": "all-main", "night": "main-yepan",
            "energy_chemical": "main-nengyuanhuagong",
            "nonferrous": "main-yousejinshu", "precious": "main-guijinshu",
            "ferrous": "main-heisejinshu", "agriculture": "main-nongchanpin",
            "financial": "main-jingrongbankuai", "shfe": "main-shangqisuo",
            "dce": "main-dashangsuo", "czce": "main-zhengshangsuo",
            "ine": "main-shangnengyuan", "gfex": "main-guangqisuo",
            "cffex": "main-zhongjinsuo",
        }
        index_securities = [
            ("850001", "64"), ("850103", "64"), ("850300", "64"),
            ("850104", "64"), ("850101", "64"), ("850102", "64"),
            ("850100", "64"), ("850200", "64"), ("USDIND", "97"),
            ("sc9999", "65"),
        ]
        errors: dict[str, str] = {}

        async def capture(name: str, request) -> dict | None:
            try:
                result = await request
                if not isinstance(result, dict):
                    raise ValueError("native response is not an object")
                return result
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"
                return None

        try:
            module_requests: dict[str, object] = {
                "hot": self._request_native_sector_bridge(
                    "/native/hurricane",
                    {
                        "frame_id": 2312, "start": 0, "count": 100,
                        "hurricane_type": "TAG",
                        "hurricane_ids": ["ths_hq_tab_fu_domestic_hot_continuous_contract"],
                        "hurricane_indicator_ids": [],
                        "mobile_indicator_ids": ["55", "10", "34821", "34818"],
                        "sort_indicator_id": "34818", "order": "DESCENDING",
                        "http_source_id": "FuturesSynthesis", "timeout_ms": 8000,
                    },
                    lane="hurricane",
                ),
                "indices": self._request_native_stock_quotes(index_securities),
            }
            for flow_name, order in {"inflow": "0", "outflow": "1"}.items():
                module_requests[f"flow:{flow_name}"] = self._request_native_unified(
                    online_id=f"futures_flow_{order}", protocol_id=4066,
                    page_id=2405,
                    request_dic=("rowcount=100\r\nstartrow=0\r\nmarketid=67\r\n"
                                 f"sortorder={order}\r\nsortid=68\r\nnewrealtime=1"),
                    timeout_seconds=6,
                )
            for group, market_key in ranking_groups.items():
                module_requests[f"ranking:{group}"] = self._request_native_unified(
                    online_id=f"futures_rank_{group}", protocol_id=4021,
                    page_id=2274,
                    request_dic=(f"marketkey={market_key}\r\nstartrow=0\r\nrowcount=500\r\n"
                                 "sortid=34818\r\nsortorder=0"),
                    timeout_seconds=6,
                )
            module_requests["market_state"] = self._request_native_unified(
                    online_id="futures_market_state", protocol_id=4051,
                    page_id=2405, request_dic="marketkey=cn_futures",
                    timeout_seconds=6,
                )
            module_requests["market_net_flow"] = self._request_native_unified(
                    online_id="futures_market_flow", protocol_id=4067,
                    page_id=2405,
                    request_dic="marketcode=64\r\nstockcode=850001",
                    timeout_seconds=6,
                )
            module_names = list(module_requests)
            module_values = await asyncio.gather(*(
                capture(name, request)
                for name, request in module_requests.items()
            ))
            captured = dict(zip(module_names, module_values, strict=True))
            hot = captured.get("hot")
            index_quotes = captured.get("indices")
            market_state = captured.get("market_state")
            market_flow = captured.get("market_net_flow")
            flows = {
                name.removeprefix("flow:"): response.get("data") or {}
                for name, response in captured.items()
                if name.startswith("flow:") and response is not None
            }
            rankings = {
                name.removeprefix("ranking:"): response.get("data") or {}
                for name, response in captured.items()
                if name.startswith("ranking:") and response is not None
            }

            hot_data = (hot or {}).get("data") or {}
            hot_rows = hot_data.get("rows") if isinstance(hot_data, dict) else None
            if isinstance(hot_rows, list):
                hot_data = {"count": len(hot_rows), "rows": hot_rows}

            data: dict[str, object] = {
                "page_config": {
                    "app_id": "FuturesSynthesis",
                    "ranking_groups": ranking_groups,
                    "ranking_fields": ["55", "10", "34818", "34821", "13", "65", "34355", "66", "72"],
                    "index_codes": [code for code, _ in index_securities],
                },
            }
            if market_state is not None or market_flow is not None:
                data["market_state"] = market_state
                data["market_net_flow"] = market_flow
            if hot is not None:
                data["hot_continuous_contracts"] = hot_data
            if index_quotes is not None:
                data["futures_indices"] = index_quotes
            if flows:
                data["commodity_fund_flow"] = flows
            if rankings:
                data["main_contract_rankings"] = rankings

            completed_modules = {
                "hot": hot is not None,
                "indices": len(index_quotes or {}) == len(index_securities),
                "fund_flow": set(flows) == {"inflow", "outflow"},
                "rankings": len(rankings) == len(ranking_groups),
                "market": market_state is not None and market_flow is not None,
            }
            if not any(completed_modules.values()):
                raise RuntimeError(f"all futures modules failed: {errors}")
            return market_result(
                provider="ths_native", market="cn",
                data=data,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "FuturesSynthesis",
                    "complete": all(completed_modules.values()),
                    "completed_modules": completed_modules,
                    "errors": errors,
                    "runs_outside_a_share_hours": True,
                },
            )
        except Exception as exc:
            return market_error(provider="ths_native", market="cn", error=exc)

    async def get_native_futures_fragment(
        self,
        kind: str,
        group: str | None = None,
    ) -> dict:
        """读取一个期货页面原子模块，避免单点失败拖垮整页快照。"""

        ranking_groups = {
            "all": "all-main", "night": "main-yepan",
            "energy_chemical": "main-nengyuanhuagong",
            "nonferrous": "main-yousejinshu", "precious": "main-guijinshu",
            "ferrous": "main-heisejinshu", "agriculture": "main-nongchanpin",
            "financial": "main-jingrongbankuai", "shfe": "main-shangqisuo",
            "dce": "main-dashangsuo", "czce": "main-zhengshangsuo",
            "ine": "main-shangnengyuan", "gfex": "main-guangqisuo",
            "cffex": "main-zhongjinsuo",
        }
        index_securities = [
            ("850001", "64"), ("850103", "64"), ("850300", "64"),
            ("850104", "64"), ("850101", "64"), ("850102", "64"),
            ("850100", "64"), ("850200", "64"), ("USDIND", "97"),
            ("sc9999", "65"),
        ]

        def require_unified_signature(
            response: dict,
            *,
            protocol_id: int,
            page_id: int,
            online_id: str,
        ) -> None:
            """Reject a successful callback that belongs to another request."""
            head = response.get("head") or {}
            expected = {
                "protocolId": protocol_id,
                "pageId": page_id,
                "onlineId": online_id,
            }
            mismatches = {
                key: {"expected": value, "actual": head.get(key)}
                for key, value in expected.items()
                if head.get(key) is not None and str(head.get(key)) != str(value)
            }
            if mismatches:
                raise RuntimeError(
                    f"futures native callback signature mismatch: {mismatches}"
                )
        try:
            if kind == "hot":
                response = await self._request_native_sector_bridge(
                    "/native/hurricane",
                    {
                        "frame_id": 2312, "start": 0, "count": 100,
                        "hurricane_type": "TAG",
                        "hurricane_ids": ["ths_hq_tab_fu_domestic_hot_continuous_contract"],
                        "hurricane_indicator_ids": [],
                        "mobile_indicator_ids": ["55", "10", "34821", "34818"],
                        "sort_indicator_id": "34818", "order": "DESCENDING",
                        "http_source_id": "FuturesSynthesis", "timeout_ms": 8000,
                    },
                    lane="hurricane",
                )
                data = response.get("data") or {}
                rows = data.get("rows") if isinstance(data, dict) else None
                if isinstance(rows, list):
                    data = {"count": len(rows), "rows": rows}
            elif kind == "indices":
                data = await self._request_native_stock_quotes(index_securities)
                missing = [code for code, _ in index_securities if code not in data]
                if missing:
                    raise RuntimeError(
                        f"futures indices callback incomplete: missing={missing}"
                    )
            elif kind in {"fund_inflow", "fund_outflow"}:
                order = "0" if kind == "fund_inflow" else "1"
                online_id = f"futures_flow_{order}"
                response = await self._request_native_unified(
                    online_id=online_id, protocol_id=4066,
                    page_id=2405,
                    request_dic=("rowcount=100\r\nstartrow=0\r\nmarketid=67\r\n"
                                 f"sortorder={order}\r\nsortid=68\r\nnewrealtime=1"),
                    timeout_seconds=6,
                )
                require_unified_signature(
                    response, protocol_id=4066, page_id=2405,
                    online_id=online_id,
                )
                data = response.get("data") or {}
            elif kind == "market_state":
                online_id = "futures_market_state"
                response = await self._request_native_unified(
                    online_id=online_id, protocol_id=4051,
                    page_id=2405, request_dic="marketkey=cn_futures",
                    timeout_seconds=6,
                )
                require_unified_signature(
                    response, protocol_id=4051, page_id=2405,
                    online_id=online_id,
                )
                data = response.get("data") or {}
            elif kind == "market_net_flow":
                online_id = "futures_market_flow"
                response = await self._request_native_unified(
                    online_id=online_id, protocol_id=4067,
                    page_id=2405,
                    request_dic="marketcode=64\r\nstockcode=850001",
                    timeout_seconds=6,
                )
                require_unified_signature(
                    response, protocol_id=4067, page_id=2405,
                    online_id=online_id,
                )
                data = response.get("data") or {}
            elif kind == "ranking" and group in ranking_groups:
                online_id = f"futures_rank_{group}"
                response = await self._request_native_unified(
                    online_id=online_id, protocol_id=4021,
                    page_id=2274,
                    request_dic=(f"marketkey={ranking_groups[group]}\r\n"
                                 "startrow=0\r\nrowcount=500\r\n"
                                 "sortid=34818\r\nsortorder=0"),
                    timeout_seconds=6,
                )
                require_unified_signature(
                    response, protocol_id=4021, page_id=2274,
                    online_id=online_id,
                )
                data = response.get("data") or {}
            else:
                raise ValueError(f"unsupported futures fragment: {kind}/{group}")
            return market_result(
                provider="ths_native",
                market="cn",
                data={"kind": kind, "group": group, "native_table": data},
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "FuturesSynthesis",
                    "complete": True,
                    "atomic_fragment": True,
                    "runs_outside_a_share_hours": True,
                },
            )
        except Exception as exc:
            return market_error(
                provider="ths_native",
                market="cn",
                error=exc,
                provider_metadata={"kind": kind, "group": group},
            )

    async def get_native_gold_zone_snapshot(self) -> dict:
        """采集同花顺黄金专区全部业务数据模块。"""
        base = "https://fund.10jqka.com.cn"
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        begin_ms = now_ms - 370 * 86400 * 1000
        etf_codes = ["518600", "518680", "159934", "159834", "159831", "518850"]
        fund_codes = ["002611", "000217", "004253", "008702", "021740", "008143"]
        rank_fields = ["week", "month", "tmonth", "fundScale", "chgpct", "hyear", "year", "nowyear", "unitNav", "etfT0"]
        rank_url = "https://dq.10jqka.com.cn/fuyao/fund_rank/fund_rank/v1/fund_rank"
        def rank_request_url(codes: list[str]) -> str:
            query = json.dumps(
                {"codeList": codes, "fieldList": rank_fields, "limit": len(codes)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return f"{rank_url}?{httpx.QueryParams({'query': query})}"

        try:
            requests = {
                "future_news": self._request_app_proxy("https://dq.10jqka.com.cn/fuyao/fund_fe_tools/gold/v1/future_news"),
                "gold_ai_summary_list": self._request_app_proxy("https://ftapi.10jqka.com.cn/futgwapi/api/news/time_news/v1/ai_summary_list?code=au&market_id=65"),
                "gold_ai_summary_count": self._request_app_proxy("https://ftapi.10jqka.com.cn/futgwapi/api/news/time_news/v1/ai_summary_count?code=au&market_id=65"),
                "gold_ai_system_time": self._request_app_proxy("https://ftapi.10jqka.com.cn/futgwapi/api/config/time/v1/get_system_time"),
                "history_spread": self._request_app_proxy("https://dq.10jqka.com.cn/fuyao/fund_fe_tools/gold/v1/his_gold_spread_detail"),
                "market_banner": self._request_app_proxy(f"{base}/marketing/operation/config/module/v1/key/hangqingtoufu"),
                "gold_cards": self._request_app_proxy(f"{base}/marketing/operation/config/module/v1/key/GoldZoneCard"),
                "grid_config": self._request_app_proxy(f"{base}/marketing/operation/config/module/v1/key/goldgongge"),
                "investment_links": self._request_app_proxy(f"{base}/marketing/operation/config/module/v1/key/touzizhuanquwenzilian"),
                "explanation": self._request_app_proxy(f"{base}/marketing/operation/config/module/v1/key/sjhqhjzqjsm"),
                "stock_recommendations": self._request_app_proxy("https://dq.10jqka.com.cn/fuyao/fund_fe_tools/gold/v1/reclist?type=stock"),
                "etf_recommendations": self._request_app_proxy("https://dq.10jqka.com.cn/fuyao/fund_fe_tools/gold/v1/reclist?type=etf"),
                "fund_recommendations": self._request_app_proxy("https://dq.10jqka.com.cn/fuyao/fund_fe_tools/gold/v1/reclist?type=fund"),
                "futures_recommendations": self._request_app_proxy("https://dq.10jqka.com.cn/fuyao/fund_fe_tools/gold/v1/reclist?type=future"),
                "jewelry_prices": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/offline/price?type=jewelry"),
                "gold_bar_prices": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/offline/price?type=goldBar"),
                "bank_gold_prices": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/offline/price?type=bank"),
                "recycle_gold_prices": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/offline/price?type=recycle"),
                "domestic_capital": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/capital", method="POST", body={"marketType": "cn", "before": 20, "tab": "au", "intervals": [3, 5, 10, 20]}),
                "international_capital": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/capital", method="POST", body={"marketType": "us", "before": 20, "tab": "au", "intervals": [3, 5, 10, 20]}),
                "domestic_gold_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["AU9999"], "market": "81"}], "trade_class": "post_market", "time_period": "day_1", "trade_date": -1, "begin_time": begin_ms, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "domestic_silver_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["AGTD"], "market": "81"}], "trade_class": "post_market", "time_period": "day_1", "trade_date": -1, "begin_time": begin_ms, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "international_gold_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["AUUSDO"], "market": "218"}], "trade_class": "post_market", "time_period": "day_1", "trade_date": -1, "begin_time": begin_ms, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "brent_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["BRN0W"], "market": "219"}], "trade_class": "post_market", "time_period": "day_1", "trade_date": -1, "begin_time": begin_ms, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "gold_spot_intraday_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["AU9999"], "market": "81"}, {"codes": ["XAUUSD", "USDCNH"], "market": "97"}], "trade_class": "intraday", "time_period": "min_1", "trade_date": -1, "begin_time": now_ms - 2 * 86400 * 1000, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "gold_spot_overseas_intraday_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["XAUUSD"], "market": "97"}], "trade_class": "intraday", "time_period": "min_1", "trade_date": -1, "begin_time": now_ms - 2 * 86400 * 1000, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "gold_silver_spot_intraday_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["AUUSDO", "AGUSDO"], "market": "218"}], "trade_class": "intraday", "time_period": "min_1", "trade_date": -1, "begin_time": now_ms - 2 * 86400 * 1000, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "gold_ratio_gold_intraday_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["AUUSDO"], "market": "218"}], "trade_class": "intraday", "time_period": "min_1", "trade_date": -1, "begin_time": now_ms - 2 * 86400 * 1000, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "gold_ratio_silver_intraday_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["AGUSDO"], "market": "218"}], "trade_class": "intraday", "time_period": "min_1", "trade_date": -1, "begin_time": now_ms - 2 * 86400 * 1000, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "gold_fx_intraday_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["USDCNH"], "market": "97"}], "trade_class": "intraday", "time_period": "min_1", "trade_date": -1, "begin_time": now_ms - 2 * 86400 * 1000, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "gold_futures_intraday_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["au9999"], "market": "65"}, {"codes": ["GC0W"], "market": "UCXF"}, {"codes": ["USDCNH"], "market": "97"}], "trade_class": "intraday", "time_period": "min_1", "trade_date": -1, "begin_time": now_ms - 2 * 86400 * 1000, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "gold_silver_futures_intraday_kline": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline", method="POST", body={"code_list": [{"codes": ["GC0W", "SI0W"], "market": "UCXF"}], "trade_class": "intraday", "time_period": "min_1", "trade_date": -1, "begin_time": now_ms - 2 * 86400 * 1000, "end_time": now_ms, "adjust_type": "forward", "gpid": 0}),
                "gold_etf_rank": self._request_app_proxy(rank_request_url(etf_codes)),
                "gold_fund_rank": self._request_app_proxy(rank_request_url(fund_codes)),
                "gold_etf_flow": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/multi_last_snapshot", method="POST", body={"code_list": [{"codes": ["518600", "518680", "518850"], "market": "20"}, {"codes": ["159831", "159834", "159934"], "market": "36"}], "trade_class": "intraday", "data_fields": ["10", "199112", "264648", "134238"], "lang": "zh_cn", "gpid": 0}),
                "gold_market_quotes": self._request_app_proxy(
                    "https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/multi_last_snapshot",
                    method="POST",
                    body={
                        "code_list": [
                            {"market": "81", "codes": ["AU9999", "AGTD"]},
                            {"market": "218", "codes": ["AUUSDO", "AGUSDO"]},
                            {"market": "65", "codes": ["au9999", "ag9999"]},
                            {"market": "UCXF", "codes": ["GC0W", "SI0W"]},
                            {"market": "UAGM", "codes": ["GF001"]},
                            {"market": "48", "codes": ["885530"]},
                            {"market": "97", "codes": ["USDIND", "USDCNH", "XAUUSD"]},
                            {"market": "120", "codes": ["931238"]},
                            {"market": "219", "codes": ["BRN0W"]},
                            {"market": "185", "codes": ["IBIT"]},
                        ],
                        "trade_class": "intraday",
                        "data_fields": ["10", "199112", "264648"],
                        "lang": "zh_cn", "gpid": 0,
                    },
                ),
                "jewelry_quotes": self._request_app_proxy("https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/multi_last_snapshot", method="POST", body={"code_list": [{"codes": ["ZS001", "MS001", "GF001"], "market": "UAGM"}], "trade_class": "intraday", "data_fields": ["10", "199112", "264648"], "lang": "zh_cn", "gpid": 0}),
                "gold_recommend_head": self._request_app_proxy(f"{base}/quotation/fund/recommend/v1/entity/head/user?userId=0&market=81&code=AU9999"),
                "gold_reserve_tabs": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/reserve/rank?type=up&timeType=year&limit=5&global=1"),
                "gold_reserve_year_up": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/reserve/rank?type=up&timeType=year&limit=10&global=0"),
                "gold_reserve_month_up": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/reserve/rank?type=up&timeType=month&limit=10&global=0"),
                "gold_reserve_month_down": self._request_app_proxy(f"{base}/quotation/quotation_tab/v1/gold_zone/reserve/rank?type=down&timeType=month&limit=10&global=0"),
                "seasonality_statistics": self._request_app_proxy(
                    f"{base}/quotation/data/query/v1/table",
                    method="POST",
                    body={
                        "indexes": [
                            {"index_id": "monthUpProb"},
                            {"index_id": "avgMonthRate"},
                        ],
                        "code_selectors": {"include": [{
                            "type": "stock_code",
                            "values": ["81:AU9999", "65:au9999"],
                        }]},
                    },
                ),
                "seasonality_monthly_change": self._request_app_proxy(
                    f"{base}/quotation/data/query/v1/line",
                    method="POST",
                    body={
                        "indexes": [
                            {"codes": ["81:AU9999"], "index_info": [{"index_id": "month_rate"}]},
                            {"codes": ["65:au9999"], "index_info": [{"index_id": "month_rate"}]},
                        ],
                        "time_range": {
                            "time_type": "DAY_1",
                            "end": int(datetime.now(timezone.utc).timestamp()),
                            "offset": -120,
                        },
                    },
                ),
                "gold_stock_correlation": self._request_app_proxy(
                    f"{base}/quotation/data/query/v1/line",
                    method="POST",
                    body={
                        "indexes": [{
                            # goldZone 常量 AG=883957（SSH 黄金股票指数）。
                            "codes": ["48:883957"],
                            "index_info": [
                                {"index_id": "gold_rise_kline_rate"},
                                {"index_id": "gold_elastic_k"},
                            ],
                        }],
                        "time_range": {
                            "time_type": "DAY_1",
                            "end": int(datetime.now(timezone.utc).timestamp()),
                            "offset": -370,
                        },
                    },
                ),
                "gold_silver_correlation": self._request_app_proxy(
                    f"{base}/quotation/data/query/v1/line",
                    method="POST",
                    body={
                        "indexes": [{
                            "codes": ["48:883957"],
                            "index_info": [
                                {"index_id": "goldsilver_rise_kline_rate"},
                                {"index_id": "goldsilver_elastic_k"},
                            ],
                        }],
                        "time_range": {
                            "time_type": "DAY_1",
                            "end": int(datetime.now(timezone.utc).timestamp()),
                            "offset": -370,
                        },
                    },
                ),
                "silver_spot_kline": self._request_app_proxy(
                    "https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline",
                    method="POST",
                    body={"code_list": [{"codes": ["AGUSDO"], "market": "218"}], "trade_class": "post_market", "time_period": "day_1", "trade_date": -1, "begin_time": begin_ms, "end_time": now_ms, "adjust_type": "forward", "gpid": 0},
                ),
                "gold_future_kline": self._request_app_proxy(
                    "https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline",
                    method="POST",
                    body={"code_list": [{"codes": ["GC0W"], "market": "UCXF"}], "trade_class": "post_market", "time_period": "day_1", "trade_date": -1, "begin_time": begin_ms, "end_time": now_ms, "adjust_type": "forward", "gpid": 0},
                ),
                "silver_future_kline": self._request_app_proxy(
                    "https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline",
                    method="POST",
                    body={"code_list": [{"codes": ["SI0W"], "market": "UCXF"}], "trade_class": "post_market", "time_period": "day_1", "trade_date": -1, "begin_time": begin_ms, "end_time": now_ms, "adjust_type": "forward", "gpid": 0},
                ),
                "ssh_gold_stock_kline": self._request_app_proxy(
                    "https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/single_kline",
                    method="POST",
                    body={"code_list": [{"codes": ["931238"], "market": "120"}], "trade_class": "post_market", "time_period": "day_1", "trade_date": -1, "begin_time": begin_ms, "end_time": now_ms, "adjust_type": "forward", "gpid": 0},
                ),
                "gold_silver_ratio_threshold": self._request_app_proxy(f"{base}/marketing/operation/config/module/v1/key/Goldsilverratiothreshold"),
                "gold_silver_ratio_products": self._request_app_proxy(f"{base}/marketing/operation/config/module/v1/key/goldsilverratioprod"),
            }
            keys = list(requests)
            values = await asyncio.gather(
                *requests.values(),
                return_exceptions=True,
            )
            module_errors: dict[str, str] = {}
            modules: dict[str, dict] = {}
            for key, value in zip(keys, values, strict=True):
                if isinstance(value, BaseException):
                    module_errors[key] = f"{type(value).__name__}: {value}"
                    modules[key] = {}
                elif isinstance(value, dict):
                    modules[key] = value
                else:
                    module_errors[key] = (
                        f"invalid response type: {type(value).__name__}"
                    )
                    modules[key] = {}
            reserve_index_ids = []
            for rank_key in (
                "gold_reserve_tabs", "gold_reserve_year_up", "gold_reserve_month_up",
                "gold_reserve_month_down",
            ):
                for item in modules.get(rank_key, {}).get("data") or []:
                    index_id = item.get("index_id") if isinstance(item, dict) else None
                    if index_id and index_id not in reserve_index_ids:
                        reserve_index_ids.append(str(index_id))
            reserve_curves = {}
            if reserve_index_ids:
                curve_requests = []
                curve_keys = []
                for index_id in reserve_index_ids:
                    for curve_type in ("all", "up"):
                        curve_keys.append(f"{index_id}:{curve_type}")
                        curve_requests.append(self._request_app_proxy(
                            f"{base}/quotation/quotation_tab/v1/gold_zone/reserve/line",
                            method="POST",
                            body={
                                "index_id": index_id,
                                "type": curve_type,
                                "before": 24,
                            },
                        ))
                curve_values = await asyncio.gather(
                    *curve_requests,
                    return_exceptions=True,
                )
                for key, value in zip(curve_keys, curve_values, strict=True):
                    if isinstance(value, BaseException):
                        module_errors[f"gold_reserve_curve:{key}"] = (
                            f"{type(value).__name__}: {value}"
                        )
                    elif isinstance(value, dict):
                        reserve_curves[key] = value
            modules["gold_reserve_curves"] = reserve_curves
            required_gold_modules = (
                "gold_market_quotes", "history_spread", "future_news",
                "domestic_capital", "international_capital",
                "gold_reserve_tabs", "gold_reserve_year_up", "gold_reserve_month_up",
                "gold_reserve_month_down", "seasonality_statistics",
                "seasonality_monthly_change", "gold_stock_correlation",
                "gold_silver_correlation", "gold_silver_ratio_threshold",
                "gold_silver_ratio_products",
            )
            complete = bool(reserve_curves) and all(
                isinstance(modules.get(key), dict)
                and modules[key].get("status_code") in (None, 0)
                for key in required_gold_modules
            )
            fetched_at = datetime.now(timezone.utc)
            return market_result(
                provider="ths_app_http", market="cn",
                data={
                    **modules,
                    "page_config": {
                        "page": "goldZone", "etf_codes": etf_codes,
                        "fund_codes": fund_codes, "rank_fields": rank_fields,
                    },
                },
                source_time=fetched_at.isoformat(),
                trade_date=fetched_at.astimezone(ZoneInfo("Asia/Shanghai")).date(),
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "goldZone",
                    "complete": complete,
                    "required_module_count": len(required_gold_modules),
                    "reserve_curve_count": len(reserve_curves),
                    "failed_modules": module_errors,
                    "runs_outside_a_share_hours": True,
                    "ui_spec": "gold-page-v1",
                },
            )
        except Exception as exc:
            return market_error(provider="ths_app_http", market="cn", error=exc)

    def _us_market_result(
        self,
        *,
        module: str,
        data: dict,
        complete: bool,
        failed_modules: list[str] | None = None,
    ) -> dict:
        fetched_at = datetime.now(timezone.utc)
        return market_result(
            provider="ths_app",
            market="us",
            data=data,
            source_time=fetched_at.isoformat(),
            trade_date=fetched_at.astimezone(ZoneInfo("America/New_York")).date(),
            timezone_name="America/New_York",
            provider_metadata={
                "source_component": f"US market home/{module}",
                "module": module,
                "complete": complete,
                "failed_modules": failed_modules or [],
                "runs_outside_a_share_hours": True,
                "includes_pre_and_after_market": True,
            },
        )

    async def get_native_us_overview_snapshot(self) -> dict:
        """高频采集美股涨跌统计；指数由 THSSTREAM 长订阅维护。"""
        base = (
            "https://eq.10jqka.com.cn/open/api/hk_us_common_data/"
            "us_stocks/home_page/quote_change"
        )
        try:
            day, month = await asyncio.gather(
                self._request_app_proxy(f"{base}/that_day/list"),
                self._request_app_proxy(f"{base}/last_month/list.json"),
            )
            return self._us_market_result(
                module="overview",
                data={
                    "breadth_today": day.get("data") or {},
                    "breadth_month": month.get("data") or {},
                    "indices_source": "ths_us_market_module/indices_stream",
                },
                complete=True,
            )
        except Exception as exc:
            return market_error(provider="ths_app", market="us", error=exc)

    async def get_native_us_sector_snapshot(self) -> dict:
        """采集美股行业、概念的当前和中期排行。"""
        try:
            # The HTTP endpoint exposes period-specific leaders, but its
            # five-day and one-month rank fields are currently identical and
            # therefore cannot drive the App tabs.  Protocol 4115 contains the
            # exact sector returns used by the App.  Read every sector once per
            # classification, sort locally by the native period field, and use
            # HTTP only to enrich the matching period leader.
            native_tables: dict[str, dict] = {}
            failed: list[str] = []
            for tag, market_id in (("industry", 2029), ("concept", 2030)):
                try:
                    native_tables[tag] = await self._request_native_unified(
                        lane="ranking",
                        online_id=f"us-{tag}-periods",
                        protocol_id=4115,
                        page_id=2371,
                        request_dic=(
                            "startrow=0\r\nrowcount=500\r\nsortid=34313"
                            f"\r\nsortorder=0\r\nmarketid={market_id}"
                        ),
                        timeout_seconds=12,
                    )
                except Exception:
                    failed.append(f"{tag}_native")

            history_requests = {
                tag: self._request_app_proxy(
                    "https://eq.10jqka.com.cn/open/api/"
                    "gmg_homepage_sector/us/sector/v1/list?"
                    + urlencode({
                        "tag": tag,
                        "page_start": 0,
                        "page_size": 500,
                        "sort_field": "three_month_sector_rank",
                        "sort_type": "desc",
                        "api_type": 0,
                    })
                )
                for tag in ("industry", "concept")
            }
            history_values = await asyncio.gather(
                *history_requests.values(), return_exceptions=True
            )
            histories: dict[str, dict] = {}
            for tag, value in zip(
                history_requests, history_values, strict=True
            ):
                if isinstance(value, Exception):
                    failed.append(f"{tag}_leaders")
                else:
                    histories[tag] = value

            period_fields = {
                "five_day": "34376",
                "one_month": "34377",
                "three_month": "34850",
            }
            data: dict[str, dict] = {}
            for tag in ("industry", "concept"):
                native = native_tables.get(tag)
                if native is None:
                    continue
                history_rows = (
                    ((histories.get(tag, {}).get("data") or {}).get("sector_list"))
                    or []
                )
                leaders_by_code = {
                    str(row.get("sector_code") or ""): row
                    for row in history_rows
                    if isinstance(row, dict) and row.get("sector_code")
                }
                native_rows = self._native_table_rows(native)
                for period, indicator_id in period_fields.items():
                    ranked = sorted(
                        (
                            row for row in native_rows
                            if _native_number(
                                (row.get("indicators") or {}).get(indicator_id)
                            ) is not None
                        ),
                        key=lambda row: _native_number(
                            (row.get("indicators") or {}).get(indicator_id)
                        ) or 0.0,
                        reverse=True,
                    )
                    sector_list = []
                    for rank, row in enumerate(ranked, start=1):
                        indicators = row.get("indicators") or {}
                        code = str(row.get("provider_sector_code") or "")
                        leader = leaders_by_code.get(code) or {}
                        sector_list.append({
                            **leader,
                            "sector_code": code,
                            "sector_name": row.get("sector_name"),
                            f"{period}_sector_rank": str(rank),
                            f"{period}_sector_uplift": indicators.get(indicator_id),
                        })
                    data[f"{tag}_{period}"] = {
                        "status_code": 0,
                        "data": {"sector_list": sector_list},
                    }
            return self._us_market_result(
                module="sectors", data={
                    "sectors": data,
                    "current_sources": {
                        "industry": "ths_us_market_module/industry_current_stream",
                        "concept": "ths_us_market_module/concept_current_stream",
                    },
                },
                complete=not failed, failed_modules=failed,
            )
        except Exception as exc:
            return market_error(provider="ths_app", market="us", error=exc)

    async def _enrich_us_sector_period_changes(self, data: dict[str, dict]) -> None:
        """Add the sector's own period return omitted by the ranking endpoint."""
        candidates: dict[str, list[dict]] = {}
        for key, payload in data.items():
            period = key.removeprefix("industry_").removeprefix("concept_")
            rank_key = f"{period}_sector_rank"
            rows = ((payload.get("data") or {}).get("sector_list") or [])
            ranked = sorted(
                (row for row in rows if isinstance(row, dict)),
                key=lambda row: int(row.get(rank_key) or 999999),
            )[:3]
            for row in ranked:
                code = str(row.get("sector_code") or "")
                if code:
                    candidates.setdefault(code, []).append(row)
        if not candidates:
            return

        async def load(code: str) -> tuple[str, list[str] | Exception]:
            try:
                response = await self._client.get(
                    f"https://d.10jqka.com.cn/v6/line/89_{code}/01/last.js",
                    headers={"Referer": "https://stockpage.10jqka.com.cn/"},
                    timeout=10,
                )
                response.raise_for_status()
                text = response.text
                body = json.loads(text[text.index("(") + 1:text.rindex(")")])
                return code, [item for item in str(body.get("data") or "").split(";") if item]
            except Exception as exc:
                return code, exc

        results = await asyncio.gather(*(load(code) for code in candidates))
        offsets = {"five_day": 5, "one_month": 20, "three_month": 60}
        for code, history in results:
            if isinstance(history, Exception) or not history:
                continue
            try:
                current = float(history[-1].split(",")[4])
            except (IndexError, ValueError):
                continue
            for row in candidates[code]:
                for period, offset in offsets.items():
                    if len(history) <= offset:
                        continue
                    try:
                        base = float(history[-1 - offset].split(",")[4])
                    except (IndexError, ValueError):
                        continue
                    if base:
                        row[f"{period}_sector_uplift"] = (
                            f"{(current / base - 1) * 100:.2f}%"
                        )

    async def get_native_us_stock_rankings_snapshot(
        self,
        tab_ids: set[str] | None = None,
    ) -> dict:
        """采集美股七类股票排行；单个 Tab 失败不覆盖其他 Tab。"""
        tabs = (
            ("all", "全部", 21208, "marketid=60", 34818),
            ("us24hremen", "24H最热", 4026, "marketkey=USA", 34822),
            ("zhonggaigu", "中概股", 21208, "marketid=35", 36065),
            ("djg", "低价股", 21208, "marketid=80", 36065),
            ("redianmeigu", "热点美股", 21208, "marketid=33", 36065),
            ("ssxg", "上市新股", 21208, "marketid=81", 36065),
            ("redianetf", "热点ETF", 21208, "marketid=36", 36065),
        )
        try:
            labels_task = asyncio.create_task(self._request_app_proxy(
                "https://eq.10jqka.com.cn/open/api/"
                "gmg_data_detail/stock_rank/us/v1/labels.json"
            ))
            rankings: dict[str, dict] = {}
            failed: list[str] = []
            selected_tabs = tuple(
                item for item in tabs
                if tab_ids is None or item[0] in tab_ids
            )
            for tab_id, _name, protocol_id, selector, sort_id in selected_tabs:
                try:
                    rankings[tab_id] = await self._request_native_unified(
                        lane="ranking",
                        online_id=f"us-stock-ranking-{tab_id}",
                        protocol_id=protocol_id,
                        page_id=2371,
                        # Six seconds was too close to the callback tail under
                        # production contention and caused an unnecessary
                        # twelve-second repair pass for otherwise valid data.
                        timeout_seconds=8,
                        request_dic=(
                            "startrow=0\r\nrowcount=500\r\n"
                            f"{selector}\r\nsortorder=0\r\nsortid={sort_id}"
                        ),
                    )
                except Exception as value:
                    failed.append(tab_id)
                    logger.warning(
                        "THS US stock ranking initial request failed: tab=%s error=%r",
                        tab_id,
                        value,
                    )
            if failed:
                retry_definitions = {
                    tab_id: (protocol_id, selector, sort_id)
                    for tab_id, _name, protocol_id, selector, sort_id in selected_tabs
                    if tab_id in failed
                }
                for tab_id, (protocol_id, selector, sort_id) in retry_definitions.items():
                    try:
                        rankings[tab_id] = await self._request_native_unified(
                            lane="ranking",
                            online_id=f"us-stock-ranking-{tab_id}-repair",
                            protocol_id=protocol_id,
                            page_id=2371,
                            # Hummer's own timeout is 20 seconds.  Keep the
                            # eight-second fast path above, but allow a delayed
                            # upstream callback enough time during repair.
                            timeout_seconds=12,
                            request_dic=(
                                "startrow=0\r\nrowcount=500\r\n"
                                f"{selector}\r\nsortorder=0\r\nsortid={sort_id}"
                            ),
                        )
                        failed.remove(tab_id)
                    except Exception as exc:
                        logger.warning(
                            "THS US stock ranking repair failed: tab=%s error=%r",
                            tab_id,
                            exc,
                        )
            labels = await labels_task
            return self._us_market_result(
                module="stock_rankings",
                data={
                    "stock_ranking_labels": labels,
                    "tabs": [{"id": tab_id, "name": name} for tab_id, name, *_ in tabs],
                    "stock_rankings": rankings,
                },
                complete=not failed,
                failed_modules=failed,
            )
        except Exception as exc:
            return market_error(provider="ths_app", market="us", error=exc)

    async def get_native_us_etf_sectors_snapshot(
        self,
        block_ids: set[str] | None = None,
    ) -> dict:
        """动态读取美股 ETF 分类，并只修复指定分类的行情。"""
        try:
            now = asyncio.get_running_loop().time()
            config = self._us_etf_sector_config_cache
            if not config or now >= self._us_etf_sector_config_deadline:
                config = await self._request_native_unified(
                    lane="ranking", online_id="us-etf-sector-config",
                    protocol_id=1361, page_id=2371, request_dic="",
                    timeout_seconds=8,
                )
                self._us_etf_sector_config_cache = config
                self._us_etf_sector_config_deadline = now + 1800
            categories = ((config.get("data") or {}).get("items") or [])
            requested = (
                {str(block_id) for block_id in block_ids}
                if block_ids is not None
                else None
            )
            request_definitions = {
                str(item["BlockID"]): str(item["BlockID"])
                for item in categories
                if isinstance(item, dict) and item.get("BlockID")
                and (requested is None or str(item["BlockID"]) in requested)
            }
            details: dict[str, dict] = {}
            failed = sorted((requested or set()).difference(request_definitions))
            # Keep the global native lane fair: one ETF category is submitted
            # at a time so higher-frequency modules can interleave.
            for key, block_id in request_definitions.items():
                try:
                    details[key] = await self._request_native_unified(
                        lane="ranking",
                        online_id=f"us-etf-sector-{block_id}",
                        protocol_id=1360,
                        page_id=2371,
                        timeout_seconds=8,
                        request_dic=(
                            f"stockcode={block_id}\r\nsortid=199112"
                            "\r\nstartrow=0\r\nrowcount=500\r\nsortorder=0"
                            "\r\ncolumnorder=55|4|34338|10|34818|19"
                        ),
                    )
                except Exception as exc:
                    failed.append(key)
                    logger.warning(
                        "THS US ETF sector initial request failed: block_id=%s error=%r",
                        key,
                        exc,
                    )
            if failed:
                for block_id in tuple(failed):
                    try:
                        details[block_id] = await self._request_native_unified(
                            lane="ranking",
                            online_id=f"us-etf-sector-{block_id}-repair",
                            protocol_id=1360,
                            page_id=2371,
                            timeout_seconds=12,
                            request_dic=(
                                f"stockcode={block_id}\r\nsortid=199112"
                                "\r\nstartrow=0\r\nrowcount=500\r\nsortorder=0"
                                "\r\ncolumnorder=55|4|34338|10|34818|19"
                            ),
                        )
                        failed.remove(block_id)
                    except Exception as exc:
                        logger.warning(
                            "THS US ETF sector repair failed: block_id=%s error=%r",
                            block_id,
                            exc,
                        )
            member_keys: set[tuple[str, str]] = set()
            for detail in details.values():
                columns = (detail.get("data") or {}).get("dataDict") or {}
                codes = columns.get("4") or []
                markets = columns.get("34338") or columns.get("36103") or []
                for index, code in enumerate(codes):
                    if code and index < len(markets) and markets[index] not in (None, ""):
                        member_keys.add((str(markets[index]), str(code)))
            quote_requests = []
            members = sorted(member_keys)
            for start in range(0, len(members), 40):
                chunk = members[start:start + 40]
                by_market: dict[str, list[str]] = {}
                for market, code in chunk:
                    by_market.setdefault(market, []).append(code)
                quote_requests.append(self._request_app_proxy(
                    "https://quota-h.10jqka.com.cn/fuyao/common_hq_aggr/quote/v1/multi_last_snapshot",
                    method="POST",
                    body={
                        "code_list": [
                            {"market": market, "codes": codes}
                            for market, codes in by_market.items()
                        ],
                        "trade_class": "intraday",
                        "data_fields": ["security_name", "10", "199112", "19", "264648"],
                        "lang": "zh_cn",
                        "gpid": 0,
                    },
                ))
            quote_payloads = (
                await asyncio.gather(*quote_requests, return_exceptions=True)
                if quote_requests else []
            )
            return self._us_market_result(
                module="etf_sectors",
                data={
                    "etf_sector_config": config,
                    "etf_sector_details": details,
                    "etf_quotes": [
                        value for value in quote_payloads
                        if isinstance(value, dict)
                    ],
                    "etf_count": len(member_keys),
                },
                complete=(
                    bool(categories)
                    and not failed
                    and (
                        requested is None
                        or set(details) == requested
                    )
                ),
                failed_modules=failed,
            )
        except Exception as exc:
            return market_error(provider="ths_app", market="us", error=exc)

    async def get_native_us_market_zone_snapshot(self) -> dict:
        """并行采集美股页面的独立子流水线，避免突发请求压满回调通道。"""
        async def safe_native(name: str, request: object) -> tuple[str, dict]:
            try:
                return name, await request
            except Exception as exc:
                return name, {
                    "status_code": -1,
                    "status_msg": str(exc),
                    "data": None,
                }

        index_fields = ["55", "4", "36103", "34821", "10", "34818"]
        period_fields = {
            "current": "34313",
            "five_day": "five_day_sector_rank",
            "one_month": "one_month_sector_rank",
            "three_month": "three_month_sector_rank",
        }
        ranking_tabs = (
            ("all", "全部"),
            ("us24hremen", "24H最热"),
            ("zhonggaigu", "中概股"),
            ("djg", "低价股"),
            ("redianmeigu", "热点美股"),
            ("ssxg", "上市新股"),
            ("redianetf", "热点ETF"),
        )
        try:
            (
                overview,
                sector_result,
                ranking_result,
                etf_result,
                index_pair,
                industry_pair,
                concept_pair,
            ) = await asyncio.gather(
                self.get_native_us_overview_snapshot(),
                self.get_native_us_sector_snapshot(),
                self.get_native_us_stock_rankings_snapshot(),
                self.get_native_us_etf_sectors_snapshot(),
                safe_native(
                    "indices",
                    self._request_native_unified(
                        lane="ranking",
                        online_id="us-home-indices",
                        protocol_id=4119,
                        page_id=2371,
                        request_dic="startrow=0\r\nrowcount=20",
                        timeout_seconds=6,
                    ),
                ),
                safe_native(
                    "industry_current",
                    self._request_native_unified(
                        lane="ranking",
                        online_id="us-industry-current",
                        protocol_id=4115,
                        page_id=2371,
                        request_dic=(
                            "startrow=0\r\nrowcount=3\r\nsortid=34313"
                            "\r\nsortorder=0\r\nmarketid=2029"
                        ),
                        timeout_seconds=6,
                    ),
                ),
                safe_native(
                    "concept_current",
                    self._request_native_unified(
                        lane="ranking",
                        online_id="us-concept-current",
                        protocol_id=4115,
                        page_id=2371,
                        request_dic=(
                            "startrow=0\r\nrowcount=3\r\nsortid=34313"
                            "\r\nsortorder=0\r\nmarketid=2030"
                        ),
                        timeout_seconds=6,
                    ),
                ),
            )
            overview_data = overview.get("data") or {}
            sector_data = sector_result.get("data") or {}
            ranking_data = ranking_result.get("data") or {}
            etf_data = etf_result.get("data") or {}
            sectors = dict(sector_data.get("sectors") or {})
            sectors["industry_current"] = industry_pair[1]
            sectors["concept_current"] = concept_pair[1]

            module_results = {
                "overview": overview,
                "sectors": sector_result,
                "stock_rankings": ranking_result,
                "etf_sectors": etf_result,
            }
            failed_modules = [
                name
                for name, result in module_results.items()
                if not (result.get("provider_metadata") or {}).get("complete")
            ]
            for name, result in (index_pair, industry_pair, concept_pair):
                if result.get("status_code") == -1:
                    failed_modules.append(name)
            complete = not failed_modules
            fetched_at = datetime.now(timezone.utc)
            return market_result(
                provider="ths_app",
                market="us",
                data={
                    "breadth_today": overview_data.get("breadth_today") or {},
                    "breadth_month": overview_data.get("breadth_month") or {},
                    "indices": index_pair[1],
                    "sectors": sectors,
                    "stock_ranking_labels": ranking_data.get(
                        "stock_ranking_labels"
                    ),
                    "stock_rankings": ranking_data.get("stock_rankings") or {},
                    "etf_sector_config": etf_data.get("etf_sector_config") or {},
                    "etf_sector_details": etf_data.get("etf_sector_details") or {},
                    "page_config": {
                        "page": "us_market_home",
                        "breadth_component": "riseFallStatistics",
                        "index_fields": index_fields,
                        "sector_period_fields": period_fields,
                        "stock_ranking_tabs": [
                            {"id": tab_id, "name": name}
                            for tab_id, name in ranking_tabs
                        ],
                    },
                },
                source_time=fetched_at.isoformat(),
                trade_date=fetched_at.astimezone(
                    ZoneInfo("America/New_York")
                ).date(),
                timezone_name="America/New_York",
                provider_metadata={
                    "source_component": "US market home",
                    "complete": complete,
                    "failed_required_modules": failed_modules,
                    "missing_ui_modules": [] if complete else failed_modules,
                    "runs_outside_a_share_hours": True,
                    "includes_pre_and_after_market": True,
                    "pipeline": "bounded_subpipelines_v2",
                },
            )
        except Exception as exc:
            return market_error(provider="ths_app", market="us", error=exc)

    async def _get_native_us_market_zone_snapshot_bulk_legacy(self) -> dict:
        """保留旧突发并发实现，仅用于性能回归对照。"""
        base = (
            "https://eq.10jqka.com.cn/open/api/hk_us_common_data/"
            "us_stocks/home_page/quote_change"
        )
        try:
            async def safe_request(name: str, request: object) -> tuple[str, dict]:
                try:
                    return name, await request
                except Exception as exc:
                    return name, {
                        "status_code": -1,
                        "status_msg": str(exc),
                        "data": None,
                    }

            index_fields = [
                "55", "4", "36103", "34821", "10", "34818",
            ]
            index_request = self._request_native_unified(
                lane="ranking",
                online_id="us-home-indices",
                protocol_id=4119,
                page_id=2371,
                request_dic="startrow=0\r\nrowcount=20",
                timeout_seconds=6,
            )
            period_fields = {
                "current": "34313",
                "five_day": "five_day_sector_rank",
                "one_month": "one_month_sector_rank",
                "three_month": "three_month_sector_rank",
            }
            sector_requests: dict[str, object] = {}
            for tag, market_id in (("industry", 2029), ("concept", 2030)):
                sector_requests[f"{tag}_current"] = self._request_native_unified(
                    lane="ranking",
                    online_id=f"us-{tag}-current",
                    protocol_id=4115,
                    page_id=2371,
                    request_dic=(
                        "startrow=0\r\nrowcount=3\r\nsortid=34313"
                        f"\r\nsortorder=0\r\nmarketid={market_id}"
                    ),
                    timeout_seconds=6,
                )
                for period, sort_field in period_fields.items():
                    if period == "current":
                        continue
                    query = urlencode({
                        "tag": tag,
                        "page_start": 0,
                        "page_size": 500,
                        "sort_field": sort_field,
                        "sort_type": "desc",
                        "api_type": 0,
                    })
                    sector_requests[f"{tag}_{period}"] = self._request_app_proxy(
                        "https://eq.10jqka.com.cn/open/api/"
                        f"gmg_homepage_sector/us/sector/v1/list?{query}"
                    )

            ranking_tabs = (
                ("all", "全部", 21208, "marketid=60", 34818),
                ("us24hremen", "24H最热", 4026, "marketkey=USA", 34822),
                ("zhonggaigu", "中概股", 21208, "marketid=35", 36065),
                ("djg", "低价股", 21208, "marketid=80", 36065),
                ("redianmeigu", "热点美股", 21208, "marketid=33", 36065),
                ("ssxg", "上市新股", 21208, "marketid=81", 36065),
                ("redianetf", "热点ETF", 21208, "marketid=36", 36065),
            )
            ranking_requests = {
                tab_id: self._request_native_unified(
                    lane="ranking",
                    online_id=f"us-stock-ranking-{tab_id}",
                    protocol_id=protocol_id,
                    page_id=2371,
                    request_dic=(
                        "startrow=0\r\nrowcount=500\r\n"
                        f"{selector}\r\nsortorder=0\r\nsortid={sort_id}"
                    ),
                    timeout_seconds=6,
                )
                for tab_id, _name, protocol_id, selector, sort_id in ranking_tabs
            }
            label_request = self._request_app_proxy(
                "https://eq.10jqka.com.cn/open/api/"
                "gmg_data_detail/stock_rank/us/v1/labels.json"
            )
            cache_now = asyncio.get_running_loop().time()
            cached_etf_config = (
                self._us_etf_sector_config_cache
                if cache_now < self._us_etf_sector_config_deadline
                else {}
            )
            etf_config_request = (
                None
                if cached_etf_config
                else self._request_native_unified(
                    lane="ranking",
                    online_id="us-etf-sector-config",
                    protocol_id=1361,
                    page_id=2371,
                    request_dic="",
                    timeout_seconds=6,
                )
            )

            breadth_day, breadth_month = await asyncio.gather(
                self._request_app_proxy(f"{base}/that_day/list"),
                self._request_app_proxy(f"{base}/last_month/list.json"),
            )
            named_requests = {
                "indices": index_request,
                "stock_labels": label_request,
                **{f"sector:{key}": value for key, value in sector_requests.items()},
                **{f"ranking:{key}": value for key, value in ranking_requests.items()},
            }
            if etf_config_request is not None:
                named_requests["etf_sector_config"] = etf_config_request
            cached_categories = (
                (cached_etf_config.get("data") or {}).get("items") or []
            )
            for category in cached_categories:
                if not isinstance(category, dict) or not category.get("BlockID"):
                    continue
                block_id = str(category["BlockID"])
                named_requests[f"etf_detail:{block_id}"] = (
                    self._request_native_unified(
                        lane="ranking",
                        online_id=f"us-etf-sector-{block_id}",
                        protocol_id=1360,
                        page_id=2371,
                        request_dic=(
                            f"stockcode={block_id}\r\nsortid=199112"
                            "\r\nstartrow=0\r\nrowcount=1\r\nsortorder=0"
                        ),
                        timeout_seconds=6,
                    )
                )
            request_results = await asyncio.gather(*(
                safe_request(name, request)
                for name, request in named_requests.items()
            ))
            collected = dict(request_results)
            etf_config = cached_etf_config or collected.get("etf_sector_config") or {}
            if etf_config and not cached_etf_config:
                self._us_etf_sector_config_cache = etf_config
                self._us_etf_sector_config_deadline = cache_now + 1800
            etf_categories = (
                (etf_config.get("data") or {}).get("items") or []
            )
            etf_detail_requests = {}
            for category in etf_categories:
                if not isinstance(category, dict) or not category.get("BlockID"):
                    continue
                block_id = str(category["BlockID"])
                etf_detail_requests[block_id] = self._request_native_unified(
                    lane="ranking",
                    online_id=f"us-etf-sector-{block_id}",
                    protocol_id=1360,
                    page_id=2371,
                    request_dic=(
                        f"stockcode={block_id}\r\nsortid=199112"
                        "\r\nstartrow=0\r\nrowcount=1\r\nsortorder=0"
                    ),
                    timeout_seconds=6,
                )
            etf_sector_details = {
                key.removeprefix("etf_detail:"): value
                for key, value in collected.items()
                if key.startswith("etf_detail:")
            }
            if etf_detail_requests and not etf_sector_details:
                etf_detail_results = await asyncio.gather(*(
                    safe_request(block_id, request)
                    for block_id, request in etf_detail_requests.items()
                ))
                etf_sector_details = dict(etf_detail_results)
            fetched_at = datetime.now(timezone.utc)
            day_data = breadth_day.get("data") or {}
            month_data = breadth_month.get("data") or {}
            sectors = {
                key.removeprefix("sector:"): value
                for key, value in collected.items()
                if key.startswith("sector:")
            }
            stock_rankings = {
                key.removeprefix("ranking:"): value
                for key, value in collected.items()
                if key.startswith("ranking:")
            }
            required = {
                "indices", "stock_labels", "sector:industry_current",
                "sector:concept_current", "sector:industry_five_day",
                "sector:concept_five_day", "ranking:all",
            }
            failed_required = sorted(
                name for name in required
                if (collected.get(name) or {}).get("status_code") == -1
            )
            complete = (
                not failed_required
                and bool(etf_categories)
                and len(etf_sector_details) == len(etf_categories)
                and all(
                    value.get("status_code") != -1
                    for value in etf_sector_details.values()
                )
            )
            return market_result(
                provider="ths_app",
                market="us",
                data={
                    "breadth_today": day_data,
                    "breadth_month": month_data,
                    "indices": collected.get("indices"),
                    "sectors": sectors,
                    "stock_ranking_labels": collected.get("stock_labels"),
                    "stock_rankings": stock_rankings,
                    "etf_sector_config": etf_config,
                    "etf_sector_details": etf_sector_details,
                    "page_config": {
                        "page": "us_market_home",
                        "breadth_component": "riseFallStatistics",
                        "index_fields": index_fields,
                        "sector_period_fields": period_fields,
                        "stock_ranking_tabs": [
                            {"id": tab_id, "name": name}
                            for tab_id, name, *_rest in ranking_tabs
                        ],
                    },
                },
                source_time=fetched_at.isoformat(),
                trade_date=fetched_at.astimezone(
                    ZoneInfo("America/New_York")
                ).date(),
                timezone_name="America/New_York",
                provider_metadata={
                    "source_component": "US market home",
                    "complete": complete,
                    "failed_required_modules": failed_required,
                    "missing_ui_modules": [] if complete else ["etf_sectors"],
                    "runs_outside_a_share_hours": True,
                    "includes_pre_and_after_market": True,
                },
            )
        except Exception as exc:
            return market_error(provider="ths_app", market="us", error=exc)

    async def get_native_sector_rotation(
        self,
        *,
        sector_type: str = "industry",
        metric: str = "main_net_inflow",
        day_count: int = 10,
        sector_count: int = 10,
    ) -> dict:
        type_values = {"industry": "industry", "concept": "con"}
        metric_values = {
            "change": "zf",
            "five_day_change": "zf5",
            "rise_rate": "riseRate",
            "limit_up_count": "riseLimCnt",
            "main_net_inflow": "zljlr",
        }
        if sector_type not in type_values or metric not in metric_values:
            raise ValueError("unsupported rotation sector_type or metric")
        try:
            url = (
                "https://eq.10jqka.com.cn/pick/block/block_hotspot/"
                "hotspot/v1/hot_block_list"
                f"?type={type_values[sector_type]}"
                f"&field={metric_values[metric]}"
                f"&day_num={max(1, min(int(day_count), 120))}"
                f"&block_num={max(1, min(int(sector_count), 50))}"
            )
            payload = await self._request_app_proxy(url)
            if payload.get("status_code") not in (None, 0):
                raise RuntimeError(str(payload.get("status_msg") or "rotation failed"))
            rows = ((payload.get("data") or {}).get("data_list") or [])
            return market_result(
                provider="ths_app_http",
                market="cn",
                data={
                    "sector_type": sector_type,
                    "metric": metric,
                    "count": len(rows),
                    "periods": rows,
                },
                timezone_name="Asia/Shanghai",
                source_time=(rows[0].get("date") if rows else None),
                trade_date=(rows[0].get("date") if rows else None),
                provider_metadata={
                    "source_component": "mobileweb_PlateChangeChart@1.0.3",
                    "signal_class": "provider_derived",
                },
            )
        except Exception as exc:
            return market_error(provider="ths_app_http", market="cn", error=exc)

    async def get_native_industry_opportunities(self) -> dict:
        try:
            payload = await self._request_app_proxy(
                "https://fund.10jqka.com.cn/quotation/wealth/v3/choose_industry"
            )
            if payload.get("status_code") not in (None, 0):
                raise RuntimeError(str(payload.get("status_msg") or "opportunity failed"))
            data = payload.get("data") or {}
            trade_date_value = str(data.get("date") or "")
            return market_result(
                provider="ths_app_http",
                market="cn",
                data=data,
                timezone_name="Asia/Shanghai",
                source_time=trade_date_value or None,
                trade_date=(
                    datetime.strptime(trade_date_value, "%Y%m%d").date()
                    if trade_date_value else None
                ),
                provider_metadata={
                    "source_component": "EtfIndustryOpportunityCard",
                    "signal_class": "provider_derived",
                },
            )
        except Exception as exc:
            return market_error(provider="ths_app_http", market="cn", error=exc)

    async def _get_native_sector_derived_table(
        self,
        *,
        data_type: str,
        sort_id: int,
        sort_name: str,
        count: int,
    ) -> dict:
        try:
            _, rows = await self._request_native_sector_table(
                page_id=4104,
                request_text=(
                    f"rowcount={max(1, min(int(count), 50))}\r\n"
                    "startrow=0\r\nsortorder=0\r\n"
                    f"sortid={sort_id}\r\npush=1\r\n"
                    "sorttype=sector_3_0\r\n"
                    f"sortname={sort_name}"
                ),
            )
            items = []
            for rank, row in enumerate(rows, start=1):
                indicators = row["indicators"]
                item = {**row, "rank": rank}
                if data_type == "sector_prosperity":
                    item.update(
                        {
                            "change_pct": _native_number(indicators.get("33001")),
                            "prosperity_score": _native_number(indicators.get("36151")),
                            "prosperity_percentile": _native_number(indicators.get("36152")),
                            "related_asset_mapping": indicators.get("36150"),
                        }
                    )
                else:
                    item.update(
                        {
                            "change_pct": _native_number(indicators.get("33001")),
                            "related_asset_mapping": indicators.get("36150"),
                        }
                    )
                items.append(item)
            return market_result(
                provider="ths_native",
                market="cn",
                data={"count": len(items), "items": items},
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "source_component": "AStockSector",
                    "signal_class": "provider_derived",
                    "data_type": data_type,
                },
            )
        except Exception as exc:
            return market_error(provider="ths_native", market="cn", error=exc)

    async def get_native_sector_prosperity(self, count: int = 20) -> dict:
        return await self._get_native_sector_derived_table(
            data_type="sector_prosperity",
            sort_id=36151,
            sort_name="sector_prosperity_all",
            count=count,
        )

    @staticmethod
    def _commodity_asset_mapping(value: object) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return decoded if isinstance(decoded, dict) else {}
        return {}

    async def _get_public_commodity_linkage_items(
        self,
        linkage_type: str,
        count: int,
    ) -> list[dict]:
        if linkage_type not in {"spot", "industry"}:
            raise ValueError("linkage_type must be spot or industry")
        requested = max(1, min(int(count), 500))
        # The public commodity endpoint rejects page sizes above 20.
        page_size = min(requested, 20)
        concurrency = max(
            1,
            min(
                int(os.getenv("THS_COMMODITY_HTTP_MAX_CONCURRENCY", "8")),
                16,
            ),
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_page(start_row: int) -> tuple[int, dict]:
            async with semaphore:
                payload = await self._get(
                    f"{self.COMMODITY_LINKAGE_BASE_URL}/{linkage_type}/"
                    f"start_row/desc/{start_row}/{page_size}"
                )
            if payload.get("status_code") not in (None, 0):
                raise RuntimeError(
                    str(
                        payload.get("status_msg")
                        or "commodity linkage request failed"
                    )
                )
            return start_row, payload

        _, first_payload = await fetch_page(0)
        first_items = (
            (first_payload.get("data") or {}).get("commodity_detail_list") or []
        )
        if not first_items:
            return []
        total_value = first_payload.get("total")
        total = int(total_value) if total_value is not None else len(first_items)
        target_count = min(requested, total)
        offsets = list(range(page_size, target_count, page_size))
        pages = await asyncio.gather(*(fetch_page(offset) for offset in offsets))

        items: list[dict] = list(first_items)
        for _, payload in sorted(pages, key=lambda item: item[0]):
            page_items = (payload.get("data") or {}).get("commodity_detail_list") or []
            items.extend(page_items)
        return items[:requested]

    @staticmethod
    def _commodity_quote_symbol(market_code: str, security_code: str) -> str | None:
        if market_code in {"48", "49"}:
            return f"bk_{security_code}"
        if market_code == "20":
            return f"hs_{security_code}"
        if market_code == "36":
            return f"sz_{security_code}"
        return None

    async def _get_direct_linked_asset_quotes(
        self,
        assets: list[tuple[str, str]],
    ) -> dict[tuple[str, str], dict]:
        concurrency = max(
            1,
            min(
                int(os.getenv("THS_COMMODITY_QUOTE_HTTP_MAX_CONCURRENCY", "16")),
                32,
            ),
        )
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_quote(
            asset: tuple[str, str],
        ) -> tuple[tuple[str, str], dict | None]:
            market_code, security_code = asset
            symbol = self._commodity_quote_symbol(market_code, security_code)
            if symbol is None:
                return asset, None
            async with semaphore:
                response = await self._client.get(
                    f"{self.QUOTE_BASE}/v6/time/{symbol}/last.js",
                    headers={
                        "Referer": "https://q.10jqka.com.cn/",
                        "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                    },
                    timeout=10,
                )
            response.raise_for_status()
            text = response.text
            payload = json.loads(text[text.index("{") : text.rindex("}") + 1])
            raw = payload.get(symbol) or {}
            previous_close = _native_number(raw.get("pre"))
            records = [
                record
                for record in str(raw.get("data") or "").split(";")
                if record
            ]
            latest = None
            if records:
                fields = records[-1].split(",")
                if len(fields) > 1:
                    latest = _native_number(fields[1])
            change_pct = (
                (latest - previous_close) / previous_close * 100
                if latest is not None and previous_close not in (None, 0)
                else None
            )
            return asset, {
                "market_code": market_code,
                "security_code": security_code,
                "security_name": raw.get("name"),
                "change_pct": change_pct,
            }

        results = await asyncio.gather(
            *(fetch_quote(asset) for asset in assets),
            return_exceptions=True,
        )
        quotes: dict[tuple[str, str], dict] = {}
        for result in results:
            if isinstance(result, Exception):
                continue
            asset, quote = result
            if quote is not None:
                quotes[asset] = quote
        return quotes

    async def _enrich_commodity_linkage_items(self, items: list[dict]) -> None:
        asset_order: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            mapping = self._commodity_asset_mapping(item.get("related_asset_mapping"))
            item["related_asset_mapping"] = mapping
            linked_assets = []
            for asset_type in ("block", "etf"):
                asset = mapping.get(asset_type)
                if not isinstance(asset, dict):
                    continue
                key = (str(asset.get("market") or ""), str(asset.get("code") or ""))
                if not all(key):
                    continue
                linked_assets.append(
                    {
                        "asset_type": asset_type,
                        "market_code": key[0],
                        "security_code": key[1],
                        "security_name": None,
                        "change_pct": None,
                    }
                )
                if key not in seen:
                    seen.add(key)
                    asset_order.append(key)
            item["linked_assets"] = linked_assets
        if not asset_order:
            return
        try:
            quotes = await self._get_direct_linked_asset_quotes(asset_order)
        except Exception:
            return
        for item in items:
            for asset in item.get("linked_assets") or []:
                quote = quotes.get(
                    (str(asset.get("market_code")), str(asset.get("security_code")))
                )
                if quote:
                    asset.update(quote)

    async def get_native_sector_commodity_linkage(self, count: int = 20) -> dict:
        requested = max(1, min(int(count), 500))
        errors: list[str] = []
        groups: dict[str, list[dict]] = {
            "futures": [],
            "spot": [],
            "industry": [],
        }
        futures, spot_items, industry_items = await asyncio.gather(
            self._get_native_sector_derived_table(
                data_type="sector_commodity_linkage",
                sort_id=33001,
                sort_name="sector_goods_futures",
                count=min(requested, 50),
            ),
            self._get_public_commodity_linkage_items("spot", requested),
            self._get_public_commodity_linkage_items("industry", requested),
            return_exceptions=True,
        )

        if isinstance(futures, Exception):
            errors.append(f"futures:{type(futures).__name__}")
        else:
            if futures.get("status") == MarketDataStatus.OK.value:
                for rank, raw in enumerate((futures.get("data") or {}).get("items") or [], start=1):
                    source_code = raw.get("provider_sector_code") or raw.get("code")
                    source_name = raw.get("sector_name") or raw.get("name")
                    groups["futures"].append(
                        {
                            **raw,
                            "provider_sector_code": source_code,
                            "sector_name": source_name,
                            "linkage_type": "futures",
                            "rank": rank,
                            "source_code": source_code,
                            "source_name": source_name,
                            "source_change_pct": raw.get("change_pct"),
                        }
                    )
            else:
                errors.append(f"futures:{futures.get('status')}")

        for linkage_type, result in zip(
            ("spot", "industry"), (spot_items, industry_items), strict=True
        ):
            if isinstance(result, Exception):
                errors.append(f"{linkage_type}:{type(result).__name__}")
                continue
            for rank, raw in enumerate(result, start=1):
                mapping = {
                    asset_type: raw.get(asset_type)
                    for asset_type in ("block", "etf")
                    if raw.get(asset_type)
                }
                groups[linkage_type].append(
                    {
                        "provider_sector_code": raw.get("code"),
                        "sector_name": raw.get("name"),
                        "linkage_type": linkage_type,
                        "rank": rank,
                        "source_code": raw.get("code"),
                        "source_name": raw.get("name"),
                        "source_change_pct": _native_number(raw.get("increase")),
                        "change_pct": _native_number(raw.get("increase")),
                        "related_asset_mapping": mapping,
                    }
                )

        items = [item for group in groups.values() for item in group]
        if not items:
            return market_error(
                provider="ths_composite",
                market="cn",
                error=RuntimeError(f"all commodity linkage sources failed: {errors}"),
            )
        await self._enrich_commodity_linkage_items(items)
        return market_result(
            provider="ths_composite",
            market="cn",
            data={
                "count": len(items),
                "counts": {key: len(value) for key, value in groups.items()},
                "linkage_types": groups,
                "items": items,
            },
            timezone_name="Asia/Shanghai",
            provider_metadata={
                "source_component": "THS commodity linkage composite",
                "signal_class": "provider_derived",
                "sources": {
                    "futures": "AStockSector/sector_goods_futures",
                    "spot": "block_quote/commodity/spot",
                    "industry": "block_quote/commodity/industry",
                    "linked_asset_quotes": "d.10jqka.com.cn/v6/time",
                },
                "errors": errors,
            },
        )

    async def get_hot_stocks(self, market: str = "a") -> dict:
        """个股热榜（A股/港股/美股）"""
        url = f"{self.HOT_LIST_BASE}/api/hot_list/v1/hot_stock/{market}/day/data.txt"
        return await self._get(url)

    async def get_hot_plate(self, plate_type: str = "concept") -> dict:
        """概念/行业热榜"""
        url = f"{self.HOT_LIST_BASE}/api/hot_list/v1/hot_plate/{plate_type}/data.txt"
        return await self._get(url)

    async def get_hot_board(
        self,
        board_type: str = "concept",
        sort: str = "heat",
        count: int = 10,
    ) -> dict:
        """获取同花顺板块热度线索，不代表板块行情或资金流。"""
        if board_type not in {"industry", "concept"}:
            raise ValueError("board_type must be industry or concept")
        try:
            raw = await self.get_hot_plate(board_type)
            plates = ((raw.get("data") or {}).get("plate_list") or [])[:count]
            items = [
                {
                    "provider_sector_code": item.get("code"),
                    "sector_name": item.get("name"),
                    "sector_type": board_type,
                    "heat_rank": item.get("order"),
                    "heat_score": self._optional_float(item.get("rate")),
                    "rank_change": item.get("hot_rank_chg"),
                    "heat_tag": item.get("hot_tag"),
                    "event_tag": item.get("tag"),
                    "representative_etf_code": item.get("etf_product_id"),
                    "representative_etf_name": item.get("etf_name"),
                }
                for item in plates
            ]
            return market_result(
                provider="ths",
                market="cn",
                data={
                    "sector_type": board_type,
                    "metric": "heat",
                    "count": len(items),
                    "sectors": items,
                },
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "requested_sort": sort,
                    "is_price_snapshot": False,
                    "is_money_flow": False,
                },
            )
        except Exception as exc:
            return market_error(provider="ths", market="cn", error=exc)

    async def get_hot_etf(self) -> dict:
        """ETF 热榜"""
        url = f"{self.HOT_LIST_BASE}/api/etf_rank/v1/hot.txt"
        return await self._get(url)

    async def get_hot_futures(self) -> dict:
        """期货热榜"""
        url = f"{self.HOT_LIST_BASE}/api/hot_list/v1/futures/data.txt"
        return await self._get(url)

    async def get_hot_bond(self) -> dict:
        """可转债热榜"""
        url = f"{self.HOT_BOND_BASE}/fuyao/hot_list_data/out/hot_list/v1/bond"
        return await self._get(url)

    async def get_hot_topics(self) -> dict:
        """热榜话题"""
        url = f"{self.HOT_TOPIC_BASE}/lgt/topic/open/api/hot_topic/v1/hot_module_list"
        return await self._get(url)

    async def get_hot_posts(self, page: int = 1, page_size: int = 10) -> dict:
        """热门文章"""
        url = f"{self.HOT_TOPIC_BASE}/lgt/hotmodules/open/api/hot_module/v1/hot_post/list"
        return await self._get(url, params={"page": page, "pageSize": page_size})

    @cached(
        ttl=1209600,
        source="ths",
        domain="market",
        frequency="daily",
        market="a_share",
        source_name="同花顺",
    )
    async def get_sector_catalog(self, sector_type: str = "industry") -> dict:
        """获取同花顺行业或概念完整目录。"""
        if sector_type not in {"industry", "concept"}:
            raise ValueError("sector_type must be industry or concept")
        try:
            fetcher = (
                ak.stock_board_industry_name_ths
                if sector_type == "industry"
                else ak.stock_board_concept_name_ths
            )
            frame = await asyncio.to_thread(fetcher)
            sectors = [
                {
                    "provider_sector_code": str(row["code"]),
                    "sector_name": str(row["name"]),
                    "sector_type": sector_type,
                    "classification": "ths",
                }
                for row in frame.to_dict("records")
                if row.get("code") and row.get("name")
            ]
            return market_result(
                provider="ths",
                market="cn",
                data={"count": len(sectors), "sectors": sectors},
                timezone_name="Asia/Shanghai",
                provider_metadata={"complete": True},
            )
        except Exception as exc:
            return market_error(provider="ths", market="cn", error=exc)

    async def get_sector_snapshot(self, sector_type: str = "industry") -> dict:
        """获取同花顺板块快照；当前来源只提供完整行业行情快照。"""
        if sector_type != "industry":
            return market_error(
                provider="ths",
                market="cn",
                error="THS concept endpoint provides events instead of price snapshots",
                provider_metadata={"error_type": "unsupported"},
            )
        try:
            frame = await asyncio.to_thread(ak.stock_board_industry_summary_ths)
            catalog = await THSClient.get_sector_catalog.__wrapped__(self, "industry")
            codes = {
                item["sector_name"]: item["provider_sector_code"]
                for item in (catalog.get("data") or {}).get("sectors", [])
            }
            sectors = []
            for row in frame.to_dict("records"):
                name = str(row.get("板块") or "")
                if not name:
                    continue
                sectors.append(
                    {
                        "provider_sector_code": codes.get(name),
                        "sector_name": name,
                        "sector_type": "industry",
                        "classification": "ths",
                        "latest": row.get("均价"),
                        "change_pct": row.get("涨跌幅"),
                        "volume": row.get("总成交量"),
                        "turnover": row.get("总成交额"),
                        "turnover_unit": "亿元",
                        "main_net_inflow": row.get("净流入"),
                        "main_net_inflow_unit": "亿元",
                        "up_count": row.get("上涨家数"),
                        "down_count": row.get("下跌家数"),
                        "lead_stock_name": row.get("领涨股"),
                        "lead_stock_price": row.get("领涨股-最新价"),
                        "lead_stock_change_pct": row.get("领涨股-涨跌幅"),
                    }
                )
            return market_result(
                provider="ths",
                market="cn",
                data={"count": len(sectors), "sectors": sectors},
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "complete": True,
                    "volume_unit": "万手",
                    "money_flow_method": "ths_industry_summary",
                },
            )
        except Exception as exc:
            return market_error(provider="ths", market="cn", error=exc)

    async def get_sector_intraday(
        self,
        provider_sector_code: str,
        *,
        sector_type: str = "industry",
    ) -> dict:
        """获取同花顺板块指数当日 1 分钟价格点和成交数据。"""
        if sector_type not in {"industry", "concept"}:
            raise ValueError("sector_type must be industry or concept")
        code = str(provider_sector_code).removeprefix("bk_").strip()
        if not code:
            raise ValueError("provider_sector_code must not be empty")
        try:
            quote_code = code
            if sector_type == "concept":
                detail_response = await self._client.get(
                    f"https://q.10jqka.com.cn/gn/detail/code/{code}/",
                    headers={"User-Agent": self.DEFAULT_HEADERS["User-Agent"]},
                )
                detail_response.raise_for_status()
                quote_code_match = re.search(
                    r'id=["\']clid["\'][^>]*value=["\'](\d+)["\']',
                    detail_response.text,
                )
                if quote_code_match is None:
                    raise ValueError(
                        f"THS quote code not found for concept {code}"
                    )
                quote_code = quote_code_match.group(1)
            response = await self._client.get(
                f"{self.QUOTE_BASE}/v6/time/bk_{quote_code}/last.js",
                headers={
                    "Referer": "https://q.10jqka.com.cn/",
                    "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                },
            )
            response.raise_for_status()
            text = response.text
            payload = json.loads(text[text.index("{") : text.rindex("}") + 1])
            quote = payload.get(f"bk_{quote_code}") or {}
            trade_date = quote.get("date")
            previous_close = self._optional_float(quote.get("pre"))
            points = []
            for record in str(quote.get("data") or "").split(";"):
                fields = record.split(",")
                if len(fields) < 5 or not fields[0]:
                    continue
                price = self._optional_float(fields[1])
                change = (
                    price - previous_close
                    if price is not None and previous_close not in (None, 0)
                    else None
                )
                points.append(
                    {
                        "datetime": (
                            f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]} "
                            f"{fields[0][:2]}:{fields[0][2:]}"
                            if trade_date and len(trade_date) == 8
                            else fields[0]
                        ),
                        "price": price,
                        "turnover": self._optional_float(fields[2]),
                        "average_price": self._optional_float(fields[3]),
                        "volume": self._optional_float(fields[4]),
                        "change": change,
                        "change_pct": (
                            change / previous_close * 100
                            if change is not None and previous_close
                            else None
                        ),
                    }
                )
            return market_result(
                provider="ths",
                market="cn",
                data={
                    "provider_sector_code": code,
                    "sector_name": quote.get("name"),
                    "sector_type": sector_type,
                    "interval": "1m",
                    "previous_close": previous_close,
                    "count": len(points),
                    "points": points,
                },
                source_time=points[-1]["datetime"] if points else None,
                trade_date=trade_date,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "is_sector_index": True,
                    "series_type": "intraday_price_points",
                    "has_true_ohlc": False,
                    "source_trading_hours": quote.get("tradeTime"),
                    "source_is_trading": quote.get("isTrading"),
                    "provider_quote_code": quote_code,
                },
            )
        except (ValueError, json.JSONDecodeError) as exc:
            return market_error(
                provider="ths",
                market="cn",
                error=exc,
                status=MarketDataStatus.PARSE_ERROR,
                provider_metadata={"provider_sector_code": code},
            )
        except Exception as exc:
            return market_error(
                provider="ths",
                market="cn",
                error=exc,
                provider_metadata={"provider_sector_code": code},
            )

    async def get_sector_kline(
        self,
        sector_name: str,
        sector_type: str = "industry",
        start_date: str = "20200101",
        end_date: str = "20500101",
    ) -> dict:
        """获取同花顺板块指数日 K 线。"""
        if sector_type not in {"industry", "concept"}:
            raise ValueError("sector_type must be industry or concept")
        try:
            fetcher = (
                ak.stock_board_industry_index_ths
                if sector_type == "industry"
                else ak.stock_board_concept_index_ths
            )
            frame = await asyncio.to_thread(
                fetcher,
                symbol=sector_name,
                start_date=start_date,
                end_date=end_date,
            )
            bars = [
                {
                    "date": row.get("日期"),
                    "open": row.get("开盘价"),
                    "high": row.get("最高价"),
                    "low": row.get("最低价"),
                    "close": row.get("收盘价"),
                    "volume": row.get("成交量"),
                    "turnover": row.get("成交额"),
                }
                for row in frame.to_dict("records")
            ]
            return market_result(
                provider="ths",
                market="cn",
                data={
                    "sector_name": sector_name,
                    "sector_type": sector_type,
                    "interval": "1d",
                    "adjustment": "source_index",
                    "count": len(bars),
                    "bars": bars,
                },
                trade_date=bars[-1]["date"] if bars else None,
                timezone_name="Asia/Shanghai",
                provider_metadata={"is_sector_index": True},
            )
        except Exception as exc:
            return market_error(provider="ths", market="cn", error=exc)

    # ========== 新闻 (news.10jqka.com.cn) ==========

    async def get_headlines(self) -> dict:
        """推荐头条（首页推荐tab头条模块）"""
        url = f"{self.NEWS_BASE}/tapp/news/headline/ths/client"
        return await self._get(url)

    async def get_discover_recommendations(
        self,
        *,
        req_type: int = 1,
        context: str = "",
        plan: int = 3,
    ) -> dict:
        """刷新页“推荐”信息流；Cookie 由 App Hook 在进程内安全注入。"""
        query = urlencode(
            {
                "req_type": req_type,
                "version": 115803,
                "plat": "android",
                "gid": 0,
                "context": context,
                "plan": plan,
            }
        )
        return await self._request_app_proxy(
            f"https://recommend.10jqka.com.cn/app/discover/api/v1/recommend?{query}"
        )

    async def get_discover_hot_topics(self) -> dict:
        """刷新页“热榜”的热点话题排行。"""
        return await self._request_app_proxy(
            "https://t.10jqka.com.cn/lgt/topic/open/api/hot_topic/v1/"
            "hot_module_list"
        )

    async def get_discover_hot_posts(
        self,
        *,
        page: int = 1,
        page_size: int = 30,
    ) -> dict:
        """刷新页“热榜”的热门正文列表。"""
        query = urlencode({"page": page, "pageSize": page_size})
        return await self._request_app_proxy(
            "https://t.10jqka.com.cn/lgt/hotmodules/open/api/hot_module/v1/"
            f"hot_post/list?{query}"
        )

    @staticmethod
    def seq_to_encoded(seq: int) -> str:
        """将新闻 seq 数字 ID 转换为 encoded 格式（逆向自 zx-detail-fronted-container）"""
        import hashlib
        MAX = 100_000_000_000
        MULTIPLIER = 2147483647
        scrambled = (seq * MULTIPLIER) % MAX
        padded = str(scrambled).zfill(11)
        check_digit = sum(int(d) for d in str(seq)) % 10
        h = hashlib.md5(f"{seq}{check_digit}".encode()).hexdigest()
        return h[:4] + padded + str(check_digit) + h[-3:]

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=1209600)
    async def get_article_detail(self, encoded_seq: str) -> dict:
        """获取新闻文章详情（type=1 新闻）
        encoded_seq: encoded 格式的文章ID，或纯数字 seq（会自动转换）
        """
        if encoded_seq.isdigit():
            encoded_seq = self.seq_to_encoded(int(encoded_seq))
        url = f"{self.NEWS_BASE}/mobile_api/news/article/v1/encoded/{encoded_seq}"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=1209600)
    async def get_news_themes(self) -> dict:
        """获取新闻主题分类列表（资讯→头条 tab 栏的主题标签）"""
        url = f"{self.NEWS_BASE}/app/headline/v1/hot-theme"
        return await self._get(url)

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=1209600)
    async def get_theme_articles(self, theme_id: str, page: int = 1, size: int = 15) -> dict:
        """获取主题下的文章列表
        theme_id: 主题ID，如 TZ-11385
        需要先查询 theme info 获取内容流 ID，再查询文章列表
        """
        # 1. 获取主题模块配置，找到内容流 ID
        theme_url = f"{self.NEWS_BASE}/app/theme/v1/theme"
        theme_info = await self._get(theme_url, params={"themeId": theme_id})
        data = theme_info.get("data", {})
        stream_id = None
        for module in data.get("module", []):
            if module.get("type") == 2:
                items = module.get("items", [])
                if items:
                    stream_id = items[0].get("id")
                    break
        if stream_id is None:
            return {"status_code": -1, "data": [], "msg": "未找到内容流ID"}

        # 2. 获取文章列表
        content_url = f"{self.NEWS_BASE}/app/theme/v1/content"
        content = await self._get(content_url, params={"id": str(stream_id), "page": page, "size": size})
        return {
            "status_code": 0,
            "data": {
                "themeId": theme_id,
                "title": data.get("title", ""),
                "description": data.get("content", ""),
                "streamId": stream_id,
                "articles": content.get("data", []),
            },
        }

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=1209600)
    async def get_flash_news_tabs(self) -> dict:
        """获取快讯分类标签列表（A股、重要、公告、期货、异动、港股、美股）"""
        url = f"{self.NEWS_BASE}/app/flash/flashnews/v2/tab"
        return await self._get(url)

    async def get_flash_news_list(self, tag_id: int = 21101, seq: int = 0) -> dict:
        """获取指定分类的快讯列表
        tag_id: 分类ID（从 get_flash_news_tabs 获取），默认21101=A股
        seq: 翻页游标，0=最新，传入上一页最后一条的 seq 加载更早的
        """
        url = f"{self.NEWS_BASE}/app/flash/flashnews/v1/list"
        return await self._get(url, params={"tagId": tag_id, "seq": seq})

    THS_ARTICLE_PATTERNS = [
        r'<div[^>]*class="[^"]*main-text[^"]*"[^>]*>',
        r'<div[^>]*class="[^"]*atc-content[^"]*"[^>]*>',
        r'<div[^>]*class="[^"]*article[^"]*"[^>]*>',
    ]

    @cached(source="ths", source_name="同花顺", domain="news", frequency="daily", market="a_share", ttl=1209600)
    async def fetch_article_content(self, url: str) -> str:
        """抓取同花顺新闻详情页正文"""
        html = await self._fetch_article_html(url, referer="https://news.10jqka.com.cn/")
        return self._extract_article_text(html, self.THS_ARTICLE_PATTERNS)

    # 不缓存：滚动列表翻页，page=N 在不同时间返回不同数据
    async def get_news_feed(self, page: int = 1, with_content: bool = True) -> dict:
        """滚动快讯（财经要闻实时滚动，每页 20 条）

        Args:
            page: 页码
            with_content: 是否并发抓取每条的详情页正文
        """
        import asyncio as _asyncio
        url = f"{self.NEWS_BASE}/tapp/news/push/stock/"
        result = await self._get(url, params={"page": page})

        # 并发抓取正文
        if with_content and isinstance(result, dict):
            data = result.get("data", {})
            items = data.get("list", []) if isinstance(data, dict) else []
            if items:
                urls = [item.get("url", "") for item in items]
                tasks = [self.fetch_article_content(u) for u in urls]
                contents = await _asyncio.gather(*tasks, return_exceptions=True)
                for item, c in zip(items, contents):
                    if isinstance(c, str):
                        item["content_full"] = c
        return result

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=1209600)
    async def get_topic_detail(self, code: str, page: int = 1, page_size: int = 10) -> dict:
        """话题详情（含推荐帖子列表）"""
        info_url = f"{self.HOT_TOPIC_BASE}/lgt/topic/open/api/topic_info/v1/topic?code={code}"
        feed_url = f"{self.HOT_TOPIC_BASE}/lgt/topic/open/api/topic_info/v3/recommend_list?code={code}&page={page}&pageSize={page_size}"
        info_resp, feed_resp = await asyncio.gather(
            self._get(info_url), self._get(feed_url)
        )
        return {"topic": info_resp.get("data", {}), "feeds": feed_resp.get("data", {})}

    @cached(source="ths", source_name="同花顺", domain="news", frequency="realtime", market="a_share", ttl=1209600)
    async def get_special_detail(self, code: str) -> dict:
        """专题详情（从 HTML 解析组件内容）"""
        import json as _json
        url = f"https://mams.10jqka.com.cn/new/server/html/{code}.html"
        resp = await self._client.get(url)
        resp.raise_for_status()
        html = resp.text
        match = re.search(r'var\s+activity\s*=\s*', html)
        if not match:
            return {"error": "无法解析专题内容"}
        decoder = _json.JSONDecoder()
        activity, _ = decoder.raw_decode(html, match.end())
        components = activity.get("page", {}).get("components", [])
        result = {"title": "", "desc": "", "abstract": "", "tabs": [], "sections": []}
        for comp in components:
            d = comp.get("detail", {})
            name = d.get("name", "")
            if name == "hot-header-image":
                result["title"] = d.get("title", {}).get("value", "")
                result["desc"] = d.get("desc", {}).get("value", "")
                result["abstract"] = d.get("abstract", {}).get("value", "")
            elif name == "v-tab":
                result["tabs"] = [t.get("content", "") for t in d.get("title", [])]
                for sub in comp.get("components", []):
                    self._extract_special_section(sub, result["sections"])
            elif name == "futures-kyc-relatedinfo":
                result["sections"].append({
                    "type": "关联商品",
                    "title": d.get("title", ""),
                    "content": d.get("content", ""),
                })
            else:
                self._extract_special_section(comp, result["sections"])
        return result

    def _extract_special_section(self, comp: dict, sections: list):
        """递归提取专题组件中的有效内容"""
        d = comp.get("detail", {})
        name = d.get("name", "")
        if name == "ai-html-component":
            text = d.get("text", "")
            clean = re.sub(r"<[^>]+>", " ", text)
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean and len(clean) > 20 and not clean.startswith(("AI摘要展示模块", ".ai-summary")):
                sections.append({"type": "内容", "content": clean})
        elif name == "event-timeline":
            title = d.get("title", "事件脉络")
            if isinstance(title, dict):
                title = title.get("value", "事件脉络")
            sections.append({"type": "事件脉络", "title": title})
        elif name == "news-content-flow-unify":
            title = d.get("title", "")
            if title:
                sections.append({"type": "资讯", "title": title})
        elif name == "event-deep-analysis":
            sections.append({"type": "深度分析"})
        elif name == "core-target-unify":
            title = d.get("title", "")
            if title:
                sections.append({"type": "相关标的", "title": title})
        elif name == "vue-title":
            title = d.get("title", "")
            if title:
                sections.append({"type": "标题", "title": title})
        elif name == "trends-read":
            content = d.get("content", "")
            if content:
                sections.append({"type": "内容", "content": content})
        elif name == "vue-text":
            text = d.get("textContent", "")
            if text:
                clean = re.sub(r"<[^>]+>", "", text).strip()
                if clean and len(clean) > 10:
                    sections.append({"type": "说明", "content": clean})
        for sub in comp.get("components", []):
            self._extract_special_section(sub, sections)

    async def get_limit_pool(self, pool_type: str = "up") -> dict:
        """获取涨停/跌停池 (data.10jqka.com.cn)

        Args:
            pool_type: "up" 涨停池, "down" 跌停池
        Returns:
            同花顺原始 JSON
        """
        endpoint = "limit_up_pool" if pool_type == "up" else "lower_limit_pool"
        headers = {
            "Referer": "https://data.10jqka.com.cn/",
            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
        }
        resp = await self._client.get(
            f"{self.THS_DATA}/dataapi/limit_up/{endpoint}/",
            params={"page": 1, "limit": 15,
                    "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914",
                    "order_field": "330324", "order_type": "0"},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def get_market_limit_counts(self) -> dict:
        """获取 A 股涨停和跌停家数，不生成市场情绪判断。"""
        try:
            limit_up, limit_down = await asyncio.gather(
                self._get_limit_pool_with_retry("up"),
                self._get_limit_pool_with_retry("down"),
            )
            if limit_up.get("status_code") != 0 or limit_down.get("status_code") != 0:
                return market_error(
                    provider="ths",
                    market="cn",
                    error="THS limit pool returned a non-success status",
                    provider_metadata={
                        "upstream_status": {
                            "up": limit_up.get("status_code"),
                            "down": limit_down.get("status_code"),
                        }
                    },
                )
            up_data = limit_up.get("data") or {}
            down_data = limit_down.get("data") or {}
            up_items = up_data.get("info") or []
            down_items = down_data.get("info") or []
            data = {
                "limit_up_count": (up_data.get("page") or {}).get("total"),
                "limit_down_count": (down_data.get("page") or {}).get("total"),
                "limit_up_sample": [
                    {
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "change_pct": item.get("change_rate"),
                    }
                    for item in up_items
                ],
                "limit_down_sample": [
                    {
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "change_pct": item.get("change_rate"),
                    }
                    for item in down_items
                ],
            }
            return market_result(
                provider="ths",
                market="cn",
                data=data,
                timezone_name="Asia/Shanghai",
                provider_metadata={
                    "sample_truncated": True,
                    "sample_limit": 15,
                    "source_endpoint": "dataapi/limit_up",
                },
            )
        except Exception as exc:
            return market_error(provider="ths", market="cn", error=exc)

    async def _get_limit_pool_with_retry(
        self,
        pool_type: str,
        attempts: int = 3,
    ) -> dict:
        last_error = None
        for attempt in range(attempts):
            try:
                return await self.get_limit_pool(pool_type)
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.3 * (attempt + 1))
        raise last_error or RuntimeError(f"failed to fetch {pool_type} limit pool")


    # ==================== 问财 (iwencai) ====================

    # 问财请求频率控制：最少间隔 60 秒，防止触发验证码
    _iwencai_last_request = 0.0
    _IWENCAI_MIN_INTERVAL = 60.0

    @cached(source="ths", source_name="同花顺", domain="sentiment", frequency="realtime", market="a_share", ttl=1209600)
    async def get_iwencai_query(self, question: str, perpage: int = 10, page: int = 1,
                                secondary_intent: str = "stock") -> dict:
        """问财自然语言选股查询（依赖数据库中的 hexin-v token）

        认证token由Zygisk Hook自动从同花顺App的WebView Cookie DB读取并上报到服务端数据库。
        每次请求实时从DB读取最新token，确保与Hook上报的token一致。
        服务端每次请求间隔≥60秒，防止触发验证码。

        Args:
            question: 自然语言问题（如"今日涨停的股票"、"AI概念股"）
            perpage: 每页条数
            page: 页码
            secondary_intent: 意图类型（stock/zhishu/fund）

        Returns:
            原始 iwencai API 响应，或包含 error 字段的 dict
        """
        import time as _time
        from src.infrastructure.db import fund_db

        # 频率限制
        now = _time.time()
        elapsed = now - THSClient._iwencai_last_request
        if elapsed < self._IWENCAI_MIN_INTERVAL:
            wait = self._IWENCAI_MIN_INTERVAL - elapsed
            return {"error": f"问财请求频率限制，请在 {wait:.0f} 秒后重试"}

        # 每次从 DB 读取最新 token（Hook 可能随时更新）
        cookies_data = fund_db.get_iwencai_cookies()
        hexin_v = cookies_data.get("hexin_v", "")
        if not hexin_v:
            return {"error": "hexin-v token 未配置，请先在手机端打开同花顺App触发上报"}

        # 构建完整 cookie（模拟真实浏览器会话）
        cookie_parts = [f"v={hexin_v}"]
        for key in ("userid", "cuc", "ticket", "sess_tk"):
            val = cookies_data.get(key, "")
            if val:
                cookie_parts.append(f"{key}={val}")

        add_info = (
            '{"urp":{"scene":1,"company":1,"business":1},'
            '"content_type":"' + secondary_intent + '",'
            '"search_cat":"' + secondary_intent + '"}'
        )

        THSClient._iwencai_last_request = _time.time()

        resp = await self._client.post(
            "https://www.iwencai.com/customized/chart/get-robot-data",
            json={
                "question": question,
                "perpage": perpage,
                "page": page,
                "source": "Ths_iwencai_Xuangu",
                "version": "2.0",
                "secondary_intent": secondary_intent,
                "add_info": add_info,
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Cookie": "; ".join(cookie_parts),
                "Referer": "https://www.iwencai.com/unifiedwap/home/index",
                "hexin-v": hexin_v,
            },
        )
        data = resp.json()

        # 检测验证码
        if data.get("data", {}).get("captcha_url") or data.get("code") == -2:
            return {"error": "问财触发验证码，hexin-v token 已失效，需要重新打开同花顺App刷新",
                    "captcha": True}

        if resp.status_code == 401:
            return {"error": "问财认证失败(401)，token可能已过期", "captcha": True}

        return data

    @cached(source="ths", source_name="同花顺", domain="sentiment", frequency="realtime", market="a_share", ttl=1209600)
    async def get_iwencai_stocks(self, question: str, limit: int = 10) -> list:
        """问财选股 — 提取结构化的股票列表

        Returns:
            [{"code": "605389.SH", "name": "长龄液压", ...}, ...]
        """
        raw = await self.get_iwencai_query(question, perpage=limit)
        if "error" in raw:
            return []

        results = []
        for answer in raw.get("data", {}).get("answer", []):
            for txt_item in answer.get("txt", []):
                content = txt_item.get("content", {})
                if not isinstance(content, dict):
                    continue
                for comp in content.get("components", []):
                    datas = comp.get("data", {}).get("datas", [])
                    for row in datas:
                        code = row.get("股票代码", "")
                        name = row.get("股票简称", "")
                        if code and name:
                            results.append(row)
        return results[:limit]


def _ths_snapshot_value(payload: dict) -> float | None:
    rows = ((payload.get("data") or {}).get("data") or [])
    values = (rows[0].get("values") or []) if rows else []
    raw_value = values[0].get("value") if values else None
    return THSClient._optional_float(raw_value)


def _ths_trend_values(payload: dict) -> list[dict]:
    data = payload.get("data") or {}
    timestamps = data.get("time_range") or []
    rows = data.get("data") or []
    value_groups = (rows[0].get("values") or []) if rows else []
    values = (
        value_groups[0].get("values") or []
        if value_groups
        else []
    )
    trend = []
    for raw_timestamp, raw_value in zip(timestamps, values):
        value = THSClient._optional_float(raw_value)
        if value is None:
            continue
        trend.append(
            {
                "timestamp": int(raw_timestamp),
                "net_inflow_yuan": value,
            }
        )
    return trend


def _ths_top_etf_inflow(
    payload: dict,
) -> tuple[dict | None, int, str | None]:
    data = payload.get("data") or {}
    indexes = data.get("indexes") or []
    rows = data.get("itemList") or []
    descriptions = {
        str(item.get("type")): item.get("desc")
        for item in indexes
        if item.get("type")
    }
    if not rows:
        return None, int(data.get("total") or 0), descriptions.get(
            "estimation_net_inflow_etf"
        )
    fields = [
        str(item.get("type"))
        for item in indexes
        if item.get("type")
    ]
    row = {
        field: value
        for field, value in zip(fields, rows[0])
    }
    sub_market = str(row.get("subMarket") or "")
    return (
        {
            "code": row.get("tradeCode"),
            "name": row.get("simpleName"),
            "market": "sz" if sub_market == "36" else sub_market,
            "net_inflow_yuan": THSClient._optional_float(
                row.get("estimation_net_inflow_etf")
            ),
        },
        int(data.get("total") or 0),
        descriptions.get("estimation_net_inflow_etf"),
    )


if __name__ == "__main__":
    import os
    import asyncio

    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)

    TEST_FUND = "110022"

    async def main():
        client = THSClient()
        try:
            # 基金数据
            print("=== 基金数据 ===")
            detail = await client.get_fund_detail(TEST_FUND)
            print(f"  基金详情: {bool(detail)}")

            holdings = await client.get_top10_holdings(TEST_FUND)
            stocks = holdings.get("data", {}).get("stock", [])
            print(f"  前十持仓: {len(stocks)}只")

            # 新闻
            print("\n=== 新闻资讯 ===")
            headlines = await client.get_headlines()
            print(f"  头条: {len(headlines.get('data', []))}条")

            flash = await client.get_flash_news_list()
            print(f"  快讯: {bool(flash)}")

            feed = await client.get_news_feed()
            print(f"  Feed: {bool(feed)}")

            # 热榜
            print("\n=== 热榜 ===")
            hot_stocks = await client.get_hot_stocks()
            print(f"  热股: {bool(hot_stocks)}")

            hot_plate = await client.get_hot_plate()
            print(f"  热板块: {bool(hot_plate)}")

            # 龙虎榜/涨停
            print("\n=== 市场数据 ===")
            limit_up = await client.get_limit_pool("up")
            count = limit_up.get("data", {}).get("page", {}).get("total", 0)
            print(f"  涨停池: {count}只")

            # 基金排行
            print("\n=== 基金排行 ===")
            ranking = await client.get_fund_ranking(sort_type="year")
            print(f"  基金排行: {bool(ranking)}")

        finally:
            await client.close()

    asyncio.run(main())
