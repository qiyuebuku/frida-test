from __future__ import annotations

import asyncio
import json

import pytest

from scripts.ths_stream_simulator import PROTOCOL, THSStreamSimulator


async def _read(reader: asyncio.StreamReader) -> dict:
    return json.loads(await reader.readline())


@pytest.mark.asyncio
async def test_simulator_replays_subscriptions_and_request_fixtures() -> None:
    simulator = THSStreamSimulator(
        {
            "subscriptions": {
                "market": {"status": 0, "data": {"value": 1}},
            },
            "routes": {
                "unified": [
                    {
                        "match": {"onlineId": "marketLabel"},
                        "response": {"success": True, "value": 2},
                    }
                ]
            },
        }
    )
    await simulator.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", simulator.port)
    try:
        writer.write(f"{PROTOCOL}\n".encode())
        await writer.drain()
        assert await _read(reader) == {"type": "hello", "protocol": PROTOCOL}

        writer.write(
            json.dumps(
                {"op": "subscribe", "subscription_id": "market"}
            ).encode()
            + b"\n"
        )
        await writer.drain()
        assert (await _read(reader))["type"] == "subscribed"
        assert (await _read(reader))["data"] == {"value": 1}

        writer.write(
            json.dumps(
                {
                    "op": "request",
                    "request_id": "request-1",
                    "route": "unified",
                    "payload": {"onlineId": "marketLabel", "pageId": 6000},
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        response = await _read(reader)
        assert response["request_id"] == "request-1"
        assert response["payload"] == {"success": True, "value": 2}
    finally:
        writer.close()
        await writer.wait_closed()
        await simulator.close()


@pytest.mark.asyncio
async def test_simulator_returns_correlated_error_for_missing_fixture() -> None:
    simulator = THSStreamSimulator()
    await simulator.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", simulator.port)
    try:
        writer.write(f"{PROTOCOL}\n".encode())
        await writer.drain()
        await _read(reader)
        writer.write(
            b'{"op":"request","request_id":"missing","route":"ranking","payload":{}}\n'
        )
        await writer.drain()
        response = await _read(reader)
        assert response["type"] == "error"
        assert response["request_id"] == "missing"
    finally:
        writer.close()
        await writer.wait_closed()
        await simulator.close()
