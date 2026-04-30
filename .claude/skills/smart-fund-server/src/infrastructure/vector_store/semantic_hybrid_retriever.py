"""Semantic hybrid retriever backed by Milvus and the embedding service."""

from __future__ import annotations

import logging
import json
import re
from typing import Any

from src.domain.knowledge.retrieval import RetrievalHit, RetrievalOptions, SemanticHybridRetriever
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, EvidenceChunk
from src.domain.knowledge.wiki import WikiPage
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.config import settings
from src.infrastructure.vector_store.milvus_hybrid_store import (
    MilvusHybridDocument,
    MilvusHybridHit,
    MilvusHybridStore,
)

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
        limit = max(options.semantic_hybrid_limit, 1)
        search_limit = _candidate_limit(limit)
        hits = self.store.hybrid_search(
            query_text=_expanded_query_text(query),
            query_vector=query_vector,
            adapter_name=options.adapter_name,
            target=options.target,
            limit=search_limit,
        )
        return [
            _retrieval_hit_from_milvus_hit(
                hit,
                score=_reranked_score(query, hit.text, hit.score),
            )
            for hit in sorted(
                hits,
                key=lambda item: (-_reranked_score(query, item.text, item.score), item.chunk_id),
            )[:limit]
        ]

    async def rebuild_index(
        self,
        *,
        adapter_name: str,
        target: str,
        chunks: list[EvidenceChunk],
        nodes: list[CompiledNode] | None = None,
        edges: list[CompiledEdge] | None = None,
        wiki_pages: list[WikiPage] | None = None,
        kg_version: str = "",
    ) -> int:
        documents = _light_rag_documents(
            chunks=chunks,
            nodes=nodes or [],
            edges=edges or [],
            wiki_pages=wiki_pages or [],
        )
        if not documents:
            return self.store.replace_documents(
                adapter_name=adapter_name,
                target=target,
                documents=[],
                vectors=[],
                embedding_model=settings.EMBEDDING_MODEL,
                kg_version=kg_version,
            )
        texts = [document.text for document in documents]
        vectors = await embed_texts(texts)
        return self.store.replace_documents(
            adapter_name=adapter_name,
            target=target,
            documents=documents,
            vectors=vectors,
            embedding_model=settings.EMBEDDING_MODEL,
            kg_version=kg_version,
        )

    async def upsert_index(
        self,
        *,
        adapter_name: str,
        target: str,
        chunks: list[EvidenceChunk],
        nodes: list[CompiledNode] | None = None,
        edges: list[CompiledEdge] | None = None,
        wiki_pages: list[WikiPage] | None = None,
        kg_version: str = "",
    ) -> int:
        documents = _light_rag_documents(
            chunks=chunks,
            nodes=nodes or [],
            edges=edges or [],
            wiki_pages=wiki_pages or [],
        )
        if not documents:
            return 0
        texts = [document.text for document in documents]
        vectors = await embed_texts(texts)
        return self.store.upsert_documents(
            adapter_name=adapter_name,
            target=target,
            documents=documents,
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


def _expanded_query_text(query: str) -> str:
    terms = _ordered_unique([query.strip(), *_strong_query_terms(query)])
    return " ".join(term for term in terms if term)


def _candidate_limit(limit: int) -> int:
    return max(limit, min(limit * 3, limit + 30))


def _retrieval_hit_from_milvus_hit(hit: MilvusHybridHit, *, score: float) -> RetrievalHit:
    source_type = str(hit.metadata.get("source_type") or "")
    source_id = str(hit.metadata.get("source_id") or "")
    evidence_refs = [hit.evidence_id] if hit.evidence_id else []
    if source_type == "kg_node" and source_id:
        return RetrievalHit(
            hit_id=hit.chunk_id,
            hit_type="node",
            title=f"node:{source_id}",
            snippet=hit.text[:800],
            score=score,
            source="semantic_hybrid",
            node_refs=[source_id],
            evidence_refs=evidence_refs,
        )
    if source_type == "kg_edge" and source_id:
        return RetrievalHit(
            hit_id=hit.chunk_id,
            hit_type="edge",
            title=f"edge:{source_id}",
            snippet=hit.text[:800],
            score=score,
            source="semantic_hybrid",
            edge_refs=[source_id],
            evidence_refs=evidence_refs,
        )
    if source_type == "kg_wiki" and source_id:
        return RetrievalHit(
            hit_id=hit.chunk_id,
            hit_type="wiki",
            title=f"wiki:{source_id}",
            snippet=hit.text[:800],
            score=score,
            source="semantic_hybrid",
            evidence_refs=evidence_refs,
        )
    return RetrievalHit(
        hit_id=hit.chunk_id,
        hit_type="semantic_hybrid",
        title=f"evidence_chunk:{hit.evidence_id}",
        snippet=hit.text[:800],
        score=score,
        source="semantic_hybrid",
        evidence_refs=evidence_refs,
    )


def _light_rag_documents(
    *,
    chunks: list[EvidenceChunk],
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    wiki_pages: list[WikiPage],
) -> list[MilvusHybridDocument]:
    node_by_id = {node.node_id: node for node in nodes}
    return [
        *[_evidence_document(chunk) for chunk in chunks],
        *[_node_document(node) for node in nodes],
        *[_edge_document(edge, node_by_id) for edge in edges],
        *[_wiki_document(page) for page in wiki_pages],
    ]


def _evidence_document(chunk: EvidenceChunk) -> MilvusHybridDocument:
    payload = dict(chunk.payload or {})
    payload.setdefault("source_type", "kg_evidence")
    payload.setdefault("source_id", chunk.evidence_id)
    return MilvusHybridDocument(
        chunk_id=chunk.chunk_id,
        evidence_id=chunk.evidence_id,
        text=chunk.content,
        metadata=payload,
    )


def _node_document(node: CompiledNode) -> MilvusHybridDocument:
    aliases = _ordered_unique([node.canonical_name, *node.aliases, *node.external_ids.values()])
    text = _join_parts(
        [
            f"Node Key: {node.canonical_name}",
            f"Node Type: {node.node_type}",
            f"Aliases: {' '.join(aliases)}",
            f"Value: {_json_text(node.properties)}",
        ]
    )
    return MilvusHybridDocument(
        chunk_id=f"kg_kv:node:{node.node_id}",
        text=text,
        metadata={"source_type": "kg_node", "source_id": node.node_id},
    )


def _edge_document(edge: CompiledEdge, node_by_id: dict[str, CompiledNode]) -> MilvusHybridDocument:
    source_name = _node_name(edge.source_node_id, node_by_id)
    target_name = _node_name(edge.target_node_id, node_by_id)
    relation_key = f"{source_name} {edge.relation_type} {target_name}"
    text = _join_parts(
        [
            f"Edge Key: {relation_key}",
            f"Relation: {edge.relation_type}",
            f"Source: {source_name}",
            f"Target: {target_name}",
            f"Evidence: {' '.join(edge.evidence_ids)}",
            f"Value: {_json_text(edge.properties)}",
        ]
    )
    return MilvusHybridDocument(
        chunk_id=f"kg_kv:edge:{edge.edge_id}",
        evidence_id=edge.evidence_ids[0] if edge.evidence_ids else "",
        text=text,
        metadata={"source_type": "kg_edge", "source_id": edge.edge_id},
    )


def _wiki_document(page: WikiPage) -> MilvusHybridDocument:
    text = _join_parts(
        [
            f"Wiki Key: {page.title}",
            f"Page Type: {page.page_type}",
            f"Subject: {page.subject_type or ''} {page.subject_id or ''}",
            f"Summary: {page.summary}",
            f"Value: {page.content}",
        ]
    )
    return MilvusHybridDocument(
        chunk_id=f"kg_kv:wiki:{page.page_id}",
        evidence_id=page.source_evidence_ids[0] if page.source_evidence_ids else "",
        text=text,
        metadata={"source_type": "kg_wiki", "source_id": page.page_id},
    )


def _node_name(node_id: str, node_by_id: dict[str, CompiledNode]) -> str:
    node = node_by_id.get(node_id)
    return node.canonical_name if node is not None else node_id


def _join_parts(parts: list[str]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else ""


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
