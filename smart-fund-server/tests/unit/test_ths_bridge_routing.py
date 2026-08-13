import asyncio
import base64
import json

import httpx
import pytest

from src.infrastructure.clients.ths import THSClient


@pytest.fixture(autouse=True)
def _legacy_http_mode_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THS_NATIVE_COMMAND_STREAM_ENABLED", "0")


def _unified_response(request: httpx.Request) -> httpx.Response:
    body = base64.b64encode(json.dumps({"items": []}).encode()).decode()
    return httpx.Response(
        200,
        request=request,
        json={
            "success": True,
            "response": {
                "head": {"errorCode": 0},
                "body": {"data": body},
            },
        },
    )


@pytest.mark.asyncio
async def test_native_unified_uses_local_stream_command_broker() -> None:
    commands: list[dict] = []
    connection_count = 0

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        nonlocal connection_count
        connection_count += 1
        while raw := await reader.readline():
            command = json.loads(raw)
            commands.append(command)
            body = base64.b64encode(json.dumps({"items": []}).encode()).decode()
            writer.write(
                json.dumps(
                    {
                        "request_id": command["request_id"],
                        "success": True,
                        "response": {
                            "success": True,
                            "response": {
                                "head": {"errorCode": 0},
                                "body": {"data": body},
                            },
                        },
                    }
                ).encode()
                + b"\n"
            )
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSClient(
        native_command_stream_enabled=True,
        native_command_host="127.0.0.1",
        native_command_port=port,
    )
    try:
        result, second = await asyncio.gather(
            client._request_native_unified(
                online_id="marketLabel",
                protocol_id=1002,
                page_id=6000,
                request_dic="action=subscribe",
            ),
            client._request_native_unified(
                online_id="marketLabel2",
                protocol_id=1002,
                page_id=6000,
                request_dic="action=subscribe",
            ),
        )
    finally:
        await client.close()
        server.close()
        await server.wait_closed()

    assert result == {"head": {"errorCode": 0}, "data": {"items": []}}
    assert second == {"head": {"errorCode": 0}, "data": {"items": []}}
    assert connection_count == 1
    assert commands[0]["route"] == "unified"
    assert commands[0]["payload"]["onlineId"] == "marketLabel"


