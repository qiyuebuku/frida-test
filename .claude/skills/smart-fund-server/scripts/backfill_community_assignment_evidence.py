#!/usr/bin/env python3
"""为旧 Community Assignment 回填精确原文证据。

默认只预览候选，不调用 LLM、不写数据库。加 ``--apply`` 后才会执行回填。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.community_assignment_evidence_backfill_service import (  # noqa: E402
    AssignmentEvidenceBackfillCommand,
    CommunityAssignmentEvidenceBackfillService,
)
from src.infrastructure.observability.langfuse_tracing import langfuse_flush  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按旧 topic intent 从 Milvus 原始 chunk 回填 evidence_span",
    )
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--target", default="prod", choices=["prod", "test"])
    parser.add_argument(
        "--community-id",
        action="append",
        default=[],
        help="只处理指定 community，可重复传入；不传则扫描全部 active Assignment",
    )
    parser.add_argument("--limit", type=int, default=100, help="本次最多处理多少个缺证据 intent")
    parser.add_argument(
        "--reprocess-source-id",
        action="append",
        default=[],
        help="强制重做指定 source_id 的证据选段，可重复传入",
    )
    parser.add_argument("--scan-limit", type=int, default=5000, help="最多扫描多少条 Assignment")
    parser.add_argument("--concurrency", type=int, default=4, help="LLM 回填并发数")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行 LLM 回填并写入 PG/Milvus；默认仅预览",
    )
    parser.add_argument(
        "--refresh-insights",
        action="store_true",
        help="回填完成后立即强制刷新受影响的 Community Insight",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict:
    if args.refresh_insights and not args.apply:
        raise ValueError("--refresh-insights 必须与 --apply 一起使用")
    service = CommunityAssignmentEvidenceBackfillService(target=args.target)
    return await service.backfill(
        AssignmentEvidenceBackfillCommand(
            adapter_name=args.adapter,
            target=args.target,
            community_ids=tuple(dict.fromkeys(args.community_id)),
            reprocess_source_ids=tuple(dict.fromkeys(args.reprocess_source_id)),
            limit=args.limit,
            scan_limit=args.scan_limit,
            concurrency=args.concurrency,
            dry_run=not args.apply,
            refresh_insights=args.refresh_insights,
        )
    )


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        langfuse_flush()


if __name__ == "__main__":
    main()
