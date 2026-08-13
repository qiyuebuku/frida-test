#!/usr/bin/env python3
"""Benchmark representative complete THS pages through the multi-App gateway."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.clients.ths import THSClient


async def run(output: Path, timeout: float) -> None:
    client = THSClient(timeout=90)
    started = time.monotonic()
    calls = {
        "a_share_anomalies": client.get_native_market_anomalies,
        "gold_page": client.get_native_gold_zone_snapshot,
        "futures_page": client.get_native_futures_zone_snapshot,
        "us_market_page": client.get_native_us_market_zone_snapshot,
    }

    async def measure(name: str, call: object) -> dict:
        task_started = time.monotonic()
        try:
            result = await asyncio.wait_for(call(), timeout=timeout)
            data = result.get("data") or {}
            lists = [value for value in data.values() if isinstance(value, list)]
            status = str(result.get("status") or "").lower()
            return {
                "name": name,
                "success": (
                    status in {"", "ok", "success", "partial_success"}
                    and not bool(result.get("error") or result.get("message"))
                ),
                "status": result.get("status"),
                "seconds": round(time.monotonic() - task_started, 3),
                "error": result.get("error") or result.get("message"),
                "list_rows": sum(len(value) for value in lists),
                "nonempty_lists": sum(bool(value) for value in lists),
            }
        except Exception as exc:  # noqa: BLE001 - benchmark boundary
            return {
                "name": name,
                "success": False,
                "seconds": round(time.monotonic() - task_started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }

    try:
        results = await asyncio.gather(
            *(measure(name, call) for name, call in calls.items())
        )
    finally:
        await client.close()
    report = {
        "wall_seconds": round(time.monotonic() - started, 3),
        "success_count": sum(item["success"] for item in results),
        "total_count": len(results),
        "results": results,
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=105)
    args = parser.parse_args()
    asyncio.run(run(args.output, args.timeout))


if __name__ == "__main__":
    main()
