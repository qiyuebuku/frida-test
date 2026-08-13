#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import time

import httpx


BASE_URL = "http://127.0.0.1:49300"


def ranking_payload(frame_id: int, page_id: int) -> dict:
    return {
        "frameId": frame_id,
        "pageId": page_id,
        "requestText": (
            "rowcount=30\r\nstartrow=0\r\nadddata=1\r\n"
            "sortorder=0\r\nsortid=34818"
        ),
        "timeoutSeconds": 60,
    }


def hurricane_payload(frame_id: int, board: str) -> dict:
    hurricane_id = "industry_l1" if board == "industry" else "cn_concept"
    return {
        "frame_id": frame_id,
        "start": 0,
        "count": 30,
        "sort_indicator_id": "ths-hot-data-minute-attention-rate",
        "order": "DESCENDING",
        "http_source_id": "AStockSector",
        "hurricane_ids": [hurricane_id],
        "hurricane_indicator_ids": ["ths-hot-data-minute-attention-rate"],
        "mobile_indicator_ids": ["34818"],
        "timeout_ms": 60000,
    }


async def call(
    client: httpx.AsyncClient,
    name: str,
    path: str,
    payload: dict,
) -> dict:
    started = time.monotonic()
    try:
        response = await client.post(path, json=payload)
        value = response.json()
        data = value.get("data") if isinstance(value, dict) else None
        rows = data.get("rows") if isinstance(data, dict) else None
        protocol = value.get("protocolResponse") if isinstance(value, dict) else None
        body = protocol.get("body") if isinstance(protocol, dict) else None
        columns = body.get("dataDict") if isinstance(body, dict) else None
        column_lengths = [len(v) for v in (columns or {}).values() if isinstance(v, list)]
        return {
            "name": name,
            "success": bool(isinstance(value, dict) and value.get("success")),
            "status_code": response.status_code,
            "duration_seconds": round(time.monotonic() - started, 3),
            "response_frame_id": value.get("frameId") if isinstance(value, dict) else None,
            "response_page_id": value.get("pageId") if isinstance(value, dict) else None,
            "row_count": len(rows) if isinstance(rows, list) else (max(column_lengths) if column_lengths else 0),
            "error": value.get("error") if isinstance(value, dict) else str(value)[:200],
        }
    except Exception as exc:
        return {
            "name": name,
            "success": False,
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def run_case(client: httpx.AsyncClient, name: str, specs: list[tuple]) -> dict:
    started = time.monotonic()
    results = await asyncio.gather(
        *(call(client, call_name, path, payload) for call_name, path, payload in specs)
    )
    await asyncio.sleep(2)
    return {
        "case": name,
        "wall_seconds": round(time.monotonic() - started - 2, 3),
        "success_count": sum(bool(item["success"]) for item in results),
        "results": results,
    }


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=90) as client:
        health = (await client.get("/health")).json()
        cases = []
        page_ids = [1358, 1209, 1297, 4046, 1337]
        cases.append(await run_case(client, "ranking_same_frame", [
            (f"ranking-{page_id}", "/native/ranking-debug", ranking_payload(2312, page_id))
            for page_id in page_ids
        ]))
        cases.append(await run_case(client, "ranking_unique_frame", [
            (f"ranking-{page_id}", "/native/ranking-debug", ranking_payload(32000 + index, page_id))
            for index, page_id in enumerate(page_ids)
        ]))
        cases.append(await run_case(client, "hurricane_same_frame", [
            (f"hurricane-{index}-{board}", "/native/hurricane", hurricane_payload(2312, board))
            for index, board in enumerate(["concept", "industry", "concept", "industry"])
        ]))
        cases.append(await run_case(client, "hurricane_unique_frame", [
            (f"hurricane-{index}-{board}", "/native/hurricane", hurricane_payload(33000 + index, board))
            for index, board in enumerate(["concept", "industry", "concept", "industry"])
        ]))
        cases.append(await run_case(client, "mixed_unique_frame", [
            ("ranking-industry", "/native/ranking-debug", ranking_payload(34001, 1209)),
            ("ranking-concept", "/native/ranking-debug", ranking_payload(34002, 1297)),
            ("hurricane-industry", "/native/hurricane", hurricane_payload(35001, "industry")),
            ("hurricane-concept", "/native/hurricane", hurricane_payload(35002, "concept")),
        ]))
    print(json.dumps({"health": health, "cases": cases}, ensure_ascii=False, indent=2))


asyncio.run(main())
