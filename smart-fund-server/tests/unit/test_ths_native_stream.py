import asyncio
import json
from datetime import datetime

import pytest

from src.application.services.ths_realtime_stream_service import (
    CN_INDEX_STREAM_DEFINITION,
    CN_MARKET_BREADTH_STREAM_DEFINITION,
    THSRealtimeStreamService,
    UnifiedEventDefinition,
    _a_share_quote_observed_at,
    _is_a_share_realtime_window,
)
from src.infrastructure.clients.ths_native_stream import (
    THSNativeCommandClient,
    THSNativeRealtimeStreamClient,
    THSRealtimeSubscription,
    THSUnifiedSubscription,
)


def _subscriptions() -> tuple[THSRealtimeSubscription, ...]:
    return (
        THSRealtimeSubscription("one", "key_one", "key_one data", "key_one_channel"),
        THSRealtimeSubscription("two", "key_two", "key_two data", "key_two_channel"),
    )


def test_cn_index_push_definition_and_snapshot_mapping() -> None:
    assert CN_INDEX_STREAM_DEFINITION.protocol_id == 1264
    assert "rowcount=14" in CN_INDEX_STREAM_DEFINITION.request_dic
    assert "push=1" in CN_INDEX_STREAM_DEFINITION.request_dic
    service = THSRealtimeStreamService(repository=object())
    rows = service._unified_event_to_snapshots({
        "subscription_id": "cn_indices",
        "sequence": 17,
        "emitted_at": 1786073418000,
        "data": {
            "head": {"errorCode": 0},
            "body": {"dataDict": {
                "4": ["1A0001", "399001", "883957"],
                "34338": ["16", "32", "48"],
                "55": ["上证指数", "深证成指", "同花顺全A(沪深京)"],
                "10": ["3919.51", "14295.08", "1837.553"],
                "34818": ["0.49%", "1.31%", "0.31%"],
                "48": ["0.01%", "0.03%", "0%"],
                "13": ["3.64亿", "4.70亿", "8.40亿"],
                "19": ["3.64亿", "4.70亿", "16887亿"],
            }},
        },
    })

    assert [row["subject_id"] for row in rows] == [
        "cn:index:000001", "cn:index:399001", "cn:a_share:ths_all_a",
    ]
    assert rows[0]["data_type"] == "ths_cn_index_quote"
    assert rows[0]["data"]["name"] == "上证指数"
    assert rows[0]["data"]["close"] == 3919.51
    assert rows[0]["data"]["change_percent"] == 0.49
    assert rows[0]["observed_at"].isoformat() == "2026-08-07T03:30:00+00:00"
    assert rows[2]["data_type"] == "ths_cn_market_summary"
    assert rows[2]["data"]["turnover"] == 1_688_700_000_000.0


def test_a_share_push_health_window_excludes_lunch_break() -> None:
    assert _is_a_share_realtime_window(
        datetime.fromisoformat("2026-08-07T05:15:00+00:00")
    )
    assert not _is_a_share_realtime_window(
        datetime.fromisoformat("2026-08-07T04:00:00+00:00")
    )
    assert _a_share_quote_observed_at(
        datetime.fromisoformat("2026-08-07T04:57:32+00:00")
    ).isoformat() == "2026-08-07T03:30:00+00:00"

    service = THSRealtimeStreamService(repository=object())
    active_now = datetime.fromisoformat("2026-08-07T05:15:00+00:00")
    service._last_event_at = {
        "cn_indices": active_now,
        "market_temperature": active_now,
    }
    assert service._business_stream_is_fresh(active_now)
    service._last_event_at["cn_indices"] = datetime.fromisoformat(
        "2026-08-07T05:13:00+00:00"
    )
    assert not service._business_stream_is_fresh(active_now)


