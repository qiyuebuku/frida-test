#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.infrastructure.clients.ths import THSClient


async def main() -> None:
    client = THSClient()
    try:
        results = {}
        for classification, page_id in {
            "all": 1358,
            "industry": 1209,
            "concept": 1297,
            "style": 4046,
            "region": 1337,
        }.items():
            started = time.monotonic()
            try:
                payload, rows = await client._request_native_sector_table(
                    page_id=page_id,
                    request_text=(
                        "rowcount=500\r\nstartrow=0\r\nadddata=1\r\n"
                        "sortorder=0\r\nsortid=34818"
                    ),
                )
                results[classification] = {
                    "success": bool(payload.get("success")),
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "row_count": len(rows),
                    "indicator_keys": sorted({
                        str(key)
                        for row in rows
                        for key in (row.get("indicators") or {})
                    }),
                }
            except Exception as exc:
                results[classification] = {
                    "success": False,
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    finally:
        await client.close()


asyncio.run(main())
