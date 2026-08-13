#!/usr/bin/env python3
"""Validate a dedicated THSSTREAM session for US security quotes."""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request

from src.infrastructure.clients.ths_native_stream import (
    THSNativeRealtimeStreamClient,
    THSUnifiedSubscription,
)


def members(count: int) -> list[tuple[str, str]]:
    with urllib.request.urlopen(
        "http://127.0.0.1:8900/api/market-observability/snapshots"
        "?data_type=ths_us_market_module&limit=100",
        timeout=15,
    ) as response:
        rows = json.load(response).get("items") or []
    ranking = next(
        item for item in rows if item.get("subject_id") == "ranking_all_stream"
    )
    columns = ranking["data"]["native_table"]["dataDict"]
    return list(zip(
        (columns.get("4") or [])[:count],
        (columns.get("34338") or [])[:count],
        strict=True,
    ))


async def main(count: int, duration: float) -> None:
    securities = members(count)
    subscription = THSUnifiedSubscription(
        subscription_id="probe_us_quote_stream",
        online_id="probeUsQuoteStream",
        protocol_id=1264,
        page_id=2371,
        request_dic=(
            f"startrow=0\r\nsortid=-1\r\nrowcount={len(securities)}\r\n"
            "newrealtime=0\r\nselfstockcustom=1\r\nupdate=1\r\n"
            "columnorder=55|4|34338|10|34818|48\r\n"
            f"marketlist={'|'.join(str(market) for _code, market in securities)}\r\n"
            f"stocklist={'|'.join(str(code) for code, _market in securities)}\r\npush=1"
        ),
    )
    client = THSNativeRealtimeStreamClient(
        host="127.0.0.1",
        port=49300,
        subscriptions=(subscription,),
        initial_response_timeout=12,
        read_timeout=30,
    )
    events: list[dict] = []

    async def handler(event: dict) -> None:
        events.append(event)

    task = asyncio.create_task(client.run(handler))
    try:
        await asyncio.sleep(duration)
    finally:
        await client.stop()
        await asyncio.wait_for(task, timeout=5)
    print(json.dumps({
        "count": count,
        "events": len(events),
        "active": sorted(client.active_subscription_ids),
        "heads": [event.get("data", {}).get("head") for event in events],
    }, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--duration", type=float, default=25)
    args = parser.parse_args()
    asyncio.run(main(args.count, args.duration))
