#!/usr/bin/env python3
"""清理 KG 中指定 source_id 前缀的 demo/test namespace 污染。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT))

from src.application.services.knowledge_service import _semantic_hybrid_retriever
from src.application.services.community_maintenance_service import (
    _community_vector_document,
    _semantic_document_from_graph_index_document,
)
from src.domain.knowledge.semantic_index_materials import SEMANTIC_COLLECTION_COMMUNITY
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCognitiveCard,
    KnowledgeCommunityAssignment,
    KnowledgeEvidence,
    KnowledgeEvidenceChunk,
    KnowledgeGraphCommunity,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import KnowledgeRepositoryImpl


def _strip_values(values: Any, prefix: str) -> list[str]:
    return [str(item) for item in values or [] if str(item) and not str(item).startswith(prefix)]


def _strip_assignment_payloads(values: Any, prefix: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        topic_intent = item.get("topic_intent") if isinstance(item.get("topic_intent"), dict) else {}
        if source_id.startswith(prefix) or str(topic_intent.get("source_id") or "").startswith(prefix):
            continue
        result.append(item)
    return result


def _strip_intents(values: Any, prefix: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("source_id") or "").startswith(prefix):
            continue
        result.append(item)
    return result


async def cleanup_demo_namespace(*, adapter_name: str, target: str, prefix: str, dry_run: bool) -> dict[str, Any]:
    repository = KnowledgeRepositoryImpl(target=target)
    deleted_community_ids: list[str] = []
    updated_community_ids: list[str] = []
    deleted_card_ids: list[str] = []
    deleted_chunk_ids: list[str] = []
    deleted_evidence_ids: list[str] = []
    counts = {
        "assignments_deleted": 0,
        "cards_deleted": 0,
        "chunks_deleted": 0,
        "evidence_deleted": 0,
        "communities_deleted": 0,
        "communities_updated": 0,
    }
    with repository._session_scope() as session:
        cards = list(
            session.scalars(
                select(KnowledgeCognitiveCard).where(
                    KnowledgeCognitiveCard.adapter_name == adapter_name,
                    KnowledgeCognitiveCard.source_id.like(f"{prefix}%"),
                )
            ).all()
        )
        deleted_card_ids = [row.cognitive_card_id for row in cards]
        deleted_evidence_ids = [row.evidence_id for row in cards if row.evidence_id]
        if deleted_evidence_ids:
            deleted_chunk_ids = list(
                session.scalars(
                    select(KnowledgeEvidenceChunk.chunk_id).where(
                        KnowledgeEvidenceChunk.adapter_name == adapter_name,
                        KnowledgeEvidenceChunk.evidence_id.in_(deleted_evidence_ids),
                    )
                ).all()
            )
        communities = list(
            session.scalars(
                select(KnowledgeGraphCommunity).where(
                    KnowledgeGraphCommunity.adapter_name == adapter_name,
                    KnowledgeGraphCommunity.projection == "cognitive_topic",
                )
            ).all()
        )
        for community in communities:
            metrics = dict(community.metrics or {})
            old_sources = [str(item) for item in metrics.get("source_ids") or [] if str(item)]
            has_demo = any(item.startswith(prefix) for item in old_sources)
            if not has_demo:
                continue
            new_sources = _strip_values(old_sources, prefix)
            new_evidence_ids = _strip_values(community.evidence_ids or [], prefix)
            new_chunk_ids = _strip_values(community.chunk_ids or [], prefix)
            new_card_ids = _strip_values(metrics.get("cognitive_card_ids") or [], prefix)
            new_intents = _strip_intents(metrics.get("assigned_intents") or [], prefix)
            new_assignments = _strip_assignment_payloads(metrics.get("assignments") or [], prefix)
            origin = str(metrics.get("origin") or "")
            if not new_sources and origin != "seed":
                deleted_community_ids.append(community.community_id)
                if not dry_run:
                    session.delete(community)
                continue
            metrics["source_ids"] = new_sources
            metrics["source_count"] = len(set(new_sources))
            metrics["unique_source_count"] = len(set(new_sources))
            metrics["evidence_count"] = len(set(new_evidence_ids))
            metrics["chunk_count"] = len(set(new_chunk_ids))
            metrics["cognitive_card_ids"] = new_card_ids
            metrics["cognitive_card_count"] = len(set(new_card_ids))
            metrics["assigned_intents"] = new_intents
            metrics["assigned_intent_count"] = len(new_intents)
            metrics["assignments"] = new_assignments
            metrics["assignment_count"] = len(new_assignments)
            community.metrics = metrics
            community.evidence_ids = new_evidence_ids
            community.chunk_ids = new_chunk_ids
            updated_community_ids.append(community.community_id)
        if deleted_card_ids and not dry_run:
            counts["assignments_deleted"] = session.execute(
                delete(KnowledgeCommunityAssignment).where(
                    KnowledgeCommunityAssignment.adapter_name == adapter_name,
                    KnowledgeCommunityAssignment.cognitive_card_id.in_(deleted_card_ids),
                )
            ).rowcount or 0
            counts["cards_deleted"] = session.execute(
                delete(KnowledgeCognitiveCard).where(
                    KnowledgeCognitiveCard.adapter_name == adapter_name,
                    KnowledgeCognitiveCard.cognitive_card_id.in_(deleted_card_ids),
                )
            ).rowcount or 0
        if deleted_evidence_ids and not dry_run:
            counts["chunks_deleted"] = session.execute(
                delete(KnowledgeEvidenceChunk).where(
                    KnowledgeEvidenceChunk.adapter_name == adapter_name,
                    KnowledgeEvidenceChunk.evidence_id.in_(deleted_evidence_ids),
                )
            ).rowcount or 0
            counts["evidence_deleted"] = session.execute(
                delete(KnowledgeEvidence).where(
                    KnowledgeEvidence.adapter_name == adapter_name,
                    KnowledgeEvidence.evidence_id.in_(deleted_evidence_ids),
                )
            ).rowcount or 0
        counts["communities_deleted"] = len(deleted_community_ids)
        counts["communities_updated"] = len(updated_community_ids)
        if dry_run:
            session.rollback()
    milvus_deleted = 0
    milvus_upserted = 0
    if not dry_run:
        retriever = _semantic_hybrid_retriever()
        if deleted_chunk_ids:
            milvus_deleted += await retriever.delete_documents(
                adapter_name=adapter_name,
                target=target,
                chunk_ids=deleted_chunk_ids,
            )
        if deleted_card_ids:
            milvus_deleted += await retriever.delete_documents(
                adapter_name=adapter_name,
                target=target,
                chunk_ids=deleted_card_ids,
            )
        if deleted_community_ids:
            milvus_deleted += await retriever.delete_documents_by_role(
                collection_role=SEMANTIC_COLLECTION_COMMUNITY,
                adapter_name=adapter_name,
                target=target,
                target_ids=deleted_community_ids,
            )
        if updated_community_ids:
            updated_communities = repository.list_graph_communities_by_ids(
                adapter_name,
                community_ids=updated_community_ids,
            )
            milvus_upserted += await retriever.upsert_semantic_documents(
                adapter_name=adapter_name,
                target=target,
                documents=[
                    _semantic_document_from_graph_index_document(_community_vector_document(community))
                    for community in updated_communities
                ],
                kg_version="demo_namespace_cleanup",
            )
    return {
        "adapter_name": adapter_name,
        "target": target,
        "prefix": prefix,
        "dry_run": dry_run,
        "counts": counts,
        "deleted_card_ids": deleted_card_ids,
        "deleted_evidence_ids": deleted_evidence_ids,
        "deleted_chunk_ids": deleted_chunk_ids,
        "deleted_community_ids": deleted_community_ids,
        "updated_community_ids": updated_community_ids,
        "milvus_deleted": milvus_deleted,
        "milvus_upserted": milvus_upserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default="financial")
    parser.add_argument("--target", default="prod")
    parser.add_argument("--prefix", default="usage_demo_write_path")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(
        cleanup_demo_namespace(
            adapter_name=args.adapter,
            target=args.target,
            prefix=args.prefix,
            dry_run=not args.apply,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
