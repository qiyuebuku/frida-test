"""Semantic candidate lookup for write-time KG normalization."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.knowledge.semantic_index_materials import SEMANTIC_COLLECTION_ENTITY, SEMANTIC_COLLECTION_RELATION
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridHit, MilvusTypedHybridStore

logger = logging.getLogger(__name__)


class MilvusNormalizationCandidateProvider:
    """Finds existing semantic entities/relations for LLM normalization.

    This provider is intentionally read-only. It gives the normalizer a small
    candidate set from Milvus so the LLM can decide whether to reuse an existing
    canonical object or create a new one.
    """

    def __init__(
        self,
        *,
        adapter_name: str = "financial",
        target: str | None = None,
        store: MilvusTypedHybridStore | None = None,
    ) -> None:
        self.adapter_name = adapter_name
        self.target = target or "prod"
        self.store = store or MilvusTypedHybridStore()

    async def search(
        self,
        *,
        query: str,
        entity_type: str = "",
        context: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        text = _candidate_query_text(query=query, entity_type=entity_type, context=context)
        if not text:
            return []
        try:
            vectors = await embed_texts([text])
            vector = vectors[0] if vectors and vectors[0] else []
            if not vector:
                return []
            hits = self.store.hybrid_search(
                collection_role=SEMANTIC_COLLECTION_ENTITY,
                query_text=text,
                query_vector=vector,
                adapter_name=self.adapter_name,
                target=self.target,
                limit=max(1, min(int(limit), 20)),
            )
        except Exception as exc:
            logger.warning(
                "[kg_normalization] semantic candidate search failed adapter=%s target=%s query=%r error=%s",
                self.adapter_name,
                self.target,
                query,
                exc,
            )
            return []
        return [_candidate_from_hit(hit) for hit in hits[:limit]]

    async def search_relations(
        self,
        *,
        query: str,
        relation_type: str = "",
        context: str = "",
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        text = _candidate_query_text(query=query, entity_type=relation_type, context=context)
        if not text:
            return []
        try:
            vectors = await embed_texts([text])
            vector = vectors[0] if vectors and vectors[0] else []
            if not vector:
                return []
            hits = self.store.hybrid_search(
                collection_role=SEMANTIC_COLLECTION_RELATION,
                query_text=text,
                query_vector=vector,
                adapter_name=self.adapter_name,
                target=self.target,
                limit=max(1, min(int(limit), 20)),
            )
        except Exception as exc:
            logger.warning(
                "[kg_normalization] semantic relation candidate search failed adapter=%s target=%s query=%r error=%s",
                self.adapter_name,
                self.target,
                query,
                exc,
            )
            return []
        return [_candidate_from_hit(hit) for hit in hits[:limit]]


def _candidate_query_text(*, query: str, entity_type: str, context: str) -> str:
    parts = [
        str(query or "").strip(),
        str(entity_type or "").strip(),
        str(context or "").strip()[:500],
    ]
    return "\n".join(part for part in parts if part)


def _candidate_from_hit(hit: MilvusHybridHit) -> dict[str, Any]:
    metadata = dict(hit.metadata or {})
    source_type = str(metadata.get("source_type") or "")
    if source_type in {"kg_node_card", "kg_event_card", "kg_node"}:
        return {
            "id": str(metadata.get("node_id") or metadata.get("source_id") or hit.target_id),
            "target_id": hit.target_id,
            "canonical_name": str(metadata.get("canonical_name") or _extract_line_value(hit.text, "Node Key") or ""),
            "entity_type": str(metadata.get("node_type") or ""),
            "aliases": metadata.get("aliases") or [],
            "score": round(float(hit.score), 6),
            "summary": hit.text[:700],
        }
    if source_type in {"kg_edge_card", "kg_edge"}:
        return {
            "id": str(metadata.get("edge_id") or metadata.get("source_id") or hit.target_id),
            "target_id": hit.target_id,
            "relation_type": str(metadata.get("relation_type") or ""),
            "source_name": str(metadata.get("source_name") or ""),
            "target_name": str(metadata.get("target_name") or ""),
            "score": round(float(hit.score), 6),
            "summary": hit.text[:700],
        }
    return {
        "id": str(metadata.get("source_id") or hit.target_id),
        "target_id": hit.target_id,
        "score": round(float(hit.score), 6),
        "summary": hit.text[:700],
    }


def _extract_line_value(text: str, label: str) -> str:
    prefix = f"{label}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""
