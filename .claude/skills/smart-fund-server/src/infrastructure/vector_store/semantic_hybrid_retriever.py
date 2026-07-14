"""Semantic hybrid retriever backed by Milvus and the embedding service."""

from __future__ import annotations

import logging
import re
import time

from src.domain.knowledge.retrieval import RetrievalHit, RetrievalOptions, SemanticHybridRetriever
from src.domain.knowledge.retrieval_profile import profile_event, profile_span
from src.domain.knowledge.schemas import CompiledEdge, CompiledNode, EvidenceChunk
from src.domain.knowledge.graph_index import GraphProjectionProfile
from src.domain.knowledge.semantic_index_materials import (
    SEMANTIC_COLLECTION_CHUNK,
    SEMANTIC_COLLECTION_CARD_RELATION,
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SEMANTIC_COLLECTION_COMMUNITY,
    SEMANTIC_COLLECTION_COMMUNITY_INSIGHT,
    SEMANTIC_COLLECTION_RELATION,
    SEMANTIC_COLLECTION_ROLES,
    SemanticVectorDocument,
    build_semantic_vector_documents,
)
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.config import settings
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_update_span,
)
from src.infrastructure.vector_store.milvus_hybrid_store import (
    MilvusHybridDocument,
    MilvusHybridHit,
    MilvusTypedHybridStore,
)

logger = logging.getLogger(__name__)


AGENT_READ_COLLECTION_ROLES = (
    SEMANTIC_COLLECTION_CHUNK,
    SEMANTIC_COLLECTION_COGNITIVE_CARD,
    SEMANTIC_COLLECTION_CARD_RELATION,
    SEMANTIC_COLLECTION_COMMUNITY,
    SEMANTIC_COLLECTION_COMMUNITY_INSIGHT,
)


