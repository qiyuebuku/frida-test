#!/usr/bin/env python3
"""Probe US security quote table push using protocol 1264."""

from __future__ import annotations

import asyncio
import argparse
import json
import urllib.request

from src.infrastructure.clients.ths import THSClient


async def main(count: int) -> None:
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
    securities = list(zip(
        (columns.get("4") or [])[:count],
        (columns.get("34338") or [])[:count],
        strict=True,
    ))
    client = THSClient()
    try:
        response = await client._request_native_unified(
            lane="realtime",
            online_id="probe-us-security-quote-push",
            protocol_id=1264,
            page_id=2371,
            timeout_seconds=15,
            request_dic=(
                f"startrow=0\r\nsortid=-1\r\nrowcount={len(securities)}\r\nnewrealtime=0\r\n"
                "selfstockcustom=1\r\nupdate=1\r\n"
                "columnorder=55|4|34338|10|34818|48\r\n"
                f"marketlist={'|'.join(str(market) for _code, market in securities)}\r\n"
                f"stocklist={'|'.join(str(code) for code, _market in securities)}\r\npush=1"
            ),
        )
        print(json.dumps(response, ensure_ascii=False, default=str))
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.count))
