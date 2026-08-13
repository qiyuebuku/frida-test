#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import os
import time
import urllib.request


BASE = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:49301")
SECURITIES = [
    {"code": "881121", "market": "48", "name": "半导体"},
    {"code": "885571", "market": "48", "name": "核电"},
    {"code": "882001", "market": "48", "name": "安徽"},
]


def call(frame_id: int) -> dict:
    payload = {
        "frame_id": frame_id,
        "start": 0,
        "count": len(SECURITIES),
        "sort_indicator_id": None,
        "order": None,
        "hurricane_type": None,
        "hurricane_ids": [],
        "hurricane_indicator_ids": [],
        "mobile_indicator_ids": ["55", "10", "34818", "36251", "34311"],
        "securities": SECURITIES,
        "settle_ms": 1000,
    }
    request = urllib.request.Request(
        BASE + "/native/hurricane",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    try:
        body = json.loads(raw)
        return {
            "frame_id": frame_id,
            "seconds": round(time.monotonic() - started, 3),
            "success": body.get("success"),
            "rows": len(((body.get("data") or {}).get("rows") or [])),
            "error": body.get("error"),
        }
    except json.JSONDecodeError as exc:
        return {
            "frame_id": frame_id,
            "seconds": round(time.monotonic() - started, 3),
            "success": False,
            "decode_error": str(exc),
            "raw_at_error": repr(raw[max(0, exc.pos - 80):exc.pos + 80]),
        }


def main() -> None:
    for frame_ids in ([2312], [180001], [180011, 180012, 180013, 180014]):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(frame_ids)) as pool:
            results = list(pool.map(call, frame_ids))
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