class MilvusSemanticHybridRetriever(SemanticHybridRetriever):
    backend_name = "milvus"

    def __init__(self, store: MilvusTypedHybridStore | None = None):
        self.store = store or MilvusTypedHybridStore()
        with profile_span("milvus_retriever.ensure_ready"):
            self.store.ensure_ready()
        self.enabled = True
        self.last_search_diagnostics: dict[str, object] = {}

    async def search(self, query: str, options: RetrievalOptions) -> list[RetrievalHit]:
        with langfuse_observation(
            name="milvus.semantic_search",
            as_type="retriever",
            input={
                "query": query,
                "adapter_name": options.adapter_name,
                "target": options.target,
                "semantic_hybrid_limit": options.semantic_hybrid_limit,
            },
            metadata={"backend": self.backend_name},
        ):
            try:
                result = await self._search_impl(query, options)
                langfuse_update_span(
                    output={
                        "hits": len(result),
                        "diagnostics": self.last_search_diagnostics,
                        "sample_hits": [
                            {
                                "id": hit.hit_id,
                                "type": hit.hit_type,
                                "title": hit.title,
                                "score": hit.score,
                                "evidence_refs": hit.evidence_refs[:5],
                            }
                            for hit in result[:10]
                        ],
                    },
                    status_message="completed",
                )
                return result
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise

    async def _search_impl(self, query: str, options: RetrievalOptions) -> list[RetrievalHit]:
        with profile_span("semantic_hybrid.embed_query", query=query):
            vectors = await embed_texts([query])
        query_vector = vectors[0] if vectors and vectors[0] else []
        limit = max(options.semantic_hybrid_limit, 1)
        search_limit = _candidate_limit(limit)
        expanded_query = _expanded_query_text(query)
        profile_event(
            "semantic_hybrid.query_ready",
            query=query,
            expanded_query=expanded_query,
            vector_dim=len(query_vector),
            limit=limit,
            search_limit=search_limit,
        )
        search_limits = _typed_search_limits(limit)
        result: list[RetrievalHit] = []
        raw_count = 0
        per_collection_hits: dict[str, int] = {}
        per_collection_selected: dict[str, int] = {}
        per_collection_latency_ms: dict[str, float] = {}
        for collection_role, role_limit in search_limits.items():
            if role_limit <= 0:
                continue
            role_search_limit = _candidate_limit(role_limit)
            started_at = time.perf_counter()
            with profile_span(
                "semantic_hybrid.milvus_typed_search",
                collection_role=collection_role,
                limit=role_search_limit,
            ):
                hits = self.store.hybrid_search(
                    collection_role=collection_role,
                    query_text=expanded_query,
                    query_vector=query_vector,
                    adapter_name=options.adapter_name,
                    target=options.target,
                    limit=role_search_limit,
                    time_start=options.semantic_time_start or options.graph_time_start,
                    time_end=options.semantic_time_end or options.graph_time_end,
                )
            per_collection_latency_ms[collection_role] = round((time.perf_counter() - started_at) * 1000, 1)
            raw_count += len(hits)
            per_collection_hits[collection_role] = len(hits)
            selected = [
                _retrieval_hit_from_milvus_hit(
                    _hit_with_collection_role(hit, collection_role),
                    score=_reranked_score(query, hit.text, hit.score),
                )
                for hit in sorted(
                    hits,
                    key=lambda item: (-_reranked_score(query, item.text, item.score), item.chunk_id),
                )[:role_limit]
            ]
            per_collection_selected[collection_role] = len(selected)
            result.extend(selected)
        self.last_search_diagnostics = {
            "query": query,
            "expanded_query": expanded_query,
            "collection_limits": search_limits,
            "per_collection_raw_hits": per_collection_hits,
            "per_collection_selected_hits": per_collection_selected,
            "per_collection_latency_ms": per_collection_latency_ms,
            "total_raw_hits": raw_count,
            "total_selected_hits": len(result),
        }
        profile_event(
            "semantic_hybrid.result",
            raw_hits=raw_count,
            hits=len(result),
            collection_limits=search_limits,
            per_collection_hits=per_collection_hits,
        )
        return result

    async def get_by_ids(self, target_ids: list[str], options: RetrievalOptions) -> list[RetrievalHit]:
        with langfuse_observation(
            name="milvus.get_by_ids",
            as_type="retriever",
            input={
                "target_ids": target_ids[:50],
                "target_id_count": len(target_ids),
                "adapter_name": options.adapter_name,
                "target": options.target,
            },
            metadata={"backend": self.backend_name},
        ):
            try:
                result = await self._get_by_ids_impl(target_ids, options)
                unique_target_ids = [target_id for target_id in dict.fromkeys(target_ids) if target_id]
                returned_ids = {hit.hit_id for hit in result}
                hit_details_limit = 50
                langfuse_update_span(
                    output={
                        "requested_count": len(unique_target_ids),
                        "returned_count": len(result),
                        "missing_count": len([target_id for target_id in unique_target_ids if target_id not in returned_ids]),
                        "missing_target_ids": [
                            target_id for target_id in unique_target_ids if target_id not in returned_ids
                        ][:hit_details_limit],
                        "hit_type_counts": _hit_type_counts(result),
                        "hit_details_count": min(len(result), hit_details_limit),
                        "hit_details_truncated": len(result) > hit_details_limit,
                        "hit_details": _hit_trace_details(result, limit=hit_details_limit),
                    },
                    status_message="completed",
                )
                return result
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise

    async def _get_by_ids_impl(self, target_ids: list[str], options: RetrievalOptions) -> list[RetrievalHit]:
        unique_ids = [target_id for target_id in dict.fromkeys(target_ids) if target_id]
        if not unique_ids:
            return []
        hit_by_id: dict[str, RetrievalHit] = {}
        per_collection_hits: dict[str, int] = {}
        for collection_role in _roles_for_target_ids(unique_ids):
            with profile_span("semantic_hybrid.get_by_ids", collection_role=collection_role, count=len(unique_ids)):
                hits = self.store.get_documents(
                    collection_role=collection_role,
                    adapter_name=options.adapter_name,
                    target=options.target,
                    target_ids=unique_ids,
                )
            per_collection_hits[collection_role] = len(hits)
            for hit in hits:
                target_id = str(hit.metadata.get("target_id") or hit.chunk_id)
                hit_by_id.setdefault(target_id, _retrieval_hit_from_milvus_hit(_hit_with_collection_role(hit, collection_role), score=1.0))
        ordered = [hit_by_id[target_id] for target_id in unique_ids if target_id in hit_by_id]
        profile_event(
            "semantic_hybrid.get_by_ids.result",
            requested=len(unique_ids),
            hits=len(ordered),
            per_collection_hits=per_collection_hits,
        )
        return ordered

    async def rebuild_index(
        self,
        *,
        adapter_name: str,
        target: str,
        chunks: list[EvidenceChunk],
        nodes: list[CompiledNode] | None = None,
        edges: list[CompiledEdge] | None = None,
        kg_version: str = "",
        include_community: bool = False,
        graph_projections: tuple[GraphProjectionProfile, ...] | None = None,
    ) -> int:
        with langfuse_observation(
            name="milvus.rebuild_index",
            as_type="span",
            input={
                "adapter_name": adapter_name,
                "target": target,
                "chunks": len(chunks),
                "nodes": len(nodes or []),
                "edges": len(edges or []),
                "kg_version": kg_version,
            },
            metadata={"backend": self.backend_name},
        ):
            try:
                result = await self._rebuild_index_impl(
                    adapter_name=adapter_name,
                    target=target,
                    chunks=chunks,
                    nodes=nodes,
                    edges=edges,
                    kg_version=kg_version,
                    include_community=include_community,
                    graph_projections=graph_projections,
                )
                langfuse_update_span(output={"documents_written": result}, status_message="completed")
                return result
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise

    async def _rebuild_index_impl(
        self,
        *,
        adapter_name: str,
        target: str,
        chunks: list[EvidenceChunk],
        nodes: list[CompiledNode] | None = None,
        edges: list[CompiledEdge] | None = None,
        kg_version: str = "",
        include_community: bool = False,
        graph_projections: tuple[GraphProjectionProfile, ...] | None = None,
    ) -> int:
        with profile_span(
            "semantic_hybrid.rebuild_index.build_documents",
            chunks=len(chunks),
            nodes=len(nodes or []),
            edges=len(edges or []),
        ):
            documents_by_role = _semantic_index_documents_by_role(
                chunks=chunks,
                nodes=nodes or [],
                edges=edges or [],
                include_community=include_community,
                graph_projections=graph_projections,
            )
        documents = _flatten_documents(documents_by_role)
        if not documents:
            with profile_span("semantic_hybrid.rebuild_index.store_replace", documents=0):
                return self.store.replace_documents_by_role(
                    adapter_name=adapter_name,
                    target=target,
                    documents_by_role={},
                    vectors_by_role={},
                    embedding_model=settings.EMBEDDING_MODEL,
                    kg_version=kg_version,
                )
        _log_embedding_plan(
            action="rebuild_index",
            adapter_name=adapter_name,
            target=target,
            documents=len(documents),
            chunks=len(chunks),
            nodes=len(nodes or []),
            edges=len(edges or []),
            role_counts=_role_counts(documents_by_role),
        )
        with profile_span("semantic_hybrid.rebuild_index.embed_documents", documents=len(documents)):
            vectors_by_role = await _embed_documents_by_role(documents_by_role)
        with profile_span("semantic_hybrid.rebuild_index.store_replace", documents=len(documents)):
            return self.store.replace_documents_by_role(
                adapter_name=adapter_name,
                target=target,
                documents_by_role=documents_by_role,
                vectors_by_role=vectors_by_role,
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
        kg_version: str = "",
        include_community: bool = False,
        graph_projections: tuple[GraphProjectionProfile, ...] | None = None,
    ) -> int:
        with langfuse_observation(
            name="milvus.upsert_index",
            as_type="span",
            input={
                "adapter_name": adapter_name,
                "target": target,
                "chunks": len(chunks),
                "nodes": len(nodes or []),
                "edges": len(edges or []),
                "kg_version": kg_version,
            },
            metadata={"backend": self.backend_name},
        ):
            try:
                result = await self._upsert_index_impl(
                    adapter_name=adapter_name,
                    target=target,
                    chunks=chunks,
                    nodes=nodes,
                    edges=edges,
                    kg_version=kg_version,
                    include_community=include_community,
                    graph_projections=graph_projections,
                )
                langfuse_update_span(output={"documents_written": result}, status_message="completed")
                return result
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise

    async def _upsert_index_impl(
        self,
        *,
        adapter_name: str,
        target: str,
        chunks: list[EvidenceChunk],
        nodes: list[CompiledNode] | None = None,
        edges: list[CompiledEdge] | None = None,
        kg_version: str = "",
        include_community: bool = False,
        graph_projections: tuple[GraphProjectionProfile, ...] | None = None,
    ) -> int:
        with profile_span(
            "semantic_hybrid.upsert_index.build_documents",
            chunks=len(chunks),
            nodes=len(nodes or []),
            edges=len(edges or []),
        ):
            documents_by_role = _semantic_index_documents_by_role(
                chunks=chunks,
                nodes=nodes or [],
                edges=edges or [],
                include_community=include_community,
                graph_projections=graph_projections,
            )
        documents = _flatten_documents(documents_by_role)
        if not documents:
            return 0
        _log_embedding_plan(
            action="upsert_index",
            adapter_name=adapter_name,
            target=target,
            documents=len(documents),
            chunks=len(chunks),
            nodes=len(nodes or []),
            edges=len(edges or []),
            role_counts=_role_counts(documents_by_role),
        )
        with profile_span("semantic_hybrid.upsert_index.embed_documents", documents=len(documents)):
            vectors_by_role = await _embed_documents_by_role(documents_by_role)
        with profile_span("semantic_hybrid.upsert_index.store_upsert", documents=len(documents)):
            return self.store.upsert_documents_by_role(
                adapter_name=adapter_name,
                target=target,
                documents_by_role=documents_by_role,
                vectors_by_role=vectors_by_role,
                embedding_model=settings.EMBEDDING_MODEL,
                kg_version=kg_version,
            )

    async def upsert_semantic_documents(
        self,
        *,
        adapter_name: str,
        target: str,
        documents: list[SemanticVectorDocument],
        kg_version: str = "",
    ) -> int:
        with profile_span(
            "semantic_hybrid.upsert_semantic_documents.build",
            documents=len(documents),
        ):
            documents_by_role: dict[str, list[MilvusHybridDocument]] = {
                role: [] for role in SEMANTIC_COLLECTION_ROLES
            }
            for document in documents:
                documents_by_role.setdefault(document.collection_role, []).append(
                    _milvus_document_from_semantic(document)
                )
            documents_by_role = {role: docs for role, docs in documents_by_role.items() if docs}
        flat_documents = _flatten_documents(documents_by_role)
        if not flat_documents:
            return 0
        _log_embedding_plan(
            action="upsert_semantic_documents",
            adapter_name=adapter_name,
            target=target,
            documents=len(flat_documents),
            chunks=0,
            nodes=0,
            edges=0,
            role_counts=_role_counts(documents_by_role),
        )
        with profile_span(
            "semantic_hybrid.upsert_semantic_documents.embed",
            documents=len(flat_documents),
        ):
            vectors_by_role = await _embed_documents_by_role(documents_by_role)
        with profile_span(
            "semantic_hybrid.upsert_semantic_documents.store_upsert",
            documents=len(flat_documents),
        ):
            return self.store.upsert_documents_by_role(
                adapter_name=adapter_name,
                target=target,
                documents_by_role=documents_by_role,
                vectors_by_role=vectors_by_role,
                embedding_model=settings.EMBEDDING_MODEL,
                kg_version=kg_version,
            )

    async def delete_evidence(
        self,
        *,
        adapter_name: str,
        target: str,
        evidence_ids: list[str],
    ) -> int:
        unique_ids = [evidence_id for evidence_id in dict.fromkeys(evidence_ids) if evidence_id]
        if not unique_ids:
            return 0
        self.store.delete_evidence(adapter_name=adapter_name, target=target, evidence_ids=unique_ids)
        return len(unique_ids)

    async def delete_documents(
        self,
        *,
        adapter_name: str,
        target: str,
        chunk_ids: list[str],
    ) -> int:
        unique_ids = [chunk_id for chunk_id in dict.fromkeys(chunk_ids) if chunk_id]
        if not unique_ids:
            return 0
        self.store.delete_documents(adapter_name=adapter_name, target=target, chunk_ids=unique_ids)
        return len(unique_ids)

    async def delete_documents_by_role(
        self,
        *,
        collection_role: str,
        adapter_name: str,
        target: str,
        target_ids: list[str],
    ) -> int:
        unique_ids = [target_id for target_id in dict.fromkeys(target_ids) if target_id]
        if not unique_ids:
            return 0
        self.store.delete_documents_by_role(
            collection_role=collection_role,
            adapter_name=adapter_name,
            target=target,
            target_ids=unique_ids,
        )
        return len(unique_ids)

    async def list_target_ids_by_role(
        self,
        *,
        collection_role: str,
        adapter_name: str,
        target: str,
        source_type: str | None = None,
        limit: int = 1_000_000,
    ) -> list[str]:
        return self.store.list_target_ids(
            collection_role=collection_role,
            adapter_name=adapter_name,
            target=target,
            source_type=source_type,
            limit=limit,
        )

    async def delete_scope(
        self,
        *,
        adapter_name: str,
        target: str,
    ) -> dict[str, str]:
        self.store.delete_scope(adapter_name=adapter_name, target=target)
        return {
            "adapter_name": adapter_name,
            "target": target,
            "scope": "adapter_target",
        }


