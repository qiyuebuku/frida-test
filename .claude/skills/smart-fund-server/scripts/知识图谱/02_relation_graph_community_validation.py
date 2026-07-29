#!/usr/bin/env python3
"""Replay active verified Card Edges into relationship-first Communities."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.relation_graph_community_service import (  # noqa: E402
    RelationGraphCommunityService,
)
from src.domain.knowledge.relation_graph_community import (  # noqa: E402
    RelationGraphClusteringConfig,
    discover_relation_graph_partition,
)
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.knowledge import (  # noqa: E402
    KnowledgeCardRelation,
    KnowledgeGraphCommunity,
)
from src.infrastructure.persistence.repositories.relation_graph_community_repository import (  # noqa: E402
    RelationGraphCommunityRepository,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="使用 active verified Card Edge 验证关系图 Community 构建链路"
    )
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--target", choices=("prod", "test"), default="prod")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="只选择最近 N 条 active Edge 作为变化种子；0 表示全部。闭包仍会完整展开。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只计算平行 Community 分区，不写数据库投影",
    )
    parser.add_argument("--session-id", default="")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="展开 Edge、Community 成员和跨 Community 关系明细。",
    )
    return parser.parse_args()


def load_changed_edge_seeds(
    *,
    target: str,
    limit: int,
) -> tuple[list[str], list[str]]:
    with get_session(target) as session:  # type: ignore[arg-type]
        statement = (
            select(KnowledgeCardRelation)
            .where(KnowledgeCardRelation.status == "active")
            .order_by(
                KnowledgeCardRelation.updated_at.desc(),
                KnowledgeCardRelation.id,
            )
        )
        if limit > 0:
            statement = statement.limit(limit)
        rows = list(session.scalars(statement).all())
    return (
        [row.id for row in rows],
        sorted(
            {
                card_id
                for row in rows
                for card_id in (row.source_card_id, row.target_card_id)
            }
        ),
    )


def load_current_communities(
    *,
    target: str,
    adapter_name: str,
) -> list[dict]:
    with get_session(target) as session:  # type: ignore[arg-type]
        rows = list(
            session.scalars(
                select(KnowledgeGraphCommunity)
                .where(
                    KnowledgeGraphCommunity.adapter_name == adapter_name,
                    KnowledgeGraphCommunity.graph_status == "active",
                )
                .order_by(KnowledgeGraphCommunity.identity_anchor_card_id)
            ).all()
        )
    return [
        {
            "community_id": row.community_id,
            "identity_anchor_card_id": row.identity_anchor_card_id,
            "member_card_ids": list(row.member_card_ids or []),
            "member_edge_ids": list(row.member_edge_ids or []),
            "graph_fingerprint": row.graph_fingerprint,
            "graph_version": row.graph_version,
            "fact_report_status": row.fact_report_status,
            "projection_status": row.projection_status,
        }
        for row in rows
    ]


async def run(args: argparse.Namespace) -> dict:
    session_id = args.session_id or f"relation-graph-community-{uuid4().hex[:12]}"
    edge_ids, card_ids = load_changed_edge_seeds(
        target=args.target,
        limit=max(0, int(args.limit)),
    )
    metadata = {
        "adapter_name": args.adapter,
        "target": args.target,
        "limit": max(0, int(args.limit)),
        "dry_run": bool(args.dry_run),
        "changed_edge_count": len(edge_ids),
        "affected_card_count": len(card_ids),
    }
    with langfuse_propagation_context(
        trace_name="kg.relation_graph_community.validation",
        session_id=session_id,
        tags=["kg", "graph_community", "validation"],
        metadata=metadata,
    ):
        with langfuse_observation(
            name="kg.relation_graph_community.validation",
            as_type="span",
            input=metadata,
            metadata=metadata,
        ):
            repository = RelationGraphCommunityRepository(
                target=args.target  # type: ignore[arg-type]
            )
            if args.dry_run:
                affected = await asyncio.to_thread(
                    repository.load_affected_graph,
                    adapter_name=args.adapter,
                    seed_card_ids=card_ids,
                )
                partition = discover_relation_graph_partition(
                    affected,
                    config=RelationGraphClusteringConfig(),
                )
                result = {
                    "status": "completed",
                    "dry_run": True,
                    "affected_edges": len(affected.edges),
                    "rejected_edge_ids": list(affected.rejected_edge_ids),
                    "components": len(partition.communities),
                    "community_relations": len(
                        partition.community_relations
                    ),
                    "connected_regions": partition.connected_region_count,
                    "clustered_regions": partition.clustered_region_count,
                    "retained_regions": partition.retained_region_count,
                    "community_sizes": sorted(
                        (
                            len(item.member_card_ids)
                            for item in partition.communities
                        ),
                        reverse=True,
                    ),
                }
                if args.verbose:
                    result["component_details"] = [
                        {
                            "community_id": item.community_id,
                            "identity_anchor_card_id": item.identity_anchor_card_id,
                            "member_card_ids": list(item.member_card_ids),
                            "member_edge_ids": list(item.member_edge_ids),
                            "graph_fingerprint": item.graph_fingerprint,
                        }
                        for item in partition.communities
                    ]
                    result["community_relation_details"] = [
                        {
                            "relation_id": item.relation_id,
                            "source_community_id": item.source_community_id,
                            "target_community_id": item.target_community_id,
                            "relation_kind": item.relation_kind,
                            "supporting_edge_ids": list(
                                item.supporting_edge_ids
                            ),
                        }
                        for item in partition.community_relations
                    ]
            else:
                result = await RelationGraphCommunityService(
                    target=args.target,
                    repository=repository,
                ).refresh_from_graph_change(
                    adapter_name=args.adapter,
                    changed_edge_ids=edge_ids,
                    affected_card_ids=card_ids,
                    changes={"validation_replay": True},
                    event_identity=f"validation:{session_id}",
                )
                current_communities = load_current_communities(
                    target=args.target,
                    adapter_name=args.adapter,
                )
                result["current_community_count"] = len(
                    current_communities
                )
                result["current_community_sizes"] = sorted(
                    (
                        len(item["member_card_ids"])
                        for item in current_communities
                    ),
                    reverse=True,
                )
                if args.verbose:
                    result["changed_edge_ids"] = edge_ids
                    result["affected_card_ids"] = card_ids
                    result["current_communities"] = current_communities
            result["session_id"] = session_id
            langfuse_update_span(output=result, status_message="completed")
            return result


def main() -> None:
    args = parse_args()
    result = asyncio.run(run(args))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
