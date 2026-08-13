#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx


BASE_URL = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:49300")
PAGES = [("all", 1358), ("industry", 1209), ("concept", 1297), ("style", 4046), ("region", 1337)]


def ranking_payload(page_id: int) -> dict:
    return {
        "frameId": 2312,
        "pageId": page_id,
        "requestText": "rowcount=500\r\nstartrow=0\r\nadddata=1\r\nsortorder=0\r\nsortid=34818",
        "timeoutSeconds": 60,
    }


def hurricane_payload(index: int) -> dict:
    board = "industry_l1" if index % 2 else "cn_concept"
    metric = "up_down_limit_up_num" if index % 3 == 0 else "ths-hot-data-minute-attention-rate"
    return {
        "frame_id": 2312,
        "start": 0,
        "count": 100,
        "sort_indicator_id": metric,
        "order": "DESCENDING",
        "http_source_id": "sif-quoter-dataapi-sector-statistics" if metric == "up_down_limit_up_num" else "AStockSector",
        "hurricane_ids": [board],
        "hurricane_indicator_ids": ["security_name", metric, "hq-fncdict-199112"],
        "mobile_indicator_ids": [],
        "timeout_ms": 60000,
    }


async def post(client: httpx.AsyncClient, name: str, path: str, payload: dict) -> dict:
    started = time.monotonic()
    try:
        response = await client.post(path, json=payload)
        value = response.json()
        data = value.get("data") if isinstance(value, dict) else None
        rows = data.get("rows") if isinstance(data, dict) else None
        protocol = value.get("protocolResponse") if isinstance(value, dict) else None
        body = protocol.get("body") if isinstance(protocol, dict) else None
        columns = body.get("dataDict") if isinstance(body, dict) else None
        lengths = [len(item) for item in (columns or {}).values() if isinstance(item, list)]
        return {
            "name": name,
            "success": bool(value.get("success")),
            "duration_seconds": round(time.monotonic() - started, 3),
            "row_count": len(rows) if isinstance(rows, list) else (max(lengths) if lengths else 0),
            "error": value.get("error"),
        }
    except Exception as exc:
        return {"name": name, "success": False, "duration_seconds": round(time.monotonic() - started, 3), "error": f"{type(exc).__name__}: {exc}"}


async def batch(client: httpx.AsyncClient, name: str, specs: list[tuple]) -> dict:
    started = time.monotonic()
    results = await asyncio.gather(*(post(client, *spec) for spec in specs))
    wall = round(time.monotonic() - started, 3)
    await asyncio.sleep(2)
    return {"case": name, "wall_seconds": wall, "success_count": sum(x["success"] for x in results), "results": results}


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=90) as client:
        cases = []
        for name, page_id in PAGES:
            cases.append(await batch(client, f"ranking_single_{name}", [(name, "/native/ranking-debug", ranking_payload(page_id))]))
        cases.append(await batch(client, "ranking_batch_3", [
            (name, "/native/ranking-debug", ranking_payload(page_id)) for name, page_id in PAGES[:3]
        ]))
        cases.append(await batch(client, "ranking_batch_2", [
            (name, "/native/ranking-debug", ranking_payload(page_id)) for name, page_id in PAGES[3:]
        ]))
        cases.append(await batch(client, "hurricane_batch_8", [
            (f"hurricane-{index}", "/native/hurricane", hurricane_payload(index)) for index in range(8)
        ]))
    print(json.dumps({"cases": cases}, ensure_ascii=False, indent=2))


asyncio.run(main())