def _log_embedding_plan(
    *,
    action: str,
    adapter_name: str,
    target: str,
    documents: int,
    chunks: int,
    nodes: int,
    edges: int,
    role_counts: dict[str, int] | None = None,
) -> None:
    estimated_requests = (documents + settings.EMBEDDING_BATCH_SIZE - 1) // settings.EMBEDDING_BATCH_SIZE
    logger.info(
        "[semantic_hybrid] %s embedding plan: adapter=%s target=%s documents=%d "
        "estimated_post_embeddings_rounds=%d batch_size=%d dim=%d request_dimensions=%s "
        "inputs={chunks:%d,nodes:%d,edges:%d} roles=%s",
        action,
        adapter_name,
        target,
        documents,
        estimated_requests,
        settings.EMBEDDING_BATCH_SIZE,
        settings.EMBEDDING_DIM,
        settings.EMBEDDING_REQUEST_DIMENSIONS,
        chunks,
        nodes,
        edges,
        role_counts or {},
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
    return query.strip()


def _candidate_limit(limit: int) -> int:
    return max(limit, min(limit * 3, limit + 30))


def _typed_search_limits(limit: int) -> dict[str, int]:
    return {
        SEMANTIC_COLLECTION_CHUNK: _configured_role_limit(settings.MILVUS_SEMANTIC_CHUNK_TOPK, fallback=limit, limit=limit),
        SEMANTIC_COLLECTION_COGNITIVE_CARD: _configured_role_limit(settings.MILVUS_SEMANTIC_COGNITIVE_CARD_TOPK, fallback=12, limit=limit),
        SEMANTIC_COLLECTION_CARD_RELATION: _configured_role_limit(settings.MILVUS_SEMANTIC_RELATION_TOPK, fallback=12, limit=limit),
        SEMANTIC_COLLECTION_COMMUNITY: _configured_role_limit(settings.MILVUS_SEMANTIC_COMMUNITY_TOPK, fallback=8, limit=limit),
    }


def _roles_for_target_ids(target_ids: list[str]) -> tuple[str, ...]:
    roles: set[str] = set()
    for target_id in target_ids:
        if target_id.startswith(("kg_chunk:", "chunk:")):
            roles.add(SEMANTIC_COLLECTION_CHUNK)
        elif target_id.startswith(("kg_cognitive_card:", "kg_card:cognitive:")):
            roles.add(SEMANTIC_COLLECTION_COGNITIVE_CARD)
        elif target_id.startswith(("kg_card_relation:", "kg_card:edge:")):
            roles.add(SEMANTIC_COLLECTION_CARD_RELATION)
        elif target_id.startswith(("kgc:", "kg_finding:", "kg_community:")):
            roles.add(SEMANTIC_COLLECTION_COMMUNITY)
        elif not roles:
            return AGENT_READ_COLLECTION_ROLES
    return tuple(role for role in AGENT_READ_COLLECTION_ROLES if role in roles)


def _configured_role_limit(value: int, *, fallback: int, limit: int) -> int:
    if value > 0:
        return value
    if fallback == limit:
        return limit
    return min(fallback, max(1, limit))


def _hit_with_collection_role(hit: MilvusHybridHit, collection_role: str) -> MilvusHybridHit:
    metadata = dict(hit.metadata)
    metadata["collection_role"] = collection_role
    return MilvusHybridHit(
        chunk_id=hit.chunk_id,
        evidence_id=hit.evidence_id,
        text=hit.text,
        score=hit.score,
        metadata=metadata,
    )


def _retrieval_hit_from_milvus_hit(hit: MilvusHybridHit, *, score: float) -> RetrievalHit:
    source_type = str(hit.metadata.get("source_type") or "")
    source_id = str(hit.metadata.get("source_id") or "")
    evidence_refs = [hit.evidence_id] if hit.evidence_id else []
    cited_evidence_ids = [str(item) for item in (hit.metadata.get("cited_evidence_ids") or []) if item]
    if cited_evidence_ids:
        evidence_refs = _ordered_unique([*evidence_refs, *cited_evidence_ids])
    cited_chunk_ids = [str(item) for item in (hit.metadata.get("cited_chunk_ids") or []) if item]
    if source_type in {"kg_node", "kg_node_card", "kg_event_card"} and source_id:
        return RetrievalHit(
            hit_id=hit.chunk_id,
            hit_type="node",
            title=_semantic_hit_title(hit, fallback=f"node:{source_id}"),
            snippet=hit.text[:800],
            score=score,
            source="semantic_hybrid",
            node_refs=[source_id],
            evidence_refs=evidence_refs,
        )
    if source_type in {"kg_edge", "kg_edge_card", "kg_card_relation"} and source_id:
        return RetrievalHit(
            hit_id=hit.chunk_id,
            hit_type="edge",
            title=_semantic_hit_title(hit, fallback=f"edge:{source_id}"),
            snippet=hit.text[:800],
            score=score,
            source="semantic_hybrid",
            edge_refs=[source_id],
            chunk_refs=cited_chunk_ids,
            evidence_refs=evidence_refs,
        )
    if source_type == "kg_cognitive_card" and source_id:
        return RetrievalHit(
            hit_id=hit.chunk_id,
            hit_type="cognitive_card",
            title=_semantic_hit_title(hit, fallback=f"cognitive_card:{source_id}"),
            snippet=hit.text[:800],
            score=score,
            source="semantic_hybrid",
            source_channels=["semantic_hybrid"],
            chunk_refs=[str(item) for item in (hit.metadata.get("cited_chunk_ids") or []) if item],
            evidence_refs=evidence_refs,
            matched_fields=["milvus.cognitive_card"],
        )
    if source_type in {"kg_community_report", "kg_community_insight", "kg_finding"} and source_id:
        return RetrievalHit(
            hit_id=hit.chunk_id,
            hit_type="semantic_hybrid",
            title=_semantic_hit_title(hit, fallback=f"community:{source_id}"),
            snippet=hit.text[:800],
            score=score,
            source="semantic_hybrid",
            source_channels=["semantic_hybrid"],
            node_refs=[str(item) for item in (hit.metadata.get("node_ids") or []) if item],
            edge_refs=[str(item) for item in (hit.metadata.get("edge_ids") or []) if item],
            chunk_refs=cited_chunk_ids,
            evidence_refs=evidence_refs,
            matched_fields=["milvus.community"],
        )
    if source_type == "kg_evidence_chunk" and source_id:
        return RetrievalHit(
            hit_id=hit.chunk_id,
            hit_type="evidence",
            title=_semantic_hit_title(hit, fallback=f"evidence:{source_id}"),
            snippet=hit.text[:800],
            score=score,
            source="semantic_hybrid",
            chunk_refs=[hit.chunk_id],
            evidence_refs=[source_id],
        )
    return RetrievalHit(
        hit_id=hit.chunk_id,
        hit_type="semantic_hybrid",
        title=_semantic_hit_title(hit, fallback=f"evidence_chunk:{hit.evidence_id}"),
        snippet=hit.text[:800],
        score=score,
        source="semantic_hybrid",
        chunk_refs=cited_chunk_ids,
        evidence_refs=evidence_refs,
    )


def _semantic_hit_title(hit: MilvusHybridHit, *, fallback: str) -> str:
    metadata = hit.metadata or {}
    for key in ("title", "canonical_name", "community_title", "finding_title"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    source_name = metadata.get("source_name")
    target_name = metadata.get("target_name")
    relation_type = metadata.get("relation_type")
    if source_name and target_name and relation_type:
        return f"{source_name} --{relation_type}--> {target_name}"

    for prefix in (
        "Edge Key:",
        "Node Key:",
        "Event Key:",
        "Evidence:",
        "Source:",
        "Community:",
        "Finding:",
    ):
        title = _line_value_after_prefix(hit.text, prefix)
        if title:
            return title
    return fallback


def _hit_trace_details(hits: list[RetrievalHit], *, limit: int) -> list[dict[str, object]]:
    return [
        {
            "id": hit.hit_id,
            "type": hit.hit_type,
            "title": hit.title,
            "score": hit.score,
            "node_refs": hit.node_refs[:5],
            "edge_refs": hit.edge_refs[:5],
            "evidence_refs": hit.evidence_refs[:5],
        }
        for hit in hits[:limit]
    ]


def _hit_type_counts(hits: list[RetrievalHit]) -> dict[str, int]:
    result: dict[str, int] = {}
    for hit in hits:
        result[hit.hit_type] = result.get(hit.hit_type, 0) + 1
    return result


def _line_value_after_prefix(text: str, prefix: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return ""


def _semantic_index_documents_by_role(
    *,
    chunks: list[EvidenceChunk],
    nodes: list[CompiledNode],
    edges: list[CompiledEdge],
    include_community: bool,
    graph_projections: tuple[GraphProjectionProfile, ...] | None = None,
) -> dict[str, list[MilvusHybridDocument]]:
    semantic_documents = build_semantic_vector_documents(
        chunks=chunks,
        nodes=nodes,
        edges=edges,
        include_community=include_community,
        graph_projections=graph_projections,
    )
    grouped: dict[str, list[MilvusHybridDocument]] = {role: [] for role in SEMANTIC_COLLECTION_ROLES}
    for document in semantic_documents:
        grouped[document.collection_role].append(_milvus_document_from_semantic(document))
    return grouped


def _milvus_document_from_semantic(document: SemanticVectorDocument) -> MilvusHybridDocument:
    return MilvusHybridDocument(
        chunk_id=document.document_id,
        evidence_id=document.evidence_id,
        text=document.text,
        metadata={
            **document.metadata,
            "collection_role": document.collection_role,
            "source_type": document.source_type,
            "source_id": document.source_id,
            "document_type": document.document_type,
            "target_type": document.document_type,
        },
    )


async def _embed_documents_by_role(
    documents_by_role: dict[str, list[MilvusHybridDocument]]
) -> dict[str, list[list[float]]]:
    vectors_by_role: dict[str, list[list[float]]] = {}
    for role in SEMANTIC_COLLECTION_ROLES:
        documents = documents_by_role.get(role, [])
        if not documents:
            vectors_by_role[role] = []
            continue
        vectors_by_role[role] = await embed_texts([document.text for document in documents])
    return vectors_by_role


def _flatten_documents(documents_by_role: dict[str, list[MilvusHybridDocument]]) -> list[MilvusHybridDocument]:
    return [document for role in SEMANTIC_COLLECTION_ROLES for document in documents_by_role.get(role, [])]


def _role_counts(documents_by_role: dict[str, list[MilvusHybridDocument]]) -> dict[str, int]:
    return {role: len(documents_by_role.get(role, [])) for role in SEMANTIC_COLLECTION_ROLES}


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
    return _ordered_unique(term for term in terms if term not in _GENERIC_CJK_TERMS)


def _normalize_stock_code(value: str) -> str:
    text = value.strip().lower()
    match = re.fullmatch(r"(?:sh|sz|bj)?(\d{6})(?:\.(?:sh|sz|bj))?", text)
    return match.group(1) if match else ""


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
