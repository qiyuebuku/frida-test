#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx


BASE_URL = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:49301")
MODES = {
    "rise": (34818, 0),
    "fall": (34818, 1),
    "quick": (48, 0),
    "turnover": (19, 0),
    "large_order": (34370, 0),
    "volume_ratio": (34311, 0),
    "turnover_rate": (34312, 0),
    "main_net_inflow": (34391, 0),
    "amplitude": (34819, 0),
}


def payload(sort_id: int, sort_order: int) -> dict:
    return {
        "frameId": 2312,
        "pageId": 1282,
        "requestText": (
            "startrow=0\r\nrowcount=50\r\nmarketId=0\r\n"
            f"sortorder={sort_order}\r\nsortid={sort_id}"
        ),
        "timeoutSeconds": 12,
    }


async def main() -> None:
    results = []
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=25) as client:
        for name, (sort_id, sort_order) in MODES.items():
            started = time.monotonic()
            try:
                response = await client.post(
                    "/native/ranking-debug",
                    json=payload(sort_id, sort_order),
                )
                value = response.json()
                protocol = value.get("protocolResponse") or {}
                columns = ((protocol.get("body") or {}).get("dataDict") or {})
                lengths = [
                    len(item) for item in columns.values() if isinstance(item, list)
                ]
                results.append(
                    {
                        "mode": name,
                        "success": bool(value.get("success")),
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "row_count": max(lengths) if lengths else 0,
                        "column_ids": sorted(columns),
                        "error": value.get("error"),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "mode": name,
                        "success": False,
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "row_count": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    print(json.dumps(results, ensure_ascii=False, indent=2))


asyncio.run(main())
