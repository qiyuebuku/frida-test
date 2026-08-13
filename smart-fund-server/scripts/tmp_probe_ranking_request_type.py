#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx


BASE_URL = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:49300")
PAGES = [("all", 1358), ("industry", 1209), ("concept", 1297), ("style", 4046), ("region", 1337)]


def payload(page_id: int, request_type: int) -> dict:
    return {
        "frameId": 2312,
        "pageId": page_id,
        "requestText": "rowcount=500\r\nstartrow=0\r\nadddata=1\r\nsortorder=0\r\nsortid=34818",
        "requestType": request_type,
        "timeoutSeconds": 10,
    }


async def one(client: httpx.AsyncClient, name: str, page_id: int, request_type: int) -> dict:
    started = time.monotonic()
    response = await client.post("/native/ranking-debug", json=payload(page_id, request_type))
    value = response.json()
    protocol = value.get("protocolResponse") or {}
    columns = ((protocol.get("body") or {}).get("dataDict") or {})
    lengths = [len(item) for item in columns.values() if isinstance(item, list)]
    return {
        "name": name,
        "request_type": request_type,
        "success": bool(value.get("success")),
        "duration_seconds": round(time.monotonic() - started, 3),
        "row_count": max(lengths) if lengths else 0,
        "error": value.get("error"),
    }


async def run_case(client: httpx.AsyncClient, case: str, request_types: list[int]) -> dict:
    started = time.monotonic()
    results = await asyncio.gather(*(
        one(client, name, page_id, request_types[index])
        for index, (name, page_id) in enumerate(PAGES)
    ))
    await asyncio.sleep(3)
    return {
        "case": case,
        "wall_seconds": round(time.monotonic() - started, 3),
        "success_count": sum(item["success"] for item in results),
        "results": results,
    }


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20) as client:
        cases = [
            await run_case(client, "request_type_262144", [262144] * 5),
            await run_case(
                client,
                "request_type_unique",
                [262144, 524288, 1048576, 2097152, 4194304],
            ),
        ]
    print(json.dumps(cases, ensure_ascii=False, indent=2))


asyncio.run(main())
