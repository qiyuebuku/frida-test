#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
import time
import urllib.request


BASE_URL = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:49301")


def fetch(name: str, hurricane_id: str) -> dict:
    payload = {
        "frame_id": 9001,
        "start": 0,
        "count": 10,
        "sort_indicator_id": "ths-hot-data-minute-attention-rate",
        "order": "DESCENDING",
        "http_source_id": "AStockSector",
        "hurricane_ids": [hurricane_id],
        "hurricane_indicator_ids": ["ths-hot-data-minute-attention-rate"],
        "mobile_indicator_ids": ["34818"],
        "timeout_ms": 40000,
        "settle_ms": 1000,
    }
    request = urllib.request.Request(
        BASE_URL + "/native/hurricane",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=75) as response:
        body = json.loads(response.read())
    rows = ((body.get("data") or {}).get("rows") or [])
    return {
        "classification": name,
        "seconds": round(time.monotonic() - started, 3),
        "row_count": len(rows),
        "sample": rows[:3],
    }


def main() -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(fetch, "concept", "cn_concept"),
            pool.submit(fetch, "industry", "industry_l1"),
        ]
        print(json.dumps([future.result() for future in futures], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
