#!/usr/bin/env python3
"""Community Insight 全链路演示脚本。

本脚本用于观察本轮改造后的完整链路：

1. 从真实 ft_news 读取少量新闻；
2. 投影为 financial KG source records；
3. 调用正式 KnowledgeService.compile_kg()，触发：
   - Evidence / Chunk 写入；
   - Cognitive Card 提取；
   - Community Assignment；
   - Graph Community 更新；
4. 对本轮更新到的 community 调用 CommunityInsightService.refresh_community_ids()；
5. 汇总 Card / Assignment / Community / Insight 的关键结果。

默认会给 source_id 增加本次 run 前缀，保证重复运行也能触发全链路。
如果需要按真实 ft_news source_id 验证增量行为，可加 --no-scope-source-id。

示例：

    python "docs/6. 使用说明/知识图谱/13_community_insight_e2e_demo.py" --limit 3
    python "docs/6. 使用说明/知识图谱/13_community_insight_e2e_demo.py" --news-id 109001 --news-id 109006
    python "docs/6. 使用说明/知识图谱/13_community_insight_e2e_demo.py" --stage 2 --limit 3
    python "docs/6. 使用说明/知识图谱/13_community_insight_e2e_demo.py" --limit 2 --dry-run
    python "docs/6. 使用说明/知识图谱/13_community_insight_e2e_demo.py" --limit 3 --compare-insight-models
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from pprint import pprint
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect, select

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

from src.application.dto.knowledge_dto import KnowledgeCompileCommand  # noqa: E402
from src.application.services.cognitive_index_service import CognitiveCardExtractor  # noqa: E402
from src.application.services.community_insight_service import CommunityInsightService  # noqa: E402
from src.application.services.knowledge_adapter_registry import get_adapter  # noqa: E402
from src.application.services.knowledge_service import create_knowledge_service  # noqa: E402
from src.application.services.knowledge_service import _normalize_records  # noqa: E402
from src.domain.knowledge.chunking import build_chunks_for_compiled_evidence  # noqa: E402
from src.domain.knowledge.compiler import KnowledgeCompiler  # noqa: E402
from src.domain.knowledge_adapters.financial.source_projection import project_ft_news_row  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.collection import News  # noqa: E402
from src.infrastructure.persistence.models.knowledge import (  # noqa: E402
    KnowledgeCognitiveCard,
    KnowledgeCommunityAssignment,
    KnowledgeCommunityInsight,
    KnowledgeGraphCommunity,
)


OUTPUT_FILE = Path(__file__).with_name("generated_community_insight_e2e_demo.json")
TRACE_NAME = "kg.community_insight.e2e_demo"
STAGE_DESCRIPTIONS = {
    1: "load/project records",
    2: "compile evidence + extract cognitive cards only",
    3: "compile through community assignment",
    4: "refresh community insight",
}


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
        _log(f"[community_insight_e2e] {self._name} START", quiet=self._quiet)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = time.perf_counter() - self._started
        status = "FAILED" if exc_type else "DONE"
        _log(f"[community_insight_e2e] {self._name} {status} duration={duration:.1f}s", quiet=self._quiet)
        return False


async def _await_with_heartbeat(
    name: str,
    awaitable,
    *,
    quiet: bool,
    interval_seconds: float = 15.0,
):
    started = time.perf_counter()
    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=interval_seconds)
            if not task.done():
                elapsed = time.perf_counter() - started
                _log(f"[community_insight_e2e] {name} still running elapsed={elapsed:.1f}s", quiet=quiet)
        return await task
    except Exception:
        if not task.done():
            task.cancel()
        raise


async def run_demo(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    run_id = args.run_id or f"community_insight_e2e:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}:{uuid4().hex[:8]}"
    session_id = args.session_id or os.getenv("KG_LANGFUSE_SESSION_ID") or f"{run_id}:session"
    os.environ["KG_LANGFUSE_SESSION_ID"] = session_id

    metadata = {
        "run_id": run_id,
        "session_id": session_id,
        "target": args.target,
        "adapter": args.adapter,
        "limit": args.limit,
        "candidate_limit": args.candidate_limit,
        "news_ids": args.news_id,
        "dry_run": args.dry_run,
        "scope_source_id": args.scope_source_id,
        "force_insight": args.force_insight,
        "skip_insight": args.skip_insight,
        "stage": args.stage,
        "stage_name": STAGE_DESCRIPTIONS[args.stage],
    }
    with langfuse_propagation_context(
        trace_name=TRACE_NAME,
        session_id=session_id,
        tags=["kg", "community_insight", "e2e_demo"],
        metadata=metadata,
    ):
        with langfuse_observation(
            name=TRACE_NAME,
            as_type="chain",
            input=metadata,
            metadata=metadata,
        ):
            try:
                output = await _run_demo_inner(args, run_id=run_id, metadata=metadata)
                output["duration_seconds"] = round(time.perf_counter() - started, 3)
                langfuse_update_span(output=_compact_for_trace(output), status_message="completed")
                return output
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise
            finally:
                langfuse_flush()


async def _run_demo_inner(args: argparse.Namespace, *, run_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {
        "run_id": run_id,
        "target": args.target,
        "adapter": args.adapter,
        "stage": {
            "number": args.stage,
            "name": STAGE_DESCRIPTIONS[args.stage],
            "available": STAGE_DESCRIPTIONS,
        },
        "langfuse": {
            "session_id": metadata["session_id"],
            "trace_name": TRACE_NAME,
            "compile_trace": f"kg.compile:{args.adapter}",
            "insight_trace": "kg.community_insight.refresh_ids",
            "llm_observations": [
                "llm:kg_cognitive_card",
                "llm:kg_assignment_bucket_planning",
                "llm:kg_community_assignment",
                "llm:kg_community_insight",
            ],
        },
    }

    with _StepTimer("load_ft_news_records", quiet=args.quiet):
        records, selected_rows = _load_projected_records(args, run_id=run_id)
    output["selected_news"] = [_news_preview(row) for row in selected_rows]
    output["records"] = [_record_preview(record) for record in records]
    if not records:
        output["compile_result"] = {"skipped": True, "reason": "no_records"}
        return output

    with langfuse_observation(
        name="community_insight_e2e.load_records",
        as_type="span",
        input={"selected_news": output["selected_news"], "record_count": len(records)},
        metadata={"record_count": len(records)},
    ):
        langfuse_update_span(output={"records": output["records"]}, status_message="completed")

    if args.stage <= 1:
        output["compile_result"] = {"skipped": True, "reason": "stage_1_records_only"}
        return output

    if args.stage == 2:
        with _StepTimer("extract_cognitive_cards_only", quiet=args.quiet):
            output["card_stage"] = await _await_with_heartbeat(
                "extract_cognitive_cards_only",
                _run_card_only_stage(args, records),
                quiet=args.quiet,
                interval_seconds=args.heartbeat,
            )
        output["compile_result"] = {
            "skipped": True,
            "reason": "stage_2_card_only_uses_non_persistent_compiler",
            "evidence": output["card_stage"].get("evidence_count"),
            "failed_records": output["card_stage"].get("failed_records_count"),
        }
        output["stages"] = {
            "evidence": output["card_stage"].get("evidence"),
            "cognitive_cards": output["card_stage"].get("cognitive_cards"),
            "assignments": [],
            "communities": [],
            "index_refresh": {"skipped": True, "reason": "stage_2_card_only"},
        }
        return output

    service = create_knowledge_service(target=args.target)
    compile_command = KnowledgeCompileCommand(
        adapter_name=args.adapter,
        target=args.target,
        records=records,
        dry_run=args.dry_run,
        request_id=run_id,
        concurrency=args.concurrency,
    )
    with _StepTimer("compile_kg_card_to_community", quiet=args.quiet):
        compile_result = await _await_with_heartbeat(
            "compile_kg",
            service.compile_kg(compile_command),
            quiet=args.quiet,
            interval_seconds=args.heartbeat,
        )
    compile_dict = _to_plain_dict(compile_result)
    output["compile_result"] = _compile_summary(compile_dict)

    source_ids = [str(record.get("source_id") or "") for record in records if record.get("source_id")]
    with _StepTimer("inspect_card_assignment_community", quiet=args.quiet):
        stage_state = _inspect_compile_outputs(
            target=args.target,
            source_ids=source_ids,
            compile_result=compile_dict,
        )
    output["stages"] = stage_state

    updated_community_ids = _updated_community_ids(compile_dict, stage_state)
    output["updated_community_ids"] = updated_community_ids
    if args.stage <= 3:
        output["insight_refresh"] = {"skipped": True, "reason": "stage_3_community_only"}
        return output
    if args.dry_run:
        output["insight_refresh"] = {"skipped": True, "reason": "dry_run"}
        return output
    if args.skip_insight:
        output["insight_refresh"] = {"skipped": True, "reason": "skip_insight"}
        return output
    if not updated_community_ids:
        output["insight_refresh"] = {"skipped": True, "reason": "no_updated_communities"}
        return output

    refresh_ids = updated_community_ids[: max(1, args.insight_limit)]
    insight_service = CommunityInsightService(target=args.target)
    with _StepTimer("refresh_community_insight", quiet=args.quiet):
        insight_refresh = await _await_with_heartbeat(
            "refresh_community_insight",
            insight_service.refresh_community_ids(refresh_ids, force=args.force_insight),
            quiet=args.quiet,
            interval_seconds=args.heartbeat,
        )
    output["insight_refresh"] = insight_refresh

    with _StepTimer("inspect_insight_outputs", quiet=args.quiet):
        output["insights"] = _inspect_insights(target=args.target, community_ids=refresh_ids)
    return output


def _load_projected_records(args: argparse.Namespace, *, run_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _fetch_ft_news_rows(
        target=args.target,
        row_ids=args.news_id,
        candidate_limit=args.candidate_limit,
        limit=args.limit,
        min_text_chars=args.min_text_chars,
    )
    records: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    for row in rows:
        projected = project_ft_news_row(row)
        if projected is None:
            continue
        if args.scope_source_id:
            projected = _scope_projected_record(projected, row=row, run_id=run_id)
        records.append(projected)
        selected_rows.append(row)
        if len(records) >= args.limit:
            break
    if not records and args.allow_fallback:
        fallback = _fallback_record(run_id=run_id, scope_source_id=args.scope_source_id)
        records.append(fallback)
        selected_rows.append(
            {
                "id": "fallback",
                "title": fallback["payload"].get("title"),
                "source_name": "内置受控样本",
                "published_at": fallback["observed_at"],
                "content": fallback.get("raw_text"),
            }
        )
    return records, selected_rows


async def _run_card_only_stage(args: argparse.Namespace, records: list[dict[str, Any]]) -> dict[str, Any]:
    adapter = get_adapter(args.adapter, target=args.target)
    inputs, normalize_failures = _normalize_records(adapter, records)
    compiler = KnowledgeCompiler(repository=None, concurrency=args.concurrency)
    with langfuse_observation(
        name="community_insight_e2e.card_only.compile_evidence",
        as_type="span",
        input={"records": len(records), "inputs": len(inputs)},
        metadata={"stage": 2, "adapter": args.adapter},
    ):
        compile_result = await compiler.compile(adapter, inputs)
        compile_result.failed_records[:0] = normalize_failures
        langfuse_update_span(
            output={
                "run_id": compile_result.run_id,
                "evidence": len(compile_result.evidence),
                "failed_records": len(compile_result.failed_records),
                "normalize_failures": len(normalize_failures),
            },
            status_message="completed",
        )

    chunks = []
    for evidence in compile_result.evidence:
        if str(getattr(evidence.status, "value", evidence.status)) != "active":
            continue
        chunks.extend(build_chunks_for_compiled_evidence(evidence))

    extractor = CognitiveCardExtractor(concurrency=args.concurrency)
    with langfuse_observation(
        name="community_insight_e2e.card_only.extract_cards",
        as_type="span",
        input={"chunks": len(chunks)},
        metadata={"stage": 2, "adapter": args.adapter},
    ):
        cards = await extractor.extract(chunks) if chunks else []
        langfuse_update_span(
            output={"cards": len(cards), "schema_versions": sorted({card.schema_version for card in cards})},
            status_message="completed",
        )

    return {
        "run_id": compile_result.run_id,
        "inputs": len(inputs),
        "normalize_failures": [_to_plain_dict(item) for item in normalize_failures],
        "failed_records_count": len(compile_result.failed_records),
        "failed_records": [_to_plain_dict(item) for item in compile_result.failed_records[:10]],
        "evidence_count": len(compile_result.evidence),
        "evidence": [_compiled_evidence_summary(item) for item in compile_result.evidence[:20]],
        "chunk_count": len(chunks),
        "chunks": [_chunk_summary(item) for item in chunks[:20]],
        "card_count": len(cards),
        "cognitive_cards": [_domain_card_summary(card) for card in cards],
    }


def _fetch_ft_news_rows(
    *,
    target: str,
    row_ids: list[int],
    candidate_limit: int,
    limit: int,
    min_text_chars: int,
) -> list[dict[str, Any]]:
    with get_session(target) as session:
        inspector = inspect(session.bind)
        if not inspector.has_table(News.__tablename__):
            return []
        if row_ids:
            rows = session.scalars(select(News).where(News.id.in_(row_ids))).all()
            order = {row_id: index for index, row_id in enumerate(row_ids)}
            return [_news_model_row(row) for row in sorted(rows, key=lambda item: order.get(int(item.id), len(order)))]
        rows = session.scalars(
            select(News)
            .order_by(News.created_at.desc().nullslast(), News.id.desc())
            .limit(max(limit, candidate_limit, 1))
        ).all()
    candidates = [_news_model_row(row) for row in rows]
    usable = [row for row in candidates if len(_news_search_text(row)) >= min_text_chars]
    return usable[:limit]


def _inspect_compile_outputs(
    *,
    target: str,
    source_ids: list[str],
    compile_result: dict[str, Any],
) -> dict[str, Any]:
    evidence_ids = [str(item) for item in compile_result.get("evidence_ids") or [] if item]
    index_refresh = compile_result.get("index_refresh") or {}
    changed_chunks = int(index_refresh.get("changed_chunks") or 0)
    changed_evidence = int(index_refresh.get("changed_evidence") or 0)
    card_ids = [str(item) for item in (index_refresh.get("card_persistence") or {}).get("upserted_card_ids") or [] if item]
    updated_ids = _updated_community_ids(compile_result, {})

    with get_session(target) as session:
        cards = session.scalars(
            select(KnowledgeCognitiveCard)
            .where(KnowledgeCognitiveCard.cognitive_card_id.in_(card_ids))
        ).all() if card_ids else []
        if not cards and evidence_ids:
            cards = session.scalars(
                select(KnowledgeCognitiveCard)
                .where(KnowledgeCognitiveCard.evidence_id.in_(evidence_ids))
            ).all()
        assignments = session.scalars(
            select(KnowledgeCommunityAssignment)
            .where(KnowledgeCommunityAssignment.cognitive_card_id.in_([card.cognitive_card_id for card in cards]))
        ).all() if cards else []
        if not updated_ids:
            updated_ids = sorted({assignment.community_id for assignment in assignments if assignment.community_id})
        communities = session.scalars(
            select(KnowledgeGraphCommunity)
            .where(KnowledgeGraphCommunity.community_id.in_(updated_ids))
        ).all() if updated_ids else []

    return {
        "source_ids": source_ids,
        "evidence": {
            "count": len(evidence_ids),
            "ids": evidence_ids[:20],
            "changed_chunks": changed_chunks,
            "changed_evidence": changed_evidence,
        },
        "cognitive_cards": [_card_summary(card) for card in cards],
        "assignments": [_assignment_summary(row) for row in assignments],
        "communities": [_community_summary(row) for row in communities],
        "index_refresh": _compact_index_refresh(index_refresh),
    }


def _inspect_insights(*, target: str, community_ids: list[str]) -> list[dict[str, Any]]:
    with get_session(target) as session:
        rows = session.scalars(
            select(KnowledgeCommunityInsight)
            .where(KnowledgeCommunityInsight.community_id.in_(community_ids))
            .where(KnowledgeCommunityInsight.status == "active")
        ).all() if community_ids else []
    order = {community_id: index for index, community_id in enumerate(community_ids)}
    rows = sorted(rows, key=lambda item: order.get(item.community_id, len(order)))
    return [_insight_summary(row) for row in rows]


def _updated_community_ids(compile_result: dict[str, Any], stage_state: dict[str, Any]) -> list[str]:
    index_refresh = compile_result.get("index_refresh") or {}
    graph_persistence = index_refresh.get("graph_persistence") or {}
    ids = [str(item) for item in graph_persistence.get("updated_community_ids") or [] if item]
    if ids:
        return list(dict.fromkeys(ids))
    communities = stage_state.get("communities") if isinstance(stage_state, dict) else []
    return list(dict.fromkeys(str(item.get("community_id")) for item in communities or [] if item.get("community_id")))


def _scope_projected_record(record: dict[str, Any], *, row: dict[str, Any], run_id: str) -> dict[str, Any]:
    original_source_id = str(record.get("source_id") or f"ft_news:{row.get('id')}")
    scoped_source_id = f"{run_id}:ft_news:{row.get('id')}"
    payload = dict(record.get("payload") or {})
    payload["source_id"] = scoped_source_id
    payload["document_id"] = scoped_source_id
    payload["original_source_id"] = original_source_id
    metadata = {
        **(record.get("metadata") or {}),
        "source_origin": "community_insight_e2e_demo",
        "source_table": "ft_news",
        "source_pk": row.get("id"),
        "original_source_id": original_source_id,
        "demo_run_id": run_id,
    }
    return {
        **record,
        "source_id": scoped_source_id,
        "payload": payload,
        "metadata": metadata,
    }


def _fallback_record(*, run_id: str, scope_source_id: bool) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat()
    source_id = "controlled_news:community_insight_e2e"
    if scope_source_id:
        source_id = f"{run_id}:controlled_news:community_insight_e2e"
    raw_text = (
        "先进封装设备订单增长带动半导体产业链景气改善。"
        "多家设备和材料厂商披露订单、扩产和客户验证进展，显示 AI 算力需求正在向封装测试、"
        "基板材料和上游设备环节传导。"
        "业内同时提示，先进封装产能扩张仍面临客户验证周期、资本开支节奏和供应链交付风险。"
    )
    return {
        "source_type": "news_articles",
        "source_id": source_id,
        "observed_at": observed_at,
        "payload": {
            "source_id": source_id,
            "document_id": source_id,
            "published_at": observed_at,
            "title": "先进封装订单增长带动半导体产业链景气",
            "text": raw_text,
            "source_name": "内置受控样本",
            "mentioned_entities": [],
            "affected_entities": [],
        },
        "raw_text": raw_text,
        "metadata": {
            "source_origin": "community_insight_e2e_fallback",
            "demo_run_id": run_id,
        },
    }


def _news_model_row(row: News) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "summary": row.summary,
        "source": row.source,
        "source_name": row.source_name,
        "source_reliability": row.source_reliability,
        "category": row.category,
        "url": row.url,
        "tags": row.tags,
        "related_stocks": row.related_stocks,
        "published_at": row.published_at,
        "fingerprint": row.fingerprint,
        "created_at": row.created_at,
    }


def _news_search_text(row: dict[str, Any]) -> str:
    return "\n".join(
        str(item or "").strip()
        for item in [row.get("title"), row.get("summary"), row.get("content")]
        if str(item or "").strip()
    )


def _news_preview(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "source_name": row.get("source_name") or row.get("source"),
        "published_at": _iso(row.get("published_at")),
        "text_chars": len(_news_search_text(row)),
    }


def _record_preview(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") or {}
    return {
        "source_type": record.get("source_type"),
        "source_id": record.get("source_id"),
        "observed_at": record.get("observed_at"),
        "title": payload.get("title"),
        "raw_text_chars": len(str(record.get("raw_text") or "")),
        "original_source_id": payload.get("original_source_id") or (record.get("metadata") or {}).get("original_source_id"),
    }


def _compiled_evidence_summary(item) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "evidence_type": getattr(item.evidence_type, "value", item.evidence_type),
        "status": getattr(item.status, "value", item.status),
        "content_chars": len(str(item.content or "")),
        "title": (item.payload or {}).get("title") or "",
        "published_at": (item.payload or {}).get("published_at") or "",
    }


def _chunk_summary(item) -> dict[str, Any]:
    return {
        "chunk_id": item.chunk_id,
        "evidence_id": item.evidence_id,
        "chunk_index": item.chunk_index,
        "text_chars": len(item.content or ""),
        "text_preview": (item.content or "")[:300],
        "text_hash": item.text_hash,
    }


def _domain_card_summary(card) -> dict[str, Any]:
    return {
        "cognitive_card_id": card.cognitive_card_id,
        "source_id": card.source_id,
        "evidence_id": card.evidence_id,
        "primary_chunk_id": card.primary_chunk_id,
        "summary": card.summary,
        "title_candidates": card.title_candidates,
        "intent_count": len(card.topic_intents),
        "topic_intents": [_intent_preview(item) for item in card.topic_intents[:10] if isinstance(item, dict)],
        "schema_version": card.schema_version,
        "supporting_text": card.supporting_text,
    }


def _compile_summary(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "adapter_name": data.get("adapter_name"),
        "run_id": data.get("run_id"),
        "nodes": data.get("nodes"),
        "edges": data.get("edges"),
        "evidence": data.get("evidence"),
        "failed_records": data.get("failed_records"),
        "dry_run": data.get("dry_run"),
        "evidence_ids": (data.get("evidence_ids") or [])[:20],
        "index_refresh": _compact_index_refresh(data.get("index_refresh") or {}),
        "failures": (data.get("failures") or [])[:5],
    }


def _compact_index_refresh(index_refresh: dict[str, Any]) -> dict[str, Any]:
    graph_persistence = index_refresh.get("graph_persistence") or {}
    diagnostics = index_refresh.get("diagnostics") or {}
    return {
        "status": index_refresh.get("status"),
        "changed_chunks": index_refresh.get("changed_chunks"),
        "changed_evidence": index_refresh.get("changed_evidence"),
        "cards": index_refresh.get("cards"),
        "assignments": index_refresh.get("assignments"),
        "assignment_rows": index_refresh.get("assignment_rows"),
        "communities": index_refresh.get("communities"),
        "documents_written": index_refresh.get("documents_written"),
        "cognitive_card_documents_written": index_refresh.get("cognitive_card_documents_written"),
        "updated_community_ids": graph_persistence.get("updated_community_ids") or [],
        "community_builder": diagnostics.get("community_builder"),
        "bucket_planning": diagnostics.get("bucket_planning"),
        "candidate_ledger": diagnostics.get("candidate_ledger"),
    }


def _card_summary(card: KnowledgeCognitiveCard) -> dict[str, Any]:
    intents = card.topic_intents or []
    return {
        "cognitive_card_id": card.cognitive_card_id,
        "source_id": card.source_id,
        "evidence_id": card.evidence_id,
        "primary_chunk_id": card.primary_chunk_id,
        "summary": card.summary,
        "title_candidates": card.title_candidates,
        "intent_count": len(intents),
        "topic_intents": [_intent_preview(item) for item in intents[:10] if isinstance(item, dict)],
        "schema_version": card.schema_version,
    }


def _intent_preview(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "raw_theme": item.get("raw_theme"),
        "title_candidate": item.get("title_candidate"),
        "parent_themes": item.get("parent_themes") or [],
        "summary": item.get("summary"),
        "evidence_span": item.get("evidence_span"),
        "evidence_support": item.get("evidence_support"),
    }


def _assignment_summary(row: KnowledgeCommunityAssignment) -> dict[str, Any]:
    decision = row.decision if isinstance(row.decision, dict) else {}
    matched = {}
    for item in decision.get("assignments") or []:
        if isinstance(item, dict) and (
            item.get("assignment_id") == row.assignment_id or item.get("community_id") == row.community_id
        ):
            matched = item
            break
    return {
        "assignment_id": row.assignment_id,
        "cognitive_card_id": row.cognitive_card_id,
        "intent_id": row.intent_id,
        "community_id": row.community_id,
        "action": row.action,
        "weight": row.weight,
        "confidence": row.confidence,
        "reason": row.reason,
        "insight_delta": matched.get("insight_delta"),
        "topic": {
            "title_candidate": (row.topic_intent or {}).get("title_candidate"),
            "parent_themes": (row.topic_intent or {}).get("parent_themes") or [],
            "evidence_span": (row.topic_intent or {}).get("evidence_span"),
        },
    }


def _community_summary(row: KnowledgeGraphCommunity) -> dict[str, Any]:
    metrics = row.metrics or {}
    assignments = [item for item in metrics.get("assignments") or [] if isinstance(item, dict)]
    return {
        "community_id": row.community_id,
        "title": row.title,
        "summary": row.summary,
        "source_count": metrics.get("unique_source_count") or metrics.get("source_count"),
        "cognitive_card_count": metrics.get("cognitive_card_count"),
        "assignment_count": metrics.get("assignment_count"),
        "last_insight_generated_at": _iso(row.last_insight_generated_at),
        "updated_at": _iso(row.updated_at),
        "recent_assignments": [
            {
                "assignment_id": item.get("assignment_id"),
                "action": item.get("action"),
                "fit_type": item.get("fit_type"),
                "insight_delta": item.get("insight_delta"),
            }
            for item in assignments[-5:]
        ],
    }


def _insight_summary(row: KnowledgeCommunityInsight) -> dict[str, Any]:
    report_json = row.report_json if isinstance(row.report_json, dict) else {}
    return {
        "insight_id": row.insight_id,
        "community_id": row.community_id,
        "title": row.title,
        "version": row.insight_version,
        "status": row.status,
        "source_count": row.source_count,
        "cognitive_card_count": row.cognitive_card_count,
        "assignment_count": row.assignment_count,
        "report_chars": len(row.insight_full_report or ""),
        "report_preview": (row.insight_full_report or "")[:1000],
        "core_thesis": report_json.get("core_thesis"),
        "updated_at": _iso(row.updated_at),
        "payload": row.payload or {},
    }


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


def _compact_for_trace(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": output.get("run_id"),
        "stage": output.get("stage"),
        "selected_news": output.get("selected_news"),
        "compile_result": output.get("compile_result"),
        "card_stage": {
            "evidence_count": (output.get("card_stage") or {}).get("evidence_count"),
            "chunk_count": (output.get("card_stage") or {}).get("chunk_count"),
            "card_count": (output.get("card_stage") or {}).get("card_count"),
        }
        if output.get("card_stage")
        else None,
        "updated_community_ids": output.get("updated_community_ids"),
        "insight_refresh": output.get("insight_refresh"),
        "insights": [
            {
                "community_id": item.get("community_id"),
                "title": item.get("title"),
                "version": item.get("version"),
                "status": item.get("status"),
                "report_chars": item.get("report_chars"),
                "core_thesis": item.get("core_thesis"),
            }
            for item in output.get("insights") or []
        ],
    }


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Community Insight 全链路演示脚本")
    parser.add_argument("--target", default="prod", choices=["prod", "test"])
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--limit", type=int, default=3, help="本次最多处理几条新闻")
    parser.add_argument(
        "--stage",
        "--until-stage",
        dest="stage",
        type=int,
        choices=sorted(STAGE_DESCRIPTIONS),
        default=4,
        help="执行到第几阶段：1=选样/投影，2=Evidence+Card，3=Community，4=Insight",
    )
    parser.add_argument("--candidate-limit", type=int, default=80, help="未指定 news-id 时，从最近多少条 ft_news 中筛选")
    parser.add_argument("--news-id", type=int, action="append", default=[], help="指定 ft_news.id，可重复传入")
    parser.add_argument("--min-text-chars", type=int, default=80, help="自动选样时最小正文长度")
    parser.add_argument("--insight-limit", type=int, default=3, help="最多刷新几个本轮更新 community 的 Insight")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--heartbeat", type=float, default=15.0)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--output", default=str(OUTPUT_FILE))
    parser.add_argument("--dry-run", action="store_true", help="只跑 compile dry-run，不写 PG/Milvus，不刷新 Insight")
    parser.add_argument("--skip-insight", action="store_true", help="只跑到 Community 写入，不刷新 Insight")
    parser.add_argument("--force-insight", action=argparse.BooleanOptionalAction, default=True, help="强制刷新本轮 community Insight")
    parser.add_argument("--scope-source-id", action=argparse.BooleanOptionalAction, default=True, help="给 source_id 增加 run 前缀，保证重复运行触发全链路")
    parser.add_argument("--allow-fallback", action=argparse.BooleanOptionalAction, default=True, help="ft_news 不可用时使用内置受控样本")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    result = await run_demo(args)
    output_path = Path(args.output)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        pprint(result)
        print(f"\n[community_insight_e2e] wrote {output_path}", file=sys.stderr)
        print(
            "[community_insight_e2e] langfuse_session_id="
            f"{result.get('langfuse', {}).get('session_id')} trace_name={TRACE_NAME}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    asyncio.run(main())
