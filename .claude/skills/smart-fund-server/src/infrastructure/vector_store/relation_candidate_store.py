"""Relation Probe 双视图召回与按 ID 精确取回的 Milvus 适配器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from src.domain.knowledge.relation_discovery import RecallView, RelationRecallHit, RelationRoute
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.config import settings
from src.infrastructure.vector_store.milvus_hybrid_store import (
    MILVUS_COLLECTION_CHUNK,
    MILVUS_COLLECTION_COGNITIVE_CARD,
    MILVUS_COLLECTION_COGNITIVE_CARD_FOCUS,
    MilvusHybridHit,
    MilvusTypedHybridStore,
)


@dataclass(frozen=True)
class RelationCardText:
    card_id: str
    text: str
    metadata: dict


class MilvusRelationCandidateStore:
    """将 Summary/Focus 两个 Collection 暴露为关系发现专用接口。"""

    _VIEW_ROLES: tuple[tuple[RecallView, str], ...] = (
        ("summary", MILVUS_COLLECTION_COGNITIVE_CARD),
        ("focus_evidence", MILVUS_COLLECTION_COGNITIVE_CARD_FOCUS),
    )

    def __init__(self, store: MilvusTypedHybridStore | None = None) -> None:
        self._store = store or MilvusTypedHybridStore()
        self._store.ensure_ready()

    async def recall_routes(
        self,
        routes: list[RelationRoute],
        *,
        adapter_name: str,
        target: str,
        limit_per_view: int,
    ) -> dict[str, list[RelationRecallHit]]:
        if not routes:
            return {}
        vectors = await embed_texts([route.query for route in routes])
        result: dict[str, list[RelationRecallHit]] = {route.route_id: [] for route in routes}
        semaphore = asyncio.Semaphore(max(1, settings.KG_RELATION_MILVUS_CONCURRENCY))

        async def search_view(route, vector, view, collection_role):
            async with semaphore:
                hits = await asyncio.to_thread(
                    self._store.hybrid_search,
                    collection_role=collection_role,
                    query_text=route.query,
                    query_vector=vector,
                    adapter_name=adapter_name,
                    target=target,
                    limit=limit_per_view,
                )
            return route.route_id, [
                RelationRecallHit(
                    candidate_card_id=hit.target_id,
                    recall_view=view,
                    recall_rank=rank,
                    recall_score=float(hit.score),
                )
                for rank, hit in enumerate(hits, start=1)
            ]

        tasks = [
            search_view(route, vector, view, collection_role)
            for route, vector in zip(routes, vectors, strict=True)
            for view, collection_role in self._VIEW_ROLES
        ]
        for route_id, hits in await asyncio.gather(*tasks):
            result[route_id].extend(hits)
        return result

    async def get_summaries(
        self,
        card_ids: list[str],
        *,
        adapter_name: str,
        target: str,
    ) -> dict[str, RelationCardText]:
        hits = await self._get_documents(
            MILVUS_COLLECTION_COGNITIVE_CARD,
            card_ids,
            adapter_name=adapter_name,
            target=target,
        )
        return {
            hit.target_id: RelationCardText(
                card_id=hit.target_id,
                text=hit.text,
                metadata=dict(hit.metadata),
            )
            for hit in hits
        }

    async def get_focus_evidence(
        self,
        card_ids: list[str],
        *,
        adapter_name: str,
        target: str,
    ) -> dict[str, RelationCardText]:
        """按 Card ID 精确读取原始焦点证据视图。"""

        hits = await self._get_documents(
            MILVUS_COLLECTION_COGNITIVE_CARD_FOCUS,
            card_ids,
            adapter_name=adapter_name,
            target=target,
        )
        return {
            hit.target_id: RelationCardText(
                card_id=hit.target_id,
                text=hit.text,
                metadata=dict(hit.metadata),
            )
            for hit in hits
        }

    async def get_chunks(
        self,
        chunk_ids: list[str],
        *,
        adapter_name: str,
        target: str,
    ) -> dict[str, RelationCardText]:
        hits = await self._get_documents(
            MILVUS_COLLECTION_CHUNK,
            chunk_ids,
            adapter_name=adapter_name,
            target=target,
        )
        return {
            hit.target_id: RelationCardText(
                card_id=hit.target_id,
                text=hit.text,
                metadata=dict(hit.metadata),
            )
            for hit in hits
        }

    async def _get_documents(
        self,
        collection_role: str,
        target_ids: list[str],
        *,
        adapter_name: str,
        target: str,
    ) -> list[MilvusHybridHit]:
        unique_ids = [item for item in dict.fromkeys(target_ids) if item]
        if not unique_ids:
            return []
        return await asyncio.to_thread(
            self._store.get_documents,
            collection_role=collection_role,
            adapter_name=adapter_name,
            target=target,
            target_ids=unique_ids,
        )
