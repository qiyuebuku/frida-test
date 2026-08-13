"""Validate THS AStockSector client methods against the Android VM bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.clients.ths import THSClient


async def run(count: int) -> dict:
    client = THSClient()
    results: dict[str, dict] = {}
    try:
        for classification in ("all", "industry", "concept", "style", "region"):
            for metric in ("change", "limit_up_count"):
                key = f"ranking:{classification}:{metric}"
                results[key] = await client.get_native_sector_ranking(
                    metric,
                    count,
                    classification,
                )
        for sector_type in ("industry", "concept", "region"):
            results[f"flow:{sector_type}"] = (
                await client.get_native_sector_fund_flow(sector_type, count)
            )
        results["hot:concept"] = await client.get_native_hot_boards(
            "concept",
            count,
        )
        results["hot:industry"] = await client.get_native_hot_boards(
            "industry",
            count,
        )
        results["hot:index"] = await client.get_native_hot_boards(
            "index",
            count,
        )
    finally:
        await client.close()

    summary = {}
    failures = []
    for key, result in results.items():
        data = result.get("data") or {}
        summary[key] = {
            "status": result.get("status"),
            "count": data.get("count"),
            "first": (data.get("sectors") or [None])[0],
            "error": result.get("error"),
        }
        if result.get("status") != "ok":
            failures.append(key)
    return {"summary": summary, "failures": failures}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    result = asyncio.run(run(max(1, min(args.count, 20))))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result["failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
