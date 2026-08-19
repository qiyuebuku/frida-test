"""Persist THS App realtime subscriptions from one long-lived native session."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import redis.asyncio as async_redis

from src.application.services.china_exchange_calendar_service import (
    ChinaExchangeCalendarService,
)
from src.application.services.market_observation_service import (
    _native_chart_snapshots,
    _snapshot_from_response,
    _ths_event_snapshots,
)
from src.infrastructure.clients.ths import THSClient
from src.infrastructure.clients.ths_native_stream import (
    THS_NATIVE_EVENT_STREAM_HEALTH_KEY,
    THS_NATIVE_STREAM_HEALTH_KEY,
    THSNativeRealtimeStreamClient,
    THSRealtimeSubscription,
    THSUnifiedSubscription,
)
from src.infrastructure.config.settings import REDIS_URL
from src.infrastructure.persistence.repositories import MarketSnapshotRepository
from src.infrastructure.persistence.repositories.collection_state_repository_impl import (
    CollectionStateRepositoryImpl,
)


logger = logging.getLogger(__name__)
CN_TIMEZONE = ZoneInfo("Asia/Shanghai")
US_TIMEZONE = ZoneInfo("America/New_York")
PUSH_TASK_IDS = (
    "push_cn_indices", "push_market_context", "push_market_events",
    "push_etf_quotes", "push_futures_hot", "push_gold_quotes", "push_us_quotes",
)


def _push_task_id(subscription_id: str) -> str:
    """Map native subscriptions to the same stable IDs used by observability."""
    if subscription_id in {"cn_indices", "market_temperature"}:
        return "push_cn_indices" if subscription_id == "cn_indices" else "push_market_context"
    if subscription_id in {"market_events", "stock_events", "sector_events", "large_order_events"}:
        return "push_market_events"
    if subscription_id.startswith("etf_"):
        return "push_etf_quotes"
    if subscription_id.startswith("futures_"):
        return "push_futures_hot"
    if subscription_id.startswith("gold_"):
        return "push_gold_quotes"
    if subscription_id.startswith("us_"):
        return "push_us_quotes"
    return "push_market_context"


def _is_a_share_realtime_window(now: datetime) -> bool:
    local = now.astimezone(CN_TIMEZONE)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return 9 * 60 + 15 <= minute <= 11 * 60 + 30 or 13 * 60 <= minute <= 15 * 60


def _a_share_quote_observed_at(fetched_at: datetime) -> datetime:
    """Clamp push receive heartbeats to the last possible A-share quote time."""
    local = fetched_at.astimezone(CN_TIMEZONE)
    minute = local.hour * 60 + local.minute
    if (local.hour, local.minute, local.second, local.microsecond) > (11, 30, 0, 0) and minute < 13 * 60:
        local = local.replace(hour=11, minute=30, second=0, microsecond=0)
    elif minute > 15 * 60:
        local = local.replace(hour=15, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def _us_ranking_session(now: datetime | None = None) -> str:
    """Return the THS ranking display session for the US market home page."""
    current = (now or datetime.now(timezone.utc)).astimezone(US_TIMEZONE)
    if current.weekday() >= 5:
        return "closed"
    minute = current.hour * 60 + current.minute
    if 240 <= minute < 570:
        return "pre_market"
    if 570 <= minute < 960:
        return "regular"
    if 960 <= minute < 1200:
        return "after_hours"
    return "closed"


def _us_ranking_sort_id(now: datetime | None = None) -> int:
    session = _us_ranking_session(now)
    if session == "pre_market":
        return 36065
    if session == "after_hours":
        return 34868
    return 34818


US_RANKING_SESSION_SORT_IDS = {
    "pre_market": 36065,
    "regular": 34818,
    "after_hours": 34868,
}


def _native_float(value: object) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).rstrip("%"))
    except ValueError:
        return None


def _native_amount_to_yuan(value: object) -> float | None:
    if value in (None, "", "--"):
        return None
    text = str(value).strip().replace(",", "")
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100_000_000.0
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RealtimeSeriesDefinition:
    indicator: str
    data_type: str
    subject_type: str
    subject_id: str
    market: str = "cn"
    latest_point_only: bool = False

    @property
    def key(self) -> str:
        return THSClient.NATIVE_REALTIME_INDICATORS[self.indicator]

    def subscription(self) -> THSRealtimeSubscription:
        return THSRealtimeSubscription(
            subscription_id=self.indicator,
            key=self.key,
            request_param=f"{self.key} data",
            request_channel=f"{self.key}_channel",
        )


REALTIME_SERIES = (
    RealtimeSeriesDefinition(
        "market_capital",
        "market_capital",
        "market",
        "cn:a_share:market_capital",
    ),
    RealtimeSeriesDefinition(
        "market_temperature",
        "market_sentiment",
        "market",
        "cn:a_share:ths_temperature",
    ),
    RealtimeSeriesDefinition(
        "northbound_capital",
        "northbound_capital_current",
        "market",
        "cn:northbound:ths",
        latest_point_only=True,
    ),
    RealtimeSeriesDefinition(
        "ftse_a50",
        "futures_intraday",
        "index",
        "global:futures:ftse_a50",
    ),
    RealtimeSeriesDefinition(
        "dow_futures",
        "futures_intraday",
        "index",
        "global:futures:dow_jones",
        market="global",
    ),
    RealtimeSeriesDefinition(
        "reverse_repo",
        "reverse_repo",
        "liquidity",
        "cn:monetary:reverse_repo",
    ),
    RealtimeSeriesDefinition(
        "usd_cny",
        "forex_intraday",
        "currency",
        "cn:forex:usd_cny:ths",
    ),
)


@dataclass(frozen=True, slots=True)
class UnifiedEventDefinition:
    subscription_id: str
    online_id: str
    protocol_id: int
    page_id: int
    request_dic: str
    cancel_request_dic: str
    source_key: str

    def subscription(self) -> THSUnifiedSubscription:
        return THSUnifiedSubscription(
            subscription_id=self.subscription_id,
            online_id=self.online_id,
            protocol_id=self.protocol_id,
            page_id=self.page_id,
            request_dic=self.request_dic,
            cancel_request_dic=self.cancel_request_dic,
        )


GOLD_FUTURES_CONTRACT_DEFINITION = UnifiedEventDefinition(
    "gold_futures_contracts",
    "goldFuturesContractsStream",
    4021,
    9001,
    "startrow=0\r\nrowcount=100\r\nsortid=-1\r\nmarketkey=au",
    "",
    "data",
)

CN_INDEX_CODES = (
    ("1A0001", "16", "000001"),
    ("399001", "32", "399001"),
    ("399006", "32", "399006"),
    ("899050", "144", "899050"),
    ("1B0680", "16", "000680"),
    ("1B0688", "16", "000688"),
    ("1B0510", "16", "000510"),
    ("1B0300", "16", "000300"),
    ("1B0852", "16", "000852"),
    ("1B0016", "16", "000016"),
    ("1B0905", "16", "000905"),
    ("399330", "32", "399330"),
    ("1B0698", "16", "000698"),
    ("883957", "48", None),
)
CN_INDEX_CANONICAL_CODES = {
    native_code: canonical_code
    for native_code, _market_id, canonical_code in CN_INDEX_CODES
}
CN_INDEX_STREAM_DEFINITION = UnifiedEventDefinition(
    "cn_indices",
    "cnMarketIndicesStream",
    1264,
    2312,
    (
        "startrow=0\r\nsortid=-1\r\nrowcount=14\r\nnewrealtime=0\r\n"
        "selfstockcustom=1\r\nupdate=1\r\n"
        "columnorder=55|4|34338|10|34818|48|13|19\r\n"
        f"marketlist={'|'.join(item[1] for item in CN_INDEX_CODES)}\r\n"
        f"stocklist={'|'.join(item[0] for item in CN_INDEX_CODES)}\r\npush=1"
    ),
    "",
    "data",
)

def _futures_hot_quotes_definition(
    members: list[tuple[str, str]],
) -> UnifiedEventDefinition:
    values = members[:6]
    return UnifiedEventDefinition(
        "futures_hot_quotes",
        "futuresHotContinuousQuotesStream",
        1264,
        2312,
        (
            f"startrow=0\r\nsortid=-1\r\nrowcount={len(values)}\r\n"
            "newrealtime=0\r\nselfstockcustom=1\r\nupdate=1\r\n"
            "columnorder=55|4|34338|10|34818|34821|48\r\n"
            f"marketlist={'|'.join(item[1] for item in values)}\r\n"
            f"stocklist={'|'.join(item[0] for item in values)}\r\npush=1"
        ),
        "",
        "data",
    )

CN_MARKET_BREADTH_STREAM_DEFINITION = UnifiedEventDefinition(
    "cn_market_breadth",
    "hs_datacenter_ztdt",
    1002,
    6001,
    (
        "action=subscribe\r\nkey=hs_datacenter_ztdt\r\n"
        "stockcode=1A0001\r\nmarketcode=16"
    ),
    (
        "action=unsubscribe\r\nkey=hs_datacenter_ztdt\r\n"
        "stockcode=1A0001\r\nmarketcode=16"
    ),
    "hs_datacenter_ztdt",
)


UNIFIED_EVENT_SERIES = (
    CN_INDEX_STREAM_DEFINITION,
    CN_MARKET_BREADTH_STREAM_DEFINITION,
    # ETF 全量榜单和首页三个榜单直接决定 ETF Web 页可用性。
    # 放在容易超时的大盘异动订阅之前，避免 App 冷启动时被后者长期阻塞。
    *(
        UnifiedEventDefinition(
            f"etf_home_{category}",
            f"etfHome{category.title()}Stream",
            4104,
            2501,
            (
                "startrow=0\r\nrowcount=6\r\nsortorder=0\r\n"
                "sortid=33001\r\n"
                f"sortname={category}\r\n"
                "sorttype=etftypelist\r\npush=1"
            ),
            "",
            "data",
        )
        for category in ("industry", "index", "t0")
    ),
    UnifiedEventDefinition(
        "market_anomaly_curve",
        "dpydLine",
        1229,
        2312,
        "fstrend=1\r\nstockcode=1A0001\r\nmarketcode=16",
        "",
        "content",
    ),
    UnifiedEventDefinition(
        "market_events",
        "marketLabel",
        1002,
        6000,
        "marketcode=16\r\naction=subscribe\r\nkey=mobiledpyd\r\nstockcode=1A0001",
        "marketcode=16\r\naction=unsubscribe\r\nkey=mobiledpyd\r\nstockcode=1A0001",
        "mobiledpyd",
    ),
    UnifiedEventDefinition(
        "stock_events",
        "ggList",
        1004,
        6002,
        (
            "id=1004\r\naction=subscribe\r\nkey=dxjl_free\r\n"
            f"data_id_list={THSClient.SHORT_SPIRIT_STOCK_DATA_IDS}\r\n"
            f"max_msg_num={THSClient.SHORT_SPIRIT_MAX_EVENTS}\r\nstock_list=all"
        ),
        (
            "id=1004\r\naction=unsubscribe\r\nkey=dxjl_free\r\n"
            f"data_id_list={THSClient.SHORT_SPIRIT_STOCK_DATA_IDS}\r\n"
            f"max_msg_num={THSClient.SHORT_SPIRIT_MAX_EVENTS}\r\nstock_list=all"
        ),
        "dxjl",
    ),
    UnifiedEventDefinition(
        "sector_events",
        "blockList",
        1004,
        6002,
        (
            "action=subscribe\r\nkey=block_dxjl\r\ndata_id_list=1,2,3,4\r\n"
            f"max_msg_num={THSClient.SHORT_SPIRIT_MAX_EVENTS}\r\nstock_list=all"
        ),
        (
            "action=unsubscribe\r\nkey=block_dxjl\r\ndata_id_list=1,2,3,4\r\n"
            f"max_msg_num={THSClient.SHORT_SPIRIT_MAX_EVENTS}\r\nstock_list=all"
        ),
        "block_dxjl",
    ),
    UnifiedEventDefinition(
        "large_order_events",
        "largeOrderList",
        1004,
        6002,
        (
            "action=subscribe\r\nkey=dbwt\r\ndata_id_list=133990,133991\r\n"
            f"max_msg_num={THSClient.SHORT_SPIRIT_MAX_EVENTS}\r\nstock_list=all"
        ),
        (
            "action=unsubscribe\r\nkey=dbwt\r\ndata_id_list=133990,133991\r\n"
            f"max_msg_num={THSClient.SHORT_SPIRIT_MAX_EVENTS}\r\nstock_list=all"
        ),
        "dbwt",
    ),
    # The US index table only becomes a live callback source when push=1 is
    # present. Keep it last because Unified subscriptions share one App init
    # frame and must finish the established A-share event subscriptions first.
    UnifiedEventDefinition(
        "us_indices",
        "usHomeIndicesStream",
        4119,
        2371,
        "startrow=0\r\nrowcount=20\r\npush=1",
        "",
        "data",
    ),
    UnifiedEventDefinition(
        "us_sector_industry",
        "usIndustryCurrentStream",
        4115,
        2371,
        (
            "startrow=0\r\nrowcount=3\r\nsortid=34313\r\n"
            "sortorder=0\r\nmarketid=2029\r\npush=1"
        ),
        "",
        "data",
    ),
    UnifiedEventDefinition(
        "us_sector_concept",
        "usConceptCurrentStream",
        4115,
        2371,
        (
            "startrow=0\r\nrowcount=3\r\nsortid=34313\r\n"
            "sortorder=0\r\nmarketid=2030\r\npush=1"
        ),
        "",
        "data",
    ),
)
UNIFIED_EVENT_SUBSCRIPTION_IDS = frozenset(
    item.subscription_id for item in UNIFIED_EVENT_SERIES
)

US_DYNAMIC_EVENT_SERIES = (
    *(
        UnifiedEventDefinition(
            f"us_ranking_all_{session}",
            f"usRankingAll{session.title()}Stream",
            21208,
            2371,
            (
                "startrow=0\r\nrowcount=500\r\nmarketid=60\r\n"
                f"sortorder=0\r\nsortid={sort_id}\r\npush=1"
            ),
            "",
            "data",
        )
        for session, sort_id in US_RANKING_SESSION_SORT_IDS.items()
    ),
    UnifiedEventDefinition(
        "us_etf_config",
        "usEtfConfigStream",
        1361,
        2371,
        "push=1",
        "",
        "data",
    ),
    *(
        UnifiedEventDefinition(
            f"us_ranking_{tab_id}",
            f"usRanking{tab_id}Stream",
            protocol_id,
            2371,
            (
                "startrow=0\r\nrowcount=500\r\n"
                f"{selector}\r\nsortorder=0\r\nsortid={sort_id}\r\npush=1"
            ),
            "",
            "data",
        )
        for tab_id, protocol_id, selector, sort_id in (
            ("us24hremen", 4026, "marketkey=USA", 34822),
            ("zhonggaigu", 21208, "marketid=35", 36065),
            ("djg", 21208, "marketid=80", 36065),
            ("redianmeigu", 21208, "marketid=33", 36065),
            ("ssxg", 21208, "marketid=81", 36065),
            ("redianetf", 21208, "marketid=36", 36065),
        )
    ),
)


class THSRealtimeStreamService:
    """Demultiplex App callbacks and persist them without blocking the socket."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        repository: MarketSnapshotRepository | None = None,
        queue_size: int = 256,
        flush_interval: float = 0.5,
        batch_size: int = 1000,
        command_host: str | None = None,
        command_port: int | None = None,
        china_exchange_calendar: ChinaExchangeCalendarService | None = None,
    ) -> None:
        self._definitions = {item.indicator: item for item in REALTIME_SERIES}
        self._event_definitions = {
            item.subscription_id: item
            for item in (
                *UNIFIED_EVENT_SERIES,
                *US_DYNAMIC_EVENT_SERIES,
                GOLD_FUTURES_CONTRACT_DEFINITION,
            )
        }
        self._repository = repository or MarketSnapshotRepository()
        self._china_exchange_calendar = (
            china_exchange_calendar or ChinaExchangeCalendarService()
        )
        self._task_states = CollectionStateRepositoryImpl()
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=max(1, queue_size)
        )
        self._flush_interval = max(0.05, float(flush_interval))
        self._batch_size = max(1, int(batch_size))
        self._stop_event = asyncio.Event()
        self._latest_buckets: dict[str, datetime | None] = {}
        self._last_us_sector_refresh_at: datetime | None = None
        self._market_anomaly_trade_date = None
        self._market_anomaly_axis: dict[str, float | None] = {}
        self._market_anomaly_state: dict[str, list[dict[str, Any]]] = {
            "curve": [],
            "market_events": [],
            "stock_events": [],
            "sector_events": [],
            "large_order_events": [],
        }
        self._us_quote_members: set[tuple[str, str]] = set()
        self._us_quote_subscription_sequence = 0
        self._command_host = command_host or os.getenv(
            "THS_NATIVE_COMMAND_HOST", "127.0.0.1"
        )
        self._command_port = command_port or int(
            os.getenv("THS_NATIVE_COMMAND_PORT", "49302")
        )
        self._command_server: asyncio.Server | None = None
        self._command_clients: set[asyncio.StreamWriter] = set()
        self._command_interface_locks: dict[str, asyncio.Lock] = {}
        self._stream_host = host or os.getenv(
            "THS_NATIVE_STREAM_HOST", "127.0.0.1"
        )
        self._stream_port = port or int(
            os.getenv("THS_NATIVE_STREAM_PORT", "49300")
        )
        self._subscription_activation_lock = asyncio.Lock()
        self._client = THSNativeRealtimeStreamClient(
            host=self._stream_host,
            port=self._stream_port,
            subscriptions=(
                *(item.subscription() for item in REALTIME_SERIES),
                CN_INDEX_STREAM_DEFINITION.subscription(),
                CN_MARKET_BREADTH_STREAM_DEFINITION.subscription(),
            ),
            heartbeat_interval=float(
                os.getenv("THS_NATIVE_STREAM_HEARTBEAT_SECONDS", "15")
            ),
            read_timeout=float(
                os.getenv("THS_NATIVE_STREAM_READ_TIMEOUT_SECONDS", "45")
            ),
            subscription_interval=float(
                os.getenv("THS_NATIVE_STREAM_SUBSCRIPTION_INTERVAL_SECONDS", "0.25")
            ),
            initial_response_timeout=float(
                os.getenv("THS_NATIVE_STREAM_INITIAL_RESPONSE_TIMEOUT_SECONDS", "4")
            ),
            dynamic_activation_interval=float(
                os.getenv("THS_NATIVE_STREAM_DYNAMIC_INTERVAL_SECONDS", "0.5")
            ),
            reconnect_min_delay=float(
                os.getenv("THS_NATIVE_STREAM_RECONNECT_MIN_SECONDS", "1")
            ),
            reconnect_max_delay=float(
                os.getenv("THS_NATIVE_STREAM_RECONNECT_MAX_SECONDS", "5")
            ),
            subscription_activation_lock=self._subscription_activation_lock,
        )
        self._us_quote_client: THSNativeRealtimeStreamClient | None = None
        self._us_quote_client_task: asyncio.Task[None] | None = None
        self._deferred_us_quote_subscriptions: list[THSUnifiedSubscription] = []
        self._gold_client: THSNativeRealtimeStreamClient | None = None
        self._last_event_at: dict[str, datetime] = {}
        self._last_stale_reconnect_at: datetime | None = None
        self._business_refresh_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        for task_id in PUSH_TASK_IDS:
            await asyncio.to_thread(
                self._task_states.ensure_task_state,
                task_id=task_id,
                aggregator="push",
                source_name=task_id,
                task_type="push",
            )
        await self._load_latest_buckets()
        cached_quote_members = await self._load_cached_us_quote_members()
        gold_stock_definition = await self._load_cached_gold_stock_definition()
        futures_hot_definition = await self._load_latest_futures_hot_definition()
        app_global_push_enabled = os.getenv(
            "THS_APP_GLOBAL_PUSH_ENABLED", "true"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if app_global_push_enabled:
            self._event_definitions[
                GOLD_FUTURES_CONTRACT_DEFINITION.subscription_id
            ] = GOLD_FUTURES_CONTRACT_DEFINITION
        # Unified subscriptions are best-effort App capabilities.  Several of
        # them legitimately return -131 outside their active window.  Making
        # them part of the initial handshake kept the command broker offline
        # for minutes and then forced a reconnect when any one was missing.
        # Bring up the stable realtime transport first and restore all Unified
        # tables in the background so one unavailable push feed cannot block
        # futures/ETF/gold one-shot commands.
            await self._client.add_subscriptions(
                item.subscription()
                for item in (
                    *UNIFIED_EVENT_SERIES,
                    *US_DYNAMIC_EVENT_SERIES,
                    GOLD_FUTURES_CONTRACT_DEFINITION,
                )
                if item.subscription_id not in {"cn_indices", "cn_market_breadth"}
            )
        # The futures overview is always enabled and must not depend on the
        # optional global-push feature flag. Membership is stable continuous
        # contracts; quote values are maintained by the App push channel.
        if futures_hot_definition is not None:
            self._event_definitions[futures_hot_definition.subscription_id] = (
                futures_hot_definition
            )
            await self._client.add_subscriptions([
                futures_hot_definition.subscription(),
            ])
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(
                    self._client.run(self._enqueue_event),
                    name="ths-realtime-stream-reader",
                )
                tasks.create_task(
                    self._persistence_loop(),
                    name="ths-realtime-stream-persistence",
                )
                tasks.create_task(
                    self._health_loop(),
                    name="ths-realtime-stream-health",
                )
                tasks.create_task(
                    self._command_server_loop(),
                    name="ths-native-command-server",
                )
                if futures_hot_definition is not None:
                    tasks.create_task(
                        self._futures_hot_quote_health_loop(),
                        name="ths-futures-hot-quote-health",
                    )
                if app_global_push_enabled:
                    tasks.create_task(
                        self._us_ranking_membership_refresh_loop(),
                        name="ths-us-ranking-membership-refresh",
                    )
                    tasks.create_task(
                        self._etf_home_refresh_loop(),
                        name="ths-etf-home-refresh",
                    )
                if app_global_push_enabled and gold_stock_definition is not None:
                    tasks.create_task(
                        self._gold_stock_refresh_loop(gold_stock_definition),
                        name="ths-gold-stock-refresh",
                    )
                if app_global_push_enabled and cached_quote_members:
                    tasks.create_task(
                        self._restore_cached_us_quote_stream(cached_quote_members),
                        name="ths-us-security-quote-cache-restore",
                    )
        finally:
            self._stop_event.set()
            await self._close_command_server()
            await self._stop_us_quote_client()
            await self._stop_gold_client()
            await self._client.stop()
            await self._clear_health()

    async def stop(self) -> None:
        self._stop_event.set()
        await self._close_command_server()
        await self._stop_us_quote_client()
        await self._stop_gold_client()
        await self._client.stop()

    async def _futures_hot_quote_health_loop(self) -> None:
        """Restore a silent futures quote push without restarting the App."""
        await self._client.wait_until_connected()
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=15.0)
                return
            except TimeoutError:
                pass
            last_event = self._last_event_at.get("futures_hot_quotes")
            now = datetime.now(timezone.utc)
            if last_event and (now - last_event).total_seconds() <= 20:
                continue
            try:
                refreshed = await self._client.refresh_subscription(
                    "futures_hot_quotes"
                )
                logger.warning(
                    "THS futures hot quote push stale; targeted resubscribe "
                    "sent=%s last_event=%s",
                    refreshed,
                    last_event.isoformat() if last_event else None,
                )
            except Exception:
                logger.exception(
                    "Failed to refresh stale THS futures hot quote subscription"
                )

    async def _stop_gold_client(self) -> None:
        client = self._gold_client
        self._gold_client = None
        if client is not None:
            await client.stop()

    async def _stop_us_quote_client(self) -> None:
        client = self._us_quote_client
        task = self._us_quote_client_task
        self._us_quote_client = None
        self._us_quote_client_task = None
        if client is not None:
            await client.stop()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, timeout=5)

    async def _command_server_loop(self) -> None:
        server = await asyncio.start_server(
            self._handle_command_client,
            self._command_host,
            self._command_port,
            limit=8 * 1024 * 1024,
        )
        self._command_server = server
        logger.info(
            "THS native command broker listening host=%s port=%s",
            self._command_host,
            self._command_port,
        )
        try:
            async with server:
                await server.serve_forever()
        except asyncio.CancelledError:
            raise
        finally:
            if self._command_server is server:
                self._command_server = None

    async def _close_command_server(self) -> None:
        server = self._command_server
        if server is None:
            return
        server.close()
        await server.wait_closed()
        if self._command_server is server:
            self._command_server = None
        clients = tuple(self._command_clients)
        for writer in clients:
            writer.close()
        for writer in clients:
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        self._command_clients.clear()

    async def _handle_command_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._command_clients.add(writer)
        write_lock = asyncio.Lock()
        tasks: set[asyncio.Task[None]] = set()
        try:
            while not self._stop_event.is_set():
                raw = await reader.readline()
                if not raw:
                    break
                task = asyncio.create_task(
                    self._execute_command(raw, writer, write_lock),
                    name="ths-native-command",
                )
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            for task in tuple(tasks):
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._command_clients.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _execute_command(
        self,
        raw: bytes,
        writer: asyncio.StreamWriter,
        write_lock: asyncio.Lock,
    ) -> None:
        request_id = ""
        try:
            command = json.loads(raw)
            if not isinstance(command, dict):
                raise ValueError("THS native command must be an object")
            request_id = str(command.get("request_id") or "").strip()
            route = str(command.get("route") or "").strip()
            payload = command.get("payload")
            if not request_id or not route or not isinstance(payload, dict):
                raise ValueError("request_id, route and payload are required")
            timeout = max(
                1.0,
                min(float(command.get("timeout_seconds") or 75.0), 180.0),
            )
            # The persistent broker may accept concurrent callers, but one
            # native interface (route/protocol/page) reuses App callback state
            # and must remain single-flight. Distinct interfaces are safe to
            # execute concurrently and should not block each other.
            interface_key = ":".join((
                route,
                str(payload.get("protocolId") or ""),
                str(payload.get("pageId") or ""),
            ))
            interface_lock = self._command_interface_locks.setdefault(
                interface_key,
                asyncio.Lock(),
            )
            async with interface_lock:
                result = await self._client.request(
                    route=route,
                    payload=payload,
                    timeout=timeout,
                )
            response = {
                "request_id": request_id,
                "success": True,
                "response": result,
            }
        except Exception as exc:
            response = {
                "request_id": request_id,
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        encoded = json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        async with write_lock:
            if writer.is_closing():
                return
            writer.write(encoded + b"\n")
            await writer.drain()

    async def _health_loop(self) -> None:
        client = async_redis.from_url(REDIS_URL, decode_responses=True)
        try:
            while not self._stop_event.is_set():
                try:
                    now = datetime.now(timezone.utc)
                    business_stream_fresh = self._business_stream_is_fresh(now)
                    if self._client.is_connected:
                        await client.set(
                            THS_NATIVE_STREAM_HEALTH_KEY,
                            datetime.now(timezone.utc).isoformat(),
                            ex=30,
                        )
                        if business_stream_fresh and UNIFIED_EVENT_SUBSCRIPTION_IDS.issubset(
                            self._client.active_subscription_ids
                        ):
                            await client.set(
                                THS_NATIVE_EVENT_STREAM_HEALTH_KEY,
                                datetime.now(timezone.utc).isoformat(),
                                ex=30,
                            )
                        else:
                            await client.delete(THS_NATIVE_EVENT_STREAM_HEALTH_KEY)
                        if not business_stream_fresh and _is_a_share_realtime_window(now):
                            if (
                                self._last_stale_reconnect_at is None
                                or (now - self._last_stale_reconnect_at).total_seconds() >= 90
                            ):
                                self._last_stale_reconnect_at = now
                                # Do not tear down the shared multiplexed transport.
                                # Futures, ETF, gold and US one-shot commands use the
                                # same gateway session; reconnecting here used to fail
                                # every pending command and exhaust all worker slots.
                                # Refresh only the two stale business feeds in place.
                                if (
                                    self._business_refresh_task is None
                                    or self._business_refresh_task.done()
                                ):
                                    logger.warning(
                                        "THS business push stale during A-share session; "
                                        "refreshing affected feeds in place last_events=%s",
                                        {
                                            key: value.isoformat()
                                            for key, value in self._last_event_at.items()
                                            if key in {"cn_indices", "market_temperature"}
                                        },
                                    )
                                    self._business_refresh_task = asyncio.create_task(
                                        self._refresh_stale_business_feeds(),
                                        name="ths-stale-business-feed-refresh",
                                    )
                        await self._refresh_stale_us_sector_feeds(now)
                    else:
                        await client.delete(THS_NATIVE_STREAM_HEALTH_KEY)
                        await client.delete(THS_NATIVE_EVENT_STREAM_HEALTH_KEY)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Redis health storage is auxiliary.  A Redis restart must
                    # not cancel the TaskGroup and tear down the stateful App
                    # subscription transport.  Recreate the Redis client and
                    # keep the native reader/session alive.
                    logger.exception(
                        "THS realtime health Redis operation failed; reconnecting"
                    )
                    with contextlib.suppress(Exception):
                        await client.aclose()
                    client = async_redis.from_url(
                        REDIS_URL,
                        decode_responses=True,
                    )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5)
                except TimeoutError:
                    pass
        finally:
            await client.aclose()

    def _business_stream_is_fresh(self, now: datetime) -> bool:
        if not _is_a_share_realtime_window(now):
            return True
        required = (
            self._last_event_at.get("cn_indices"),
            self._last_event_at.get("market_temperature"),
        )
        return all(
            last_event is not None
            and (now - last_event).total_seconds() <= 90
            for last_event in required
        )

    async def _refresh_stale_us_sector_feeds(self, now: datetime) -> None:
        """Refresh stale US sorted tables for the currently visible session."""
        session = _us_ranking_session(now)
        if session not in {"pre_market", "regular", "after_hours"}:
            return
        monitored_ids = [
            f"us_ranking_all_{session}",
            "us_sector_industry",
            "us_sector_concept",
        ]
        stale_ids = [
            subscription_id
            for subscription_id in monitored_ids
            if self._last_event_at.get(subscription_id) is None
            or (now - self._last_event_at[subscription_id]).total_seconds() > 60
        ]
        if not stale_ids or (
            self._last_us_sector_refresh_at is not None
            and (now - self._last_us_sector_refresh_at).total_seconds() < 60
        ):
            return
        self._last_us_sector_refresh_at = now
        for subscription_id in stale_ids:
            try:
                await self._client.refresh_subscription(subscription_id)
            except Exception:
                logger.exception(
                    "Failed to refresh stale US sector subscription id=%s",
                    subscription_id,
                )

    async def _refresh_stale_business_feeds(self) -> None:
        """Refresh stale A-share feeds without aborting unrelated commands."""
        emitted_at = int(datetime.now(timezone.utc).timestamp() * 1000)
        try:
            response = await self._client.request(
                route="unified",
                payload={
                    "onlineId": CN_INDEX_STREAM_DEFINITION.online_id,
                    "protocolId": CN_INDEX_STREAM_DEFINITION.protocol_id,
                    "pageId": CN_INDEX_STREAM_DEFINITION.page_id,
                    "requestType": 262144,
                    "requestDic": CN_INDEX_STREAM_DEFINITION.request_dic,
                    "cancelRequestDic": CN_INDEX_STREAM_DEFINITION.cancel_request_dic,
                    "timeoutSeconds": 25,
                },
                timeout=45,
            )
            native_response = response.get("response")
            if not isinstance(native_response, dict):
                raise RuntimeError("CN index refresh response is missing")
            await self._enqueue_event({
                "type": "event",
                "topic": "unified",
                "subscription_id": "cn_indices",
                "emitted_at": emitted_at,
                "data": native_response,
            })
        except Exception:
            logger.exception("Failed to refresh stale CN index feed in place")

        definition = self._definitions.get("market_temperature")
        if definition is None:
            return
        try:
            response = await self._client.request(
                route="realtime",
                payload={
                    "key": definition.key,
                    "requestParam": f"{definition.key} data",
                    "requestChannel": f"{definition.key}_channel",
                },
                timeout=45,
            )
            payload = response.get("data", response)
            await self._enqueue_event({
                "type": "event",
                "topic": "realtime",
                "subscription_id": "market_temperature",
                "status": 0,
                "emitted_at": int(datetime.now(timezone.utc).timestamp() * 1000),
                "data": payload,
            })
        except Exception:
            logger.exception("Failed to refresh stale market-temperature feed in place")

    async def _clear_health(self) -> None:
        client = async_redis.from_url(REDIS_URL, decode_responses=True)
        try:
            await client.delete(
                THS_NATIVE_STREAM_HEALTH_KEY,
                THS_NATIVE_EVENT_STREAM_HEALTH_KEY,
            )
        finally:
            await client.aclose()

    async def _load_latest_buckets(self) -> None:
        for definition in REALTIME_SERIES:
            rows = await asyncio.to_thread(
                self._repository.query_history,
                subject_id=definition.subject_id,
                data_type=definition.data_type,
                limit=1,
            )
            self._latest_buckets[definition.indicator] = (
                rows[0].get("bucket_at") if rows else None
            )

    async def _load_latest_futures_hot_definition(
        self,
    ) -> UnifiedEventDefinition | None:
        rows = await asyncio.to_thread(
            self._repository.query_history,
            subject_id="hot",
            data_type="ths_futures_module",
            limit=1,
        )
        native_rows = (
            ((rows[0].get("data") or {}).get("native_table") or {}).get("rows")
            if rows
            else []
        ) or []
        members = [
            (str(row.get("code")), str(row.get("market")))
            for row in native_rows
            if row.get("code") and row.get("market")
        ][:6]
        if not members:
            logger.warning("No THS futures hot membership available for quote push")
            return None
        logger.info("Loaded THS futures hot quote membership members=%s", members)
        return _futures_hot_quotes_definition(members)

    async def _load_cached_us_quote_members(self) -> list[tuple[str, str]]:
        """Restore quote membership without waiting for every table callback.

        Ranking tables are read first so the first quote subscription represents
        the rows users are most likely to see. Per-security snapshots then fill
        the remainder of the previously discovered universe.
        """
        module_rows, quote_rows = await asyncio.gather(
            asyncio.to_thread(
                self._repository.list_latest,
                data_types=["ths_us_market_module"],
                subject_type="us_market",
                limit=100,
            ),
            asyncio.to_thread(
                self._repository.list_latest,
                data_types=["ths_us_security_quote"],
                subject_type="security",
                limit=5000,
            ),
        )
        priority = {
            "ranking_all_stream": 0,
            "ranking_us24hremen_stream": 1,
            "ranking_redianmeigu_stream": 2,
            "ranking_redianetf_stream": 3,
        }
        members: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def append(code: object, market: object) -> None:
            if code in (None, "") or market in (None, ""):
                return
            member = (str(code), str(market))
            if member not in seen:
                seen.add(member)
                members.append(member)

        for row in sorted(
            module_rows,
            key=lambda item: priority.get(str(item.get("subject_id")), 100),
        ):
            native_table = (row.get("data") or {}).get("native_table") or {}
            columns = native_table.get("dataDict") or {}
            if not isinstance(columns, dict):
                continue
            codes = columns.get("4") or []
            markets = columns.get("34338") or columns.get("36103") or []
            for index, code in enumerate(codes):
                append(code, markets[index] if index < len(markets) else None)
        for row in quote_rows:
            data = row.get("data") or {}
            append(data.get("code"), data.get("market_id"))

        self._us_quote_members.update(members)
        logger.info(
            "Loaded cached US security quote membership members=%s priority_members=%s",
            len(members),
            min(len(members), 40),
        )
        return members

    async def _load_cached_gold_stock_definition(
        self,
    ) -> UnifiedEventDefinition | None:
        """Build the App's gold-stock performance query from the latest rec list."""

        rows = await asyncio.to_thread(
            self._repository.list_latest,
            data_types=["ths_gold_module"],
            subject_type="gold_market",
            limit=100,
        )
        opportunity = next(
            (row for row in rows if row.get("subject_id") == "opportunities"),
            None,
        )
        recommendation_response = (
            ((opportunity or {}).get("data") or {}).get("stock_recommendations")
            or {}
        )
        groups: dict[str, list[str]] = {}
        for item in recommendation_response.get("data") or []:
            if not isinstance(item, dict):
                continue
            market = str(item.get("submarket") or "")
            code = str(item.get("code") or "")
            # The App's stock opportunity card is the A-share company list.
            # ``reclist`` also contains HK and BSE members using HTTP-internal
            # negative market IDs; protocol 4106 rejects those IDs and one such
            # member invalidates the entire batch. Keep the native request on
            # its actual Shanghai/Shenzhen scope.
            if market in {"17", "33"} and code:
                groups.setdefault(market, []).append(code)
        if not groups:
            logger.warning("No cached gold stock recommendation membership found")
            return None
        code_list = "".join(
            f"{market}({','.join(groups[market])},);"
            for market in sorted(groups, key=int)
        )
        definition = UnifiedEventDefinition(
            "gold_stock_period_performance",
            "etfpriodrise",
            4106,
            2501,
            (
                f"codelist={code_list}\r\n"
                "dataitem=10,1968584,3475914,3250,3252,33001,33002,33003,35281,4,55,36103\r\n"
                "push=0\r\nscenario=5\r\n"
            ),
            "",
            "data",
        )
        self._event_definitions[definition.subscription_id] = definition
        logger.info(
            "Loaded cached gold stock membership markets=%s stocks=%s",
            len(groups),
            sum(len(codes) for codes in groups.values()),
        )
        return definition

    async def _restore_cached_us_quote_stream(
        self,
        members: list[tuple[str, str]],
    ) -> None:
        # The quote stream owns a separate socket/session. It only needs the
        # primary bridge handshake; the shared activation lock keeps App-global
        # Unified frame initialization serialized with the base subscriptions.
        # ETF 首页的 4104 首帧优先级更高；4154 只美股恢复会长期占用 App
        # 全局 Unified 通道，必须等基础首页订阅完成后再启动。
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=75.0)
            return
        except TimeoutError:
            pass
        await self._client.wait_until_transport_ready()
        if self._stop_event.is_set():
            return
        await self._register_us_security_quote_members(
            members,
            defer_remaining=True,
        )
        await self._client.wait_until_connected()
        if self._stop_event.is_set():
            return
        deferred = self._deferred_us_quote_subscriptions
        self._deferred_us_quote_subscriptions = []
        if deferred:
            await self._client.add_subscriptions(deferred)
            logger.info(
                "Released deferred US security quote streams chunks=%s",
                len(deferred),
            )

    async def _run_gold_client_delayed(self) -> None:
        """Let the primary ETF home subscriptions acquire their first frames first."""

        client = self._gold_client
        if client is None:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=45.0)
            return
        except TimeoutError:
            pass
        await client.run(self._enqueue_event)

    async def _us_ranking_membership_refresh_loop(self) -> None:
        interval = max(
            15.0,
            float(os.getenv("THS_US_RANKING_MEMBERSHIP_REFRESH_SECONDS", "30")),
        )
        await self._client.wait_until_connected()
        delay = max(
            60.0,
            float(os.getenv("THS_US_RANKING_STARTUP_DELAY_SECONDS", "90")),
        )
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
            delay = interval
            if not self._client.dynamic_subscriptions_ready:
                continue
            for session, sort_id in US_RANKING_SESSION_SORT_IDS.items():
                try:
                    response = await self._client.request(
                        route="unified",
                        payload={
                            "onlineId": f"usRankingAllCalibration{session.title()}",
                            "protocolId": 21208,
                            "pageId": 2371,
                            "requestType": 262144,
                            "requestDic": (
                                "startrow=0\r\nrowcount=500\r\nmarketid=60\r\n"
                                f"sortorder=0\r\nsortid={sort_id}"
                            ),
                            "cancelRequestDic": "",
                            "timeoutSeconds": 25,
                        },
                        timeout=45,
                    )
                    if not response.get("success"):
                        raise RuntimeError(str(
                            response.get("error") or "US ranking calibration failed"
                        ))
                    native_response = response.get("response")
                    if not isinstance(native_response, dict):
                        raise RuntimeError("US ranking calibration response is missing")
                    head = native_response.get("head") or {}
                    if isinstance(head, dict) and head.get("errorCode") not in (None, 0):
                        raise RuntimeError(str(
                            head.get("errorMsg") or head.get("errorCode")
                        ))
                    await self._enqueue_event({
                        "type": "event",
                        "topic": "unified",
                        "subscription_id": f"us_ranking_all_{session}",
                        "emitted_at": int(
                            datetime.now(timezone.utc).timestamp() * 1000
                        ),
                        "data": native_response,
                    })
                    logger.info(
                        "Calibrated US all-ranking membership snapshot "
                        "session=%s sort_id=%s",
                        session,
                        sort_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to calibrate US all-ranking membership session=%s",
                        session,
                    )

    async def _etf_home_refresh_loop(self) -> None:
        """Refresh one ETF home category per interval when push membership is quiet."""

        interval = max(
            10.0,
            float(os.getenv("THS_ETF_HOME_REFRESH_SECONDS", "10")),
        )
        await self._client.wait_until_connected()
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=35.0)
            return
        except TimeoutError:
            pass
        categories = ("industry", "index", "t0")
        position = 0
        while not self._stop_event.is_set():
            if self._client.dynamic_subscriptions_ready:
                category = categories[position % len(categories)]
                position += 1
                definition = self._event_definitions[f"etf_home_{category}"]
                try:
                    response = await self._client.request(
                        route="unified",
                        payload={
                            "onlineId": f"{definition.online_id}Calibration",
                            "protocolId": definition.protocol_id,
                            "pageId": definition.page_id,
                            "requestType": 262144,
                            "requestDic": definition.request_dic,
                            "cancelRequestDic": definition.cancel_request_dic,
                            "timeoutSeconds": 15,
                        },
                        timeout=30,
                    )
                    if not response.get("success"):
                        raise RuntimeError(str(
                            response.get("error") or "ETF home calibration failed"
                        ))
                    native_response = response.get("response")
                    if not isinstance(native_response, dict):
                        raise RuntimeError("ETF home calibration response is missing")
                    await self._enqueue_event({
                        "type": "event",
                        "topic": "unified",
                        "subscription_id": definition.subscription_id,
                        "emitted_at": int(
                            datetime.now(timezone.utc).timestamp() * 1000
                        ),
                        "data": native_response,
                    })
                    logger.info("Calibrated ETF home category=%s", category)
                except Exception:
                    logger.exception(
                        "Failed to calibrate ETF home category=%s",
                        category,
                    )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                pass

    async def _gold_stock_refresh_loop(
        self,
        definition: UnifiedEventDefinition,
    ) -> None:
        """Reliable one-shot fallback for protocol 4106, which is not push-native."""

        interval = max(
            60.0,
            float(os.getenv("THS_GOLD_STOCK_REFRESH_SECONDS", "60")),
        )
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=3.0)
            return
        except TimeoutError:
            pass
        while not self._stop_event.is_set():
            try:
                await self._refresh_gold_stock_performance_once(definition)
            except Exception:
                logger.exception("Failed to refresh gold stock performance")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                return
            except TimeoutError:
                pass

    async def _refresh_gold_stock_performance_once(
        self,
        fallback_definition: UnifiedEventDefinition,
    ) -> None:
        """Refresh protocol 4106 using the latest persisted recommendation pool."""

        # Gold-zone HTTP collection can change membership without restarting
        # this process. Rebuild the query every round instead of retaining the
        # startup code list.
        definition = (
            await self._load_cached_gold_stock_definition()
            or fallback_definition
        )
        query_client = THSClient(native_command_stream_enabled=False)
        try:
            response = await query_client._request_native_unified(
                online_id=definition.online_id,
                protocol_id=definition.protocol_id,
                page_id=definition.page_id,
                request_dic=definition.request_dic,
                timeout_seconds=25,
            )
            # Protocol 4106 exposes indicator 34306 (float market value), while
            # the App card labels and displays total market value. The gold
            # concept constituent endpoint exposes both exact THS fields; join
            # total value back by security code before persisting the table.
            try:
                constituents = await query_client.get_native_sector_constituents(
                    "885530",
                    count=100,
                )
                total_values = {
                    str(row.get("security_code") or ""): row.get(
                        "total_market_value"
                    )
                    for row in (
                        (constituents.get("data") or {}).get("constituents")
                        or []
                    )
                    if row.get("security_code")
                }
                columns = (response.get("data") or {}).get("dataDict") or {}
                codes = columns.get("4") or []
                if isinstance(columns, dict) and isinstance(codes, list):
                    columns["total_market_value"] = [
                        total_values.get(str(code)) for code in codes
                    ]
            except Exception:
                logger.exception(
                    "Failed to enrich gold stock total market values"
                )
        finally:
            await query_client.close()
        native_response = {
            "head": response.get("head") or {"errorCode": 0},
            "body": response.get("data") or {},
        }
        if not native_response["body"]:
            raise RuntimeError("gold stock refresh response is empty")
        await self._enqueue_event({
            "type": "event",
            "topic": "unified",
            "subscription_id": definition.subscription_id,
            "emitted_at": int(datetime.now(timezone.utc).timestamp() * 1000),
            "data": native_response,
        })

    async def _enqueue_event(self, event: dict[str, Any]) -> None:
        subscription_id = str(event.get("subscription_id") or "")
        if (
            subscription_id not in self._definitions
            and subscription_id not in self._event_definitions
            and not subscription_id.startswith("us_quote_")
            and not subscription_id.startswith("us_ranking_all_")
        ):
            logger.warning("Unknown THS realtime subscription: %s", subscription_id)
            return
        if event.get("topic") != "unified" and int(event.get("status", -1)) != 0:
            logger.warning("THS realtime event failed: %s", event)
            return
        emitted_at = event.get("emitted_at")
        self._last_event_at[subscription_id] = (
            datetime.fromtimestamp(float(emitted_at) / 1000, tz=timezone.utc)
            if isinstance(emitted_at, (int, float))
            else datetime.now(timezone.utc)
        )
        if subscription_id == "us_etf_config":
            await self._register_us_etf_sector_subscriptions(event)
        elif subscription_id == "gold_futures_contracts":
            await self._register_gold_futures_quote_subscription(event)
        elif (
            subscription_id.startswith("us_ranking_")
        ):
            await self._register_us_security_quote_subscriptions(event)
        await self._queue.put(event)

    async def _register_gold_futures_quote_subscription(
        self,
        event: dict[str, Any],
    ) -> None:
        """Turn the gold contract discovery response into one live quote stream."""

        native_response = event.get("data")
        if not isinstance(native_response, dict):
            return
        decoded = THSClient._decode_native_unified_body(native_response.get("body"))
        columns = decoded.get("dataDict") or {}
        codes = [str(code) for code in columns.get("4") or [] if code]
        if not codes or "gold_futures_quotes" in self._event_definitions:
            return
        definition = UnifiedEventDefinition(
            "gold_futures_quotes",
            "goldFuturesQuotesStream",
            1264,
            9001,
            (
                f"startrow=0\r\nsortid=-1\r\nrowcount={len(codes)}\r\n"
                "newrealtime=0\r\nselfstockcustom=1\r\nupdate=1\r\n"
                "columnorder=55|4|34338|10|34818|34821|13|65\r\n"
                f"marketlist={'|'.join('65' for _ in codes)}\r\n"
                f"stocklist={'|'.join(codes)}\r\npush=1"
            ),
            "",
            "data",
        )
        self._event_definitions[definition.subscription_id] = definition
        added = await self._client.add_subscriptions([definition.subscription()])
        logger.info(
            "Registered gold futures live quote stream added=%s contracts=%s",
            added,
            len(codes),
        )

    async def _register_us_etf_sector_subscriptions(
        self,
        event: dict[str, Any],
    ) -> None:
        native_response = event.get("data")
        if not isinstance(native_response, dict):
            return
        head = native_response.get("head") or {}
        if isinstance(head, dict) and head.get("errorCode") not in (None, 0):
            return
        decoded = THSClient._decode_native_unified_body(native_response.get("body"))
        categories = decoded.get("items") or (
            (decoded.get("data") or {}).get("items") or []
        )
        definitions: list[UnifiedEventDefinition] = []
        for item in categories:
            if not isinstance(item, dict) or not item.get("BlockID"):
                continue
            block_id = str(item["BlockID"])
            subscription_id = f"us_etf_sector_{block_id}"
            definition = UnifiedEventDefinition(
                subscription_id,
                f"usEtfSector{block_id}Stream",
                1360,
                2371,
                (
                    f"stockcode={block_id}\r\nsortid=199112\r\nstartrow=0\r\n"
                    "rowcount=500\r\nsortorder=0\r\n"
                    "columnorder=55|4|34338|10|34818|19\r\npush=1"
                ),
                "",
                "data",
            )
            self._event_definitions[subscription_id] = definition
            definitions.append(definition)
            for member in item.get("Member") or []:
                if not isinstance(member, dict) or not member.get("BlockID"):
                    continue
                member_id = str(member["BlockID"])
                hotspot_id = f"us_etf_hotspot_{member_id}"
                hotspot = UnifiedEventDefinition(
                    hotspot_id,
                    f"usEtfHotspot{member_id}Stream",
                    1360,
                    2371,
                    (
                        f"stockcode={member_id}\r\nsortid=199112\r\n"
                        "startrow=0\r\nrowcount=1\r\nsortorder=0\r\npush=1"
                    ),
                    "",
                    "data",
                )
                self._event_definitions[hotspot_id] = hotspot
                definitions.append(hotspot)
        if definitions:
            added = await self._client.add_subscriptions(
                definition.subscription() for definition in definitions
            )
            logger.info(
                "Registered dynamic US ETF stream subscriptions added=%s total=%s",
                added,
                len(definitions),
            )

    async def _register_us_security_quote_subscriptions(
        self,
        event: dict[str, Any],
    ) -> None:
        native_response = event.get("data")
        if not isinstance(native_response, dict):
            return
        head = native_response.get("head") or {}
        if isinstance(head, dict) and head.get("errorCode") not in (None, 0):
            return
        decoded = THSClient._decode_native_unified_body(native_response.get("body"))
        columns = decoded.get("dataDict") or {}
        if not isinstance(columns, dict):
            return
        codes = columns.get("4") or []
        markets = columns.get("34338") or columns.get("36103") or []
        discovered: list[tuple[str, str]] = []
        for index, code in enumerate(codes):
            if index >= len(markets) or not code or markets[index] in (None, ""):
                continue
            member = (str(code), str(markets[index]))
            if member in self._us_quote_members:
                continue
            self._us_quote_members.add(member)
            discovered.append(member)
        await self._register_us_security_quote_members(discovered)

    async def _register_us_security_quote_members(
        self,
        discovered: list[tuple[str, str]],
        *,
        defer_remaining: bool = False,
    ) -> None:
        subscriptions: list[THSUnifiedSubscription] = []
        for offset in range(0, len(discovered), 40):
            chunk = discovered[offset : offset + 40]
            self._us_quote_subscription_sequence += 1
            sequence = self._us_quote_subscription_sequence
            subscriptions.append(THSUnifiedSubscription(
                subscription_id=f"us_quote_{sequence:04d}",
                online_id=f"usSecurityQuote{sequence:04d}Stream",
                protocol_id=1264,
                page_id=2371,
                request_dic=(
                    f"startrow=0\r\nsortid=-1\r\nrowcount={len(chunk)}\r\n"
                    "newrealtime=0\r\nselfstockcustom=1\r\nupdate=1\r\n"
                    "columnorder=55|4|34338|10|34818|48|19\r\n"
                    f"marketlist={'|'.join(market for _code, market in chunk)}\r\n"
                    f"stocklist={'|'.join(code for code, _market in chunk)}\r\npush=1"
                ),
            ))
        if subscriptions:
            quote_client = self._us_quote_client or self._client
            if defer_remaining:
                first, *remaining = subscriptions
                await quote_client.add_subscriptions([first])
                self._deferred_us_quote_subscriptions.extend(remaining)
            else:
                await quote_client.add_subscriptions(subscriptions)
            logger.info(
                "Registered US security quote streams members=%s chunks=%s total_members=%s",
                len(discovered),
                len(subscriptions),
                len(self._us_quote_members),
            )

    async def _persistence_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            events: list[dict[str, Any]] = []
            try:
                first = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=self._flush_interval,
                )
                events.append(first)
            except TimeoutError:
                continue
            deadline = asyncio.get_running_loop().time() + self._flush_interval
            while len(events) < self._batch_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    events.append(
                        await asyncio.wait_for(self._queue.get(), timeout=remaining)
                    )
                except TimeoutError:
                    break

            snapshots: list[dict[str, Any]] = []
            event_maxima: dict[str, datetime] = {}
            task_counts: dict[str, int] = {}
            task_snapshot_counts: dict[str, int] = {}
            for event in events:
                rows = self._event_to_snapshots(event)
                snapshots.extend(rows)
                indicator = str(event["subscription_id"])
                task_id = _push_task_id(indicator)
                task_counts[task_id] = task_counts.get(task_id, 0) + 1
                task_snapshot_counts[task_id] = task_snapshot_counts.get(task_id, 0) + len(rows)
                if rows:
                    event_maxima[indicator] = max(
                        row["bucket_at"] for row in rows
                    )
            try:
                for task_id, received_count in task_counts.items():
                    await asyncio.to_thread(
                        self._task_states.mark_received,
                        task_id=task_id,
                        aggregator="push",
                        source_name=task_id,
                        received_count=received_count,
                        details={"transport": "ths_native_stream"},
                    )
                if snapshots:
                    saved = await asyncio.to_thread(
                        self._repository.upsert_batch,
                        snapshots,
                    )
                    for indicator, bucket_at in event_maxima.items():
                        current = self._latest_buckets.get(indicator)
                        if current is None or bucket_at > current:
                            self._latest_buckets[indicator] = bucket_at
                    logger.info(
                        "Persisted THS realtime stream events=%s snapshots=%s saved=%s",
                        len(events),
                        len(snapshots),
                        saved,
                    )
                for task_id, received_count in task_counts.items():
                    task_source_times = [
                        bucket_at for indicator, bucket_at in event_maxima.items()
                        if _push_task_id(indicator) == task_id
                    ]
                    await asyncio.to_thread(
                        self._task_states.mark_finished,
                        task_id=task_id,
                        aggregator="push",
                        source_name=task_id,
                        task_type="push",
                        status="success",
                        fetched_count=received_count,
                        saved_count=task_snapshot_counts.get(task_id, 0),
                        source_at=max(task_source_times, default=None),
                        details={"transport": "ths_native_stream"},
                    )
            except Exception as exc:
                for task_id in task_counts:
                    await asyncio.to_thread(
                        self._task_states.mark_finished,
                        task_id=task_id,
                        aggregator="push",
                        source_name=task_id,
                        task_type="push",
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                        details={"transport": "ths_native_stream"},
                    )
                logger.exception(
                    "Failed to persist THS realtime stream batch events=%s snapshots=%s",
                    len(events),
                    len(snapshots),
                )
                raise
            finally:
                for _event in events:
                    self._queue.task_done()

    def _event_to_snapshots(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        indicator = str(event["subscription_id"])
        if indicator in self._event_definitions or indicator.startswith("us_quote_"):
            return self._unified_event_to_snapshots(event)
        definition = self._definitions[indicator]
        payload = event.get("data")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            logger.warning("THS realtime payload is not an object: %s", indicator)
            return []
        points = THSClient._normalize_native_chart_points(payload)
        if definition.latest_point_only and points:
            points = points[-1:]
        emitted_at = event.get("emitted_at")
        if isinstance(emitted_at, (int, float)):
            fetched_at = datetime.fromtimestamp(
                float(emitted_at) / 1000,
                tz=timezone.utc,
            )
        else:
            fetched_at = datetime.now(timezone.utc)
        response = {
            "provider": "ths_native",
            "market": definition.market,
            "fetched_at": fetched_at,
            "data": {
                "indicator": indicator,
                "indicator_key": definition.key,
                "name": payload.get("name"),
                "points": points,
                "lines": payload.get("lines") or [],
                "summary": payload.get("summary") or {},
            },
        }
        rows = _native_chart_snapshots(
            response=response,
            data_type=definition.data_type,
            subject_type=definition.subject_type,
            subject_id=definition.subject_id,
            latest_bucket_at=self._latest_buckets.get(indicator),
        )
        for row in rows:
            row["data"]["response_type"] = event.get("response_type")
            row["data"]["stream_sequence"] = event.get("sequence")
        return rows

    def _unified_event_to_snapshots(
        self,
        event: dict[str, Any],
    ) -> list[dict[str, Any]]:
        subscription_id = str(event["subscription_id"])
        definition = self._event_definitions.get(subscription_id)
        native_response = event.get("data")
        if not isinstance(native_response, dict):
            logger.warning(
                "THS unified stream payload is not an object: %s",
                subscription_id,
            )
            return []
        head = native_response.get("head") or {}
        if isinstance(head, dict) and head.get("errorCode") not in (None, 0):
            logger.warning(
                "THS unified stream response failed subscription=%s head=%s",
                subscription_id,
                head,
            )
            return []
        decoded = THSClient._decode_native_unified_body(native_response.get("body"))
        emitted_at = event.get("emitted_at")
        fetched_at = (
            datetime.fromtimestamp(float(emitted_at) / 1000, tz=timezone.utc)
            if isinstance(emitted_at, (int, float))
            else datetime.now(timezone.utc)
        )
        if subscription_id in {"cn_indices", "cn_market_breadth"}:
            session = self._china_exchange_calendar.resolve(fetched_at)
            if not session.is_trading_day:
                logger.debug(
                    "Ignore repeated A-share push outside a trading day "
                    "subscription=%s fetched_at=%s latest_trade_date=%s",
                    subscription_id,
                    fetched_at.isoformat(),
                    session.trade_date.isoformat(),
                )
                return []
        if subscription_id.startswith("etf_home_"):
            category = subscription_id.removeprefix("etf_home_")
            rows = THSClient._native_etf_home_rows({"data": decoded})
            if not rows:
                logger.warning(
                    "THS ETF home stream returned no rows category=%s",
                    category,
                )
                return []
            response = {
                "provider": "ths_native_stream",
                "market": "cn",
                "fetched_at": fetched_at,
                "source_time": fetched_at.isoformat(),
                "trade_date": fetched_at.astimezone(CN_TIMEZONE).date(),
                "timezone": "Asia/Shanghai",
                "provider_metadata": {
                    "source_component": f"ETFZone/{category} push",
                    "stream_sequence": event.get("sequence"),
                    "complete": len(rows) >= 6,
                    "runs_outside_a_share_hours": True,
                    "sort_fields": ["33001", "48", "19"],
                },
                "data": {
                    "category": category,
                    "count": len(rows),
                    "rows": rows,
                    "field_ids": ["33001", "48", "19", "34307"],
                    "native_table": decoded,
                    "stream_sequence": event.get("sequence"),
                },
            }
            return [_snapshot_from_response(
                response=response,
                data_type="ths_etf_home_ranking",
                subject_type="etf_market",
                subject_id=category,
                bucket_seconds=5,
            )]
        if subscription_id == "futures_hot_quotes":
            columns = decoded.get("dataDict") or {}
            if not isinstance(columns, dict) or not (columns.get("4") or []):
                logger.warning("THS futures hot quote stream returned no rows")
                return []
            response = {
                "provider": "ths_native_stream",
                "market": "cn",
                "fetched_at": fetched_at,
                "source_time": fetched_at.isoformat(),
                "trade_date": fetched_at.astimezone(CN_TIMEZONE).date(),
                "timezone": "Asia/Shanghai",
                "provider_metadata": {
                    "source_component": "FuturesSynthesis/hot quotes push",
                    "stream_sequence": event.get("sequence"),
                    "complete": len(columns.get("4") or []) == 6,
                    "runs_outside_a_share_hours": True,
                },
                "data": {
                    "kind": "hot_quotes_stream",
                    "group": None,
                    "native_table": decoded,
                    "stream_sequence": event.get("sequence"),
                },
            }
            return [_snapshot_from_response(
                response=response,
                data_type="ths_futures_module",
                subject_type="futures_market",
                subject_id="hot_quotes_stream",
                bucket_seconds=5,
            )]
        if subscription_id == "cn_indices":
            columns = decoded.get("dataDict") or {}
            if not isinstance(columns, dict):
                return []
            codes = columns.get("4") or []
            snapshots: list[dict[str, Any]] = []
            quote_observed_at = _a_share_quote_observed_at(fetched_at)
            for index, native_code in enumerate(codes):
                def cell(field: str) -> object:
                    values = columns.get(field) or []
                    return values[index] if index < len(values) else None

                native_code = str(native_code)
                canonical_code = CN_INDEX_CANONICAL_CODES.get(native_code)
                if native_code == "883957":
                    response = {
                        "provider": "ths_native_stream",
                        "market": "cn",
                        "fetched_at": fetched_at,
                        "observed_at": quote_observed_at,
                        "source_time": quote_observed_at.isoformat(),
                        "trade_date": fetched_at.astimezone(CN_TIMEZONE).date(),
                        "timezone": "Asia/Shanghai",
                        "provider_metadata": {
                            "source_component": "THS all-A quote push",
                            "stream_sequence": event.get("sequence"),
                            "subscription_id": subscription_id,
                        },
                        "data": {
                            "code": native_code,
                            "name": cell("55"),
                            "turnover": _native_amount_to_yuan(cell("19")),
                            "turnover_text": cell("19"),
                            "turnover_unit": "yuan",
                            "source_time": quote_observed_at.astimezone(
                                CN_TIMEZONE
                            ).isoformat(),
                            "stream_sequence": event.get("sequence"),
                        },
                    }
                    snapshots.append(_snapshot_from_response(
                        response=response,
                        data_type="ths_cn_market_summary",
                        subject_type="market",
                        subject_id="cn:a_share:ths_all_a",
                        bucket_seconds=1,
                    ))
                    continue
                if canonical_code is None:
                    continue
                response = {
                    "provider": "ths_native_stream",
                    "market": "cn",
                    "fetched_at": fetched_at,
                    "observed_at": quote_observed_at,
                    "source_time": quote_observed_at.isoformat(),
                    "trade_date": fetched_at.astimezone(CN_TIMEZONE).date(),
                    "timezone": "Asia/Shanghai",
                    "provider_metadata": {
                        "source_component": "A-share index quote push",
                        "stream_sequence": event.get("sequence"),
                        "subscription_id": subscription_id,
                    },
                    "data": {
                        "code": canonical_code,
                        "native_code": native_code,
                        "market_id": str(cell("34338") or ""),
                        "name": cell("55"),
                        "close": _native_float(cell("10")),
                        "change_percent": _native_float(cell("34818")),
                        "speed": _native_float(cell("48")),
                        "turnover": _native_amount_to_yuan(cell("19")),
                        "turnover_text": cell("13"),
                        "source_time": quote_observed_at.astimezone(
                            CN_TIMEZONE
                        ).isoformat(),
                        "stream_sequence": event.get("sequence"),
                    },
                }
                snapshots.append(_snapshot_from_response(
                    response=response,
                    data_type="ths_cn_index_quote",
                    subject_type="index",
                    subject_id=f"cn:index:{canonical_code}",
                    bucket_seconds=1,
                ))
            return snapshots
        if subscription_id == "cn_market_breadth":
            entries = decoded.get("hs_datacenter_ztdt") or []
            if not isinstance(entries, list) or not entries:
                return []
            raw_value = entries[0].get("value") if isinstance(entries[0], dict) else None
            if isinstance(raw_value, str):
                try:
                    payload = json.loads(raw_value)
                except json.JSONDecodeError:
                    return []
            elif isinstance(raw_value, dict):
                payload = raw_value
            else:
                return []
            aggregate = payload.get("all") or {}
            totals = aggregate.get("total") or {}
            distribution = aggregate.get("data") or {}
            if not isinstance(totals, dict):
                return []
            breadth_observed_at = _a_share_quote_observed_at(fetched_at)
            response = {
                "provider": "ths_native_stream",
                "market": "cn",
                "fetched_at": fetched_at,
                # The upstream can repeat its closing payload long after 15:00.
                # Receiving that heartbeat does not make the market data newer.
                "observed_at": breadth_observed_at,
                "source_time": breadth_observed_at.isoformat(),
                "trade_date": fetched_at.astimezone(CN_TIMEZONE).date(),
                "timezone": "Asia/Shanghai",
                "provider_metadata": {
                    "source_component": "hs_datacenter_ztdt push",
                    "protocol_id": 1002,
                    "stream_sequence": event.get("sequence"),
                },
                "data": {
                    "up_count": int(totals.get("up") or 0),
                    "down_count": int(totals.get("down") or 0),
                    "flat_count": int(totals.get("deuce") or 0),
                    "limit_up_count": int(distribution.get("zt") or 0),
                    "limit_down_count": int(distribution.get("dt") or 0),
                    "distribution": distribution,
                    "times": payload.get("time") or [],
                    "limit_up_series": payload.get("zt") or [],
                    "limit_down_series": payload.get("dt") or [],
                    "stream_sequence": event.get("sequence"),
                },
            }
            return [_snapshot_from_response(
                response=response,
                data_type="ths_cn_market_breadth",
                subject_type="market",
                subject_id="cn:a_share:ths_breadth",
                bucket_seconds=1,
            )]
        if subscription_id.startswith("us_quote_"):
            columns = decoded.get("dataDict") or {}
            if not isinstance(columns, dict):
                return []
            codes = columns.get("4") or []
            snapshots: list[dict[str, Any]] = []
            for index, code in enumerate(codes):
                def cell(field: str) -> object:
                    values = columns.get(field) or []
                    return values[index] if index < len(values) else None

                market_id = str(cell("34338") or "")
                response = {
                    "provider": "ths_native_stream",
                    "market": "us",
                    "fetched_at": fetched_at,
                    "source_time": fetched_at.isoformat(),
                    "trade_date": fetched_at.astimezone(
                        ZoneInfo("America/New_York")
                    ).date(),
                    "timezone": "America/New_York",
                    "data": {
                        "code": str(code),
                        "market_id": market_id,
                        "name": cell("55"),
                        "latest": _native_float(cell("10")),
                        "change_rate": _native_float(cell("34818")),
                        "speed": _native_float(cell("48")),
                        "stream_sequence": event.get("sequence"),
                    },
                }
                snapshots.append(_snapshot_from_response(
                    response=response,
                    data_type="ths_us_security_quote",
                    subject_type="security",
                    subject_id=f"us:{market_id}:{code}",
                    bucket_seconds=1,
                ))
            return snapshots
        if (
            subscription_id == "us_indices"
            or subscription_id.startswith("us_sector_")
            or subscription_id.startswith("us_ranking_")
            or subscription_id == "us_etf_config"
            or subscription_id.startswith("us_etf_sector_")
            or subscription_id.startswith("us_etf_hotspot_")
        ):
            subject_id = (
                "indices_stream"
                if subscription_id == "us_indices"
                else (
                    f"{subscription_id.removeprefix('us_sector_')}_current_stream"
                    if subscription_id.startswith("us_sector_")
                    else (
                        f"ranking_{subscription_id.removeprefix('us_ranking_')}_stream"
                        if subscription_id.startswith("us_ranking_")
                        else (
                            "etf_config_stream"
                            if subscription_id == "us_etf_config"
                            else (
                                f"etf_sector_{subscription_id.removeprefix('us_etf_sector_')}_stream"
                                if subscription_id.startswith("us_etf_sector_")
                                else f"etf_hotspot_{subscription_id.removeprefix('us_etf_hotspot_')}_stream"
                            )
                        )
                    )
                )
            )
            response = {
                "provider": "ths_native_stream",
                "market": "us",
                "fetched_at": fetched_at,
                "source_time": fetched_at.isoformat(),
                "trade_date": fetched_at.astimezone(
                    ZoneInfo("America/New_York")
                ).date(),
                "timezone": "America/New_York",
                "provider_metadata": {
                    "source_component": f"US market home/{subject_id} push",
                    "stream_sequence": event.get("sequence"),
                    "complete": True,
                    "ranking_session": _us_ranking_session(),
                    "ranking_sort_id": _us_ranking_sort_id(),
                },
                "data": {
                    "native_table": decoded,
                    "fields": (
                        ["55", "4", "36103", "34821", "10", "34818"]
                        if subscription_id == "us_indices"
                        else ([
                            "55", "4", "34313", "36103", "35284",
                            "34376", "34377", "34849", "34850", "35279",
                            "35286",
                        ] if subscription_id.startswith("us_sector_") else ([
                            "10", "34818", "34387", "36065", "36066",
                            "34868", "34869", "34312", "48", "13", "19", "34304", "34305",
                            "34307", "55", "4", "36103", "34393",
                        ] if subscription_id.startswith("us_ranking_") else [
                            "10", "4", "36103", "55", "34338", "34818",
                        ]))
                    ),
                    "stream_sequence": event.get("sequence"),
                },
            }
            return [
                _snapshot_from_response(
                    response=response,
                    data_type="ths_us_market_module",
                    subject_type="us_market",
                    subject_id=subject_id,
                    bucket_seconds=5,
                )
            ]
        if subscription_id in {
            "gold_futures_contracts",
            "gold_futures_quotes",
            "gold_stock_period_performance",
        }:
            subject_id = (
                "futures_contracts_stream"
                if subscription_id == "gold_futures_contracts"
                else (
                    "futures_quotes_stream"
                    if subscription_id == "gold_futures_quotes"
                    else "stock_period_performance_stream"
                )
            )
            response = {
                "provider": "ths_native_stream",
                "market": "cn",
                "fetched_at": fetched_at,
                "source_time": fetched_at.isoformat(),
                "trade_date": fetched_at.astimezone(CN_TIMEZONE).date(),
                "timezone": "Asia/Shanghai",
                "provider_metadata": {
                    "source_component": "goldZone/futures push",
                    "stream_sequence": event.get("sequence"),
                    "complete": True,
                    "runs_outside_a_share_hours": True,
                },
                "data": {
                    "native_table": decoded,
                    "fields": (
                        ["55", "4", "10", "1968584", "3475914", "3250", "3252", "33001", "33002", "33003", "35281", "36103"]
                        if subscription_id == "gold_stock_period_performance"
                        else ["55", "4", "10", "34818", "34821", "13", "65"]
                    ),
                    "stream_sequence": event.get("sequence"),
                },
            }
            return [
                _snapshot_from_response(
                    response=response,
                    data_type="ths_gold_module",
                    subject_type="gold_market",
                    subject_id=subject_id,
                    bucket_seconds=5,
                )
            ]
        trade_date = fetched_at.astimezone(CN_TIMEZONE).date()
        if definition is None:
            logger.warning("Unknown THS unified stream definition: %s", subscription_id)
            return []
        if self._market_anomaly_trade_date != trade_date:
            self._market_anomaly_trade_date = trade_date
            for key in self._market_anomaly_state:
                self._market_anomaly_state[key] = []

        if subscription_id == "market_anomaly_curve":
            content = decoded.get("content") or {}
            if not isinstance(content, dict):
                return []
            index_values = content.get("10") or []
            turnover_values = content.get("19") or []
            time_keys = content.get("1") or []
            ext_data = decoded.get("extDataDict") or decoded.get("exDataDict") or {}
            if isinstance(ext_data, str):
                ext_data = json.loads(ext_data)
            center = _native_float(ext_data.get("6"))
            low = _native_float(ext_data.get("8"))
            high = _native_float(ext_data.get("9"))
            radius = (
                max(abs(high - center), abs(low - center))
                if center is not None and low is not None and high is not None
                else None
            )
            self._market_anomaly_axis = {
                "center": center,
                "min": center - radius if radius is not None else None,
                "max": center + radius if radius is not None else None,
                "percent_min": -radius / center * 100 if radius and center else None,
                "percent_max": radius / center * 100 if radius and center else None,
            }
            self._market_anomaly_state["curve"] = [
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
            return [self._market_anomaly_snapshot(event, fetched_at)]

        rows = decoded.get(definition.source_key) or []
        if definition.source_key == "dxjl":
            rows = rows or decoded.get("dxjl_free") or []
        if not isinstance(rows, list):
            return []
        if subscription_id == "market_events":
            rows = THSClient._normalize_market_anomaly_labels(rows)
        self._market_anomaly_state[subscription_id] = rows
        response = {
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": fetched_at,
            "data": {definition.subscription_id: rows},
        }
        if subscription_id == "market_events":
            return [self._market_anomaly_snapshot(event, fetched_at)]
        return [
            *_ths_event_snapshots(response),
            self._market_anomaly_snapshot(event, fetched_at),
        ]

    def _market_anomaly_snapshot(
        self,
        event: dict[str, Any],
        fetched_at: datetime,
    ) -> dict[str, Any]:
        state = self._market_anomaly_state
        response = {
            "provider": "ths_native",
            "market": "cn",
            "fetched_at": fetched_at,
            "data": {
                "count": sum(
                    len(state[key])
                    for key in (
                        "market_events",
                        "stock_events",
                        "sector_events",
                        "large_order_events",
                    )
                ),
                "market_events": state["market_events"],
                "curve": state["curve"],
                "axis": self._market_anomaly_axis,
                "stock_events": state["stock_events"][-2:],
                "sector_events": state["sector_events"][-2:],
                "large_order_events": state["large_order_events"][-3:],
                "event_counts": {
                    key: len(state[key])
                    for key in (
                        "stock_events",
                        "sector_events",
                        "large_order_events",
                    )
                },
                "stream_sequence": event.get("sequence"),
                "stream_subscription_id": event.get("subscription_id"),
            },
        }
        return _snapshot_from_response(
            response=response,
            data_type="market_anomaly",
            subject_type="market",
            subject_id="cn:a_share:ths_anomaly",
            bucket_seconds=30,
        )
