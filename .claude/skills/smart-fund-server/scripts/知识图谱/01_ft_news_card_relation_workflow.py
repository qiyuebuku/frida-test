#!/usr/bin/env python3
"""从真实 ft_news 同步验证 Evidence、原子 Card 和正式关系 Edge 全链路。

脚本默认先清理当前 target 下由该工作流维护的 KG 数据，再复用生产应用服务写入
正式当前态。需要验证幂等或跨批关系时，可通过命令行显式保留已有数据。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.ft_news_knowledge_graph_workflow_service import (  # noqa: E402
    FtNewsKnowledgeGraphWorkflowService,
)
from src.application.services.atomic_cognitive_card_service import (  # noqa: E402
    AtomicCognitiveCardExtractor,
)
from src.domain.knowledge.chunking import build_evidence_chunks  # noqa: E402
from src.domain.knowledge.schemas import EvidenceChunk  # noqa: E402
from src.domain.knowledge_adapters.financial.source_projection import (  # noqa: E402
    project_ft_news_row,
)
from src.domain.knowledge.semantic_index_materials import (  # noqa: E402
    SEMANTIC_COLLECTION_CARD_RELATION,
    SEMANTIC_COLLECTION_CHUNK,
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS,
)
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.config import settings  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.collection import News  # noqa: E402
from src.infrastructure.persistence.models.knowledge import (  # noqa: E402
    KnowledgeCardRelation,
    KnowledgeCognitiveCard,
    KnowledgeCommunityAssignment,
    KnowledgeCompilationRun,
    KnowledgeEvidence,
    KnowledgeEvidenceChunk,
)
from src.infrastructure.vector_store.semantic_hybrid_retriever import (  # noqa: E402
    MilvusSemanticHybridRetriever,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ft_news -> Evidence/Chunk -> 原子 Card -> Relation Edge 全链路验证"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="未指定 --news-id 时处理最新多少条 ft_news，范围 1-100",
    )
    parser.add_argument(
        "--news-id",
        action="append",
        type=int,
        default=[],
        help="精确处理指定 ft_news.id，可重复传入；指定后不使用 --limit 选数",
    )
    parser.add_argument("--target", choices=["prod", "test"], default="prod")
    parser.add_argument(
        "--provider",
        default="",
        help="仅 cards 模式：固定 LLM 厂商 Provider；不传时使用网关自动路由",
    )
    parser.add_argument(
        "--model",
        default="",
        help=(
            "仅 cards 模式：覆盖 kg_cognitive_card 的 canonical model；"
            "不传时按 span 数和正文字符数在 Flash/Pro 间动态路由"
        ),
    )
    parser.add_argument(
        "--thinking-type",
        choices=["enabled", "disabled"],
        default="",
        help="仅 cards 模式：显式覆盖 Card 思考模式；默认不传，使用模型默认行为",
    )
    parser.add_argument(
        "--probe-thinking-type",
        choices=["enabled", "disabled"],
        default="",
        help="仅 cards 模式：单独覆盖 Relation Probe 思考模式；默认不传",
    )
    parser.add_argument(
        "--prompt-profile",
        default="production",
        help=(
            "仅 cards 模式：隔离本地精确缓存和实验观测的 profile；"
            "该值不进入 Prompt 正文"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["workflow", "cards"],
        default="workflow",
        help="workflow 执行完整写入链路；cards 仅并发验证 Card，不写 PG/Milvus",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="cards 模式请求的最大并发 Chunk 数；实际值不超过当前模型供应商并发上限",
    )
    parser.add_argument(
        "--chunk-timeout",
        type=float,
        default=500.0,
        help=(
            "cards 模式单个 Chunk 的外层等待秒数，默认 500 秒，"
            "应大于 LLM Provider 默认的 180 秒超时；超时只隔离当前 Chunk"
        ),
    )
    parser.add_argument(
        "--include-evaluation-details",
        action="store_true",
        help="在结果 JSON 中保留各阶段候选 ID，便于质量评测",
    )
    parser.add_argument(
        "--keep-existing-data",
        action="store_true",
        help="跳过运行前清理，仅用于验证幂等或让本批 Card 与历史 Card 建立关系",
    )
    parser.add_argument("--session-id", default="", help="指定 Langfuse session id")
    parser.add_argument("--output", default="", help="结果 JSON 路径；默认写入 /tmp")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def load_news_rows(args: argparse.Namespace) -> list[News]:
    """按命令行条件读取 ft_news ORM 行。"""

    explicit_ids = _ordered_unique_positive_ints(args.news_id)
    with get_session(args.target) as session:
        if explicit_ids:
            rows = list(
                session.scalars(select(News).where(News.id.in_(explicit_ids))).all()
            )
            row_by_id = {int(row.id): row for row in rows}
            ordered_rows = [row_by_id[item] for item in explicit_ids if item in row_by_id]
        else:
            limit = max(1, min(100, int(args.limit)))
            ordered_rows = list(
                session.scalars(
                    select(News)
                    .order_by(
                        News.published_at.desc(),
                        News.created_at.desc().nullslast(),
                        News.id.desc(),
                    )
                    .limit(limit)
                ).all()
            )
    return ordered_rows


def load_news(args: argparse.Namespace) -> list[dict]:
    """只选择 ft_news 行；完整工作流的业务转换仍由应用服务完成。"""

    return [
        {
            "id": int(row.id),
            "title": row.title,
            "source": row.source,
            "source_name": row.source_name,
            "published_at": _iso(row.published_at),
            "created_at": _iso(row.created_at),
        }
        for row in load_news_rows(args)
    ]


async def run(args: argparse.Namespace) -> dict:
    selected_news = load_news(args)
    if not selected_news:
        raise RuntimeError("没有找到符合条件的 ft_news 数据")
    selected_ids = [item["id"] for item in selected_news]
    cleanup = {
        "executed": False,
        "adapter_name": "financial",
        "target": args.target,
        "postgres": {},
        "milvus": {},
    }
    if not args.keep_existing_data:
        cleanup = await cleanup_workflow_state(
            adapter_name="financial",
            target=args.target,
        )
    service = FtNewsKnowledgeGraphWorkflowService(target=args.target)
    workflow = await service.run(
        selected_ids,
        include_evaluation_details=args.include_evaluation_details,
    )
    return {
        "status": workflow["status"],
        "selected_news": selected_news,
        "cleanup": cleanup,
        "workflow": workflow,
    }


async def run_card_validation(
    args: argparse.Namespace,
    *,
    session_id: str,
) -> dict[str, Any]:
    """并发验证 Card 抽取；单个 Chunk 失败或超时不影响其他结果。"""

    rows = load_news_rows(args)
    if not rows:
        raise RuntimeError("没有找到符合条件的 ft_news 数据")
    chunks, owners = _build_validation_chunks(rows, run_id=session_id)
    requested_concurrency = max(1, min(20, int(args.concurrency)))
    concurrency = min(requested_concurrency, max(1, settings.DEEPSEEK_MAX_CONCURRENCY))
    chunk_timeout = max(1.0, float(args.chunk_timeout))
    semaphore = asyncio.Semaphore(concurrency)
    extractor = AtomicCognitiveCardExtractor(
        model=str(args.model or "").strip() or None,
        provider=str(args.provider or "").strip() or None,
        thinking_type=str(args.thinking_type or "").strip() or None,
        relation_probe_thinking_type=(
            str(args.probe_thinking_type or "").strip() or None
        ),
        prompt_profile=str(args.prompt_profile or "production").strip(),
        concurrency=concurrency,
    )

    async def process(chunk: EvidenceChunk) -> dict[str, Any]:
        owner = owners[chunk.chunk_id]
        queued_at = time.monotonic()
        try:
            async with semaphore:
                started = time.monotonic()
                result = (
                    await asyncio.wait_for(
                        extractor.extract_with_diagnostics([chunk]),
                        timeout=chunk_timeout,
                    )
                )[0]
            return {
                **owner,
                "status": "completed",
                "queue_seconds": round(started - queued_at, 3),
                "execution_seconds": round(time.monotonic() - started, 3),
                "duration_seconds": round(time.monotonic() - queued_at, 3),
                "span_count": len(result.spans),
                "input_text_chars": result.input_text_chars,
                "selected_model": result.selected_model,
                "model_route": result.model_route,
                "repaired": result.repaired,
                "repair_attempted": result.repair_attempted,
                "discarded_card_count": result.discarded_card_count,
                "discarded_relation_count": result.discarded_relation_count,
                "validation_issues": list(result.validation_issues),
                "skip_reason": result.skip_reason,
                "llm_stage_usage": result.llm_stage_usage,
                "cards": [
                    {
                        "summary": card.summary,
                        "focus_evidence_refs": list(card.focus_evidence_refs),
                        "relation_probes": [
                            probe.as_dict() for probe in card.relation_probes
                        ],
                    }
                    for card in result.cards
                ],
                "relations": [relation.as_dict() for relation in result.relations],
            }
        except asyncio.TimeoutError:
            return {
                **owner,
                "status": "timeout",
                "duration_seconds": round(time.monotonic() - queued_at, 3),
                "error": f"chunk timeout after {chunk_timeout:g}s",
                "cards": [],
                "relations": [],
            }
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "Card 验证失败 news_id=%s chunk_index=%s",
                owner["news_id"],
                owner["chunk_index"],
            )
            return {
                **owner,
                "status": "failed",
                "duration_seconds": round(time.monotonic() - queued_at, 3),
                "error": f"{type(exc).__name__}: {exc}",
                "cards": [],
                "relations": [],
            }

    tasks = [asyncio.create_task(process(chunk)) for chunk in chunks]
    chunk_results: list[dict[str, Any]] = []
    for completed in asyncio.as_completed(tasks):
        item = await completed
        chunk_results.append(item)
        print(
            "[cards] "
            f"news_id={item['news_id']} chunk={item['chunk_index'] + 1}/{item['chunk_count']} "
            f"status={item['status']} cards={len(item['cards'])} "
            f"relations={len(item['relations'])} duration={item['duration_seconds']}s",
            flush=True,
        )

    chunk_results.sort(key=lambda item: (item["news_order"], item["chunk_index"]))
    failed_chunks = [item for item in chunk_results if item["status"] != "completed"]
    completed_news_ids, failed_news_ids = _news_completion(chunk_results)
    return {
        "status": "completed" if not failed_chunks else "completed_with_errors",
        "mode": "cards",
        "target": args.target,
        "model": str(args.model or "").strip() or None,
        "model_routing": (
            "explicit_override"
            if str(args.model or "").strip()
            else "span_and_text_chars"
        ),
        "provider": str(args.provider or "").strip() or None,
        "card_thinking_type": str(args.thinking_type or "").strip() or None,
        "probe_thinking_type": str(args.probe_thinking_type or "").strip() or None,
        "prompt_profile": str(args.prompt_profile or "production").strip(),
        "session_id": session_id,
        "requested_concurrency": requested_concurrency,
        "effective_concurrency": concurrency,
        "chunk_timeout_seconds": chunk_timeout,
        "news_count": len(rows),
        "news_ids": [int(row.id) for row in rows],
        "chunk_count": len(chunks),
        "completed_chunks": len(chunk_results) - len(failed_chunks),
        "failed_chunks": len(failed_chunks),
        "completed_news_ids": completed_news_ids,
        "failed_news_ids": failed_news_ids,
        "card_count": sum(len(item["cards"]) for item in chunk_results),
        "relation_count": sum(len(item["relations"]) for item in chunk_results),
        "repair_count": sum(bool(item.get("repaired")) for item in chunk_results),
        "repair_attempt_count": sum(
            bool(item.get("repair_attempted")) for item in chunk_results
        ),
        "discarded_card_count": sum(
            int(item.get("discarded_card_count") or 0) for item in chunk_results
        ),
        "discarded_relation_count": sum(
            int(item.get("discarded_relation_count") or 0) for item in chunk_results
        ),
        "results": chunk_results,
    }


def _build_validation_chunks(
    rows: list[News],
    *,
    run_id: str,
) -> tuple[list[EvidenceChunk], dict[str, dict[str, Any]]]:
    chunks: list[EvidenceChunk] = []
    owners: dict[str, dict[str, Any]] = {}
    for news_order, row in enumerate(rows):
        record = project_ft_news_row(_news_model_row(row))
        if record is None:
            continue
        evidence_id = f"kg_ev:financial:validation:ft_news:{row.id}:{run_id}"
        news_chunks = build_evidence_chunks(
            adapter_name="financial",
            evidence_id=evidence_id,
            content=record["raw_text"],
            payload={
                **record["payload"],
                "source_type": record["source_type"],
                "source_id": record["source_id"],
            },
        )
        for chunk in news_chunks:
            owners[chunk.chunk_id] = {
                "news_order": news_order,
                "news_id": int(row.id),
                "title": row.title,
                "chunk_index": chunk.chunk_index,
                "chunk_count": len(news_chunks),
                "chunk_id": chunk.chunk_id,
            }
        chunks.extend(news_chunks)
    return chunks, owners


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


def _news_completion(chunk_results: list[dict[str, Any]]) -> tuple[list[int], list[int]]:
    statuses: dict[int, list[str]] = {}
    for item in chunk_results:
        statuses.setdefault(int(item["news_id"]), []).append(str(item["status"]))
    completed = [news_id for news_id, values in statuses.items() if all(v == "completed" for v in values)]
    failed = [news_id for news_id, values in statuses.items() if any(v != "completed" for v in values)]
    return completed, failed


async def cleanup_workflow_state(*, adapter_name: str, target: str) -> dict:
    """清理 Card/Relation 验证链路状态，不删除 ft_news 和 LLM 审计数据。"""

    retriever = MilvusSemanticHybridRetriever()
    milvus_counts: dict[str, int] = {}
    for collection_role in (
        SEMANTIC_COLLECTION_CARD_RELATION,
        SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS,
        SEMANTIC_COLLECTION_COGNITIVE_CARD,
        SEMANTIC_COLLECTION_CHUNK,
    ):
        target_ids = await retriever.list_target_ids_by_role(
            collection_role=collection_role,
            adapter_name=adapter_name,
            target=target,
        )
        milvus_counts[collection_role] = await retriever.delete_documents_by_role(
            collection_role=collection_role,
            adapter_name=adapter_name,
            target=target,
            target_ids=target_ids,
        )

    with get_session(target) as session:
        card_ids = list(
            session.scalars(
                select(KnowledgeCognitiveCard.cognitive_card_id).where(
                    KnowledgeCognitiveCard.adapter_name == adapter_name
                )
            ).all()
        )
        relation_predicate = (
            KnowledgeCardRelation.source_card_id.in_(card_ids)
            | KnowledgeCardRelation.target_card_id.in_(card_ids)
        )
        relation_count = 0
        if card_ids:
            relation_count = int(
                session.execute(
                    delete(KnowledgeCardRelation).where(relation_predicate)
                ).rowcount
                or 0
            )
        assignment_count = int(
            session.execute(
                delete(KnowledgeCommunityAssignment).where(
                    KnowledgeCommunityAssignment.adapter_name == adapter_name
                )
            ).rowcount
            or 0
        )
        card_count = int(
            session.execute(
                delete(KnowledgeCognitiveCard).where(
                    KnowledgeCognitiveCard.adapter_name == adapter_name
                )
            ).rowcount
            or 0
        )
        source_card_exists = (
            select(KnowledgeCognitiveCard.cognitive_card_id)
            .where(
                KnowledgeCognitiveCard.cognitive_card_id
                == KnowledgeCardRelation.source_card_id
            )
            .exists()
        )
        target_card_exists = (
            select(KnowledgeCognitiveCard.cognitive_card_id)
            .where(
                KnowledgeCognitiveCard.cognitive_card_id
                == KnowledgeCardRelation.target_card_id
            )
            .exists()
        )
        orphan_relation_count = int(
            session.execute(
                delete(KnowledgeCardRelation).where(
                    ~source_card_exists | ~target_card_exists
                )
            ).rowcount
            or 0
        )
        chunk_count = int(
            session.execute(
                delete(KnowledgeEvidenceChunk).where(
                    KnowledgeEvidenceChunk.adapter_name == adapter_name
                )
            ).rowcount
            or 0
        )
        evidence_count = int(
            session.execute(
                delete(KnowledgeEvidence).where(
                    KnowledgeEvidence.adapter_name == adapter_name
                )
            ).rowcount
            or 0
        )
        compilation_run_count = int(
            session.execute(
                delete(KnowledgeCompilationRun).where(
                    KnowledgeCompilationRun.adapter_name == adapter_name
                )
            ).rowcount
            or 0
        )

    return {
        "executed": True,
        "adapter_name": adapter_name,
        "target": target,
        "postgres": {
            "card_relations": relation_count + orphan_relation_count,
            "orphan_card_relations": orphan_relation_count,
            "community_assignments": assignment_count,
            "cognitive_cards": card_count,
            "evidence_chunks": chunk_count,
            "evidence": evidence_count,
            "compilation_runs": compilation_run_count,
        },
        "milvus": milvus_counts,
    }


async def main_async(args: argparse.Namespace) -> None:
    if (
        str(args.provider or "").strip() or str(args.model or "").strip()
        or str(args.prompt_profile or "production").strip() != "production"
    ) and args.mode != "cards":
        raise ValueError(
            "--model、--provider 和自定义 --prompt-profile "
            "当前仅用于 cards 质量验证模式"
        )
    session_id = args.session_id or f"ft-news-card-relation-{uuid4().hex[:12]}"
    trace_name = (
        "kg.atomic_card.batch_validation"
        if args.mode == "cards"
        else "kg.ft_news_card_relation.workflow"
    )
    trace_input = {
        "mode": args.mode,
        "target": args.target,
        "model": str(args.model or "").strip() or None,
        "provider": str(args.provider or "").strip() or None,
        "limit": max(1, min(100, int(args.limit))),
        "news_ids": _ordered_unique_positive_ints(args.news_id),
        "include_evaluation_details": args.include_evaluation_details,
        "clean_before_run": args.mode == "workflow" and not args.keep_existing_data,
        "concurrency": max(1, min(20, int(args.concurrency))),
        "chunk_timeout_seconds": max(1.0, float(args.chunk_timeout)),
        "prompt_profile": str(args.prompt_profile or "production").strip(),
        "session_id": session_id,
    }
    with langfuse_propagation_context(
        trace_name=trace_name,
        session_id=session_id,
        tags=["kg", "ft-news", "atomic-card", "relation-discovery", "workflow"],
        metadata=trace_input,
    ):
        with langfuse_observation(
            name=trace_name,
            as_type="chain",
            input=trace_input,
            metadata=trace_input,
        ):
            result = (
                await run_card_validation(args, session_id=session_id)
                if args.mode == "cards"
                else await run(args)
            )
            output_path = Path(args.output) if args.output else Path(
                f"/tmp/01_ft_news_card_relation_{args.mode}_{session_id}.json"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary = (
                _card_result_summary(result)
                if args.mode == "cards"
                else _result_summary(result)
            )
            langfuse_update_span(
                output={**summary, "output_file": str(output_path)},
                status_message="completed",
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print(f"\nLangfuse trace: {trace_name}")
            print(f"Session ID: {session_id}")
            print(f"结果文件: {output_path}")


def _result_summary(result: dict) -> dict:
    workflow = result["workflow"]
    ingestion = workflow["ingestion"]
    statistics = workflow.get("relation_statistics") or {}
    total_statistics = statistics.get("total") or {}
    cross_statistics = statistics.get("cross_chunk") or {}
    edge = workflow.get("edge_persistence") or {}
    return {
        "status": result["status"],
        "news_count": len(result["selected_news"]),
        "news_ids": [item["id"] for item in result["selected_news"]],
        "cleanup": result["cleanup"],
        "compiled_evidence": int(ingestion.get("compiled_evidence") or 0),
        "failed_records": int(ingestion.get("failed_records") or 0),
        "card_count": len(ingestion.get("relation_card_ids") or []),
        "observed": int(total_statistics.get("observed") or 0),
        "inferred": int(total_statistics.get("inferred") or 0),
        "positive_relations": int(total_statistics.get("positive_relations") or 0),
        "cross_chunk_no_relation": int(cross_statistics.get("no_relation") or 0),
        "relation_statistics": statistics,
        "changed_edge_ids": list(edge.get("changed_edge_ids") or []),
        "graph_event_ids": list(edge.get("graph_event_ids") or []),
    }


def _card_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    relation_probes = [
        {
            "news_id": item.get("news_id"),
            "chunk_index": item.get("chunk_index"),
            "card_index": card_index,
            "summary": card.get("summary") or "",
            "items": list(card.get("relation_probes") or []),
        }
        for item in result.get("results") or []
        for card_index, card in enumerate(item.get("cards") or [], start=1)
        if card.get("relation_probes")
    ]
    return {
        key: result[key]
        for key in (
            "status",
            "mode",
            "news_count",
            "news_ids",
            "chunk_count",
            "completed_chunks",
            "failed_chunks",
            "completed_news_ids",
            "failed_news_ids",
            "card_count",
            "relation_count",
            "repair_count",
            "repair_attempt_count",
            "discarded_card_count",
            "discarded_relation_count",
            "requested_concurrency",
            "effective_concurrency",
            "chunk_timeout_seconds",
        )
    } | {
        "relation_probe_count": sum(
            len(item["items"]) for item in relation_probes
        ),
        "relation_probes": relation_probes,
    }


def _ordered_unique_positive_ints(values: list[int]) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values:
        item = int(value)
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(main_async(args))
    finally:
        langfuse_flush()


if __name__ == "__main__":
    main()
