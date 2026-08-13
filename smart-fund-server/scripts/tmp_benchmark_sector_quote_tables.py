#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

PRODUCTION_ROOT = Path("/home/yuyangruan/smart-fund/smart-fund-server")
ROOT = PRODUCTION_ROOT if PRODUCTION_ROOT.exists() else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.infrastructure.clients.ths import THSClient


async def main() -> None:
    client = THSClient()
    started = time.monotonic()

    async def run(classification: str) -> dict:
        item_started = time.monotonic()
        result = await client.get_native_sector_ranking_bundle(classification, 50)
        return {
            "classification": classification,
            "status": result.get("status"),
            "message": result.get("message"),
            "source_row_count": (result.get("data") or {}).get("source_row_count"),
            "seconds": round(time.monotonic() - item_started, 3),
        }

    try:
        results = await asyncio.gather(
            *(run(name) for name in ("all", "industry", "concept", "style", "region"))
        )
    finally:
        await client.close()
    print(json.dumps({
        "wall_seconds": round(time.monotonic() - started, 3),
        "results": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
