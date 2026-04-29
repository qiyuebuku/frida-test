"""Semantic hybrid retriever backed by Milvus and the embedding service."""

from __future__ import annotations

import logging
import re

from src.domain.knowledge.retrieval import RetrievalHit, RetrievalOptions, SemanticHybridRetriever
from src.domain.knowledge.schemas import EvidenceChunk
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.config import settings
from src.infrastructure.vector_store.milvus_hybrid_store import MilvusHybridStore

logger = logging.getLogger(__name__)


class MilvusSemanticHybridRetriever(SemanticHybridRetriever):
    backend_name = "milvus"

    def __init__(self, store: MilvusHybridStore | None = None):
        self.store = store or MilvusHybridStore()
        self.store.ensure_ready()
        self.enabled = True

    async def search(self, query: str, options: RetrievalOptions) -> list[RetrievalHit]:
        vectors = await embed_texts([query])
        query_vector = vectors[0] if vectors and vectors[0] else []
        hits = self.store.hybrid_search(
            query_text=query,
            query_vector=query_vector,
            adapter_name=options.adapter_name,
            target=options.target,
            limit=options.semantic_hybrid_limit,
        )
        return [
            RetrievalHit(
                hit_id=hit.chunk_id,
                hit_type="semantic_hybrid",
                title=f"evidence_chunk:{hit.evidence_id}",
                snippet=hit.text[:800],
                score=_reranked_score(query, hit.text, hit.score),
                source="semantic_hybrid",
                evidence_refs=[hit.evidence_id] if hit.evidence_id else [],
            )
            for hit in sorted(
                hits,
                key=lambda item: (-_reranked_score(query, item.text, item.score), item.chunk_id),
            )
        ]

    async def rebuild_index(
        self,
        *,
        adapter_name: str,
        target: str,
        chunks: list[EvidenceChunk],
        kg_version: str = "",
    ) -> int:
        texts = [chunk.content for chunk in chunks]
        vectors = await embed_texts(texts)
        return self.store.replace_chunks(
            adapter_name=adapter_name,
            target=target,
            chunks=chunks,
            vectors=vectors,
            embedding_model=settings.EMBEDDING_MODEL,
            kg_version=kg_version,
        )


def _reranked_score(query: str, text: str, base_score: float) -> float:
    strong_terms = _strong_query_terms(query)
    if not strong_terms:
        return base_score
    haystack = text.lower()
    matched = sum(1 for term in strong_terms if term in haystack)
    if matched:
        return base_score * (1.0 + min(matched, 4) * 0.35)
    return base_score * 0.35


def _strong_query_terms(query: str) -> list[str]:
    raw_terms = [
        item.lower()
        for item in re.findall(r"[A-Za-z0-9_.:]+|[\u4e00-\u9fff]+", query)
        if item.strip()
    ]
    terms: list[str] = []
    for term in raw_terms:
        normalized_code = _normalize_stock_code(term)
        if normalized_code:
            terms.append(normalized_code)
            continue
        if re.search(r"\d", term) and len(term) >= 3:
            terms.append(term)
            continue
        if _is_cjk(term):
            terms.extend(_cjk_strong_terms(term))
    return _ordered_unique(term for term in terms if term not in _GENERIC_CJK_TERMS)


def _normalize_stock_code(value: str) -> str:
    text = value.strip().lower()
    match = re.fullmatch(r"(?:sh|sz|bj)?(\d{6})(?:\.(?:sh|sz|bj))?", text)
    return match.group(1) if match else ""


def _is_cjk(value: str) -> bool:
    return bool(value) and all("\u4e00" <= char <= "\u9fff" for char in value)


def _cjk_strong_terms(value: str) -> list[str]:
    if len(value) <= 4:
        return [value] if len(value) >= 3 else []
    terms: list[str] = []
    for size in (4, 3):
        terms.extend(value[idx : idx + size] for idx in range(0, len(value) - size + 1))
    return terms


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


_GENERIC_CJK_TERMS = {
    "最近",
    "哪些",
    "事件",
    "影响",
    "受哪些",
    "哪些事",
    "事件影",
}
