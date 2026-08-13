#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
import time
import urllib.request


BASE_URL = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:49301")


def ranking(page_id: int) -> dict:
    return {
        "frameId": 2312,
        "pageId": page_id,
        "requestText": (
            "rowcount=500\r\nstartrow=0\r\nadddata=1\r\n"
            "sortorder=0\r\nsortid=34818"
        ),
        "timeoutSeconds": 5,
        "dispatchMethod": os.getenv("THS_RANKING_DISPATCH_METHOD", "a0"),
        "persistentSession": os.getenv("THS_RANKING_PERSISTENT", "0") == "1",
    }


def hurricane(index: int) -> dict:
    return {
        "frame_id": 2312,
        "start": 0,
        "count": 30,
        "sort_indicator_id": "ths-hot-data-minute-attention-rate",
        "order": "DESCENDING",
        "http_source_id": "AStockSector",
        "hurricane_ids": ["industry_l1" if index % 2 else "cn_concept"],
        "hurricane_indicator_ids": ["ths-hot-data-minute-attention-rate"],
        "mobile_indicator_ids": ["34818"],
        "timeout_ms": 40000,
    }


def call(name: str, path: str, payload: dict) -> dict:
    started = time.monotonic()
    try:
        request = urllib.request.Request(
            BASE_URL + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=75) as response:
            body = json.loads(response.read())
        return {
            "name": name,
            "success": bool(body.get("success")),
            "seconds": round(time.monotonic() - started, 3),
            "error": body.get("error"),
        }
    except Exception as exc:
        return {
            "name": name,
            "success": False,
            "seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_case(name: str, calls: list[tuple]) -> dict:
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
        results = list(pool.map(lambda spec: call(*spec), calls))
    wall = round(time.monotonic() - started, 3)
    time.sleep(3)
    return {
        "case": name,
        "wall_seconds": wall,
        "success_count": sum(item["success"] for item in results),
        "results": results,
    }


def main() -> None:
    cases = []
    started = time.monotonic()
    serial_results = []
    pages = [1358, 1209, 1297, 4046, 1337]
    fixed_page = os.getenv("THS_RANKING_FIXED_PAGE")
    if fixed_page:
        pages = [int(fixed_page)]
    for index in range(20):
        page_id = pages[index % len(pages)]
        serial_results.append(call(
            f"ranking-burst-{index}",
            "/native/ranking-debug",
            ranking(page_id),
        ))
    cases.append({
        "case": "ranking_serial_burst_20",
        "wall_seconds": round(time.monotonic() - started, 3),
        "success_count": sum(item["success"] for item in serial_results),
        "results": serial_results,
    })
    if os.getenv("THS_PROBE_RANKING_ONLY") == "1":
        print(json.dumps({"base_url": BASE_URL, "cases": cases}, ensure_ascii=False, indent=2))
        return
    time.sleep(3)
    cases.append(run_case("ranking_serial", [
        ("ranking-industry", "/native/ranking-debug", ranking(1209)),
    ]))
    cases.append(run_case("hurricane_parallel", [
        (f"hurricane-{index}", "/native/hurricane", hurricane(index))
        for index in range(4)
    ]))
    cases.append(run_case("mixed_one_plus_four", [
        ("ranking-concept", "/native/ranking-debug", ranking(1297)),
        *[
            (f"hurricane-{index}", "/native/hurricane", hurricane(index))
            for index in range(4)
        ],
    ]))
    cases.append(run_case("ranking_after_mixed", [
        ("ranking-style", "/native/ranking-debug", ranking(4046)),
    ]))
    print(json.dumps({"base_url": BASE_URL, "cases": cases}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
