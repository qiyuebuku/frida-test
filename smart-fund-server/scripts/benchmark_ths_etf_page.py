#!/usr/bin/env python3
"""Benchmark every data path consumed by the ETF observability page."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.clients.ths import THSClient


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
    return round(ordered[index], 2)


def _summary(values: list[float]) -> dict[str, float | None]:
    return {
        "count": len(values),
        "min_ms": round(min(values), 2) if values else None,
        "mean_ms": round(statistics.fmean(values), 2) if values else None,
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": round(max(values), 2) if values else None,
    }


async def _timed(call: Callable[[], Awaitable[Any]]) -> tuple[float, Any]:
    started = time.perf_counter()
    result = await call()
    return (time.perf_counter() - started) * 1000, result


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def benchmark_api(base_url: str, samples: int) -> dict[str, Any]:
    paths = {
        "zone_snapshot": "/api/market-observability/snapshots?data_type=ths_etf_zone&limit=1",
        "native_home_rankings": "/api/market-observability/snapshots?data_type=ths_etf_home_ranking&limit=10",
        "estimated_flow_history": "/api/market-observability/history?data_type=etf_estimated_net_inflow&subject_id=cn%3Aetf%3Aszse%3Aestimated_net_inflow&limit=500",
    }
    report: dict[str, Any] = {}
    async with httpx.AsyncClient(base_url=base_url, timeout=20) as client:
        for name, path in paths.items():
            timings: list[float] = []
            payload = None
            for _ in range(samples):
                duration, response = await _timed(lambda: client.get(path))
                response.raise_for_status()
                timings.append(duration)
                payload = response.json()
            items = (payload or {}).get("items") or []
            bytes_size = len(json.dumps(payload, ensure_ascii=False).encode())
            report[name] = {
                **_summary(timings),
                "response_bytes": bytes_size,
                "item_count": len(items),
            }
            if name == "zone_snapshot" and items:
                item = items[0]
                data = item.get("data") or {}
                fetched_at = _parse_time(item.get("fetched_at"))
                report[name].update({
                    "freshness_status": item.get("freshness_status"),
                    "age_seconds": round(
                        (datetime.now(timezone.utc) - fetched_at).total_seconds(), 2
                    ) if fetched_at else None,
                    "etf_count": data.get("etf_count"),
                    "full_ranking_count": (data.get("full_ranking") or {}).get("count"),
                    "track_filtered_count": (data.get("track_filtered_ranking") or {}).get("count"),
                    "tracking_index_count": (data.get("tracking_index_filtered_ranking") or {}).get("count"),
                    "quote_batch_count": len(data.get("etf_quotes") or []),
                })
            elif name == "native_home_rankings":
                now = datetime.now(timezone.utc)
                report[name]["categories"] = [{
                    "category": item.get("subject_id"),
                    "row_count": len((item.get("data") or {}).get("rows") or []),
                    "age_seconds": round(
                        (now - fetched_at).total_seconds(), 2
                    ) if (fetched_at := _parse_time(item.get("fetched_at"))) else None,
                } for item in items]
            elif name == "estimated_flow_history" and items:
                newest = items[0]
                fetched_at = _parse_time(newest.get("fetched_at"))
                report[name].update({
                    "age_seconds": round(
                        (datetime.now(timezone.utc) - fetched_at).total_seconds(), 2
                    ) if fetched_at else None,
                    "point_count": len(items),
                })
    return report


async def benchmark_sources(samples: int) -> dict[str, Any]:
    client = THSClient()
    report: dict[str, Any] = {}
    try:
        zone_timings: list[float] = []
        zone_results: list[dict] = []
        for _ in range(samples):
            duration, result = await _timed(client.get_native_etf_zone_snapshot)
            zone_timings.append(duration)
            zone_results.append(result)
        latest_zone = zone_results[-1].get("data") or {}
        report["etf_zone_source"] = {
            **_summary(zone_timings),
            "runs_ms": [round(item, 2) for item in zone_timings],
            "status": zone_results[-1].get("status"),
            "etf_count": latest_zone.get("etf_count"),
            "quote_batch_count": len(latest_zone.get("etf_quotes") or []),
            "full_ranking_count": (latest_zone.get("full_ranking") or {}).get("count"),
            "track_filtered_count": (latest_zone.get("track_filtered_ranking") or {}).get("count"),
            "tracking_index_count": (latest_zone.get("tracking_index_filtered_ranking") or {}).get("count"),
            "hot_categories": sorted((latest_zone.get("hot_rankings") or {}).keys()),
            "complete": (zone_results[-1].get("provider_metadata") or {}).get("complete"),
        }

        flow_timings: list[float] = []
        flow_results: list[dict] = []
        for _ in range(samples):
            duration, result = await _timed(client.get_etf_estimated_net_inflow)
            flow_timings.append(duration)
            flow_results.append(result)
        latest_flow = flow_results[-1].get("data") or {}
        report["estimated_flow_source"] = {
            **_summary(flow_timings),
            "runs_ms": [round(item, 2) for item in flow_timings],
            "status": flow_results[-1].get("status"),
            "trend_point_count": len(latest_flow.get("trend") or []),
            "has_top_inflow": bool(latest_flow.get("top_inflow")),
            "has_benchmark_trend": bool(latest_flow.get("benchmark_trend")),
        }
    finally:
        await client.close()
    return report


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8900")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--api-only", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    started = time.perf_counter()
    report = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "api": await benchmark_api(args.base_url, args.samples),
    }
    if not args.api_only:
        report["sources"] = await benchmark_sources(args.samples)
    report["wall_duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    asyncio.run(main())
