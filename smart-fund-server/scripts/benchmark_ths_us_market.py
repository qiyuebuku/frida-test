#!/usr/bin/env python3
"""Measure one US-market collector module against the production THS bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

from src.infrastructure.clients.ths import THSClient


COLLECTORS = {
    "overview": "get_native_us_overview_snapshot",
    "sectors": "get_native_us_sector_snapshot",
    "stock_rankings": "get_native_us_stock_rankings_snapshot",
    "etf_sectors": "get_native_us_etf_sectors_snapshot",
}


def _row_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        return 0
    data_dict = value.get("dataDict")
    if isinstance(data_dict, dict):
        return max(
            (len(item) for item in data_dict.values() if isinstance(item, list)),
            default=0,
        )
    return max((_row_count(item) for item in value.values()), default=0)


def _summarize(module: str, result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") or {}
    metadata = result.get("provider_metadata") or {}
    summary: dict[str, Any] = {
        "status": result.get("status"),
        "complete": metadata.get("complete"),
        "failed_modules": metadata.get("failed_modules") or [],
        "payload_bytes": len(json.dumps(result, ensure_ascii=False, default=str)),
    }
    if module == "overview":
        summary["breadth_today_rows"] = _row_count(data.get("breadth_today"))
        summary["breadth_month_rows"] = _row_count(data.get("breadth_month"))
    elif module == "sectors":
        groups = data.get("sectors") or {}
        summary["groups"] = {
            key: _row_count(value) for key, value in groups.items()
        }
    elif module == "stock_rankings":
        groups = data.get("stock_rankings") or {}
        summary["groups"] = {
            key: _row_count(value) for key, value in groups.items()
        }
    elif module == "etf_sectors":
        config = data.get("etf_sector_config") or {}
        categories = ((config.get("data") or {}).get("items") or [])
        details = data.get("etf_sector_details") or {}
        summary["category_count"] = len(categories)
        summary["detail_count"] = len(details)
        summary["groups"] = {
            key: _row_count(value) for key, value in details.items()
        }
    return summary


async def benchmark(module: str, rounds: int) -> dict[str, Any]:
    client = THSClient()
    samples = []
    try:
        collector = getattr(client, COLLECTORS[module])
        for round_number in range(1, rounds + 1):
            started = time.monotonic()
            result = await collector()
            elapsed = time.monotonic() - started
            samples.append({
                "round": round_number,
                "elapsed_seconds": round(elapsed, 3),
                **_summarize(module, result),
            })
    finally:
        await client.close()
    durations = [sample["elapsed_seconds"] for sample in samples]
    return {
        "module": module,
        "rounds": rounds,
        "min_seconds": min(durations),
        "mean_seconds": round(statistics.mean(durations), 3),
        "max_seconds": max(durations),
        "success_rounds": sum(bool(sample["complete"]) for sample in samples),
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("module", choices=tuple(COLLECTORS))
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(
        asyncio.run(benchmark(args.module, args.rounds)),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