@pytest.mark.asyncio
async def test_native_realtime_uses_local_stream_command_broker() -> None:
    commands: list[dict] = []

    async def handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        command = json.loads(await reader.readline())
        commands.append(command)
        writer.write(
            json.dumps(
                {
                    "request_id": command["request_id"],
                    "success": True,
                    "response": {
                        "success": True,
                        "data": {"point_list": [["09:30", "1.0"]]},
                    },
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        await reader.read()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = THSClient(
        native_command_stream_enabled=True,
        native_command_host="127.0.0.1",
        native_command_port=port,
    )
    try:
        result = await client._request_native_realtime("market_capital")
    finally:
        await client.close()
        server.close()
        await server.wait_closed()

    assert result == {"point_list": [["09:30", "1.0"]]}
    assert commands == [
        {
            "request_id": commands[0]["request_id"],
            "route": "realtime",
            "payload": {
                "key": "market_capital",
                "requestParam": "market_capital data",
                "requestChannel": "market_capital_channel",
            },
            "timeout_seconds": 65.0,
        }
    ]


@pytest.mark.asyncio
async def test_app_http_proxy_does_not_wait_for_native_instance_lock() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://app-http-bridge/proxy")
        return httpx.Response(200, request=request, json={"status_code": 0})

    client = THSClient(
        native_bridge_url="http://native-bridge",
        app_http_bridge_url="http://app-http-bridge",
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    native_lock = client._native_lock_for("default")
    try:
        await native_lock.acquire()
        result = await asyncio.wait_for(
            client._request_app_proxy("https://eq.10jqka.com.cn/example"),
            timeout=0.2,
        )
    finally:
        if native_lock.locked():
            native_lock.release()
        await client.close()

    assert result == {"status_code": 0}


@pytest.mark.asyncio
async def test_app_http_proxy_forwards_post_json_body() -> None:
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, request=request, json={"status_code": 0})

    client = THSClient(app_http_bridge_url="http://app-http-bridge")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await client._request_app_proxy(
            "https://fund.10jqka.com.cn/query",
            method="POST",
            body={"businessKey": "etf-ranking"},
        )
    finally:
        await client.close()

    assert seen["method"] == "POST"
    assert json.loads(seen["body"]) == {"businessKey": "etf-ranking"}
    assert seen["content_type"] == "application/json"


@pytest.mark.asyncio
async def test_app_http_proxy_serializes_requests_to_same_endpoint() -> None:
    active = 0
    max_active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, request=request, json={"status_code": 0})

    client = THSClient(app_http_bridge_url="http://app-http-bridge")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await asyncio.gather(
            client._request_app_proxy(
                "https://quota-h.10jqka.com.cn/quote/v1/single_kline?a=1",
            ),
            client._request_app_proxy(
                "https://quota-h.10jqka.com.cn/quote/v1/single_kline?a=2",
            ),
        )
    finally:
        await client.close()

    assert max_active == 1


@pytest.mark.asyncio
async def test_app_http_proxy_keeps_different_endpoints_parallel() -> None:
    active = 0
    max_active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, request=request, json={"status_code": 0})

    client = THSClient(app_http_bridge_url="http://app-http-bridge")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await asyncio.gather(
            client._request_app_proxy("https://fund.10jqka.com.cn/first"),
            client._request_app_proxy("https://fund.10jqka.com.cn/second"),
        )
    finally:
        await client.close()

    assert max_active == 2


@pytest.mark.asyncio
async def test_lanes_sharing_one_app_instance_remain_serialized() -> None:
    active = 0
    max_active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return _unified_response(request)

    client = THSClient(native_bridge_url="http://owner-bridge")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await asyncio.gather(
            client._request_native_unified(
                lane="events",
                online_id="events",
                protocol_id=1004,
                page_id=2312,
                request_dic="stock_list=all",
            ),
            client._request_native_unified(
                lane="hurricane",
                online_id="hurricane",
                protocol_id=1208,
                page_id=2312,
                request_dic="rowcount=10",
            ),
        )
    finally:
        await client.close()

    assert max_active == 1


@pytest.mark.asyncio
async def test_native_sector_requests_share_app_transport_capacity() -> None:
    active_by_family = {"ranking": 0, "hurricane": 0, "indicator": 0}
    max_by_family = {"ranking": 0, "hurricane": 0, "indicator": 0}
    total_active = 0
    max_total_active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal total_active, max_total_active
        if (request_body := request.content) and b'"securities"' in request_body:
            family = "indicator"
        elif request.url.path.endswith("/hurricane"):
            family = "hurricane"
        else:
            family = "ranking"
        active_by_family[family] += 1
        max_by_family[family] = max(max_by_family[family], active_by_family[family])
        total_active += 1
        max_total_active = max(max_total_active, total_active)
        await asyncio.sleep(0.05)
        total_active -= 1
        active_by_family[family] -= 1
        return httpx.Response(200, request=request, json={"success": True})

    client = THSClient(native_bridge_url="http://owner-bridge")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await asyncio.gather(
            *(client._request_native_sector_bridge(
                "/native/ranking-debug", {"index": index}
            ) for index in range(3)),
            *(client._request_native_sector_bridge(
                "/native/hurricane", {"index": index}, lane="hurricane"
            ) for index in range(3)),
            *(client._request_native_sector_bridge(
                "/native/hurricane",
                {"index": index, "securities": [{"code": str(index)}]},
                lane="hurricane",
            ) for index in range(3)),
        )
    finally:
        await client.close()

    assert max_by_family == {"ranking": 1, "hurricane": 1, "indicator": 1}
    assert max_total_active == 1


@pytest.mark.asyncio
async def test_native_sector_bridge_tolerates_legacy_control_characters() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=b'{"success":true,"note":"line1\nline2"}',
        )

    client = THSClient(native_bridge_url="http://owner-bridge")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await client._request_native_sector_bridge(
            "/native/hurricane",
            {"hurricane_ids": ["cn_concept"]},
            lane="hurricane",
        )
    finally:
        await client.close()

    assert result["note"] == "line1\nline2"


@pytest.mark.asyncio
async def test_native_sector_bridge_does_not_retry_incomplete_rows() -> None:
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            request=request,
            json={"success": False, "error": "timeout_incomplete_rows"},
        )

    client = THSClient(native_bridge_url="http://owner-bridge")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="timeout_incomplete_rows"):
            await client._request_native_sector_bridge(
                "/native/hurricane",
                {"hurricane_ids": ["cn_concept"]},
                lane="hurricane",
            )
    finally:
        await client.close()

    assert request_count == 1


@pytest.mark.asyncio
async def test_native_sector_bridge_preserves_page_bound_frame() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, request=request, json={"success": True})

    client = THSClient(native_bridge_url="http://owner-bridge")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await client._request_native_sector_bridge(
            "/native/hurricane",
            {"frame_id": 2267, "_preserve_frame_id": True},
            lane="hurricane",
        )
    finally:
        await client.close()

    assert captured == {"frame_id": 2267}


def test_native_bridge_rejects_unknown_lane() -> None:
    client = THSClient(native_bridge_url="http://owner-bridge")
    with pytest.raises(ValueError, match="unknown THS native bridge lane"):
        client._native_bridge_for("unknown")
