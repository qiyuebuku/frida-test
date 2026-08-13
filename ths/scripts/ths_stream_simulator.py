#!/usr/bin/env python3
"""Local THSSTREAM/1 simulator for deterministic client development."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any


PROTOCOL = "THSSTREAM/1"


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and actual == expected
    return actual == expected


class THSStreamSimulator:
    """Replay request fixtures while preserving the production wire protocol."""

    def __init__(
        self,
        scenario: dict[str, Any] | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self._scenario = scenario or {}
        self._host = host
        self._port = int(port)
        self._server: asyncio.Server | None = None
        self.commands: list[dict[str, Any]] = []

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("simulator is not running")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            self._host,
            self._port,
            limit=8 * 1024 * 1024,
        )

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def serve_forever(self) -> None:
        await self.start()
        assert self._server is not None
        print(f"THSSTREAM simulator listening on {self._host}:{self.port}")
        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            handshake = await reader.readline()
            if handshake != f"{PROTOCOL}\n".encode():
                return
            await self._write(writer, {"type": "hello", "protocol": PROTOCOL})
            while raw := await reader.readline():
                command = json.loads(raw)
                if not isinstance(command, dict):
                    continue
                self.commands.append(command)
                operation = command.get("op")
                if operation == "ping":
                    await self._write(writer, {"type": "pong"})
                elif operation == "subscribe":
                    await self._handle_subscribe(writer, command)
                elif operation == "unsubscribe":
                    await self._write(
                        writer,
                        {
                            "type": "unsubscribed",
                            "subscription_id": command.get("subscription_id"),
                        },
                    )
                elif operation == "request":
                    await self._handle_request(writer, command)
                else:
                    await self._write(
                        writer,
                        {"type": "error", "error": f"unsupported op: {operation}"},
                    )
        finally:
            writer.close()
            await writer.wait_closed()

    async def _handle_subscribe(
        self,
        writer: asyncio.StreamWriter,
        command: dict[str, Any],
    ) -> None:
        subscription_id = str(command.get("subscription_id") or "")
        await self._write(
            writer,
            {"type": "subscribed", "subscription_id": subscription_id},
        )
        event = (self._scenario.get("subscriptions") or {}).get(subscription_id)
        if isinstance(event, dict):
            payload = dict(event)
            payload.setdefault("type", "event")
            payload.setdefault("subscription_id", subscription_id)
            await self._write(writer, payload)

    async def _handle_request(
        self,
        writer: asyncio.StreamWriter,
        command: dict[str, Any],
    ) -> None:
        request_id = str(command.get("request_id") or "")
        route = str(command.get("route") or "")
        payload = command.get("payload")
        fixture = self._match_fixture(route, payload)
        if fixture is None:
            await self._write(
                writer,
                {
                    "type": "error",
                    "request_id": request_id,
                    "route": route,
                    "error": f"no fixture for route={route}",
                },
            )
            return
        delay_ms = max(0, int(fixture.get("delay_ms") or 0))
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        await self._write(
            writer,
            {
                "type": "response",
                "request_id": request_id,
                "route": route,
                "payload": fixture.get("response") or {},
            },
        )

    def _match_fixture(
        self,
        route: str,
        payload: Any,
    ) -> dict[str, Any] | None:
        routes = self._scenario.get("routes") or {}
        candidates = list(routes.get(route) or []) + list(routes.get("*") or [])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            expected = candidate.get("match") or {}
            if _contains(payload, expected):
                return candidate
        return None

    @staticmethod
    async def _write(
        writer: asyncio.StreamWriter,
        message: dict[str, Any],
    ) -> None:
        writer.write(
            json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
            + b"\n"
        )
        await writer.drain()


def _load_scenario(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("scenario root must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=49310)
    args = parser.parse_args()
    simulator = THSStreamSimulator(
        _load_scenario(args.scenario),
        host=args.host,
        port=args.port,
    )
    try:
        asyncio.run(simulator.serve_forever())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
