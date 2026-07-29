#!/usr/bin/env python3
"""Reclassify historical identity edges and rebuild Card fact identities."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services.card_relation_write_service import (  # noqa: E402
    CardRelationWriteService,
)
from src.application.services.relation_discovery_service import (  # noqa: E402
    RELATION_VERIFICATION_PROMPT_VERSION,
    RelationDiscoveryService,
)
from src.infrastructure.persistence.repositories.card_relation_repository import (  # noqa: E402
    CardRelationRepository,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (  # noqa: E402
    KnowledgeRepositoryImpl,
)
from src.infrastructure.tasks.jettask_dispatcher import (  # noqa: E402
    send_kg_graph_changed,
)


async def _defer_batch_graph_event(**_kwargs) -> list[str]:
    """Defer expensive Community rebuilding until the full backfill completes."""

    return ["deferred-to-final-backfill-event"]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "用新关系契约重分类历史 same_fact/same_event，并根据 active observed "
            "same_fact 重建 Card fact_id"
        ),
    )
    parser.add_argument(
        "--target",
        choices=("test", "prod"),
        default="test",
    )
    parser.add_argument("--adapter-name", default="financial")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--skip-reclassification",
        action="store_true",
        help="不调用 LLM 重分类历史 identity 边，只重建 fact_id 与 Milvus",
    )
    parser.add_argument(
        "--same-fact-only",
        action="store_true",
        help="只重判当前 active same_fact；用于新增事实身份门禁后的低成本修复",
    )
    parser.add_argument(
        "--skip-graph-event",
        action="store_true",
        help="只回填 fact_id 和 Milvus，不额外投递最终 Community 重算事件",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> dict:
    if args.batch_size < 1:
        raise ValueError("--batch-size 必须大于等于 1")

    relation_repository = CardRelationRepository(target=args.target)
    knowledge_repository = KnowledgeRepositoryImpl(target=args.target)
    writer = CardRelationWriteService(
        knowledge_repository=knowledge_repository,
        relation_repository=relation_repository,
        graph_event_publisher=_defer_batch_graph_event,
    )
    manifests = knowledge_repository.list_atomic_cognitive_card_manifests(
        args.adapter_name,
        status="active",
    )
    adapter_card_ids = {item.cognitive_card_id for item in manifests}
    active_edges_before = relation_repository.list_active_edges()
    identity_kinds = (
        {"same_fact"} if args.same_fact_only else {"same_fact", "same_event"}
    )
    pending_identity_edges = [
        edge
        for edge in active_edges_before
        if edge.relation_kind in identity_kinds
        and edge.prompt_version != RELATION_VERIFICATION_PROMPT_VERSION
        and edge.source_card_id in adapter_card_ids
        and edge.target_card_id in adapter_card_ids
    ]

    batch_results: list[dict] = []
    failed_pairs: list[dict[str, str]] = []
    if pending_identity_edges and not args.skip_reclassification:
        discovery = RelationDiscoveryService(
            repository=knowledge_repository,
            relation_writer=writer,
        )
        for start in range(0, len(pending_identity_edges), args.batch_size):
            batch = pending_identity_edges[start : start + args.batch_size]
            await _reverify_batch(
                discovery,
                [(edge.source_card_id, edge.target_card_id) for edge in batch],
                adapter_name=args.adapter_name,
                target=args.target,
                workflow_id=(f"kg_card_fact_reclassify:{start // args.batch_size + 1}"),
                results=batch_results,
                failed_pairs=failed_pairs,
            )

    projection = await writer.refresh_fact_projection(
        [item.cognitive_card_id for item in manifests],
        adapter_name=args.adapter_name,
        target=args.target,
    )

    active_edges_after = relation_repository.list_active_edges()
    changed_edge_ids = sorted(
        {
            edge.id
            for edge in active_edges_after
            if edge.source_card_id in projection.fact_by_card_id
            and edge.target_card_id in projection.fact_by_card_id
        }
    )
    graph_event_ids: list[str] = []
    if projection.affected_card_ids and not args.skip_graph_event:
        identity_payload = {
            "adapter_name": args.adapter_name,
            "edge_ids": changed_edge_ids,
            "fact_projection": sorted(projection.fact_by_card_id.items()),
        }
        digest = hashlib.sha256(
            json.dumps(
                identity_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        graph_event_ids = await send_kg_graph_changed(
            adapter_name=args.adapter_name,
            changed_edge_ids=changed_edge_ids,
            affected_card_ids=list(projection.affected_card_ids),
            changes={
                "fact_id_changed_card_ids": list(projection.changed_card_ids),
                "historical_identity_edges_reclassified": len(pending_identity_edges),
                "backfill": True,
            },
            event_identity=f"kg_card_fact_backfill:{digest}",
            workflow_id=f"kg_card_fact_backfill:{digest}",
        )

    return {
        "target": args.target,
        "adapter_name": args.adapter_name,
        "same_fact_only": args.same_fact_only,
        "historical_identity_edges_pending": len(pending_identity_edges),
        "historical_identity_edges_reclassified": (
            0
            if args.skip_reclassification
            else sum(item["pairs_requested"] for item in batch_results)
        ),
        "reclassified_same_fact": sum(item["same_fact"] for item in batch_results),
        "reclassified_same_event": sum(item["same_event"] for item in batch_results),
        "reclassified_other_relation": sum(
            item["other_relation"] for item in batch_results
        ),
        "reclassified_no_relation": sum(item["no_relation"] for item in batch_results),
        "reclassification_failed_count": len(failed_pairs),
        "reclassification_failed_pairs": failed_pairs,
        "affected_card_count": len(projection.affected_card_ids),
        "changed_card_count": len(projection.changed_card_ids),
        "fact_count": len(projection.fact_ids),
        "changed_card_ids": list(projection.changed_card_ids),
        "graph_event_ids": graph_event_ids,
    }


async def _reverify_batch(
    discovery: RelationDiscoveryService,
    pairs: list[tuple[str, str]],
    *,
    adapter_name: str,
    target: str,
    workflow_id: str,
    results: list[dict],
    failed_pairs: list[dict[str, str]],
) -> None:
    """Isolate bad historical pairs without abandoning the full backfill."""

    if not pairs:
        return
    try:
        results.append(
            await discovery.reverify_card_pairs(
                pairs,
                adapter_name=adapter_name,
                target=target,
                persist_edges=True,
                workflow_id=workflow_id,
            )
        )
        return
    except Exception as exc:
        if len(pairs) == 1:
            left, right = pairs[0]
            failed_pairs.append(
                {
                    "source_card_id": left,
                    "target_card_id": right,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return

    midpoint = len(pairs) // 2
    await _reverify_batch(
        discovery,
        pairs[:midpoint],
        adapter_name=adapter_name,
        target=target,
        workflow_id=f"{workflow_id}:left",
        results=results,
        failed_pairs=failed_pairs,
    )
    await _reverify_batch(
        discovery,
        pairs[midpoint:],
        adapter_name=adapter_name,
        target=target,
        workflow_id=f"{workflow_id}:right",
        results=results,
        failed_pairs=failed_pairs,
    )


def main() -> None:
    print(
        json.dumps(
            asyncio.run(_run(_arguments())),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
