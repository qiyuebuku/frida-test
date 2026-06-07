"""High-level tool registry for agentic KG retrieval."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from src.domain.knowledge.retrieval import (
    HybridRetrievalRuntime,
    RetrievalHit,
    RetrievalOptions,
    RetrievalStep,
    dedupe_hits,
    parse_retrieval_query,
)
from src.domain.knowledge.retrieval_profile import profile_event, profile_span
from src.domain.knowledge.retrieval_rerank import (
    RerankScoredIndex,
    apply_rerank_scores,
    prepare_rerank_candidates,
    rerank_index_payload,
)
from src.domain.knowledge.schemas import KnowledgeBaseModel

RetrievalToolName = Literal[
    "search",
    "scoped_search",
    "find",
    "open",
    "expand",
    "summarize",
    "rerank",
]


class RetrievalToolCall(KnowledgeBaseModel):
    tool: RetrievalToolName
    query: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    seed_node_ids: list[str] = Field(default_factory=list)
    seed_edge_ids: list[str] = Field(default_factory=list)
    seed_chunk_ids: list[str] = Field(default_factory=list)
    seed_finding_ids: list[str] = Field(default_factory=list)
    relation_filters: list[str] = Field(default_factory=list)
    include_neighbors: Literal["none", "one_hop", "window"] = "none"
    hop_limit: int | None = Field(default=None, ge=1, le=3)
    candidate_pool: list[RetrievalHit] = Field(default_factory=list, exclude=True)
    limit: int | None = None

    @model_validator(mode="after")
    def _has_required_input(self) -> "RetrievalToolCall":
        if self.tool == "search" and not _has_text(self.query):
            raise ValueError("search requires query")
        if self.tool == "scoped_search" and (
            not _has_text(self.query) or (not self.evidence_ids and not self.candidate_ids)
        ):
            raise ValueError("scoped_search requires query and evidence_ids or candidate_ids")
        if self.tool == "rerank" and (not _has_text(self.query) or not self.candidate_pool):
            raise ValueError("rerank requires query")
        if self.tool == "find" and (not _has_text(self.query) or (not self.evidence_ids and not self.chunk_ids)):
            raise ValueError("find requires query and evidence_ids or chunk_ids")
        if self.tool == "open" and not self.evidence_ids and not self.chunk_ids and not self.candidate_ids:
            raise ValueError("open requires evidence_ids, chunk_ids, or candidate_ids")
        if self.tool == "expand" and not _has_expand_seed(self):
            raise ValueError(
                "expand requires candidate_ids, seed_node_ids, seed_edge_ids, seed_chunk_ids, "
                "seed_finding_ids, or evidence_ids"
            )
        return self


class RetrievalToolResult(KnowledgeBaseModel):
    tool: RetrievalToolName
    hits: list[RetrievalHit] = Field(default_factory=list)
    step: RetrievalStep
    summary: str | None = None


class RetrievalToolRegistry:
    """Whitelisted high-level tools exposed to the retrieval controller."""

    available_tools: tuple[RetrievalToolName, ...] = (
        "search",
        "scoped_search",
        "find",
        "open",
        "expand",
        "summarize",
        "rerank",
    )

    def __init__(
        self,
        runtime: HybridRetrievalRuntime,
        options: RetrievalOptions,
        *,
        reranker_client=None,
        reranker_max_documents: int = 100,
        reranker_default_top_n: int = 0,
    ):
        self.runtime = runtime
        self.options = options
        self.reranker_client = reranker_client
        self.reranker_max_documents = reranker_max_documents
        self.reranker_default_top_n = reranker_default_top_n
        self._last_search_diagnostics: dict[str, object] = {}
        self._last_rerank_diagnostics: dict[str, object] = {}

    async def execute(self, call: RetrievalToolCall) -> RetrievalToolResult:
        with profile_span(
            "retrieval_tool.execute",
            tool=call.tool,
            query=call.query,
            evidence_count=len(call.evidence_ids),
            candidate_count=len(call.candidate_ids),
            limit=call.limit,
        ):
            if call.tool == "search":
                hits = await self._search(call.query or "", call.limit)
                summary = None
            elif call.tool == "scoped_search":
                hits = await self._scoped_search(
                    call.query or "",
                    call.evidence_ids,
                    call.candidate_ids,
                    call.limit,
                )
                summary = None
            elif call.tool == "find":
                hits = await self._find(call.query or "", call.evidence_ids, call.chunk_ids, call.limit)
                summary = None
            elif call.tool == "open":
                hits = await self._open(
                    call.evidence_ids,
                    call.chunk_ids,
                    call.candidate_ids,
                    include_neighbors=call.include_neighbors,
                    limit=call.limit,
                )
                summary = None
            elif call.tool == "expand":
                hits = await self._expand(
                    candidate_ids=call.candidate_ids,
                    seed_node_ids=call.seed_node_ids,
                    seed_edge_ids=call.seed_edge_ids,
                    seed_chunk_ids=call.seed_chunk_ids,
                    seed_finding_ids=call.seed_finding_ids,
                    evidence_ids=call.evidence_ids,
                    relation_filters=call.relation_filters,
                    hop_limit=call.hop_limit,
                    limit=call.limit,
                )
                summary = None
            elif call.tool == "summarize":
                hits = []
                summary = await self._summarize(call)
            elif call.tool == "rerank":
                hits = await self._rerank(call.query or "", call.candidate_pool, call.limit)
                summary = None
            else:  # pragma: no cover - Literal and pydantic validation make this unreachable.
                raise ValueError(f"unsupported retrieval tool: {call.tool}")
        profile_event("retrieval_tool.result", tool=call.tool, hits=len(hits))
        step_input = call.model_dump(mode="json")
        if call.tool == "search" and self._last_search_diagnostics:
            step_input["search_diagnostics"] = self._last_search_diagnostics
        if call.tool == "scoped_search" and self._last_search_diagnostics:
            step_input["search_diagnostics"] = self._last_search_diagnostics
        if call.tool == "rerank" and self._last_rerank_diagnostics:
            step_input["input_hit_count"] = len(call.candidate_pool)
            step_input["rerank_diagnostics"] = self._last_rerank_diagnostics
        return RetrievalToolResult(
            tool=call.tool,
            hits=hits,
            summary=summary,
            step=RetrievalStep(
                tool=call.tool,
                input=step_input,
                output_refs=[hit.hit_id for hit in hits],
                hit_count=len(hits),
                warning=summary if call.tool == "summarize" else None,
            ),
        )

    async def _rerank(
        self,
        query: str,
        candidate_pool: list[RetrievalHit],
        limit: int | None,
    ) -> list[RetrievalHit]:
        if self.reranker_client is None:
            raise RuntimeError("rerank requires a configured reranker_client")
        with profile_span(
            "retrieval_tool.rerank.prepare",
            query=query,
            candidates=len(candidate_pool),
            max_documents=self.reranker_max_documents,
        ):
            preparation = prepare_rerank_candidates(
                query,
                candidate_pool,
                max_documents=self.reranker_max_documents,
            )
        if not preparation.candidates:
            raise RuntimeError(
                "reranker hygiene produced no candidates; "
                f"input_hit_count={len(candidate_pool)} diagnostics={preparation.diagnostics}"
            )
        top_n = limit if limit and limit > 0 else self.reranker_default_top_n
        top_n = min(top_n, len(preparation.candidates)) if top_n and top_n > 0 else None
        with profile_span(
            "retrieval_tool.rerank.external",
            query=query,
            documents=len(preparation.candidates),
            top_n=top_n or "all",
        ):
            response = await self.reranker_client.rerank(
                query=query,
                documents=[candidate.document for candidate in preparation.candidates],
                top_n=top_n,
            )
        scored_indexes = [
            RerankScoredIndex(index=item.index, relevance_score=item.relevance_score)
            for item in response.results
        ]
        reranked_hits = apply_rerank_scores(preparation.candidates, scored_indexes)
        self._last_search_diagnostics = {}
        self._last_rerank_diagnostics = {
            **preparation.diagnostics,
            "top_n": top_n or "all",
            "model": response.model,
            "service_latency_ms": round(response.latency_ms, 1),
            "total_documents": response.total_documents,
            "ranked_count": len(reranked_hits),
            "ranking": rerank_index_payload(preparation.candidates, scored_indexes[:12]),
        }
        profile_event(
            "retrieval_tool.rerank.counts",
            input_hits=len(candidate_pool),
            prepared=len(preparation.candidates),
            ranked=len(reranked_hits),
        )
        return reranked_hits

    async def _search(self, query: str, limit: int | None) -> list[RetrievalHit]:
        max_hits = limit or self.options.max_hits
        deterministic_limit = max(self.options.keyword_limit, max_hits)
        semantic_limit = max(self.options.semantic_hybrid_limit, max_hits)
        parsed_query = parse_retrieval_query(query)
        deterministic_hits: list[RetrievalHit] = []
        if parsed_query.has_strong_identifiers:
            with profile_span(
                "retrieval_tool.search.pg_deterministic",
                query=query,
                limit=deterministic_limit,
            ):
                deterministic_hits = self.runtime.pg_deterministic_search(
                    query,
                    self.options,
                    limit=deterministic_limit,
                )
        semantic_hits: list[RetrievalHit] = []
        if self.options.semantic_hybrid_limit > 0 and getattr(
            self.runtime.semantic_retriever,
            "enabled",
            False,
        ):
            with profile_span(
                "retrieval_tool.search.vector_semantic",
                query=parsed_query.vector_query,
                limit=semantic_limit,
            ):
                semantic_hits = await self.runtime.semantic_hybrid_search(
                    parsed_query.vector_query,
                    self.options,
                    limit=semantic_limit,
                )
        raw_hits = [*deterministic_hits, *semantic_hits]
        chunk_hits = await self._project_hits_to_evidence_chunks(raw_hits, limit=max(deterministic_limit, semantic_limit))
        merged_hits = dedupe_hits(chunk_hits)
        selected_hits = merged_hits[:max_hits]
        self._last_search_diagnostics = {
            "query_parser": parsed_query.model_dump(mode="json"),
            "pre_dedupe_counts": {
                "pg_deterministic": len(deterministic_hits),
                "vector_semantic": len(semantic_hits),
                "raw": len(raw_hits),
                "evidence_chunks": len(chunk_hits),
            },
            "post_dedupe_channel_counts": _source_counts(merged_hits),
            "post_dedupe_primary_source_counts": _source_counts(merged_hits),
            "selected_channel_counts": _source_counts(selected_hits),
            "selected_primary_source_counts": _source_counts(selected_hits),
            "merged_channel_counts": _merged_channel_counts(merged_hits),
            "selected_merged_channel_counts": _merged_channel_counts(selected_hits),
            "source_samples": _source_samples(raw_hits),
            "vector_semantic": {
                "enabled": bool(
                    self.options.semantic_hybrid_limit > 0
                    and getattr(self.runtime.semantic_retriever, "enabled", False)
                ),
                "configured_limit": self.options.semantic_hybrid_limit,
                "requested_limit": semantic_limit if self.options.semantic_hybrid_limit > 0 else 0,
                "returned": len(semantic_hits),
                "disabled_reason": _semantic_disabled_reason(self.runtime, self.options),
                "backend_diagnostics": getattr(
                    self.runtime.semantic_retriever,
                    "last_search_diagnostics",
                    {},
                ),
            },
            "max_hits": max_hits,
        }
        profile_event(
            "retrieval_tool.search.counts",
            pg_deterministic=len(deterministic_hits),
            vector_semantic=len(semantic_hits),
            evidence_chunks=len(chunk_hits),
            max_hits=max_hits,
        )
        return selected_hits

    async def _scoped_search(
        self,
        query: str,
        evidence_ids: list[str],
        candidate_ids: list[str],
        limit: int | None,
    ) -> list[RetrievalHit]:
        max_hits = limit or self.options.max_hits
        deterministic_limit = max(self.options.keyword_limit, max_hits * 2)
        semantic_limit = max(self.options.semantic_hybrid_limit, max_hits * 2)
        parsed_query = parse_retrieval_query(query)
        scope = _scope_from_candidates(
            self.runtime.repository,
            self.options.adapter_name,
            evidence_ids=evidence_ids,
            candidate_ids=candidate_ids,
        )
        if not scope["node_ids"] and not scope["edge_ids"] and not scope["evidence_ids"]:
            self._last_search_diagnostics = {
                "query_parser": parsed_query.model_dump(mode="json"),
                "scope": scope,
                "pre_dedupe_counts": {"pg_deterministic": 0, "vector_semantic": 0, "graph_scope": 0},
                "max_hits": max_hits,
            }
            return []

        with profile_span(
            "retrieval_tool.scoped_search.pg_deterministic",
            query=query,
            limit=deterministic_limit,
        ):
            deterministic_hits = self.runtime.pg_deterministic_search(
                query,
                self.options,
                limit=deterministic_limit,
            )
        graph_hits: list[RetrievalHit] = []
        if scope["node_ids"]:
            with profile_span(
                "retrieval_tool.scoped_search.graph_scope",
                seed_nodes=len(scope["node_ids"]),
            ):
                graph_hits = self.runtime.graph_search(
                    scope["node_ids"],
                    self.options,
                    limit=max(self.options.graph_limit, max_hits),
                )
        semantic_hits: list[RetrievalHit] = []
        if self.options.semantic_hybrid_limit > 0 and getattr(
            self.runtime.semantic_retriever,
            "enabled",
            False,
        ):
            with profile_span(
                "retrieval_tool.scoped_search.vector_semantic",
                query=parsed_query.vector_query,
                limit=semantic_limit,
            ):
                semantic_hits = await self.runtime.semantic_hybrid_search(
                    parsed_query.vector_query,
                    self.options,
                    limit=semantic_limit,
                )
        raw_hits = [
            *_filter_hits_to_scope(deterministic_hits, scope),
            *graph_hits,
            *_filter_hits_to_scope(semantic_hits, scope),
        ]
        chunk_hits = await self._project_hits_to_evidence_chunks(raw_hits, limit=max(deterministic_limit, semantic_limit))
        merged_hits = dedupe_hits(chunk_hits)
        selected_hits = merged_hits[:max_hits]
        self._last_search_diagnostics = {
            "query_parser": parsed_query.model_dump(mode="json"),
            "scope": scope,
            "pre_dedupe_counts": {
                "pg_deterministic": len(deterministic_hits),
                "vector_semantic": len(semantic_hits),
                "graph_scope": len(graph_hits),
                "scoped_raw": len(raw_hits),
                "evidence_chunks": len(chunk_hits),
            },
            "post_dedupe_channel_counts": _source_counts(merged_hits),
            "post_dedupe_primary_source_counts": _source_counts(merged_hits),
            "selected_channel_counts": _source_counts(selected_hits),
            "selected_primary_source_counts": _source_counts(selected_hits),
            "merged_channel_counts": _merged_channel_counts(merged_hits),
            "selected_merged_channel_counts": _merged_channel_counts(selected_hits),
            "source_samples": _source_samples(raw_hits),
            "vector_semantic": {
                "enabled": bool(
                    self.options.semantic_hybrid_limit > 0
                    and getattr(self.runtime.semantic_retriever, "enabled", False)
                ),
                "configured_limit": self.options.semantic_hybrid_limit,
                "requested_limit": semantic_limit if self.options.semantic_hybrid_limit > 0 else 0,
                "returned": len(semantic_hits),
                "disabled_reason": _semantic_disabled_reason(self.runtime, self.options),
                "backend_diagnostics": getattr(
                    self.runtime.semantic_retriever,
                    "last_search_diagnostics",
                    {},
                ),
            },
            "max_hits": max_hits,
        }
        profile_event(
            "retrieval_tool.scoped_search.counts",
            pg_deterministic=len(deterministic_hits),
            vector_semantic=len(semantic_hits),
            graph_scope=len(graph_hits),
            scoped_raw=len(raw_hits),
            evidence_chunks=len(chunk_hits),
            max_hits=max_hits,
        )
        return selected_hits

    async def _find(
        self,
        query: str,
        evidence_ids: list[str],
        chunk_ids: list[str],
        limit: int | None,
    ) -> list[RetrievalHit]:
        terms = _terms(query)
        if not terms:
            return []
        hits: list[RetrievalHit] = []
        candidates = await self._open(
            evidence_ids,
            chunk_ids,
            [],
            include_neighbors="none",
            limit=limit,
        )
        for hit in candidates:
            haystack = f"{hit.title}\n{hit.snippet}".lower()
            matched = sum(1 for term in terms if term in haystack)
            if matched:
                hits.append(hit.model_copy(update={"score": max(hit.score, float(matched))}))
        return sorted(hits, key=lambda item: (-item.score, item.hit_id))[: limit or self.options.evidence_limit]

    async def _open(
        self,
        evidence_ids: list[str],
        chunk_ids: list[str],
        candidate_ids: list[str],
        *,
        include_neighbors: Literal["none", "one_hop", "window"],
        limit: int | None,
    ) -> list[RetrievalHit]:
        expanded_chunk_ids = _expand_chunk_ids_from_manifest(
            self.runtime.repository,
            self.options.adapter_name,
            chunk_ids,
            include_neighbors=include_neighbors,
            limit=limit or self.options.evidence_limit,
        )
        chunk_hits = await self._chunk_hits_by_ids(expanded_chunk_ids, limit=limit or self.options.evidence_limit)
        ids = _ordered_unique([*evidence_ids, *self._candidate_evidence_ids(candidate_ids, limit=limit)])
        evidence_hits = self.runtime.chunk_read(ids, self.options, limit=limit) if ids else []
        return dedupe_hits([*chunk_hits, *evidence_hits])[: limit or self.options.evidence_limit]

    def _candidate_evidence_ids(self, candidate_ids: list[str], *, limit: int | None) -> list[str]:
        if not candidate_ids:
            return []
        max_ids = limit or self.options.evidence_limit
        evidence_ids: list[str] = []
        repository = self.runtime.repository
        adapter_name = self.options.adapter_name
        for candidate_id in candidate_ids:
            if candidate_id.startswith("kg_ev:"):
                evidence_ids.append(candidate_id)
            elif candidate_id.startswith("kg_edge:"):
                edge = repository.get_edge(candidate_id)
                if edge is not None:
                    evidence_ids.extend(edge.evidence_ids)
            else:
                for edge in repository.list_edges(adapter_name):
                    if edge.source_node_id == candidate_id or edge.target_node_id == candidate_id:
                        evidence_ids.extend(edge.evidence_ids)
            evidence_ids = _ordered_unique(evidence_ids)
            if len(evidence_ids) >= max_ids:
                break
        return evidence_ids[:max_ids]

    async def _expand(
        self,
        *,
        candidate_ids: list[str],
        seed_node_ids: list[str],
        seed_edge_ids: list[str],
        seed_chunk_ids: list[str],
        seed_finding_ids: list[str] | None = None,
        evidence_ids: list[str],
        relation_filters: list[str],
        hop_limit: int | None,
        limit: int | None,
    ) -> list[RetrievalHit]:
        seed_nodes = _candidate_seed_nodes(
            self.runtime.repository,
            self.options.adapter_name,
            candidate_ids=candidate_ids,
        )
        seed_nodes.extend(seed_node_ids)
        if seed_edge_ids:
            seed_nodes.extend(
                _candidate_seed_nodes(
                    self.runtime.repository,
                    self.options.adapter_name,
                    candidate_ids=seed_edge_ids,
                )
            )
        finding_evidence_ids: list[str] = []
        if seed_finding_ids:
            finding_hits = await self._semantic_hits_by_ids(seed_finding_ids)
            for hit in finding_hits:
                seed_nodes.extend(hit.node_refs)
                seed_nodes.extend(
                    _candidate_seed_nodes(
                        self.runtime.repository,
                        self.options.adapter_name,
                        candidate_ids=hit.edge_refs,
                    )
                )
                finding_evidence_ids.extend(hit.evidence_refs)
        seed_evidence_ids = _ordered_unique(
            [
                *evidence_ids,
                *finding_evidence_ids,
                *_candidate_evidence_ids_from_chunk_ids(
                    self.runtime.repository,
                    self.options.adapter_name,
                    seed_chunk_ids,
                ),
            ]
        )
        if seed_evidence_ids:
            seed_nodes.extend(
                _seed_nodes_from_evidence_refs(
                    self.runtime.repository,
                    self.options.adapter_name,
                    evidence_refs=seed_evidence_ids,
                )
            )
        seed_nodes = _ordered_unique(seed_nodes)
        if not seed_nodes:
            return []
        hits = self.runtime.graph_search(
            seed_nodes,
            self.options,
            depth=hop_limit,
            limit=limit or self.options.graph_limit,
            relation_filters=relation_filters,
        )
        return dedupe_hits(await self._project_hits_to_evidence_chunks(hits, limit=limit or self.options.graph_limit))

    async def _summarize(self, call: RetrievalToolCall) -> str:
        if call.candidate_pool:
            hits = dedupe_hits(call.candidate_pool)[: call.limit or 8]
            return _summarize_hits(hits, title="summary_candidates")
        opened_hits: list[RetrievalHit] = []
        if call.chunk_ids or call.evidence_ids or call.candidate_ids:
            opened_hits = await self._open(
                call.evidence_ids,
                call.chunk_ids,
                call.candidate_ids,
                include_neighbors=call.include_neighbors,
                limit=call.limit,
            )
        if opened_hits:
            return _summarize_hits(opened_hits[: call.limit or 8], title="summary_opened_evidence")
        refs = _ordered_unique([*call.chunk_ids, *call.evidence_ids, *call.candidate_ids, *call.seed_finding_ids])
        if refs:
            return "summary_refs: " + ", ".join(refs[:12])
        return "summary_empty: no candidate_pool or refs supplied"

    async def _project_hits_to_evidence_chunks(
        self,
        hits: list[RetrievalHit],
        *,
        limit: int | None,
    ) -> list[RetrievalHit]:
        """Convert navigation hits into readable evidence chunks.

        Node/edge/entity/relation hits are useful retrieval handles, but they
        are not the evidence surface consumed by rerank or the Agent. This
        projection keeps the original channel metadata while replacing the
        handle with chunk-backed evidence hits.
        """

        projected: list[RetrievalHit] = []
        max_hits = limit or self.options.evidence_limit
        for hit in hits:
            chunk_hits = await self._chunk_hits_for_navigation_hit(hit, limit=max_hits)
            if not chunk_hits:
                continue
            projected.extend(_inherit_navigation_metadata(chunk_hit, hit) for chunk_hit in chunk_hits)
            if len(projected) >= max_hits:
                break
        return projected[:max_hits]

    async def _chunk_hits_for_navigation_hit(self, hit: RetrievalHit, *, limit: int) -> list[RetrievalHit]:
        if hit.hit_type == "evidence" and hit.hit_id.startswith("kg_chunk:"):
            return [hit]
        if hit.chunk_refs:
            chunk_hits = await self._chunk_hits_by_ids(hit.chunk_refs, limit=limit)
            if chunk_hits:
                return chunk_hits
        evidence_ids = list(hit.evidence_refs)
        if hit.hit_type == "evidence" and hit.hit_id.startswith("kg_ev:"):
            evidence_ids.append(hit.hit_id)
        evidence_ids = _ordered_unique(evidence_ids)
        if not evidence_ids:
            return []
        repository_hits = self.runtime.chunk_read(evidence_ids, self.options, limit=limit)
        target_ids = [chunk_hit.hit_id for chunk_hit in repository_hits if chunk_hit.hit_id.startswith("kg_chunk:")]
        milvus_hits: list[RetrievalHit] = []
        get_by_ids = getattr(self.runtime.semantic_retriever, "get_by_ids", None)
        if target_ids and callable(get_by_ids) and getattr(self.runtime.semantic_retriever, "enabled", False):
            milvus_hits = await get_by_ids(target_ids, self.options)
        return milvus_hits or repository_hits

    async def _chunk_hits_by_ids(self, chunk_ids: list[str], *, limit: int) -> list[RetrievalHit]:
        ids = _ordered_unique(chunk_ids)[:limit]
        if not ids:
            return []
        get_by_ids = getattr(self.runtime.semantic_retriever, "get_by_ids", None)
        if callable(get_by_ids) and getattr(self.runtime.semantic_retriever, "enabled", False):
            hits = await get_by_ids(ids, self.options)
            if hits:
                return hits[:limit]
        return _chunk_hits_from_repository_by_ids(
            self.runtime.repository,
            self.options.adapter_name,
            ids,
            limit=limit,
        )

    async def _semantic_hits_by_ids(self, target_ids: list[str]) -> list[RetrievalHit]:
        ids = _ordered_unique(target_ids)
        if not ids:
            return []
        get_by_ids = getattr(self.runtime.semantic_retriever, "get_by_ids", None)
        if callable(get_by_ids) and getattr(self.runtime.semantic_retriever, "enabled", False):
            return await get_by_ids(ids, self.options)
        return []


def _has_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_expand_seed(call: RetrievalToolCall) -> bool:
    return bool(
        call.candidate_ids
        or call.seed_node_ids
        or call.seed_edge_ids
        or call.seed_chunk_ids
        or call.seed_finding_ids
        or call.evidence_ids
    )


def _terms(query: str) -> list[str]:
    return _ordered_unique(term.lower() for term in query.split() if len(term.strip()) >= 2)


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _seed_nodes_from_evidence_refs(repository, adapter_name: str, *, evidence_refs: list[str]) -> list[str]:
    evidence_ref_set = set(evidence_refs)
    if not evidence_ref_set:
        return []
    node_ids: list[str] = []
    for edge in repository.list_edges(adapter_name):
        if evidence_ref_set.intersection(edge.evidence_ids):
            node_ids.extend([edge.source_node_id, edge.target_node_id])
    return _ordered_unique(node_ids)


def _candidate_seed_nodes(repository, adapter_name: str, *, candidate_ids: list[str]) -> list[str]:
    seed_nodes: list[str] = []
    evidence_refs: list[str] = []
    for candidate_id in candidate_ids:
        if candidate_id.startswith("kg_edge:"):
            edge = repository.get_edge(candidate_id)
            if edge is not None:
                seed_nodes.extend([edge.source_node_id, edge.target_node_id])
                evidence_refs.extend(edge.evidence_ids)
            continue
        if candidate_id.startswith("kg_ev:"):
            evidence_refs.append(candidate_id)
            continue
        if candidate_id.startswith("kg:"):
            seed_nodes.append(candidate_id)
    if evidence_refs:
        seed_nodes.extend(
            _seed_nodes_from_evidence_refs(
                repository,
                adapter_name,
                evidence_refs=evidence_refs,
            )
        )
    return _ordered_unique(seed_nodes)


def _candidate_evidence_ids_from_chunk_ids(repository, adapter_name: str, chunk_ids: list[str]) -> list[str]:
    wanted = set(chunk_ids)
    if not wanted:
        return []
    return _ordered_unique(
        chunk.evidence_id
        for chunk in getattr(repository, "list_evidence_chunks")(adapter_name)
        if chunk.chunk_id in wanted
    )


def _expand_chunk_ids_from_manifest(
    repository,
    adapter_name: str,
    chunk_ids: list[str],
    *,
    include_neighbors: Literal["none", "one_hop", "window"],
    limit: int,
) -> list[str]:
    ordered_ids = _ordered_unique(chunk_ids)
    if not ordered_ids:
        return []
    if include_neighbors == "none":
        return ordered_ids[:limit]

    chunks = getattr(repository, "list_evidence_chunks")(adapter_name)
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    by_evidence: dict[str, list] = {}
    for chunk in chunks:
        by_evidence.setdefault(chunk.evidence_id, []).append(chunk)
    for bucket in by_evidence.values():
        bucket.sort(key=lambda item: (item.chunk_index, item.chunk_id))

    result: list[str] = []
    for chunk_id in ordered_ids:
        chunk = by_id.get(chunk_id)
        if chunk is None:
            result.append(chunk_id)
            continue
        if include_neighbors == "window":
            result.extend(item.chunk_id for item in by_evidence.get(chunk.evidence_id, []))
        else:
            result.extend([chunk.previous_chunk_id or "", chunk.chunk_id, chunk.next_chunk_id or ""])
        result = _ordered_unique(result)
        if len(result) >= limit:
            break
    return result[:limit]


def _chunk_hits_from_repository_by_ids(repository, adapter_name: str, chunk_ids: list[str], *, limit: int) -> list[RetrievalHit]:
    wanted = set(chunk_ids)
    if not wanted:
        return []
    hit_by_id = {
        chunk.chunk_id: _chunk_hit_from_repository(chunk, repository=repository)
        for chunk in getattr(repository, "list_evidence_chunks")(adapter_name)
        if chunk.chunk_id in wanted
    }
    return [hit_by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in hit_by_id][:limit]


def _chunk_hit_from_repository(chunk, *, repository) -> RetrievalHit:
    evidence = repository.get_evidence(chunk.evidence_id)
    title = f"evidence:{chunk.evidence_id}"
    if evidence is not None:
        title = f"{evidence.source_type}:{evidence.source_id}"
    return RetrievalHit(
        hit_id=chunk.chunk_id,
        hit_type="evidence",
        title=title,
        snippet=chunk.content[:800],
        score=1.0,
        source="chunk",
        source_channels=["chunk"],
        evidence_refs=[chunk.evidence_id],
        matched_fields=["kg_evidence_chunks.manifest", "kg_evidence.content"],
        consumption_scope="context",
    )


def _summarize_hits(hits: list[RetrievalHit], *, title: str) -> str:
    if not hits:
        return f"{title}: empty"
    lines = [f"{title}: {len(hits)} hit(s)"]
    evidence_refs = _ordered_unique(ref for hit in hits for ref in hit.evidence_refs)
    node_refs = _ordered_unique(ref for hit in hits for ref in hit.node_refs)
    edge_refs = _ordered_unique(ref for hit in hits for ref in hit.edge_refs)
    if evidence_refs:
        lines.append("evidence_refs: " + ", ".join(evidence_refs[:8]))
    if node_refs:
        lines.append("node_refs: " + ", ".join(node_refs[:8]))
    if edge_refs:
        lines.append("edge_refs: " + ", ".join(edge_refs[:8]))
    for index, hit in enumerate(hits[:8], start=1):
        channels = ",".join(hit.source_channels or [hit.source])
        snippet = " ".join((hit.snippet or "").split())
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        lines.append(f"{index}. [{hit.hit_type}] {hit.title} ({channels}) :: {snippet}")
    return "\n".join(lines)


def _scope_from_candidates(
    repository,
    adapter_name: str,
    *,
    evidence_ids: list[str],
    candidate_ids: list[str],
) -> dict[str, list[str]]:
    node_ids: list[str] = []
    edge_ids: list[str] = []
    scoped_evidence_ids: list[str] = list(evidence_ids)
    for candidate_id in candidate_ids:
        if candidate_id.startswith("kg_edge:"):
            edge = repository.get_edge(candidate_id)
            if edge is None:
                continue
            edge_ids.append(edge.edge_id)
            node_ids.extend([edge.source_node_id, edge.target_node_id])
            scoped_evidence_ids.extend(edge.evidence_ids)
            continue
        if candidate_id.startswith("kg_ev:"):
            scoped_evidence_ids.append(candidate_id)
            continue
        if candidate_id.startswith("kg:"):
            node_ids.append(candidate_id)
            for edge in repository.list_edges(adapter_name):
                if edge.source_node_id == candidate_id or edge.target_node_id == candidate_id:
                    edge_ids.append(edge.edge_id)
                    scoped_evidence_ids.extend(edge.evidence_ids)
    if scoped_evidence_ids:
        node_ids.extend(
            _seed_nodes_from_evidence_refs(
                repository,
                adapter_name,
                evidence_refs=scoped_evidence_ids,
            )
        )
    return {
        "node_ids": _ordered_unique(node_ids),
        "edge_ids": _ordered_unique(edge_ids),
        "evidence_ids": _ordered_unique(scoped_evidence_ids),
    }


def _filter_hits_to_scope(hits: list[RetrievalHit], scope: dict[str, list[str]]) -> list[RetrievalHit]:
    node_ids = set(scope.get("node_ids") or [])
    edge_ids = set(scope.get("edge_ids") or [])
    evidence_ids = set(scope.get("evidence_ids") or [])
    return [
        hit
        for hit in hits
        if node_ids.intersection(hit.node_refs)
        or edge_ids.intersection(hit.edge_refs)
        or evidence_ids.intersection(hit.evidence_refs)
        or hit.hit_id in node_ids
        or hit.hit_id in edge_ids
        or hit.hit_id in evidence_ids
    ]


def _inherit_navigation_metadata(chunk_hit: RetrievalHit, source_hit: RetrievalHit) -> RetrievalHit:
    source = source_hit.source or chunk_hit.source or "unknown"
    matched_fields = _ordered_unique(
        [
            *chunk_hit.matched_fields,
            *source_hit.matched_fields,
            f"traced_from:{source_hit.hit_type}",
        ]
    )
    source_score = float(source_hit.score or 0.0)
    chunk_score = float(chunk_hit.score or 0.0)
    return chunk_hit.model_copy(
        update={
            "score": source_score if source_score > 0.0 else chunk_score,
            "source": source,
            "source_channels": _ordered_unique([*(chunk_hit.source_channels or []), *(source_hit.source_channels or [source])]),
            "channel_ranks": {**(chunk_hit.channel_ranks or {}), **(source_hit.channel_ranks or {})},
            "raw_scores": {**(chunk_hit.raw_scores or {}), **(source_hit.raw_scores or {})},
            "node_refs": _ordered_unique([*chunk_hit.node_refs, *source_hit.node_refs]),
            "edge_refs": _ordered_unique([*chunk_hit.edge_refs, *source_hit.edge_refs]),
            "evidence_refs": _ordered_unique([*chunk_hit.evidence_refs, *source_hit.evidence_refs]),
            "matched_terms": _ordered_unique([*chunk_hit.matched_terms, *source_hit.matched_terms]),
            "matched_fields": matched_fields,
        }
    )


def _source_counts(hits: list[RetrievalHit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        source = hit.source or "unknown"
        counts[source] = counts.get(source, 0) + 1
    return counts


def _merged_channel_counts(hits: list[RetrievalHit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        for channel in hit.source_channels or [hit.source or "unknown"]:
            counts[channel] = counts.get(channel, 0) + 1
    return counts


def _source_samples(hits: list[RetrievalHit], *, per_source: int = 5) -> dict[str, list[dict[str, object]]]:
    samples: dict[str, list[dict[str, object]]] = {}
    for hit in hits:
        source = hit.source or "unknown"
        bucket = samples.setdefault(source, [])
        if len(bucket) >= per_source:
            continue
        bucket.append(
            {
                "id": hit.hit_id,
                "type": hit.hit_type,
                "title": hit.title,
                "score": round(float(hit.score or 0.0), 4),
                "evidence_refs": hit.evidence_refs[:2],
            }
        )
    return samples


def _semantic_disabled_reason(runtime: HybridRetrievalRuntime, options: RetrievalOptions) -> str | None:
    if options.semantic_hybrid_limit <= 0:
        return "semantic_hybrid_limit<=0"
    if not getattr(runtime.semantic_retriever, "enabled", False):
        return "semantic_retriever_disabled"
    return None
