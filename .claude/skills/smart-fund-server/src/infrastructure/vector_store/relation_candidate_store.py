"""Relation Probe 双视图召回与按 ID 精确取回的 Milvus 适配器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

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


@dataclass(frozen=True)
class RelationCardSearchHit:
    card_id: str
    summary: str
    metadata: dict
    matched_views: tuple[RecallView, ...]
    fusion_score: float
    semantic_score: float


class MilvusRelationCandidateStore:
    """将 Summary/Focus 两个 Collection 暴露为关系发现专用接口。"""

    _VIEW_ROLES: tuple[tuple[RecallView, str], ...] = (
        ("summary", MILVUS_COLLECTION_COGNITIVE_CARD),
        ("focus_evidence", MILVUS_COLLECTION_COGNITIVE_CARD_FOCUS),
    )

    def __init__(self, store: MilvusTypedHybridStore | None = None) -> None:
        self._store = store or MilvusTypedHybridStore()
        self._store.ensure_ready()

    @property
    def store(self) -> MilvusTypedHybridStore:
        return self._store

    async def search_cards(
        self,
        query: str,
        *,
        adapter_name: str,
        target: str,
        limit: int,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
    ) -> list[RelationCardSearchHit]:
        """Search Card summary and focus evidence views, then fuse by rank."""

        normalized_query = str(query or "").strip()
        if not normalized_query or limit <= 0:
            return []
        vector = (await embed_texts([normalized_query]))[0]
        per_view_limit = max(limit, min(100, limit * 2))

        async def search_view(
            view: RecallView,
            collection_role: str,
        ) -> tuple[RecallView, list[MilvusHybridHit]]:
            hits = await asyncio.to_thread(
                self._store.hybrid_search,
                collection_role=collection_role,
                query_text=normalized_query,
                query_vector=vector,
                adapter_name=adapter_name,
                target=target,
                limit=per_view_limit,
                time_start=time_start,
                time_end=time_end,
            )
            return view, hits

        view_results = await asyncio.gather(
            *[
                search_view(view, collection_role)
                for view, collection_role in self._VIEW_ROLES
            ]
        )
        fused: dict[str, dict] = {}
        for view, hits in view_results:
            for rank, hit in enumerate(hits, start=1):
                card_id = hit.target_id
                if not card_id:
                    continue
                item = fused.setdefault(
                    card_id,
                    {
                        "fusion_score": 0.0,
                        "semantic_score": 0.0,
                        "views": set(),
                        "metadata": {},
                        "focus_text": "",
                    },
                )
                item["fusion_score"] += 1.0 / (60.0 + rank)
                item["semantic_score"] = max(
                    float(item["semantic_score"]),
                    float(hit.score),
                )
                item["views"].add(view)
                if view == "summary":
                    item["metadata"] = dict(hit.metadata)
                elif not item["focus_text"]:
                    item["focus_text"] = hit.text
                    if not item["metadata"]:
                        item["metadata"] = dict(hit.metadata)

        ranked_ids = [
            card_id
            for card_id, _ in sorted(
                fused.items(),
                key=lambda item: (
                    -float(item[1]["fusion_score"]),
                    -float(item[1]["semantic_score"]),
                    item[0],
                ),
            )[:limit]
        ]
        summaries = await self.get_summaries(
            ranked_ids,
            adapter_name=adapter_name,
            target=target,
        )
        result: list[RelationCardSearchHit] = []
        for card_id in ranked_ids:
            item = fused[card_id]
            summary = summaries.get(card_id)
            result.append(
                RelationCardSearchHit(
                    card_id=card_id,
                    summary=summary.text if summary else "",
                    metadata=(
                        dict(summary.metadata)
                        if summary
                        else dict(item["metadata"])
                    ),
                    matched_views=tuple(sorted(item["views"])),
                    fusion_score=float(item["fusion_score"]),
                    semantic_score=float(item["semantic_score"]),
                )
            )
        return result

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
