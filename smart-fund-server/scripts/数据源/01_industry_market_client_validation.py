#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.clients.eastmoney import EastmoneyClient
from src.infrastructure.clients.exchange_fund import ExchangeFundClient
from src.infrastructure.clients.market_calendar import MarketCalendarClient
from src.infrastructure.clients.pboc import PBOCClient
from src.infrastructure.clients.sina import SinaClient
from src.infrastructure.clients.tencent import TencentClient
from src.infrastructure.clients.ths import THSClient


async def _run_check(
    name: str,
    operation: Callable[[], Awaitable[dict]],
    timeout: float,
) -> dict:
    started_at = time.monotonic()
    try:
        result = await asyncio.wait_for(operation(), timeout=timeout)
        status = result.get("status", "legacy")
        data = result.get("data")
        count = None
        if isinstance(data, dict):
            count = data.get("count", data.get("total"))
        report = {
            "name": name,
            "status": status,
            "status_code": result.get("status_code"),
            "count": count,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "message": result.get("message") or result.get("msg"),
            "provider": result.get("provider"),
        }
    except Exception as exc:
        report = {
            "name": name,
            "status": "exception",
            "status_code": -1,
            "count": None,
            "elapsed_seconds": round(time.monotonic() - started_at, 3),
            "message": f"{type(exc).__name__}: {exc}",
            "provider": None,
        }
    print(
        f"{report['name']:<32} {report['status']:<16} "
        f"count={str(report['count']):<6} {report['elapsed_seconds']:>7.3f}s"
    )
    return report


