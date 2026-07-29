#!/usr/bin/env python3
"""基于真实关系 Community 验证事实报告、条件性预测和 Milvus 发布链路。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.relation_graph_community_cognition_service import (  # noqa: E402
    RelationGraphCommunityCognitionService,
)
from src.domain.knowledge.relation_graph_cognition import (  # noqa: E402
    projection_target_id,
)
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.knowledge import (  # noqa: E402
    KnowledgeGraphCommunity,
)
from src.infrastructure.vector_store.milvus_hybrid_store import (  # noqa: E402
    MILVUS_COLLECTION_GRAPH_COMMUNITY_PROJECTION,
    MILVUS_COLLECTION_GRAPH_COMMUNITY_REPORT,
    MilvusTypedHybridStore,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="关系图 Community -> 事实报告 -> 条件性预测端到端验证"
    )
    parser.add_argument("--community-id", action="append", default=[])
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="未指定 Community 时，按成员数从高到低处理前 N 条",
    )
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--target", choices=("prod", "test"), default="prod")
    parser.add_argument(
        "--skip-projection",
        action="store_true",
        help="只生成事实报告，不生成条件性预测",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="清空所选 Community 的派生状态后重新调用 LLM",
    )
    parser.add_argument("--session-id", default="")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_communities(args: argparse.Namespace) -> list[dict[str, Any]]:
    selected_ids = _ordered_unique(args.community_id)
    with get_session(args.target) as session:
        statement = select(KnowledgeGraphCommunity).where(
            KnowledgeGraphCommunity.adapter_name == args.adapter,
            KnowledgeGraphCommunity.graph_status == "active",
        )
        if selected_ids:
            statement = statement.where(
                KnowledgeGraphCommunity.community_id.in_(selected_ids)
            )
        else:
            statement = statement.order_by(
                func.jsonb_array_length(
                    KnowledgeGraphCommunity.member_card_ids
                ).desc(),
                func.jsonb_array_length(
                    KnowledgeGraphCommunity.member_edge_ids
                ).desc(),
                KnowledgeGraphCommunity.graph_changed_at.desc(),
                KnowledgeGraphCommunity.community_id,
            ).limit(max(1, int(args.limit)))
        rows = list(session.scalars(statement).all())
        return [_community_payload(row) for row in rows]


def reset_derivations(*, target: str, community_ids: list[str]) -> None:
    with get_session(target) as session:
        rows = list(
            session.scalars(
                select(KnowledgeGraphCommunity).where(
                    KnowledgeGraphCommunity.community_id.in_(community_ids),
                    KnowledgeGraphCommunity.graph_status == "active",
                )
            ).all()
        )
        for row in rows:
            _reset_derivation(row)
        session.commit()


def _reset_derivation(row: KnowledgeGraphCommunity) -> None:
    row.title = ""
    row.fact_report = ""
    row.fact_referenced_card_ids = []
    row.fact_referenced_edge_ids = []
    row.fact_report_version = 0
    row.fact_report_generator_version = ""
    row.fact_report_graph_fingerprint = ""
    row.fact_report_status = "missing"
    row.fact_report_error = ""
    row.report_task_dispatched_fingerprint = ""
    row.fact_semantic_synced_version = ""
    row.conditional_projections = []
    row.projection_version = 0
    row.projection_generator_version = ""
    row.projection_graph_fingerprint = ""
    row.projection_fact_report_version = 0
    row.projection_status = "missing"
    row.projection_error = ""
    row.projection_task_dispatched_version = 0
    row.projection_semantic_synced_version = ""
    row.fact_report_generated_at = None
    row.projection_generated_at = None


def _community_payload(row: KnowledgeGraphCommunity) -> dict[str, Any]:
    return {
        "community_id": row.community_id,
        "adapter_name": row.adapter_name,
        "graph_fingerprint": row.graph_fingerprint,
        "graph_version": row.graph_version,
        "member_card_ids": list(row.member_card_ids or []),
        "member_edge_ids": list(row.member_edge_ids or []),
        "title": row.title,
        "fact_report": row.fact_report,
        "fact_report_version": row.fact_report_version,
        "fact_report_status": row.fact_report_status,
        "fact_semantic_synced_version": row.fact_semantic_synced_version,
        "conditional_projections": list(row.conditional_projections or []),
        "projection_version": row.projection_version,
        "projection_status": row.projection_status,
        "projection_semantic_synced_version": (
            row.projection_semantic_synced_version
        ),
    }


def reload_community(*, target: str, community_id: str) -> dict[str, Any]:
    with get_session(target) as session:
        row = session.get(KnowledgeGraphCommunity, community_id)
        if row is None:
            raise RuntimeError(f"Community 不存在: {community_id}")
        return _community_payload(row)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    communities = load_communities(args)
    if not communities:
        raise RuntimeError("没有找到符合条件的 active 关系 Community")
    session_id = args.session_id or f"community-cognition-{uuid4().hex[:12]}"
    metadata = {
        "target": args.target,
        "adapter_name": args.adapter,
        "community_ids": [item["community_id"] for item in communities],
        "force": bool(args.force),
        "skip_projection": bool(args.skip_projection),
    }

    async def record_projection_dispatch(messages: list[dict]) -> list[str]:
        return [
            f"validation-projection:{item['community_id']}:{index}"
            for index, item in enumerate(messages, start=1)
        ]

    service = RelationGraphCommunityCognitionService(
        target=args.target,
        projection_dispatcher=record_projection_dispatch,
    )
    if args.force:
        community_ids = [item["community_id"] for item in communities]
        await service.delete_stale_targets(
            adapter_name=args.adapter,
            fact_community_ids=community_ids,
            projection_community_ids=community_ids,
        )
        reset_derivations(
            target=args.target,
            community_ids=community_ids,
        )
        communities = [
            reload_community(
                target=args.target,
                community_id=community_id,
            )
            for community_id in community_ids
        ]
    results: list[dict[str, Any]] = []
    with langfuse_propagation_context(
        trace_name="kg.relation_graph_community.cognition.validation",
        session_id=session_id,
        tags=["kg", "graph_community", "cognition", "validation"],
        metadata=metadata,
    ):
        with langfuse_observation(
            name="kg.relation_graph_community.cognition.validation",
            as_type="span",
            input=metadata,
            metadata=metadata,
        ):
            for selected in communities:
                community_id = selected["community_id"]
                graph_fingerprint = selected["graph_fingerprint"]
                fact_result = await service.generate_fact_report(
                    community_id=community_id,
                    expected_graph_fingerprint=graph_fingerprint,
                )
                current = reload_community(
                    target=args.target,
                    community_id=community_id,
                )
                projection_result: dict[str, Any] | None = None
                if not args.skip_projection:
                    projection_result = await service.generate_projection(
                        community_id=community_id,
                        expected_graph_fingerprint=graph_fingerprint,
                        expected_fact_report_version=current[
                            "fact_report_version"
                        ],
                    )
                    current = reload_community(
                        target=args.target,
                        community_id=community_id,
                    )
                results.append(
                    {
                        "selected": selected,
                        "fact_result": fact_result,
                        "projection_result": projection_result,
                        "current": current,
                        "milvus": await asyncio.to_thread(
                            load_milvus_documents,
                            target=args.target,
                            adapter_name=args.adapter,
                            community_id=community_id,
                            include_projection=not args.skip_projection,
                        ),
                    }
                )
            output = {
                "status": "completed",
                "session_id": session_id,
                "community_count": len(results),
                "results": results,
            }
            langfuse_update_span(output=output, status_message="completed")
    langfuse_flush()
    return output


def load_milvus_documents(
    *,
    target: str,
    adapter_name: str,
    community_id: str,
    include_projection: bool,
) -> dict[str, Any]:
    store = MilvusTypedHybridStore()
    store.ensure_ready()
    report_hits = store.get_documents(
        collection_role=MILVUS_COLLECTION_GRAPH_COMMUNITY_REPORT,
        adapter_name=adapter_name,
        target=target,
        target_ids=[community_id],
    )
    projection_hits = []
    if include_projection:
        projection_hits = store.get_documents(
            collection_role=MILVUS_COLLECTION_GRAPH_COMMUNITY_PROJECTION,
            adapter_name=adapter_name,
            target=target,
            target_ids=[projection_target_id(community_id)],
        )
    return {
        "fact_reports": [_hit_payload(item) for item in report_hits],
        "projections": [_hit_payload(item) for item in projection_hits],
    }


def _hit_payload(hit: Any) -> dict[str, Any]:
    return {
        "target_id": hit.target_id,
        "text": hit.text,
        "metadata": dict(hit.metadata or {}),
    }


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def main() -> None:
    args = parse_args()
    result = asyncio.run(run(args))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output_path = args.output
    else:
        output_path = Path(
            f"/tmp/kg_relation_community_cognition_{result['session_id']}.json"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")
    print(f"\n结果已写入: {output_path}")


if __name__ == "__main__":
    main()
