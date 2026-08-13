#!/usr/bin/env python3
"""Validate one-App multi-subscription delivery without writing the database."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.services.ths_realtime_stream_service import REALTIME_SERIES
from src.infrastructure.clients.ths_native_stream import (
    THSNativeRealtimeStreamClient,
)


async def validate(
    host: str,
    port: int,
    duration: float,
    indicators: set[str] | None = None,
) -> dict:
    definitions = tuple(
        item
        for item in REALTIME_SERIES
        if indicators is None or item.indicator in indicators
    )
    if not definitions:
        raise ValueError("no matching realtime indicators")
    counts: Counter[str] = Counter()
    response_types: dict[str, set[int]] = {
        item.indicator: set() for item in definitions
    }
    all_received = asyncio.Event()
    client = THSNativeRealtimeStreamClient(
        host=host,
        port=port,
        subscriptions=(item.subscription() for item in definitions),
        heartbeat_interval=5,
        read_timeout=20,
        reconnect_min_delay=0.5,
        reconnect_max_delay=2,
    )

    async def handler(event: dict) -> None:
        subscription_id = str(event.get("subscription_id") or "")
        counts[subscription_id] += 1
        response_types.setdefault(subscription_id, set()).add(
            int(event.get("response_type", -1))
        )
        if all(counts[item.indicator] > 0 for item in definitions):
            all_received.set()

    run_task = asyncio.create_task(client.run(handler))
    timed_out = False
    try:
        try:
            await asyncio.wait_for(all_received.wait(), timeout=duration)
        except TimeoutError:
            timed_out = True
        else:
            remaining = min(10.0, max(0.0, duration / 4))
            if remaining:
                await asyncio.sleep(remaining)
    finally:
        await client.stop()
        await asyncio.wait_for(run_task, timeout=5)

    missing = [
        item.indicator for item in definitions if counts[item.indicator] == 0
    ]
    return {
        "success": not missing,
        "host": host,
        "port": port,
        "subscription_count": len(definitions),
        "timed_out": timed_out,
        "counts": dict(counts),
        "response_types": {
            key: sorted(values) for key, values in response_types.items()
        },
        "missing": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=49300)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument(
        "--indicators",
        help="Comma-separated indicator names; defaults to every configured stream",
    )
    args = parser.parse_args()
    indicators = (
        {value.strip() for value in args.indicators.split(",") if value.strip()}
        if args.indicators
        else None
    )
    result = asyncio.run(
        validate(args.host, args.port, args.duration, indicators)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
