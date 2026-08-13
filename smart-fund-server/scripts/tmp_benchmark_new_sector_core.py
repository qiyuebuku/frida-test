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
    calls = []
    for classification in ("all", "industry", "concept", "style", "region"):
        calls.append((
            f"table:{classification}",
            lambda classification=classification: client.get_native_sector_ranking_bundle(
                classification, 50
            ),
        ))
        calls.append((
            f"limit_up:{classification}",
            lambda classification=classification: client.get_native_sector_ranking(
                "limit_up_count", 50, classification
            ),
        ))
    for classification in ("industry", "concept", "region"):
        calls.append((
            f"flow:{classification}",
            lambda classification=classification: client.get_native_sector_fund_flow(
                classification, 500
            ),
        ))
    for classification in ("concept", "industry", "index"):
        calls.append((
            f"hot:{classification}",
            lambda classification=classification: client.get_native_hot_boards(
                classification, 50
            ),
        ))

    started = time.monotonic()

    async def timed(name, factory):
        item_started = time.monotonic()
        try:
            result = await factory()
            return {
                "name": name,
                "status": result.get("status"),
                "success": result.get("status") == "ok",
                "count": (result.get("data") or {}).get("count"),
                "duration_seconds": round(time.monotonic() - item_started, 3),
                "error": result.get("error"),
            }
        except Exception as exc:
            return {
                "name": name,
                "success": False,
                "duration_seconds": round(time.monotonic() - item_started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }

    try:
        results = await asyncio.gather(
            *(timed(name, factory) for name, factory in calls)
        )
    finally:
        await client.close()
    failures = [item for item in results if not item["success"]]
    print(json.dumps({
        "wall_seconds": round(time.monotonic() - started, 3),
        "task_count": len(results),
        "success_count": len(results) - len(failures),
        "failure_count": len(failures),
        "failures": failures,
        "slowest": sorted(
            results, key=lambda item: item["duration_seconds"], reverse=True
        )[:8],
    }, ensure_ascii=False, indent=2, default=str))


asyncio.run(main())
