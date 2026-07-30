#!/usr/bin/env python3
"""Community Insight 高级认知索引链路验收脚本。

这个脚本从真实数据库读取少量 active community，调用正式的
CommunityInsightService 生成高级认知报告，并验证：

- PG 是否写入 / 更新 kg_community_insights；
- kg_graph_communities.last_insight_generated_at 是否更新；
- Milvus community_insight collection 是否可按 target_id 精准取回；
- 生成报告是否具备完整报告、结构化辅助字段和基本长度。

默认只处理 1 个 community，避免测试时产生过多 LLM 与 embedding 调用。

运行方式：

    python "docs/6. 使用说明/知识图谱/12_community_insight_refresh_validation.py"

常用参数：

    --limit 2
    --community-id kgc:financial:l0:509
    --dry-run
    --no-force
    --skip-refresh
    --timeout 180
    --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from pprint import pprint
from typing import Any

from sqlalchemy import select

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)


def _project_root() -> Path:
    for workspace_root in Path(__file__).resolve().parents:
        candidate = workspace_root / "smart-fund-server"
        if (candidate / "src").is_dir():
            return candidate
    raise RuntimeError("cannot locate smart-fund-server project root")


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.community_insight_service import CommunityInsightService  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import langfuse_flush  # noqa: E402
from src.infrastructure.persistence.models.knowledge import (  # noqa: E402
    KnowledgeCommunityInsight,
    KnowledgeGraphCommunity,
)
from src.infrastructure.vector_store.milvus_hybrid_store import (  # noqa: E402
    MILVUS_COLLECTION_COMMUNITY_INSIGHT,
    MilvusTypedHybridStore,
)


OUTPUT_FILE = Path(__file__).with_name("generated_community_insight_validation.json")


def _log(message: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(message, file=sys.stderr, flush=True)


class _StepTimer:
    def __init__(self, name: str, *, quiet: bool = False) -> None:
        self._name = name
        self._quiet = quiet
        self._started = 0.0

    def __enter__(self) -> "_StepTimer":
        self._started = time.perf_counter()
        _log(f"[community_insight_validation] {self._name} START", quiet=self._quiet)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = time.perf_counter() - self._started
        status = "FAILED" if exc_type else "DONE"
        _log(f"[community_insight_validation] {self._name} {status} duration={duration:.1f}s", quiet=self._quiet)
        return False


def _community_counts(community: KnowledgeGraphCommunity) -> tuple[int, int, int]:
    metrics = community.metrics or {}
    source_count = _int_metric(metrics.get("unique_source_count") or metrics.get("source_count"), len(community.evidence_ids or []))
    card_count = _int_metric(
        metrics.get("cognitive_card_count") or metrics.get("assigned_intent_count"),
        len(metrics.get("cognitive_card_ids") or []),
    )
    assignment_count = _int_metric(metrics.get("assignment_count") or metrics.get("assigned_intent_count"), 0)
    return source_count, card_count, assignment_count


def _load_sample_communities(
    *,
    target: str,
    community_ids: list[str],
    limit: int,
    scan_limit: int,
    force: bool,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    with get_session(target) as session:
        if community_ids:
            communities = session.scalars(
                select(KnowledgeGraphCommunity)
                .where(KnowledgeGraphCommunity.community_id.in_(community_ids))
                .where(KnowledgeGraphCommunity.status == "active")
            ).all()
            order = {community_id: index for index, community_id in enumerate(community_ids)}
            communities = sorted(communities, key=lambda item: order.get(item.community_id, len(order)))
        else:
            communities = session.scalars(
                select(KnowledgeGraphCommunity)
                .where(KnowledgeGraphCommunity.status == "active")
                .order_by(KnowledgeGraphCommunity.updated_at.desc())
                .limit(scan_limit)
            ).all()

        ids = [community.community_id for community in communities]
        insights = session.scalars(
            select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id.in_(ids))
        ).all() if ids else []
        insight_by_community = {insight.community_id: insight for insight in insights}

        selected: list[dict[str, Any]] = []
        for community in communities:
            source_count, card_count, assignment_count = _community_counts(community)
            if max(source_count, card_count) <= 1:
                continue
            insight = insight_by_community.get(community.community_id)
            due = force or _is_due(community, insight, now=now)
            if not due:
                continue
            selected.append(
                {
                    "community_id": community.community_id,
                    "title": community.title,
                    "updated_at": _iso(community.updated_at),
                    "last_insight_generated_at": _iso(community.last_insight_generated_at),
                    "source_count": source_count,
                    "cognitive_card_count": card_count,
                    "assignment_count": assignment_count,
                    "existing_insight": _insight_summary(insight),
                }
            )
            if len(selected) >= limit:
                break
        return selected


def _load_insights(*, target: str, community_ids: list[str]) -> dict[str, dict[str, Any]]:
    with get_session(target) as session:
        rows = session.scalars(
            select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id.in_(community_ids))
        ).all() if community_ids else []
    return {row.community_id: _insight_summary(row, include_preview=True) for row in rows}


def _load_communities_after(*, target: str, community_ids: list[str]) -> dict[str, dict[str, Any]]:
    with get_session(target) as session:
        rows = session.scalars(
            select(KnowledgeGraphCommunity).where(KnowledgeGraphCommunity.community_id.in_(community_ids))
        ).all() if community_ids else []
    return {
        row.community_id: {
            "updated_at": _iso(row.updated_at),
            "last_insight_generated_at": _iso(row.last_insight_generated_at),
        }
        for row in rows
    }


def _milvus_exact_hits(*, target: str, selected: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    store = MilvusTypedHybridStore()
    result: dict[str, dict[str, Any]] = {}
    for item in selected:
        community_id = item["community_id"]
        target_id = f"kgi:{community_id}"[:220]
        try:
            hits = store.get_documents(
                collection_role=MILVUS_COLLECTION_COMMUNITY_INSIGHT,
                adapter_name="financial",
                target=target,
                target_ids=[target_id],
            )
        except Exception as exc:
            result[community_id] = {
                "target_id": target_id,
                "hit": False,
                "error": str(exc)[:500],
            }
            continue
        result[community_id] = {
            "target_id": target_id,
            "hit": bool(hits),
            "text_chars": len(hits[0].text) if hits else 0,
            "metadata_keys": sorted((hits[0].metadata or {}).keys())[:30] if hits else [],
            "text_preview": hits[0].text[:240] if hits else "",
        }
    return result


async def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    output: dict[str, Any] = {
        "target": args.target,
        "limit": args.limit,
        "scan_limit": args.scan_limit,
        "force": args.force,
        "dry_run": args.dry_run,
        "skip_refresh": args.skip_refresh,
        "langfuse": {
            "session_id": os.getenv("KG_LANGFUSE_SESSION_ID") or os.getenv("LANGFUSE_SESSION_ID") or "",
            "trace_name": "kg.community_insight.refresh_ids",
            "llm_observation": "llm:kg_community_insight",
        },
    }

    with _StepTimer("select_communities", quiet=args.quiet):
        selected = _load_sample_communities(
            target=args.target,
            community_ids=args.community_id,
            limit=args.limit,
            scan_limit=args.scan_limit,
            force=args.force,
        )
    output["selected"] = selected

    if not selected:
        output["refresh_result"] = {
            "skipped": True,
            "reason": "no_eligible_community",
            "hint": "如果只是想重复验证链路，可加 --force 或指定 --community-id。",
        }
        output["duration_seconds"] = round(time.perf_counter() - started, 3)
        return output

    community_ids = [item["community_id"] for item in selected]
    if args.dry_run:
        output["refresh_result"] = {"skipped": True, "reason": "dry_run"}
        output["duration_seconds"] = round(time.perf_counter() - started, 3)
        return output

    service = CommunityInsightService(target=args.target)
    if args.skip_refresh:
        output["refresh_result"] = {"skipped": True, "reason": "skip_refresh"}
    else:
        with _StepTimer("refresh_insights", quiet=args.quiet):
            output["refresh_result"] = await asyncio.wait_for(
                service.refresh_community_ids(community_ids, force=args.force),
                timeout=args.timeout,
            )

    with _StepTimer("load_pg_results", quiet=args.quiet):
        output["pg_insights"] = _load_insights(target=args.target, community_ids=community_ids)
        output["communities_after"] = _load_communities_after(target=args.target, community_ids=community_ids)

    with _StepTimer("check_milvus", quiet=args.quiet):
        output["milvus"] = _milvus_exact_hits(target=args.target, selected=selected)

    output["quality_summary"] = _quality_summary(output)
    output["duration_seconds"] = round(time.perf_counter() - started, 3)
    return output


def _quality_summary(output: dict[str, Any]) -> dict[str, Any]:
    selected = output.get("selected") or []
    pg = output.get("pg_insights") or {}
    milvus = output.get("milvus") or {}
    return {
        "selected_count": len(selected),
        "pg_active_count": sum(1 for item in pg.values() if item.get("status") == "active"),
        "pg_report_min_chars": min((item.get("report_chars") or 0 for item in pg.values()), default=0),
        "pg_report_max_chars": max((item.get("report_chars") or 0 for item in pg.values()), default=0),
        "milvus_hit_count": sum(1 for item in milvus.values() if item.get("hit")),
        "report_json_missing_core_thesis": [
            community_id
            for community_id, item in pg.items()
            if "core_thesis" not in (item.get("report_json_keys") or [])
        ],
    }


def _insight_summary(row: KnowledgeCommunityInsight | None, *, include_preview: bool = False) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = {
        "insight_id": row.insight_id,
        "status": row.status,
        "insight_version": row.insight_version,
        "report_chars": len(row.insight_full_report or ""),
        "report_json_keys": sorted((row.report_json or {}).keys()),
        "updated_at": _iso(row.updated_at),
        "source_count": row.source_count,
        "cognitive_card_count": row.cognitive_card_count,
        "assignment_count": row.assignment_count,
    }
    if include_preview:
        payload["title"] = row.title
        payload["report_preview"] = (row.insight_full_report or "")[:500]
        payload["core_thesis"] = (row.report_json or {}).get("core_thesis")
        payload["quality_flags"] = (row.report_json or {}).get("quality_flags") or []
    return payload


def _is_due(
    community: KnowledgeGraphCommunity,
    insight: KnowledgeCommunityInsight | None,
    *,
    now: datetime,
) -> bool:
    if insight is None or insight.status != "active":
        return True
    generated_at = community.last_insight_generated_at or insight.updated_at
    updated_at = community.updated_at
    if generated_at is None:
        return True
    if updated_at is None or updated_at <= generated_at:
        return False
    return (now - updated_at).total_seconds() >= 300


def _int_metric(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 Community Insight 生成、PG 写入和 Milvus 索引链路")
    parser.add_argument("--target", default="prod", choices=["prod", "test"], help="数据库 / Milvus target")
    parser.add_argument("--limit", type=int, default=1, help="本次最多处理 community 数量")
    parser.add_argument("--scan-limit", type=int, default=80, help="未指定 community-id 时最多扫描的 community 数量")
    parser.add_argument("--community-id", action="append", default=[], help="指定 community_id，可重复")
    parser.add_argument("--no-force", dest="force", action="store_false", help="只处理 due community，不强制重刷")
    parser.set_defaults(force=True)
    parser.add_argument("--dry-run", action="store_true", help="只选择样本，不调用 LLM / embedding / Milvus 写入")
    parser.add_argument("--skip-refresh", action="store_true", help="不刷新正式 Insight，只检查已有 PG / Milvus 结果")
    parser.add_argument("--timeout", type=float, default=180.0, help="刷新阶段超时时间")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE, help="结果 JSON 输出路径")
    parser.add_argument("--langfuse-session-id", default="", help="指定本次验证写入 Langfuse 的 session_id")
    parser.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    parser.add_argument("--quiet", action="store_true", help="减少 stderr 日志")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _configure_langfuse_session(args)
    try:
        result = asyncio.run(run_validation(args))
    finally:
        langfuse_flush()

    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        pprint(result)
        print(f"\n[community_insight_validation] wrote {args.output}", file=sys.stderr)


def _configure_langfuse_session(args: argparse.Namespace) -> None:
    if args.dry_run:
        return
    session_id = args.langfuse_session_id.strip()
    if not session_id:
        suffix = "-".join(args.community_id[:3]) if args.community_id else f"limit-{args.limit}"
        session_id = f"community-insight-validation-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{suffix}"
    os.environ["KG_LANGFUSE_SESSION_ID"] = session_id[:190]
    if not args.quiet:
        print(
            "[community_insight_validation] langfuse_session_id="
            f"{os.environ['KG_LANGFUSE_SESSION_ID']} trace_name=kg.community_insight.refresh_ids",
            file=sys.stderr,
            flush=True,
        )


if __name__ == "__main__":
    main()
