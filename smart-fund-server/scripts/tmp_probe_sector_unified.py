#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx


BASE_URL = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:49300")
PAGES = [("all", 1358), ("industry", 1209), ("concept", 1297), ("style", 4046), ("region", 1337)]


async def request(client: httpx.AsyncClient, name: str, page_id: int, request_type: int) -> dict:
    started = time.monotonic()
    response = await client.post(
        "/native/unified",
        json={
            "onlineId": f"sector-{name}-{time.monotonic_ns()}",
            "protocolId": 2312,
            "pageId": page_id,
            "requestDic": "rowcount=500\r\nstartrow=0\r\nadddata=1\r\nsortorder=0\r\nsortid=34818",
            "requestType": request_type,
            "timeoutSeconds": 8,
        },
    )
    value = response.json()
    data = ((value.get("response") or {}).get("body") or {}).get("dataDict") or {}
    lengths = [len(item) for item in data.values() if isinstance(item, list)]
    return {
        "name": name,
        "request_type": request_type,
        "success": bool(value.get("success")),
        "duration_seconds": round(time.monotonic() - started, 3),
        "row_count": max(lengths) if lengths else 0,
        "error": value.get("error"),
    }


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20) as client:
        results = []
        for request_type in (0, 262144):
            for name, page_id in PAGES:
                results.append(await request(client, name, page_id, request_type))
    print(json.dumps(results, ensure_ascii=False, indent=2))


asyncio.run(main())
