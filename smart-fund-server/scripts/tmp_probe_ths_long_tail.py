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


async def timed(name: str, call) -> dict:
    started = time.monotonic()
    try:
        result = await call()
        data = result.get("data") or {}
        return {
            "name": name,
            "seconds": round(time.monotonic() - started, 3),
            "status": result.get("status"),
            "count": data.get("count"),
            "error": result.get("error") or result.get("message"),
            "result": result,
        }
    except Exception as exc:
        return {
            "name": name,
            "seconds": round(time.monotonic() - started, 3),
            "status": "exception",
            "error": f"{type(exc).__name__}: {exc}",
        }


async def main() -> None:
    client = THSClient(timeout=120)
    report: list[dict] = []
    try:
        hot_results = await asyncio.gather(
            timed("hot_concept", lambda: client.get_native_hot_boards("concept", 50)),
            timed("hot_industry", lambda: client.get_native_hot_boards("industry", 50)),
            timed("hot_index", lambda: client.get_native_hot_boards("index", 50)),
        )
        report.extend(hot_results)
        seen: set[str] = set()
        candidates: list[dict] = []
        for item in hot_results:
            for sector in ((item.get("result") or {}).get("data") or {}).get("sectors") or []:
                code = str(sector.get("provider_sector_code") or "")
                if code and code not in seen:
                    seen.add(code)
                    candidates.append(sector)
        for index, sector in enumerate(candidates[:3], start=1):
            code = str(sector.get("provider_sector_code") or "")
            result = await timed(
                f"constituents_{index}_{code}",
                lambda code=code, sector=sector: client.get_native_sector_constituents(
                    code,
                    market_code=str(sector.get("market_code") or "48"),
                    count=1000,
                ),
            )
            result.pop("result", None)
            report.append(result)
        commodity = await timed(
            "commodity_linkage_500",
            lambda: client.get_native_sector_commodity_linkage(500),
        )
        commodity.pop("result", None)
        report.append(commodity)
    finally:
        await client.close()
    for item in report:
        item.pop("result", None)
    print(json.dumps(report, ensure_ascii=False, indent=2))


asyncio.run(main())
