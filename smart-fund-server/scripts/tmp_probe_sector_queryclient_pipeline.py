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
METRICS = ["55", "10", "34818", "36251", "34311"]


def post(payload: dict) -> tuple[dict, float]:
    request = urllib.request.Request(
        BASE_URL + "/native/hurricane",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read()), time.monotonic() - started


def run_classification(name: str, hurricane_ids: list[str]) -> dict:
    listing, listing_seconds = post({
        "frame_id": 2312,
        "start": 0,
        "count": 500,
        "sort_indicator_id": "up_down_limit_up_num",
        "order": "DESCENDING",
        "http_source_id": "sif-quoter-dataapi-sector-statistics",
        "hurricane_ids": hurricane_ids,
        "hurricane_indicator_ids": ["security_name", "up_down_limit_up_num"],
        "mobile_indicator_ids": [],
        "timeout_ms": 10000,
        "settle_ms": 500,
    })
    listing_rows = ((listing.get("data") or {}).get("rows") or [])
    securities = [
        {
            "code": row.get("code"),
            "market": row.get("market") or "48",
            "name": row.get("name") or "",
        }
        for row in listing_rows
        if row.get("code")
    ]
    metrics, metrics_seconds = post({
        "frame_id": 2312,
        "start": 0,
        "count": max(1, len(securities)),
        "sort_indicator_id": None,
        "order": None,
        "hurricane_type": None,
        "hurricane_ids": [],
        "hurricane_indicator_ids": [],
        "mobile_indicator_ids": METRICS,
        "securities": securities,
        "timeout_ms": 10000,
        "settle_ms": 1000,
    })
    metric_rows = ((metrics.get("data") or {}).get("rows") or [])
    coverage = {
        metric: sum(
            bool(((row.get("indicators") or {}).get(metric) or {}).get("content"))
            for row in metric_rows
        )
        for metric in METRICS
    }
    return {
        "classification": name,
        "listing_seconds": round(listing_seconds, 3),
        "metrics_seconds": round(metrics_seconds, 3),
        "listing_rows": len(listing_rows),
        "metric_rows": len(metric_rows),
        "coverage": coverage,
    }


def main() -> None:
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(run_classification, name, ids)
            for name, ids in CLASSIFICATIONS.items()
        ]
        results = [future.result() for future in futures]
    print(json.dumps({
        "wall_seconds": round(time.monotonic() - started, 3),
        "results": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
