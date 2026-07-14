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
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.ft_news_knowledge_graph_workflow_service import (  # noqa: E402
    FtNewsKnowledgeGraphWorkflowService,
)
from src.domain.knowledge.semantic_index_materials import (  # noqa: E402
    SEMANTIC_COLLECTION_CARD_RELATION,
    SEMANTIC_COLLECTION_CHUNK,
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SEMANTIC_COLLECTION_COGNITIVE_CARD_FOCUS,
)
from src.infrastructure.connections import get_session  # noqa: E402
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


def load_news(args: argparse.Namespace) -> list[dict]:
    """只选择 ft_news 行；知识图谱业务转换由应用服务完成。"""

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
    return [
        {
            "id": int(row.id),
            "title": row.title,
            "source": row.source,
            "source_name": row.source_name,
            "published_at": _iso(row.published_at),
            "created_at": _iso(row.created_at),
        }
        for row in ordered_rows
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
            "card_relations": relation_count,
            "community_assignments": assignment_count,
            "cognitive_cards": card_count,
            "evidence_chunks": chunk_count,
            "evidence": evidence_count,
            "compilation_runs": compilation_run_count,
        },
        "milvus": milvus_counts,
    }


async def main_async(args: argparse.Namespace) -> None:
    session_id = args.session_id or f"ft-news-card-relation-{uuid4().hex[:12]}"
    trace_input = {
        "target": args.target,
        "limit": max(1, min(100, int(args.limit))),
        "news_ids": _ordered_unique_positive_ints(args.news_id),
        "include_evaluation_details": args.include_evaluation_details,
        "clean_before_run": not args.keep_existing_data,
        "session_id": session_id,
    }
    with langfuse_propagation_context(
        trace_name="kg.ft_news_card_relation.workflow",
        session_id=session_id,
        tags=["kg", "ft-news", "atomic-card", "relation-discovery", "workflow"],
        metadata=trace_input,
    ):
        with langfuse_observation(
            name="kg.ft_news_card_relation.workflow",
            as_type="chain",
            input=trace_input,
            metadata=trace_input,
        ):
            result = await run(args)
            output_path = Path(args.output) if args.output else Path(
                f"/tmp/01_ft_news_card_relation_workflow_{session_id}.json"
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary = _result_summary(result)
            langfuse_update_span(
                output={**summary, "output_file": str(output_path)},
                status_message="completed",
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print("\nLangfuse trace: kg.ft_news_card_relation.workflow")
            print(f"Session ID: {session_id}")
            print(f"结果文件: {output_path}")


def _result_summary(result: dict) -> dict:
    workflow = result["workflow"]
    ingestion = workflow["ingestion"]
    relation = workflow["relation_discovery"]
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