def test_cn_market_breadth_push_definition_and_snapshot_mapping() -> None:
    assert CN_MARKET_BREADTH_STREAM_DEFINITION.protocol_id == 1002
    assert "key=hs_datacenter_ztdt" in (
        CN_MARKET_BREADTH_STREAM_DEFINITION.request_dic
    )
    service = THSRealtimeStreamService(repository=object())
    rows = service._unified_event_to_snapshots({
        "subscription_id": "cn_market_breadth",
        "sequence": 18,
        "emitted_at": 1786076340000,
        "data": {
            "head": {"errorCode": 0},
            "body": {
                "hs_datacenter_ztdt": [{
                    "stockcode": "1A0001",
                    "value": json.dumps({
                        "time": ["11:29", "11:30"],
                        "zt": [58, 60],
                        "dt": [5, 4],
                        "all": {
                            "data": {"zt": 60, "dt": 4, "z03": 1795},
                            "total": {"up": 2353, "down": 3046, "deuce": 136},
                        },
                    }),
                }],
            },
        },
    })

    assert len(rows) == 1
    assert rows[0]["data_type"] == "ths_cn_market_breadth"
    assert rows[0]["data"]["up_count"] == 2353
    assert rows[0]["data"]["down_count"] == 3046
    assert rows[0]["data"]["flat_count"] == 136
    assert rows[0]["data"]["limit_up_count"] == 60
    assert rows[0]["data"]["limit_down_count"] == 4


@pytest.mark.asyncio
async def test_command_broker_serializes_same_native_interface() -> None:
    service = THSRealtimeStreamService(repository=object())
    active = 0
    max_active = 0

    class NativeClient:
        async def request(self, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"route": kwargs["route"]}

    class Writer:
        def __init__(self) -> None:
            self.rows: list[bytes] = []

        def is_closing(self) -> bool:
            return False

        def write(self, value: bytes) -> None:
            self.rows.append(value)

        async def drain(self) -> None:
            return None

    service._client = NativeClient()
    writer = Writer()
    write_lock = asyncio.Lock()
    await asyncio.gather(
        service._execute_command(
            json.dumps({
                "request_id": "one", "route": "unified",
                "payload": {"onlineId": "one"}, "timeout_seconds": 5,
            }).encode(),
            writer,
            write_lock,
        ),
        service._execute_command(
            json.dumps({
                "request_id": "two", "route": "unified",
                "payload": {"onlineId": "two"}, "timeout_seconds": 5,
            }).encode(),
            writer,
            write_lock,
        ),
    )

    assert max_active == 1
    assert len(writer.rows) == 2


