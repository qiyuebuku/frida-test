#!/usr/bin/env python3
"""Benchmark the complete THS market-data workload with production routing."""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.clients.ths import THSClient


CallFactory = Callable[[], Awaitable[dict]]


@dataclass(frozen=True)
class Probe:
    name: str
    lane: str
    call: CallFactory


def _result_count(result: dict) -> int | None:
    data = result.get("data") or {}
    if data.get("count") is not None:
        return int(data["count"])
    for key in ("stocks", "sectors", "groups", "items", "periods", "points"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def _is_success(result: dict) -> bool:
    status = str(result.get("status") or "").strip().lower()
    return (
        not result.get("error")
        and not result.get("message")
        and status in {"", "ok", "success", "partial_success"}
    )


def _failure_detail(result: dict) -> str | None:
    direct = result.get("error") or result.get("message")
    if direct:
        return str(direct)
    metadata = result.get("provider_metadata") or {}
    for key in ("error", "message", "errors", "warnings"):
        value = metadata.get(key)
        if value:
            return json.dumps(value, ensure_ascii=False, default=str)
    status = str(result.get("status") or "").strip()
    return (
        f"status={status}"
        if status and status.lower() not in {"ok", "success", "partial_success"}
        else None
    )


async def _timed(probe: Probe, benchmark_started: float) -> dict:
    started = time.monotonic()
    try:
        result = await probe.call()
        success = _is_success(result)
        return {
            "name": probe.name,
            "lane": probe.lane,
            "success": success,
            "status": result.get("status"),
            "count": _result_count(result),
            "error": _failure_detail(result),
            "started_ms": round((started - benchmark_started) * 1000),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "completed_ms": round((time.monotonic() - benchmark_started) * 1000),
        }
    except Exception as exc:
        return {
            "name": probe.name,
            "lane": probe.lane,
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "started_ms": round((started - benchmark_started) * 1000),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "completed_ms": round((time.monotonic() - benchmark_started) * 1000),
        }


def _build_report(
    *,
    client: THSClient,
    results: list[dict],
    started: float,
    expected_count: int,
    deadline_reached: bool,
    execution_errors: list[str] | None = None,
) -> dict:
    lane_results: dict[str, list[dict]] = defaultdict(list)
    for result in results:
        lane_results[result["lane"]].append(result)
    lanes = {
        lane: {
            "count": len(items),
            "success_count": sum(bool(item["success"]) for item in items),
            "failed_count": sum(not item["success"] for item in items),
            "elapsed_ms": max(item["completed_ms"] for item in items),
            "request_duration_ms": sum(item["duration_ms"] for item in items),
            "slowest": sorted(
                (
                    {"name": item["name"], "duration_ms": item["duration_ms"]}
                    for item in items
                ),
                key=lambda item: item["duration_ms"],
                reverse=True,
            )[:5],
        }
        for lane, items in sorted(lane_results.items())
    }
    failures = [item for item in results if not item["success"]]
    task_errors = execution_errors or []
    return {
        "success": (
            len(results) == expected_count and not failures and not task_errors
        ),
        "deadline_reached": deadline_reached,
        "wall_duration_ms": round((time.monotonic() - started) * 1000),
        "expected_probe_count": expected_count,
        "completed_probe_count": len(results),
        "success_count": len(results) - len(failures),
        "failed_count": len(failures),
        "execution_errors": task_errors,
        "native_bridge_url": client._native_bridge_url,
        "app_http_bridge_url": client._app_http_bridge_url,
        "lanes": lanes,
        "failures": failures,
        "results": sorted(results, key=lambda item: item["completed_ms"]),
    }


def _write_report(path: str | None, report: dict) -> None:
    if path:
        Path(path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )


async def _active_sector_references(
    client: THSClient,
    count: int,
    sector_count: int,
) -> dict:
    hot_results = await asyncio.gather(
        client.get_native_hot_boards("concept", 50),
        client.get_native_hot_boards("industry", 50),
        client.get_native_hot_boards("index", 50),
    )
    candidates: list[dict] = []
    seen: set[str] = set()
    for result in hot_results:
        for sector in (result.get("data") or {}).get("sectors") or []:
            code = str(sector.get("provider_sector_code") or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            candidates.append(sector)
    responses = await asyncio.gather(
        *(
            client.get_native_sector_constituents(
                str(sector["provider_sector_code"]),
                market_code=str(sector.get("market_code") or "48"),
                count=count,
            )
            for sector in candidates[:sector_count]
        )
    )
    selected_candidates = candidates[:sector_count]
    failures = [
        (sector, item)
        for sector, item in zip(selected_candidates, responses, strict=True)
        if not _is_success(item)
    ]
    failure_details = [
        (
            f"{sector.get('sector_type') or 'unknown'}:"
            f"{sector.get('provider_sector_code')}:"
            f"{_failure_detail(item) or 'unknown sector reference failure'}"
        )
        for sector, item in failures
    ]
    constituent_count = sum(
        len((item.get("data") or {}).get("constituents") or [])
        for item in responses
    )
    return {
        "status": "ok" if responses and not failures else "error",
        "data": {"count": constituent_count},
        "error": (
            None
            if responses and not failures
            else "; ".join(failure_details) or "sector reference failed"
        ),
    }


def _build_probes(
    client: THSClient,
    *,
    row_count: int,
    reference_sector_count: int,
    include_cold_dynamic_groups: bool,
) -> list[Probe]:
    probes = [
        Probe("market_anomalies", "owner_native", client.get_native_market_anomalies),
        Probe("call_auction", "owner_native", client.get_native_call_auction),
        Probe(
            "dynamic_groups_home",
            "owner_native",
            lambda: client.get_native_stock_dynamic_groups(4, homepage_layout=True),
        ),
        Probe(
            "hot_boards_concept",
            "owner_native",
            lambda: client.get_native_hot_boards("concept", 50),
        ),
        Probe(
            "hot_boards_industry",
            "owner_native",
            lambda: client.get_native_hot_boards("industry", 50),
        ),
        Probe(
            "active_sector_references",
            "owner_native",
            lambda: _active_sector_references(
                client,
                row_count,
                reference_sector_count,
            ),
        ),
        Probe(
            "hot_boards_index",
            "direct_http",
            lambda: client.get_native_hot_boards("index", 50),
        ),
        Probe(
            "northbound_history",
            "direct_http",
            client.get_northbound_turnover_history,
        ),
        Probe(
            "sentiment_sh50",
            "direct_http",
            lambda: client.get_index_sentiment_history("sh50"),
        ),
        Probe(
            "sentiment_growth",
            "direct_http",
            lambda: client.get_index_sentiment_history("growth"),
        ),
        Probe(
            "valuation_thresholds",
            "direct_http",
            client.get_market_valuation_thresholds,
        ),
        Probe(
            "etf_estimated_net_inflow",
            "direct_http",
            client.get_etf_estimated_net_inflow,
        ),
        Probe(
            "sector_snapshot_industry",
            "direct_http",
            lambda: client.get_sector_snapshot("industry"),
        ),
        Probe(
            "industry_opportunity",
            "app_http",
            client.get_native_industry_opportunities,
        ),
    ]
    if include_cold_dynamic_groups:
        probes.append(
            Probe(
                "dynamic_groups_cold_candidates",
                "owner_native",
                lambda: client.get_native_stock_dynamic_groups(100),
            )
        )

    for indicator in (
        "market_capital",
        "market_temperature",
        "northbound_capital",
        "reverse_repo",
    ):
        probes.append(
            Probe(
                f"realtime_{indicator}",
                "owner_native",
                lambda indicator=indicator: client.get_native_realtime_indicator(
                    indicator
                ),
            )
        )

    for mode in (
        "rise",
        "fall",
        "quick",
        "turnover",
        "large_order",
        "volume_ratio",
        "turnover_rate",
        "main_net_inflow",
        "amplitude",
    ):
        probes.append(
            Probe(
                f"stock_ranking_{mode}",
                "profile_native",
                lambda mode=mode: client.get_native_stock_ranking(mode, row_count),
            )
        )

    for classification in ("all", "industry", "concept", "style", "region"):
        for metric in ("change", "speed", "volume_ratio", "limit_up_count"):
            lane = "owner_native" if metric == "limit_up_count" else "profile_native"
            probes.append(
                Probe(
                    f"sector_ranking_{classification}_{metric}",
                    lane,
                    lambda classification=classification, metric=metric: (
                        client.get_native_sector_ranking(
                            metric,
                            row_count,
                            classification,
                        )
                    ),
                )
            )

    for sector_type in ("industry", "concept", "region"):
        probes.append(
            Probe(
                f"sector_flow_{sector_type}",
                "profile_native",
                lambda sector_type=sector_type: client.get_native_sector_fund_flow(
                    sector_type,
                    500,
                ),
            )
        )

    for tenor in ("long", "short", "benchmark"):
        probes.append(
            Probe(
                f"bond_{tenor}",
                "profile_native",
                lambda tenor=tenor: client.get_native_bond_market_history(tenor),
            )
        )

    probes.extend(
        [
            Probe(
                "sector_prosperity",
                "profile_native",
                lambda: client.get_native_sector_prosperity(50),
            ),
            Probe(
                "sector_commodity_linkage",
                "profile_native",
                lambda: client.get_native_sector_commodity_linkage(500),
            ),
        ]
    )

    for sector_type in ("industry", "concept"):
        for metric in (
            "change",
            "five_day_change",
            "rise_rate",
            "limit_up_count",
            "main_net_inflow",
        ):
            probes.append(
                Probe(
                    f"rotation_{sector_type}_{metric}",
                    "app_http",
                    lambda sector_type=sector_type, metric=metric: (
                        client.get_native_sector_rotation(
                            sector_type=sector_type,
                            metric=metric,
                            day_count=60,
                            sector_count=10,
                        )
                    ),
                )
            )
    return probes


async def run(args: argparse.Namespace) -> dict:
    client = THSClient(timeout=args.timeout)
    started = time.monotonic()
    results: list[dict] = []
    result_lock = asyncio.Lock()
    deadline_reached = False
    execution_errors: list[str] = []
    probes = _build_probes(
        client,
        row_count=args.row_count,
        reference_sector_count=args.reference_sector_count,
        include_cold_dynamic_groups=args.include_cold_dynamic_groups,
    )
    if args.lane:
        selected_lanes = set(args.lane)
        probes = [probe for probe in probes if probe.lane in selected_lanes]
    if args.probe:
        probes = [
            probe
            for probe in probes
            if any(fnmatch.fnmatch(probe.name, pattern) for pattern in args.probe)
        ]
    if not probes:
        raise ValueError("No probes matched --lane/--probe filters")

    async def record(probe: Probe) -> None:
        try:
            result = await asyncio.wait_for(
                _timed(probe, started),
                timeout=args.probe_timeout,
            )
        except asyncio.TimeoutError:
            result = {
                "name": probe.name,
                "lane": probe.lane,
                "success": False,
                "error": f"probe exceeded {args.probe_timeout}s",
                "started_ms": None,
                "duration_ms": round(args.probe_timeout * 1000),
                "completed_ms": round((time.monotonic() - started) * 1000),
            }
        async with result_lock:
            results.append(result)
            _write_report(
                args.output,
                _build_report(
                    client=client,
                    results=results,
                    started=started,
                    expected_count=len(probes),
                    deadline_reached=False,
                    execution_errors=execution_errors,
                ),
            )
            completed_count = len(results)
        print(
            f"[{completed_count:02d}/{len(probes):02d}] {probe.lane} "
            f"{probe.name}: {result['duration_ms']}ms "
            f"{'ok' if result['success'] else 'failed'}",
            flush=True,
        )

    async def run_serial_lane(lane_probes: list[Probe]) -> None:
        for probe in lane_probes:
            await record(probe)

    async def run_concurrent_lane(lane_probes: list[Probe]) -> None:
        await asyncio.gather(*(record(probe) for probe in lane_probes))

    try:
        grouped: dict[str, list[Probe]] = defaultdict(list)
        for probe in probes:
            grouped[probe.lane].append(probe)
        native_probes = [
            probe
            for probe in probes
            if probe.lane in {"owner_native", "profile_native"}
        ]
        lane_tasks: list[asyncio.Task] = []
        if native_probes:
            # One App owns one native transport. Running owner/profile lanes as
            # separate tasks only measures lock queue time and can trip each
            # probe's timeout before its HTTP request starts.
            lane_tasks.append(asyncio.create_task(run_serial_lane(native_probes)))
        for lane, lane_probes in grouped.items():
            if lane in {"owner_native", "profile_native"}:
                continue
            lane_tasks.append(
                asyncio.create_task(
                    run_concurrent_lane(lane_probes)
                    if lane in {"direct_http", "app_http"}
                    else run_serial_lane(lane_probes)
                )
            )
        done, pending = await asyncio.wait(lane_tasks, timeout=args.deadline)
        if pending:
            deadline_reached = True
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        completed = await asyncio.gather(*done, return_exceptions=True)
        for result in completed:
            if isinstance(result, BaseException):
                detail = f"{type(result).__name__}: {result}"
                execution_errors.append(detail)
                print(f"execution task failed: {detail}", flush=True)
    finally:
        try:
            await asyncio.wait_for(client.close(), timeout=10)
        except asyncio.TimeoutError:
            pass
    report = _build_report(
        client=client,
        results=results,
        started=started,
        expected_count=len(probes),
        deadline_reached=deadline_reached,
        execution_errors=execution_errors,
    )
    _write_report(args.output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--row-count", type=int, default=50)
    parser.add_argument("--reference-sector-count", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--probe-timeout", type=int, default=45)
    parser.add_argument("--deadline", type=int, default=600)
    parser.add_argument("--include-cold-dynamic-groups", action="store_true")
    parser.add_argument(
        "--lane",
        action="append",
        help="Run only this lane. May be repeated.",
    )
    parser.add_argument(
        "--probe",
        action="append",
        help="Run only matching probe names; shell-style wildcards are supported.",
    )
    parser.add_argument("--output")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Write the complete report to --output and print only aggregate results.",
    )
    args = parser.parse_args()
    report = asyncio.run(run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    if args.summary_only:
        summary = {
            key: report[key]
            for key in (
                "success",
                "deadline_reached",
                "wall_duration_ms",
                "expected_probe_count",
                "completed_probe_count",
                "success_count",
                "failed_count",
                "lanes",
                "failures",
            )
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        print(rendered)
    raise SystemExit(0 if report["success"] else 1)


if __name__ == "__main__":
    main()
