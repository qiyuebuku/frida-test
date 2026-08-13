#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx


BASE_URL = os.getenv("THS_PROBE_BASE_URL", "http://127.0.0.1:18900")
ROW_COUNT = max(1, int(os.getenv("THS_PROBE_ROW_COUNT", "50")))
MODE_FILTER = {
    value.strip()
    for value in os.getenv("THS_PROBE_MODES", "").split(",")
    if value.strip()
}
MODES = {
    "rise": ("zhangfu", 34818, 0),
    "fall": ("diefu", 34818, 1),
    "quick": ("zhangsu", 48, 0),
    "turnover": ("chengjiaoe", 19, 0),
    "large_order": ("dadanjingliang", 34370, 0),
    "volume_ratio": ("liangbi", 34311, 0),
    "turnover_rate": ("huanshoulv", 34312, 0),
    "main_net_inflow": ("zhulijingliuru", 34391, 0),
    "amplitude": ("zhenfu", 34819, 0),
}


async def main() -> None:
    results = []
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=25) as client:
        for name, (online_id, sort_id, sort_order) in MODES.items():
            if MODE_FILTER and name not in MODE_FILTER:
                continue
            started = time.monotonic()
            try:
                response = await client.post(
                    "/native/unified",
                    json={
                        "onlineId": f"probe-{online_id}-{time.time_ns()}",
                        "protocolId": 1208,
                        "pageId": 2312,
                        "requestDic": (
                            f"startrow=0\r\nrowcount={ROW_COUNT}\r\nmarketId=0\r\n"
                            f"sortorder={sort_order}\r\nsortid={sort_id}"
                        ),
                        "requestType": 262144,
                        "timeoutSeconds": 12,
                    },
                )
                value = response.json()
                response_payload = value.get("response") or value
                data = response_payload.get("body") or response_payload.get("data") or {}
                columns = data.get("dataDict") or {}
                lengths = [len(item) for item in columns.values() if isinstance(item, list)]
                head = response_payload.get("head") or {}
                results.append(
                    {
                        "mode": name,
                        "success": head.get("errorCode") == 0 and bool(columns),
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "row_count": max(lengths) if lengths else 0,
                        "column_ids": sorted(columns),
                        "error": value.get("error") or head.get("errorMsg"),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "mode": name,
                        "success": False,
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "row_count": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    print(json.dumps(results, ensure_ascii=False, indent=2))


asyncio.run(main())