@pytest.mark.asyncio
async def test_command_broker_runs_distinct_native_interfaces_concurrently() -> None:
    service = THSRealtimeStreamService(repository=object())
    both_active = asyncio.Event()
    active = 0

    class NativeClient:
        async def request(self, **kwargs):
            nonlocal active
            active += 1
            if active == 2:
                both_active.set()
            await asyncio.wait_for(both_active.wait(), timeout=0.2)
            active -= 1
            return {"route": kwargs["route"]}

    class Writer:
        def is_closing(self) -> bool:
            return False

        def write(self, value: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

    service._client = NativeClient()
    writer = Writer()
    write_lock = asyncio.Lock()
    await asyncio.gather(
        service._execute_command(
            json.dumps({
                "request_id": "flow", "route": "unified",
                "payload": {"protocolId": 4066, "pageId": 2405},
            }).encode(), writer, write_lock,
        ),
        service._execute_command(
            json.dumps({
                "request_id": "state", "route": "unified",
                "payload": {"protocolId": 4051, "pageId": 2405},
            }).encode(), writer, write_lock,
        ),
    )

    assert both_active.is_set()


def test_unified_subscription_command_preserves_native_request_contract() -> None:
    subscription = THSUnifiedSubscription(
        subscription_id="stock_events",
        online_id="stockEventStream",
        protocol_id=1004,
        page_id=6002,
        request_dic="action=subscribe\r\nkey=dxjl_free",
        cancel_request_dic="action=unsubscribe\r\nkey=dxjl_free",
    )

    assert subscription.command() == {
        "op": "subscribe",
        "kind": "unified",
        "subscription_id": "stock_events",
        "online_id": "stockEventStream",
        "protocol_id": 1004,
        "page_id": 6002,
        "request_type": 262144,
        "request_dic": "action=subscribe\r\nkey=dxjl_free",
        "cancel_request_dic": "action=unsubscribe\r\nkey=dxjl_free",
    }


@pytest.mark.asyncio
async def test_us_etf_config_registers_dynamic_push_subscriptions() -> None:
    service = THSRealtimeStreamService(repository=object())
    registered: list[THSUnifiedSubscription] = []

    async def add_subscriptions(subscriptions) -> int:
        values = list(subscriptions)
        registered.extend(values)
        return len(values)

    service._client.add_subscriptions = add_subscriptions
    await service._register_us_etf_sector_subscriptions({
        "data": {
            "head": {"errorCode": 0},
            "body": {"items": [
                {"BlockID": "D2F6"},
                {"BlockID": "D30B"},
            ]},
        },
    })

    assert [item.subscription_id for item in registered] == [
        "us_etf_sector_D2F6",
        "us_etf_sector_D30B",
    ]
    assert all("push=1" in item.request_dic for item in registered)
    assert "us_etf_sector_D2F6" in service._event_definitions


@pytest.mark.asyncio
async def test_us_table_members_register_deduplicated_quote_push_chunks() -> None:
    service = THSRealtimeStreamService(repository=object())
    registered: list[THSUnifiedSubscription] = []

    async def add_subscriptions(subscriptions) -> int:
        values = list(subscriptions)
        registered.extend(values)
        return len(values)

    class FakeQuoteClient:
        pass

    fake_quote_client = FakeQuoteClient()
    fake_quote_client.add_subscriptions = add_subscriptions
    service._us_quote_client = fake_quote_client
    event = {
        "data": {
            "head": {"errorCode": 0},
            "body": {"dataDict": {
                "4": ["NVDA", "AAPL", "MSFT"],
                "34338": ["185", "185", "185"],
            }},
        },
    }
    await service._register_us_security_quote_subscriptions(event)
    await service._register_us_security_quote_subscriptions(event)

    assert len(registered) == 1
    assert registered[0].protocol_id == 1264
    assert "stocklist=NVDA|AAPL|MSFT" in registered[0].request_dic
    assert "push=1" in registered[0].request_dic


@pytest.mark.asyncio
async def test_cached_us_quote_members_prioritize_visible_ranking_rows() -> None:
    class Repository:
        def list_latest(self, *, data_types, subject_type, limit):
            if data_types == ["ths_us_market_module"]:
                return [{
                    "subject_id": "ranking_all_stream",
                    "data": {"native_table": {"dataDict": {
                        "4": ["NVDA", "AAPL"],
                        "36103": ["185", "185"],
                    }}},
                }]
            return [{
                "data": {"code": "MSFT", "market_id": "185"},
            }, {
                "data": {"code": "NVDA", "market_id": "185"},
            }]

    service = THSRealtimeStreamService(repository=Repository())
    members = await service._load_cached_us_quote_members()

    assert members == [
        ("NVDA", "185"),
        ("AAPL", "185"),
        ("MSFT", "185"),
    ]
    assert service._us_quote_members == set(members)


def test_us_quote_push_creates_per_security_snapshots() -> None:
    service = THSRealtimeStreamService(repository=object())
    rows = service._unified_event_to_snapshots({
        "subscription_id": "us_quote_0001",
        "sequence": 9,
        "emitted_at": 1785906000000,
        "data": {
            "head": {"errorCode": 0},
            "body": {"dataDict": {
                "4": ["NVDA", "AAPL"],
                "55": ["英伟达", "苹果"],
                "34338": ["185", "185"],
                "10": ["211.94", "309.38"],
                "34818": ["2.56%", "1.96%"],
                "48": ["-0.12%", "0.06%"],
            }},
        },
    })

    assert [row["subject_id"] for row in rows] == [
        "us:185:NVDA",
        "us:185:AAPL",
    ]
    assert rows[0]["data"]["latest"] == 211.94
    assert rows[0]["data"]["change_rate"] == 2.56


def test_gold_futures_push_creates_gold_module_snapshot() -> None:
    service = THSRealtimeStreamService(repository=object())
    rows = service._unified_event_to_snapshots({
        "subscription_id": "gold_futures_contracts",
        "sequence": 10,
        "emitted_at": 1785906000000,
        "data": {
            "head": {"errorCode": 0},
            "body": {"dataDict": {
                "4": ["au2608"],
                "55": ["沪金2608"],
                "10": ["904.50"],
                "34818": ["2.98%"],
                "65": ["3810"],
            }},
        },
    })

    assert len(rows) == 1
    assert rows[0]["data_type"] == "ths_gold_module"
    assert rows[0]["subject_id"] == "futures_contracts_stream"
    assert rows[0]["data"]["native_table"]["dataDict"]["55"] == ["沪金2608"]


def test_etf_home_push_creates_sortable_native_ranking_snapshot() -> None:
    service = THSRealtimeStreamService(repository=object())
    rows = service._unified_event_to_snapshots({
        "subscription_id": "etf_home_industry",
        "sequence": 11,
        "emitted_at": 1785906000000,
        "data": {
            "head": {"errorCode": 0},
            "body": {"dataDict": {
                "4": ["588200"],
                "55": ["科创芯片ETF"],
                "34338": ["17"],
                "33001": ["+6.76%"],
                "48": ["-0.12%"],
                "19": ["18.35亿"],
                "34307": ["245.08亿"],
            }},
        },
    })

    assert len(rows) == 1
    assert rows[0]["data_type"] == "ths_etf_home_ranking"
    assert rows[0]["subject_id"] == "industry"
    assert rows[0]["data"]["rows"][0]["code"] == "588200"
    assert rows[0]["data"]["rows"][0]["scale_yuan"] == 24_508_000_000.0


@pytest.mark.asyncio
async def test_cached_gold_stock_members_build_app_performance_query() -> None:
    class Repository:
        def list_latest(self, *, data_types, subject_type, limit):
            assert data_types == ["ths_gold_module"]
            assert subject_type == "gold_market"
            return [{
                "subject_id": "opportunities",
                "data": {"stock_recommendations": {"data": [
                    {"code": "600489", "submarket": "17"},
                    {"code": "000975", "submarket": "33"},
                    {"code": "920038", "submarket": "-105"},
                    {"code": "HK1818", "submarket": "-79"},
                ]}},
            }]

    service = THSRealtimeStreamService(repository=Repository())
    definition = await service._load_cached_gold_stock_definition()

    assert definition is not None
    assert definition.protocol_id == 4106
    assert definition.request_dic == (
        "codelist=17(600489,);33(000975,);\r\n"
        "dataitem=10,1968584,3475914,3250,3252,33001,33002,33003,35281,4,55,36103\r\n"
        "push=0\r\nscenario=5\r\n"
    )
    assert service._event_definitions[definition.subscription_id] is definition


@pytest.mark.asyncio
async def test_gold_stock_refresh_rebuilds_membership_without_gold_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = UnifiedEventDefinition(
        "gold_stock_period_performance",
        "fallback",
        4106,
        2501,
        "codelist=17(OLD,);",
        "",
        "data",
    )
    latest = UnifiedEventDefinition(
        "gold_stock_period_performance",
        "latest",
        4106,
        2501,
        "codelist=17(600489,);33(002552,);",
        "",
        "data",
    )
    calls: list[dict] = []
    events: list[dict] = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def _request_native_unified(self, **kwargs) -> dict:
            calls.append(kwargs)
            return {"head": {"errorCode": 0}, "data": {"dataDict": {"4": ["002552"]}}}

        async def get_native_sector_constituents(self, *_args, **_kwargs) -> dict:
            return {
                "status": "ok",
                "data": {
                    "constituents": [{
                        "security_code": "002552",
                        "total_market_value": "18468101755.6",
                    }]
                },
            }

        async def close(self) -> None:
            pass

    service = THSRealtimeStreamService(repository=object())

    async def load_latest() -> UnifiedEventDefinition:
        return latest

    async def enqueue(event: dict) -> None:
        events.append(event)

    monkeypatch.setattr(
        "src.application.services.ths_realtime_stream_service.THSClient",
        FakeClient,
    )
    monkeypatch.setattr(service, "_load_cached_gold_stock_definition", load_latest)
    monkeypatch.setattr(service, "_enqueue_event", enqueue)

    await service._refresh_gold_stock_performance_once(fallback)

    assert calls[0]["online_id"] == "latest"
    assert calls[0]["request_dic"] == latest.request_dic
    assert events[0]["subscription_id"] == "gold_stock_period_performance"
    assert events[0]["data"]["body"]["dataDict"]["total_market_value"] == [
        "18468101755.6"
    ]


@pytest.mark.asyncio
async def test_stream_initializes_unified_after_previous_native_success() -> None:
    commands: list[str] = []
    second_arrived_before_first_native_success = False
    delivered = asyncio.Event()

    async def server_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal second_arrived_before_first_native_success
        assert await reader.readline() == b"THSSTREAM/1\n"
        await _write(writer, {"type": "hello", "protocol": "THSSTREAM/1"})

        first = json.loads(await reader.readline())
        commands.append(first["subscription_id"])
        try:
            await asyncio.wait_for(reader.readline(), timeout=0.05)
            second_arrived_before_first_ack = True
        except TimeoutError:
            pass
        await _write(
            writer,
            {
                "type": "subscribed",
                "subscription_id": first["subscription_id"],
            },
        )
        try:
            await asyncio.wait_for(reader.readline(), timeout=0.05)
            second_arrived_before_first_native_success = True
        except TimeoutError:
            pass
        await _write(
            writer,
            {
                "type": "event",
                "topic": "unified",
                "subscription_id": first["subscription_id"],
                "data": {"head": {"errorCode": 0}, "body": {}},
            },
        )

        second = json.loads(await reader.readline())
        commands.append(second["subscription_id"])
        await _write(
            writer,
            {
                "type": "subscribed",
                "subscription_id": second["subscription_id"],
            },
        )
        await _write(
            writer,
            {
                "type": "event",
                "topic": "unified",
                "subscription_id": second["subscription_id"],
                "data": {"head": {"errorCode": 0}, "body": {}},
            },
        )
        await reader.read()
        writer.close()
        await writer.wait_closed()

    subscriptions = (
        THSUnifiedSubscription("first", "firstOnline", 1004, 6002, "first"),
        THSUnifiedSubscription("second", "secondOnline", 1004, 6002, "second"),
    )
    server = await asyncio.start_server(server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSNativeRealtimeStreamClient(
        host="127.0.0.1",
        port=port,
        subscriptions=subscriptions,
        heartbeat_interval=60,
        read_timeout=60,
        initial_response_timeout=0.1,
    )
    received: set[str] = set()

    async def handler(event: dict) -> None:
        received.add(event["subscription_id"])
        if received == {"first", "second"}:
            delivered.set()

    run_task = asyncio.create_task(client.run(handler))
    try:
        await asyncio.wait_for(delivered.wait(), timeout=2)
        await client.stop()
        await asyncio.wait_for(run_task, timeout=2)
    finally:
        server.close()
        await server.wait_closed()

    assert commands == ["first", "second"]
    assert not second_arrived_before_first_native_success


async def _write(writer: asyncio.StreamWriter, value: dict) -> None:
    writer.write(
        json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
    )
    await writer.drain()


@pytest.mark.asyncio
async def test_command_client_reuses_connection_and_correlates_out_of_order() -> None:
    connection_count = 0

    async def server_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal connection_count
        connection_count += 1
        commands = [json.loads(await reader.readline()) for _ in range(2)]
        for command in reversed(commands):
            await _write(
                writer,
                {
                    "request_id": command["request_id"],
                    "success": True,
                    "response": {"value": command["payload"]["value"]},
                },
            )
        await reader.read()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSNativeCommandClient(host="127.0.0.1", port=port)
    try:
        first, second = await asyncio.gather(
            client.request(route="unified", payload={"value": 1}, timeout=2),
            client.request(route="ranking", payload={"value": 2}, timeout=2),
        )
    finally:
        await client.close()
        server.close()
        await server.wait_closed()

    assert connection_count == 1
    assert first == {"value": 1}
    assert second == {"value": 2}


@pytest.mark.asyncio
async def test_command_client_reconnects_after_broker_disconnect() -> None:
    connection_count = 0
    first_connection_closed = asyncio.Event()

    async def server_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal connection_count
        connection_count += 1
        command = json.loads(await reader.readline())
        await _write(
            writer,
            {
                "request_id": command["request_id"],
                "success": True,
                "response": {"connection": connection_count},
            },
        )
        if connection_count == 1:
            writer.close()
            await writer.wait_closed()
            first_connection_closed.set()
            return
        await reader.read()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSNativeCommandClient(host="127.0.0.1", port=port)
    try:
        first = await client.request(route="unified", payload={}, timeout=2)
        await asyncio.wait_for(first_connection_closed.wait(), timeout=2)
        for _ in range(20):
            if not client.is_connected:
                break
            await asyncio.sleep(0.01)
        second = await client.request(route="unified", payload={}, timeout=2)
    finally:
        await client.close()
        server.close()
        await server.wait_closed()

    assert first == {"connection": 1}
    assert second == {"connection": 2}
    assert connection_count == 2


@pytest.mark.asyncio
async def test_stream_service_broker_keeps_session_and_multiplexes_requests() -> None:
    class FakeAppStream:
        async def request(
            self,
            *,
            route: str,
            payload: dict,
            timeout: float,
        ) -> dict:
            await asyncio.sleep(float(payload["delay"]))
            return {"route": route, "value": payload["value"], "timeout": timeout}

    service = THSRealtimeStreamService(repository=object())
    service._client = FakeAppStream()  # type: ignore[assignment]
    connection_count = 0

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal connection_count
        connection_count += 1
        await service._handle_command_client(reader, writer)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSNativeCommandClient(host="127.0.0.1", port=port)
    try:
        slow, fast = await asyncio.gather(
            client.request(
                route="unified",
                payload={"value": "slow", "delay": 0.05},
                timeout=2,
            ),
            client.request(
                route="ranking",
                payload={"value": "fast", "delay": 0.01},
                timeout=2,
            ),
        )
    finally:
        await client.close()
        server.close()
        await server.wait_closed()

    assert connection_count == 1
    assert slow["value"] == "slow"
    assert fast["value"] == "fast"


@pytest.mark.asyncio
async def test_stream_subscribes_once_and_delivers_incremental_events() -> None:
    commands: list[dict] = []

    async def server_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        assert await reader.readline() == b"THSSTREAM/1\n"
        await _write(writer, {"type": "hello", "protocol": "THSSTREAM/1"})
        for _ in range(2):
            command = json.loads(await reader.readline())
            commands.append(command)
            await _write(
                writer,
                {
                    "type": "subscribed",
                    "subscription_id": command["subscription_id"],
                },
            )
        await _write(
            writer,
            {
                "type": "event",
                "subscription_id": "one",
                "status": 0,
                "response_type": 1,
                "sequence": 7,
                "data": {"point_key_list": ["time", "value"]},
            },
        )
        await reader.read()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSNativeRealtimeStreamClient(
        host="127.0.0.1",
        port=port,
        subscriptions=_subscriptions(),
        heartbeat_interval=60,
        read_timeout=60,
        reconnect_min_delay=0.01,
    )
    received: list[dict] = []
    delivered = asyncio.Event()

    async def handler(event: dict) -> None:
        received.append(event)
        delivered.set()

    run_task = asyncio.create_task(client.run(handler))
    try:
        await asyncio.wait_for(delivered.wait(), timeout=2)
        await client.stop()
        await asyncio.wait_for(run_task, timeout=2)
    finally:
        server.close()
        await server.wait_closed()

    assert [item["subscription_id"] for item in commands] == ["one", "two"]
    assert received[0]["response_type"] == 1


@pytest.mark.asyncio
async def test_stream_reconnects_and_restores_every_subscription() -> None:
    connection_count = 0
    subscription_rounds: list[list[str]] = []
    delivered = asyncio.Event()

    async def server_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal connection_count
        connection_count += 1
        assert await reader.readline() == b"THSSTREAM/1\n"
        await _write(writer, {"type": "hello", "protocol": "THSSTREAM/1"})
        current: list[str] = []
        for _ in range(2):
            command = json.loads(await reader.readline())
            current.append(command["subscription_id"])
        subscription_rounds.append(current)
        if connection_count == 1:
            writer.close()
            await writer.wait_closed()
            return
        await _write(
            writer,
            {
                "type": "event",
                "subscription_id": "two",
                "status": 0,
                "response_type": 0,
                "sequence": 1,
                "data": {},
            },
        )
        await reader.read()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSNativeRealtimeStreamClient(
        host="127.0.0.1",
        port=port,
        subscriptions=_subscriptions(),
        heartbeat_interval=60,
        read_timeout=60,
        reconnect_min_delay=0.01,
        reconnect_max_delay=0.02,
    )

    async def handler(_event: dict) -> None:
        delivered.set()

    run_task = asyncio.create_task(client.run(handler))
    try:
        await asyncio.wait_for(delivered.wait(), timeout=3)
        await client.stop()
        await asyncio.wait_for(run_task, timeout=2)
    finally:
        server.close()
        await server.wait_closed()

    assert connection_count >= 2
    assert subscription_rounds[:2] == [["one", "two"], ["one", "two"]]


@pytest.mark.asyncio
async def test_stream_retries_subscriptions_without_an_initial_event() -> None:
    attempts: dict[str, int] = {"one": 0, "two": 0}
    delivered = asyncio.Event()

    async def server_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        assert await reader.readline() == b"THSSTREAM/1\n"
        await _write(writer, {"type": "hello", "protocol": "THSSTREAM/1"})
        while True:
            raw = await reader.readline()
            if not raw:
                break
            command = json.loads(raw)
            if command.get("op") != "subscribe":
                continue
            subscription_id = command["subscription_id"]
            attempts[subscription_id] += 1
            if subscription_id == "one" or attempts[subscription_id] > 1:
                await _write(
                    writer,
                    {
                        "type": "event",
                        "subscription_id": subscription_id,
                        "status": 0,
                        "response_type": 0,
                        "data": {},
                    },
                )
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSNativeRealtimeStreamClient(
        host="127.0.0.1",
        port=port,
        subscriptions=_subscriptions(),
        heartbeat_interval=60,
        read_timeout=60,
        subscription_interval=0,
        initial_response_timeout=0.05,
    )
    received: set[str] = set()

    async def handler(event: dict) -> None:
        received.add(event["subscription_id"])
        if received == {"one", "two"}:
            delivered.set()

    run_task = asyncio.create_task(client.run(handler))
    try:
        await asyncio.wait_for(delivered.wait(), timeout=2)
        await client.stop()
        await asyncio.wait_for(run_task, timeout=2)
    finally:
        server.close()
        await server.wait_closed()

    assert attempts == {"one": 1, "two": 2}


@pytest.mark.asyncio
async def test_stream_routes_request_response_by_request_id() -> None:
    command_received = asyncio.Event()

    async def server_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        assert await reader.readline() == b"THSSTREAM/1\n"
        await _write(writer, {"type": "hello", "protocol": "THSSTREAM/1"})
        for _ in range(2):
            await reader.readline()
        while True:
            raw = await reader.readline()
            if not raw:
                break
            command = json.loads(raw)
            if command.get("op") != "request":
                continue
            command_received.set()
            await _write(
                writer,
                {
                    "type": "response",
                    "request_id": command["request_id"],
                    "route": command["route"],
                    "payload": {"success": True, "echo": command["payload"]},
                },
            )
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSNativeRealtimeStreamClient(
        host="127.0.0.1",
        port=port,
        subscriptions=_subscriptions(),
        heartbeat_interval=60,
        read_timeout=60,
    )
    run_task = asyncio.create_task(client.run(lambda _event: asyncio.sleep(0)))
    try:
        response = await client.request(
            route="unified",
            payload={"onlineId": "marketLabel"},
            timeout=2,
        )
        await asyncio.wait_for(command_received.wait(), timeout=2)
        assert response == {
            "success": True,
            "echo": {"onlineId": "marketLabel"},
        }
        await client.stop()
        await asyncio.wait_for(run_task, timeout=2)
    finally:
        server.close()
        await server.wait_closed()


def test_stream_service_normalizes_full_chart_and_preserves_provider_identity() -> None:
    service = THSRealtimeStreamService(repository=object())
    service._latest_buckets["market_capital"] = None

    rows = service._event_to_snapshots(
        {
            "type": "event",
            "subscription_id": "market_capital",
            "status": 0,
            "response_type": 1,
            "sequence": 9,
            "emitted_at": 1785722400000,
            "data": {
                "name": "大盘资金",
                "point_key_list": ["time", "value"],
                "point_list": [
                    ["202608030930", "1.5"],
                    ["202608030931", "2.5"],
                ],
            },
        }
    )

    assert len(rows) == 2
    assert rows[-1]["provider"] == "ths_native"
    assert rows[-1]["data"]["value"] == 2.5
    assert rows[-1]["data"]["stream_sequence"] == 9


def test_stream_service_keeps_only_current_northbound_point() -> None:
    service = THSRealtimeStreamService(repository=object())
    service._latest_buckets["northbound_capital"] = None

    rows = service._event_to_snapshots(
        {
            "type": "event",
            "subscription_id": "northbound_capital",
            "status": 0,
            "response_type": 1,
            "emitted_at": 1785722400000,
            "data": {
                "point_key_list": ["time", "value"],
                "point_list": [
                    ["202608030930", "100"],
                    ["202608030931", "101"],
                ],
            },
        }
    )

    assert len(rows) == 1
    assert rows[0]["data"]["time"] == "202608030931"


def test_stream_service_converts_unified_stock_event_to_event_snapshot() -> None:
    service = THSRealtimeStreamService(repository=object())

    rows = service._event_to_snapshots(
        {
            "type": "event",
            "topic": "unified",
            "subscription_id": "stock_events",
            "sequence": 12,
            "emitted_at": 1785722400000,
            "data": {
                "head": {"errorCode": 0},
                "body": {
                    "dxjl": [
                        {
                            "dataid": "592572",
                            "marketcode": "17",
                            "stockcode": "000001",
                            "stockname": "平安银行",
                            "time": "1785722399",
                            "value": "436手",
                        }
                    ]
                },
            },
        }
    )

    event_rows = [row for row in rows if row["data_type"] == "ths_stock_anomaly"]
    aggregate = next(row for row in rows if row["data_type"] == "market_anomaly")

    assert len(event_rows) == 1
    assert event_rows[0]["data"]["event_type"] == "特大主动买"
    assert event_rows[0]["data"]["stockcode"] == "000001"
    assert aggregate["data"]["stock_events"][0]["stockcode"] == "000001"


def test_stream_service_merges_curve_and_event_buffers_into_market_snapshot() -> None:
    service = THSRealtimeStreamService(repository=object())

    curve_rows = service._event_to_snapshots(
        {
            "type": "event",
            "topic": "unified",
            "subscription_id": "market_anomaly_curve",
            "sequence": 20,
            "emitted_at": 1785722400000,
            "data": {
                "head": {"errorCode": 0},
                "body": {
                    "content": {
                        "10": [3800.0, 3801.5],
                        "19": [100.0, 120.0],
                    }
                },
            },
        }
    )
    market_rows = service._event_to_snapshots(
        {
            "type": "event",
            "topic": "unified",
            "subscription_id": "market_events",
            "sequence": 21,
            "emitted_at": 1785722401000,
            "data": {
                "head": {"errorCode": 0},
                "body": {"mobiledpyd": [{"title": "指数快速下探"}]},
            },
        }
    )

    assert curve_rows[0]["data"]["curve"] == [
        {
            "position": 0,
            "time_key": None,
            "index_value": 3800.0,
            "turnover": 100.0,
        },
        {
            "position": 1,
            "time_key": None,
            "index_value": 3801.5,
            "turnover": 120.0,
        },
    ]
    aggregate = market_rows[0]
    assert aggregate["data_type"] == "market_anomaly"
    assert aggregate["data"]["curve"] == curve_rows[0]["data"]["curve"]
    assert aggregate["data"]["market_events"] == [
        {"title": "指数快速下探"}
    ]
    assert aggregate["data"]["count"] == 1
