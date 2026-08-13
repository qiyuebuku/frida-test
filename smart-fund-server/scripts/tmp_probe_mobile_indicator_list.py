#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import urllib.request


base_url = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:49301")
payload = {
    "frame_id": 2312,
    "start": 0,
    "count": 10,
    "sort_indicator_id": None,
    "order": None,
    "hurricane_type": None,
    "hurricane_ids": [],
    "hurricane_indicator_ids": [],
    "mobile_indicator_ids": [
        "55", "10", "34818", "36251", "34311", "34391",
        "275", "35284", "35286",
    ],
    "securities": [
        {"code": "881121", "market": "48", "name": "半导体"},
        {"code": "885571", "market": "48", "name": "核电"},
        {"code": "882001", "market": "48", "name": "安徽"},
    ],
    "timeout_ms": 10000,
    "settle_ms": 1000,
}
request = urllib.request.Request(
    base_url + "/native/hurricane",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
started = time.monotonic()
with urllib.request.urlopen(request, timeout=20) as response:
    body = json.loads(response.read())
print(json.dumps({
    "seconds": round(time.monotonic() - started, 3),
    "response": body,
}, ensure_ascii=False, indent=2))
