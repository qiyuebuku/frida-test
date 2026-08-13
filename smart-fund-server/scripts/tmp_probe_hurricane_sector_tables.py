#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
import time
import urllib.request


BASE_URL = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:49301")
CLASSIFICATIONS = {
    "industry": ["industry_l1"],
    "concept": ["cn_concept"],
    "style": ["tszs"],
    "region": ["region"],
}
INDICATORS = [
    "security_name",
    "34818",
    "36251",
    "34311",
    "34391",
    "275",
    "35284",
    "35286",
    "up_down_limit_up_num",
]


def fetch(name: str, hurricane_ids: list[str]) -> dict:
    payload = {
        "frame_id": 2312,
        "start": 0,
        "count": 100,
        "sort_indicator_id": "34818",
        "order": "DESCENDING",
        "http_source_id": "sif-quoter-dataapi-sector-statistics",
        "hurricane_ids": hurricane_ids,
        "hurricane_indicator_ids": ["security_name", "up_down_limit_up_num"],
        "mobile_indicator_ids": [
            "55", "10", "34818", "36251", "34311", "34391",
            "275", "35284", "35286",
        ],
        "timeout_ms": 40000,
        "settle_ms": 1500,
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
    data = body.get("data") or {}
    rows = data.get("rows") or []
    sample = []
    for row in rows[:3]:
        sample.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "indicators": row.get("indicators"),
        })
    return {
        "classification": name,
        "success": body.get("success"),
        "seconds": round(time.monotonic() - started, 3),
        "total": data.get("total"),
        "row_count": len(rows),
        "sample": sample,
        "error": body.get("error"),
    }


def main() -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fetch, name, ids) for name, ids in CLASSIFICATIONS.items()]
        results = [future.result() for future in futures]
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