async def validate(args: argparse.Namespace) -> dict:
    calendar = MarketCalendarClient()
    ths = THSClient(timeout=args.timeout)
    sina = SinaClient(timeout=args.timeout)
    eastmoney = EastmoneyClient(timeout=args.timeout)
    exchange_fund = ExchangeFundClient(timeout=args.timeout)
    pboc = PBOCClient(timeout=args.timeout)
    tencent = TencentClient(timeout=args.timeout)
    checks: list[tuple[str, Callable[[], Awaitable[dict]]]] = [
        ("calendar_cn", lambda: calendar.get_market_session("cn")),
        ("calendar_hk", lambda: calendar.get_market_session("hk")),
        ("calendar_us", lambda: calendar.get_market_session("us")),
        (
            "industry_catalog_ths",
            lambda: THSClient.get_sector_catalog.__wrapped__(ths, "industry"),
        ),
        (
            "concept_catalog_ths",
            lambda: THSClient.get_sector_catalog.__wrapped__(ths, "concept"),
        ),
        ("industry_snapshot_ths", lambda: ths.get_sector_snapshot("industry")),
        (
            "concept_snapshot_sina",
            lambda: SinaClient.get_sector_ranking.__wrapped__(sina, "concept", 20),
        ),
        ("market_breadth_eastmoney", eastmoney.get_market_breadth),
        ("market_limit_counts_ths", ths.get_market_limit_counts),
        ("industry_kline_1", lambda: ths.get_sector_kline("半导体", "industry", "20260101")),
        ("industry_kline_2", lambda: ths.get_sector_kline("白酒", "industry", "20260101")),
        ("concept_kline_1", lambda: ths.get_sector_kline("阿里巴巴概念", "concept", "20260101")),
        ("concept_kline_2", lambda: ths.get_sector_kline("人工智能", "concept", "20260101")),
        (
            "sector_intraday_ths",
            lambda: ths.get_sector_intraday("881121", sector_type="industry"),
        ),
        (
            "concept_intraday_ths",
            lambda: ths.get_sector_intraday("308700", sector_type="concept"),
        ),
        (
            "sector_constituents_sina",
            lambda: sina.get_sector_constituents(
                "hangye_ZB11",
                sector_type="industry",
            ),
        ),
        ("industry_money_flow", lambda: sina.get_sector_money_flow("industry", 20)),
        ("concept_money_flow", lambda: sina.get_sector_money_flow("concept", 20)),
        (
            "industry_margin_1d",
            lambda: eastmoney.get_sector_margin(
                "industry",
                interval_days=1,
            ),
        ),
        (
            "industry_margin_5d",
            lambda: eastmoney.get_sector_margin(
                "industry",
                interval_days=5,
            ),
        ),
        (
            "concept_margin_3d",
            lambda: eastmoney.get_sector_margin(
                "concept",
                interval_days=3,
            ),
        ),
        ("etf_catalog", sina.get_etf_catalog),
        ("etf_identity_510300", lambda: ths.get_etf_identity("510300")),
        ("etf_identity_159915", lambda: ths.get_etf_identity("159915")),
        ("etf_share_history_510300", lambda: ths.get_etf_share_history("510300")),
        (
            "etf_daily_shares_sse",
            lambda: exchange_fund.get_sse_etf_daily_shares("20260730"),
        ),
        (
            "etf_daily_shares_szse",
            lambda: exchange_fund.get_szse_etf_daily_shares(
                "20260728",
                "20260730",
            ),
        ),
        (
            "etf_daily_shares_cn",
            lambda: exchange_fund.get_etf_daily_shares("20260730"),
        ),
        ("etf_kline_510300", lambda: sina.get_etf_kline("510300", 60)),
        ("etf_kline_159915", lambda: sina.get_etf_kline("159915", 60)),
        ("benchmark_cn", lambda: sina.get_benchmark_kline("cn", "sh000300", 60)),
        ("benchmark_hk", lambda: sina.get_benchmark_kline("hk", "HSI", 60)),
        ("benchmark_us", lambda: sina.get_benchmark_kline("us", ".DJI", 60)),
        ("quote_cn_tencent", lambda: tencent.get_cross_market_quotes("cn", ["510300"])),
        (
            "quote_cn_index_tencent",
            lambda: tencent.get_cross_market_quotes("cn", ["sh000001"]),
        ),
        ("quote_hk_tencent", lambda: tencent.get_cross_market_quotes("hk", ["00700"])),
        ("quote_us_tencent", lambda: tencent.get_cross_market_quotes("us", ["AAPL"])),
        ("global_index_snapshot", sina.get_global_index),
        ("domestic_futures_snapshot", sina.get_futures),
        ("forex_snapshot", sina.get_forex),
        ("interest_rates", lambda: pboc.get_interest_rates(30)),
        ("government_bond_yields", lambda: pboc.get_government_bond_yields("20260101", 120)),
        (
            "commodity_domestic_au",
            lambda: sina.get_commodity_kline("AU0", start_date="20260101", limit=120),
        ),
        (
            "commodity_international_gc",
            lambda: sina.get_commodity_kline("GC", international=True, limit=120),
        ),
        (
            "futures_curve_au",
            lambda: sina.get_futures_term_structure(
                "AU",
                exchange="SHFE",
                trade_date="20260730",
                contract_limit=6,
            ),
        ),
        (
            "futures_curve_m_dce",
            lambda: sina.get_futures_term_structure(
                "M",
                exchange="DCE",
                trade_date="20260730",
                contract_limit=6,
            ),
        ),
        (
            "futures_curve_sc_ine",
            lambda: sina.get_futures_term_structure(
                "SC",
                exchange="INE",
                trade_date="20260730",
                contract_limit=6,
            ),
        ),
        ("inventory_copper", lambda: eastmoney.get_futures_inventory("沪铜")),
    ]
    if args.quick:
        omitted = {
            "concept_catalog_ths",
            "concept_kline_1",
            "concept_kline_2",
            "industry_kline_2",
            "etf_identity_159915",
            "etf_kline_159915",
            "sector_constituents_sina",
        }
        checks = [item for item in checks if item[0] not in omitted]

    try:
        reports = []
        for name, operation in checks:
            reports.append(await _run_check(name, operation, args.timeout))
    finally:
        for client in (ths, sina, eastmoney, exchange_fund, pboc, tencent):
            await client.close()

    counts = {
        status: sum(1 for report in reports if report["status"] == status)
        for status in ("ok", "empty", "upstream_error", "parse_error", "exception")
    }
    result = {
        "summary": {
            "total": len(reports),
            **counts,
            "passed": counts["ok"] + counts["empty"],
        },
        "checks": reports,
    }
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"report={output_path}")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="行业市场数据 Client 真实接口验收")
    parser.add_argument("--quick", action="store_true", help="跳过少量耗时的重复样本")
    parser.add_argument("--timeout", type=float, default=45.0, help="单项请求超时秒数")
    parser.add_argument("--output", help="可选 JSON 报告路径")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(validate(parse_args()))
