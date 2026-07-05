"""Agent-facing retrieval decision context.

This module is the read-side counterpart of the Cognitive Card write path:

    evidence chunk -> cognitive card -> community card -> agent retrieval package

It deliberately avoids query-time LLM decisions. The runtime uses Milvus for
readable semantic targets and PG for refs, coverage and traceability.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Literal

from pydantic import Field

from src.domain.knowledge.cognitive_index import CognitiveCard
from src.domain.knowledge.graph_index import GraphIndexCommunity
from src.domain.knowledge.repositories import KnowledgeRepository
from src.domain.knowledge.retrieval import RetrievalHit, RetrievalOptions, SemanticHybridRetriever
from src.domain.knowledge.retrieval_profile import profile_span
from src.domain.knowledge.schemas import EvidenceChunk, KnowledgeBaseModel
from src.infrastructure.observability.langfuse_tracing import langfuse_observation, langfuse_update_span

AgentRetrievalSort = Literal["relevance", "freshness", "evidence_strength", "diversity"]
AgentRetrievalLayer = Literal["community", "cognitive_card", "evidence_chunk", "unknown"]
AGENT_RERANK_MAX_DOCUMENTS = 32
AGENT_STRUCTURAL_CALIBRATION_BUDGET = 0.08
AGENT_UNRERANKED_CALIBRATION_BUDGET = 0.025
AGENT_SESSION_LEDGER_MAX_RESULTS = 2000
AGENT_REFINE_MIN_TOP_SCORE = 0.08
AgentAvailableOperationAction = Literal[
    "open_result",
    "expand_context",
    "refine_query",
    "refine_uncovered_observed_aspect",
]


class AgentTimeRange(KnowledgeBaseModel):
    start: datetime | None = None
    end: datetime | None = None


class AgentSearchRequest(KnowledgeBaseModel):
    query: str
    adapter_name: str = "financial"
    target: str = "prod"
    session_id: str | None = None
    limit: int = Field(default=8, ge=1, le=50)
    candidate_limit: int | None = Field(default=None, ge=1, le=300)
    sort: AgentRetrievalSort = "relevance"
    time_range: AgentTimeRange | None = None
    max_chars: int = Field(default=8000, ge=1000, le=40000)
    focus_aspects: list[str] = Field(default_factory=list)


class AgentOpenRequest(KnowledgeBaseModel):
    target_ids: list[str] = Field(default_factory=list)
    query: str | None = None
    adapter_name: str = "financial"
    target: str = "prod"
    session_id: str | None = None
    include_neighbors: bool = True
    limit: int = Field(default=12, ge=1, le=100)
    max_chars: int = Field(default=12000, ge=1000, le=60000)


class AgentExpandRequest(KnowledgeBaseModel):
    target_id: str
    query: str | None = None
    adapter_name: str = "financial"
    target: str = "prod"
    session_id: str | None = None
    direction: Literal["supporting_cards", "supporting_chunks", "neighbors", "auto"] = "auto"
    limit: int = Field(default=20, ge=1, le=150)
    max_chars: int = Field(default=12000, ge=1000, le=60000)


class AgentRefineRequest(AgentSearchRequest):
    previous_context: dict[str, Any] = Field(default_factory=dict)
    refinement: str = ""


class RetrievalEvidencePackage(KnowledgeBaseModel):
    result_id: str
    layer: AgentRetrievalLayer
    title: str
    snippet: str
    score: float = 0.0
    why_relevant: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    chunk_refs: list[str] = Field(default_factory=list)
    cognitive_card_refs: list[str] = Field(default_factory=list)
    community_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    expandable: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoverageSummary(KnowledgeBaseModel):
    topics: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    impact_targets: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    communities: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    chunk_count: int = 0
    cognitive_card_count: int = 0
    community_count: int = 0
    time_range: dict[str, str | None] = Field(default_factory=dict)


class QualityDiagnostics(KnowledgeBaseModel):
    relevance: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    evidence_sufficiency: dict[str, Any] = Field(default_factory=dict)
    diversity: dict[str, Any] = Field(default_factory=dict)
    information_redundancy: dict[str, Any] = Field(default_factory=dict)
    conflict: dict[str, Any] = Field(default_factory=dict)
    freshness: dict[str, Any] = Field(default_factory=dict)
    expandability: dict[str, Any] = Field(default_factory=dict)


class RetrievalAvailableOperation(KnowledgeBaseModel):
    action: AgentAvailableOperationAction
    availability_reason: str
    target_ids: list[str] = Field(default_factory=list)
    query_template: str | None = None
    aspect: str | None = None


class RetrievalDecisionContext(KnowledgeBaseModel):
    query: str
    session_id: str | None = None
    mode: str
    request: dict[str, Any] = Field(default_factory=dict)
    evidence_package: list[RetrievalEvidencePackage] = Field(default_factory=list)
    coverage_summary: CoverageSummary = Field(default_factory=CoverageSummary)
    quality_diagnostics: QualityDiagnostics = Field(default_factory=QualityDiagnostics)
    available_operations: list[RetrievalAvailableOperation] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class _Indexes:
    communities_by_id: dict[str, GraphIndexCommunity]
    cards_by_id: dict[str, CognitiveCard]
    chunks_by_id: dict[str, EvidenceChunk]
    card_ids_by_community_id: dict[str, list[str]]
    community_ids_by_card_id: dict[str, list[str]]
    chunk_ids_by_card_id: dict[str, list[str]]
    source_refs_by_evidence_id: dict[str, str]


@dataclass(frozen=True)
class _QueryIntent:
    name: str
    layer_weights: dict[str, float]
    component_weights: dict[str, float]


class _AgentRetrievalSessionLedger:
    def __init__(self, *, max_results_per_session: int = AGENT_SESSION_LEDGER_MAX_RESULTS):
        self._max_results_per_session = max_results_per_session
        self._result_ids_by_session: dict[str, list[str]] = {}

    def filter_hits(
        self,
        session_id: str | None,
        hits: list[RetrievalHit],
        *,
        protected_ids: set[str] | None = None,
        fallback_if_all_seen: bool = True,
    ) -> tuple[list[RetrievalHit], dict[str, Any]]:
        if not session_id:
            return hits, {"enabled": False, "reason": "missing_session_id"}
        seen = set(self._result_ids_by_session.get(session_id) or [])
        protected_ids = protected_ids or set()
        filtered: list[RetrievalHit] = []
        removed: list[str] = []
        for hit in hits:
            if hit.hit_id in seen and hit.hit_id not in protected_ids:
                removed.append(hit.hit_id)
                continue
            filtered.append(hit)
        if not filtered and hits:
            if not fallback_if_all_seen:
                return [], {
                    "enabled": True,
                    "seen_count": len(seen),
                    "input_count": len(hits),
                    "removed_count": len(removed),
                    "removed_result_ids": removed[:20],
                    "fallback_return_repeated": False,
                    "reason": "all_candidates_were_seen",
                }
            return hits, {
                "enabled": True,
                "seen_count": len(seen),
                "input_count": len(hits),
                "removed_count": len(removed),
                "removed_result_ids": removed[:20],
                "fallback_return_repeated": True,
                "reason": "all_candidates_were_seen",
            }
        return filtered, {
            "enabled": True,
            "seen_count": len(seen),
            "input_count": len(hits),
            "removed_count": len(removed),
            "removed_result_ids": removed[:20],
            "fallback_return_repeated": False,
        }

    def record(self, session_id: str | None, packages: list[RetrievalEvidencePackage]) -> dict[str, Any]:
        if not session_id:
            return {"enabled": False, "reason": "missing_session_id"}
        current = self._result_ids_by_session.setdefault(session_id, [])
        current_set = set(current)
        added: list[str] = []
        for package in packages:
            if package.result_id and package.result_id not in current_set:
                current.append(package.result_id)
                current_set.add(package.result_id)
                added.append(package.result_id)
        if len(current) > self._max_results_per_session:
            overflow = len(current) - self._max_results_per_session
            del current[:overflow]
        return {
            "enabled": True,
            "recorded_result_count": len(current),
            "new_result_count": len(added),
            "new_result_ids": added[:20],
        }


_AGENT_SESSION_LEDGER = _AgentRetrievalSessionLedger()


class AgentRetrievalContextFacade:
    """Build Agent-facing search/open/expand/refine contexts."""

    def __init__(self, repository: KnowledgeRepository, semantic_retriever: SemanticHybridRetriever, reranker_client: Any | None = None):
        self.repository = repository
        self.semantic_retriever = semantic_retriever
        self.reranker_client = reranker_client
        self._last_rerank_diagnostics: dict[str, Any] = {}
        self._last_fusion_diagnostics: dict[str, Any] = {}

    async def search(self, request: AgentSearchRequest) -> RetrievalDecisionContext:
        return await self._search_impl(
            request,
            mode="search",
            request_payload=request.model_dump(mode="json"),
            weak_result_guard=False,
            fallback_repeated_results=True,
            trace_extra={},
        )

    async def _search_impl(
        self,
        request: AgentSearchRequest,
        *,
        mode: str,
        request_payload: dict[str, Any],
        weak_result_guard: bool,
        fallback_repeated_results: bool,
        trace_extra: dict[str, Any],
    ) -> RetrievalDecisionContext:
        with profile_span(
            f"agent_retrieval.{mode}",
            adapter=request.adapter_name,
            limit=request.limit,
            candidate_limit=request.candidate_limit,
            sort=request.sort,
        ):
            options = self._options_from_search_request(request)
            hits = await self.semantic_retriever.search(request.query, options)
            indexes = self._load_indexes_for_hits(request.adapter_name, hits, include_chunks=False)
            candidates = self._candidate_pool(hits, indexes)
            ranked = await self._rank_candidates(
                candidates,
                request.query,
                request.sort,
                indexes,
                focus_aspects=request.focus_aspects,
            )
            ranked, session_dedup_trace = _AGENT_SESSION_LEDGER.filter_hits(
                request.session_id,
                ranked,
                fallback_if_all_seen=fallback_repeated_results,
            )
            ranked, weak_guard_trace = _apply_weak_result_guard(ranked, enabled=weak_result_guard)
            selected = self._select_coverage(ranked, request.sort, indexes=indexes, limit=request.limit)
            indexes = self._merge_indexes(
                indexes,
                self._load_indexes_for_hits(request.adapter_name, selected, include_chunks=True),
            )
            packages = self._packages_from_hits(selected, indexes)
            packages = self._apply_budget(packages, request.max_chars)
            return self._decision_context(
                query=request.query,
                session_id=request.session_id,
                mode=mode,
                request_payload=request_payload,
                packages=packages,
                indexes=indexes,
                focus_aspects=request.focus_aspects,
                trace={
                    **trace_extra,
                    "candidate_count": len(candidates),
                    "selected_count": len(packages),
                    "sort": request.sort,
                    "index_load_mode": "scoped_by_semantic_hits",
                    "session_dedup": {"search_filter": session_dedup_trace},
                    "weak_result_guard": weak_guard_trace,
                    "semantic_diagnostics": getattr(self.semantic_retriever, "last_search_diagnostics", {}),
                    "rerank_diagnostics": self._last_rerank_diagnostics,
                    "fusion_diagnostics": self._last_fusion_diagnostics,
                },
            )

    async def open(self, request: AgentOpenRequest) -> RetrievalDecisionContext:
        with profile_span("agent_retrieval.open", targets=len(request.target_ids), adapter=request.adapter_name):
            target_ids = _ordered_unique(request.target_ids)[: request.limit]
            hits = await self._hits_by_ids(target_ids, request.adapter_name, request.target)
            query_aware_rank = bool((request.query or "").strip())
            neighbor_fetch_limit = max(request.limit * 8, 12) if request.include_neighbors and query_aware_rank else request.limit
            indexes = self._load_indexes_for_targets(
                request.adapter_name,
                target_ids,
                hits,
                include_neighbors=request.include_neighbors,
                limit=neighbor_fetch_limit,
            )
            expanded_hits = list(hits)
            neighbor_hits: list[RetrievalHit] = []
            if request.include_neighbors:
                neighbor_hits = await self._neighbor_hits_for_ids(
                    target_ids,
                    request.adapter_name,
                    request.target,
                    indexes,
                    limit=neighbor_fetch_limit,
                )
                if query_aware_rank and len(_dedupe_hits_by_id(neighbor_hits)) > 1:
                    neighbor_hits = await self._rank_candidates(
                        _dedupe_hits_by_id(neighbor_hits),
                        request.query or "",
                        "relevance",
                        indexes,
                    )
                neighbor_hits, session_dedup_trace = _AGENT_SESSION_LEDGER.filter_hits(
                    request.session_id,
                    neighbor_hits,
                    protected_ids=set(target_ids),
                )
                expanded_hits.extend(neighbor_hits[: request.limit])
            else:
                session_dedup_trace = {"enabled": bool(request.session_id), "skipped": "include_neighbors_false"}
            packages = self._packages_from_hits(_dedupe_hits_preserve_order(expanded_hits), indexes)
            packages = self._apply_budget(packages, request.max_chars)
            return self._decision_context(
                query=request.query or "open",
                session_id=request.session_id,
                mode="open",
                request_payload=request.model_dump(mode="json"),
                packages=packages,
                indexes=indexes,
                focus_aspects=[],
                trace={
                    "requested_ids": target_ids,
                    "opened_count": len(packages),
                    "neighbor_count": len(neighbor_hits),
                    "query_aware_rank": query_aware_rank,
                    "index_load_mode": "scoped_by_target_ids",
                    "session_dedup": {"neighbor_filter": session_dedup_trace},
                    "rerank_diagnostics": self._last_rerank_diagnostics if query_aware_rank else {},
                    "fusion_diagnostics": self._last_fusion_diagnostics if query_aware_rank else {},
                },
            )

    async def expand(self, request: AgentExpandRequest) -> RetrievalDecisionContext:
        with profile_span("agent_retrieval.expand", target_id=request.target_id, direction=request.direction):
            seed_hits = await self._hits_by_ids([request.target_id], request.adapter_name, request.target)
            indexes = self._load_indexes_for_targets(
                request.adapter_name,
                [request.target_id],
                seed_hits,
                include_neighbors=True,
                limit=request.limit,
            )
            target_ids = self._expand_target_ids(request.target_id, request.direction, indexes, limit=request.limit)
            hits = await self._hits_by_ids(target_ids, request.adapter_name, request.target)
            indexes = self._merge_indexes(
                indexes,
                self._load_indexes_for_targets(
                    request.adapter_name,
                    target_ids,
                    hits,
                    include_neighbors=False,
                    limit=request.limit,
                ),
            )
            query_aware_rank = bool((request.query or "").strip())
            if query_aware_rank and len(_dedupe_hits_by_id(hits)) > 1:
                hits = await self._rank_candidates(
                    _dedupe_hits_by_id(hits),
                    request.query or "",
                    "relevance",
                    indexes,
                )
            hits, session_dedup_trace = _AGENT_SESSION_LEDGER.filter_hits(
                request.session_id,
                hits,
                protected_ids={request.target_id},
            )
            packages = self._packages_from_hits(_dedupe_hits_by_id(hits), indexes)
            packages = self._apply_budget(packages, request.max_chars)
            return self._decision_context(
                query=request.query or "expand",
                session_id=request.session_id,
                mode="expand",
                request_payload=request.model_dump(mode="json"),
                packages=packages,
                indexes=indexes,
                focus_aspects=[],
                trace={
                    "seed_id": request.target_id,
                    "direction": request.direction,
                    "expanded_ids": target_ids,
                    "expanded_count": len(packages),
                    "query_aware_rank": query_aware_rank,
                    "index_load_mode": "scoped_by_target_ids",
                    "session_dedup": {"expand_filter": session_dedup_trace},
                    "rerank_diagnostics": self._last_rerank_diagnostics if query_aware_rank else {},
                    "fusion_diagnostics": self._last_fusion_diagnostics if query_aware_rank else {},
                },
            )

    async def refine(self, request: AgentRefineRequest) -> RetrievalDecisionContext:
        refined_query = _refined_query(request.query, request.refinement, request.previous_context)
        search_request = AgentSearchRequest(
            query=refined_query,
            adapter_name=request.adapter_name,
            target=request.target,
            session_id=request.session_id,
            limit=request.limit,
            candidate_limit=request.candidate_limit,
            sort=request.sort,
            time_range=request.time_range,
            max_chars=request.max_chars,
            focus_aspects=request.focus_aspects,
        )
        return await self._search_impl(
            search_request,
            mode="refine",
            request_payload=request.model_dump(mode="json"),
            weak_result_guard=True,
            fallback_repeated_results=False,
            trace_extra={
                "previous_context_refs": _previous_context_refs(request.previous_context),
                "refined_query": refined_query,
            },
        )

    def _load_indexes(self, adapter_name: str) -> _Indexes:
        with profile_span("agent_retrieval.load_indexes", adapter=adapter_name):
            with langfuse_observation(
                name="agent_retrieval.load_indexes",
                as_type="span",
                input={"adapter_name": adapter_name},
                metadata={"adapter_name": adapter_name},
            ):
                try:
                    with profile_span("agent_retrieval.load_indexes.pg_communities", adapter=adapter_name):
                        with langfuse_observation(
                            name="agent_retrieval.load_indexes.pg_communities",
                            as_type="span",
                            input={"adapter_name": adapter_name},
                            metadata={"adapter_name": adapter_name, "table": "kg_graph_communities"},
                        ):
                            communities = self.repository.list_graph_communities(adapter_name)
                            active_communities = len([item for item in communities if item.status == "active"])
                            langfuse_update_span(
                                output={
                                    "rows": len(communities),
                                    "active_rows": active_communities,
                                },
                                status_message="completed",
                            )
                    with profile_span("agent_retrieval.load_indexes.pg_cards", adapter=adapter_name):
                        with langfuse_observation(
                            name="agent_retrieval.load_indexes.pg_cards",
                            as_type="span",
                            input={"adapter_name": adapter_name, "status": "active"},
                            metadata={"adapter_name": adapter_name, "table": "kg_cognitive_cards"},
                        ):
                            cards = self.repository.list_cognitive_cards(adapter_name)
                            active_cards = len([item for item in cards if item.status == "active"])
                            langfuse_update_span(
                                output={
                                    "rows": len(cards),
                                    "active_rows": active_cards,
                                },
                                status_message="completed",
                            )
                    with profile_span("agent_retrieval.load_indexes.pg_chunks", adapter=adapter_name):
                        with langfuse_observation(
                            name="agent_retrieval.load_indexes.pg_chunks",
                            as_type="span",
                            input={"adapter_name": adapter_name},
                            metadata={"adapter_name": adapter_name, "table": "kg_evidence_chunks"},
                        ):
                            chunks = self.repository.list_evidence_chunks(adapter_name)
                            langfuse_update_span(
                                output={"rows": len(chunks)},
                                status_message="completed",
                            )
                    with profile_span(
                        "agent_retrieval.load_indexes.build_maps",
                        communities=len(communities),
                        cards=len(cards),
                        chunks=len(chunks),
                    ):
                        with langfuse_observation(
                            name="agent_retrieval.load_indexes.build_maps",
                            as_type="span",
                            input={
                                "communities": len(communities),
                                "cards": len(cards),
                                "chunks": len(chunks),
                            },
                            metadata={"adapter_name": adapter_name},
                        ):
                            indexes = self._build_indexes_from_rows(communities, cards, chunks)
                            langfuse_update_span(
                                output={
                                    "active_communities": len(indexes.communities_by_id),
                                    "active_cards": len(indexes.cards_by_id),
                                    "chunks": len(indexes.chunks_by_id),
                                    "community_card_links": sum(
                                        len(items) for items in indexes.card_ids_by_community_id.values()
                                    ),
                                    "card_chunk_links": sum(len(items) for items in indexes.chunk_ids_by_card_id.values()),
                                    "evidence_source_refs": len(indexes.source_refs_by_evidence_id),
                                },
                                status_message="completed",
                            )
                    langfuse_update_span(
                        output={
                            "communities": len(indexes.communities_by_id),
                            "cards": len(indexes.cards_by_id),
                            "chunks": len(indexes.chunks_by_id),
                        },
                        status_message="completed",
                    )
                    return indexes
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise

    def _load_indexes_for_hits(
        self,
        adapter_name: str,
        hits: list[RetrievalHit],
        *,
        include_chunks: bool = True,
    ) -> _Indexes:
        community_ids, card_ids, chunk_ids, evidence_ids = _index_refs_from_hits(hits)
        with profile_span(
            "agent_retrieval.load_indexes_scoped",
            adapter=adapter_name,
            hits=len(hits),
            community_ids=len(community_ids),
            card_ids=len(card_ids),
            chunk_ids=len(chunk_ids),
            evidence_ids=len(evidence_ids),
            include_chunks=include_chunks,
        ):
            with langfuse_observation(
                name="agent_retrieval.load_indexes_scoped",
                as_type="span",
                input={
                    "adapter_name": adapter_name,
                    "hit_count": len(hits),
                    "community_ids": community_ids[:50],
                    "card_ids": card_ids[:50],
                    "chunk_ids": chunk_ids[:50],
                    "evidence_ids": evidence_ids[:50],
                    "include_chunks": include_chunks,
                },
                metadata={"adapter_name": adapter_name, "mode": "scoped_by_semantic_hits"},
            ):
                try:
                    communities_loader = getattr(self.repository, "list_graph_communities_by_ids", None)
                    cards_loader = getattr(self.repository, "list_cognitive_cards_by_ids", None)
                    chunks_loader = getattr(self.repository, "list_evidence_chunks_by_refs", None)
                    if not callable(communities_loader) or not callable(cards_loader) or not callable(chunks_loader):
                        langfuse_update_span(
                            metadata={"fallback_reason": "repository_missing_scoped_loaders"},
                            status_message="fallback_full_load_indexes",
                        )
                        return self._load_indexes(adapter_name)

                    with profile_span("agent_retrieval.load_indexes_scoped.pg_communities", adapter=adapter_name):
                        with langfuse_observation(
                            name="agent_retrieval.load_indexes_scoped.pg_communities",
                            as_type="span",
                            input={"adapter_name": adapter_name, "community_ids": community_ids[:50]},
                            metadata={"adapter_name": adapter_name, "table": "kg_graph_communities"},
                        ):
                            communities = communities_loader(adapter_name, community_ids=community_ids)
                            active_communities = [item for item in communities if item.status == "active"]
                            langfuse_update_span(
                                output={"rows": len(communities), "active_rows": len(active_communities)},
                                status_message="completed",
                            )
                    with profile_span("agent_retrieval.load_indexes_scoped.pg_cards", adapter=adapter_name):
                        with langfuse_observation(
                            name="agent_retrieval.load_indexes_scoped.pg_cards",
                            as_type="span",
                            input={"adapter_name": adapter_name, "card_ids": card_ids[:50], "status": "active"},
                            metadata={"adapter_name": adapter_name, "table": "kg_cognitive_cards"},
                        ):
                            cards = cards_loader(adapter_name, cognitive_card_ids=card_ids, status="active")
                            active_cards = [item for item in cards if item.status == "active"]
                            langfuse_update_span(
                                output={"rows": len(cards), "active_rows": len(active_cards)},
                                status_message="completed",
                            )
                    chunks: list[EvidenceChunk] = []
                    if include_chunks:
                        with profile_span("agent_retrieval.load_indexes_scoped.pg_chunks", adapter=adapter_name):
                            with langfuse_observation(
                                name="agent_retrieval.load_indexes_scoped.pg_chunks",
                                as_type="span",
                                input={
                                    "adapter_name": adapter_name,
                                    "chunk_ids": chunk_ids[:50],
                                    "evidence_ids": evidence_ids[:50],
                                },
                                metadata={"adapter_name": adapter_name, "table": "kg_evidence_chunks"},
                            ):
                                chunks = chunks_loader(adapter_name, chunk_ids=chunk_ids, evidence_ids=evidence_ids)
                                langfuse_update_span(output={"rows": len(chunks)}, status_message="completed")

                    with profile_span(
                        "agent_retrieval.load_indexes_scoped.build_maps",
                        communities=len(communities),
                        cards=len(cards),
                        chunks=len(chunks),
                    ):
                        indexes = self._build_indexes_from_rows(communities, cards, chunks)
                    langfuse_update_span(
                        output={
                            "communities": len(indexes.communities_by_id),
                            "cards": len(indexes.cards_by_id),
                            "chunks": len(indexes.chunks_by_id),
                            "fallback_full_load": False,
                        },
                        status_message="completed",
                    )
                    return indexes
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise

    def _load_indexes_for_targets(
        self,
        adapter_name: str,
        target_ids: list[str],
        hits: list[RetrievalHit],
        *,
        include_neighbors: bool,
        limit: int,
    ) -> _Indexes:
        community_ids, card_ids, chunk_ids, evidence_ids = _index_refs_from_hits(hits)
        for target_id in target_ids:
            if _is_community_id(target_id):
                community_ids.append(target_id)
            elif _is_card_id(target_id):
                card_ids.append(target_id)
            elif _is_chunk_id(target_id):
                chunk_ids.append(target_id)
        community_ids = _ordered_unique(community_ids)
        card_ids = _ordered_unique(card_ids)
        chunk_ids = _ordered_unique(chunk_ids)
        evidence_ids = _ordered_unique(evidence_ids)
        with profile_span(
            "agent_retrieval.load_indexes_targets_scoped",
            adapter=adapter_name,
            targets=len(target_ids),
            hits=len(hits),
            include_neighbors=include_neighbors,
        ):
            with langfuse_observation(
                name="agent_retrieval.load_indexes_targets_scoped",
                as_type="span",
                input={
                    "adapter_name": adapter_name,
                    "target_ids": target_ids[:50],
                    "hit_count": len(hits),
                    "include_neighbors": include_neighbors,
                },
                metadata={"adapter_name": adapter_name, "mode": "scoped_by_target_ids"},
            ):
                try:
                    communities_loader = getattr(self.repository, "list_graph_communities_by_ids", None)
                    communities_by_card_loader = getattr(self.repository, "list_graph_communities_by_card_ids", None)
                    cards_loader = getattr(self.repository, "list_cognitive_cards_by_ids", None)
                    cards_by_chunk_loader = getattr(self.repository, "list_cognitive_cards_by_chunk_refs", None)
                    chunks_loader = getattr(self.repository, "list_evidence_chunks_by_refs", None)
                    if (
                        not callable(communities_loader)
                        or not callable(communities_by_card_loader)
                        or not callable(cards_loader)
                        or not callable(cards_by_chunk_loader)
                        or not callable(chunks_loader)
                    ):
                        langfuse_update_span(
                            metadata={"fallback_reason": "repository_missing_target_scoped_loaders"},
                            status_message="fallback_full_load_indexes",
                        )
                        return self._load_indexes(adapter_name)

                    communities = communities_loader(adapter_name, community_ids=community_ids)
                    cards = cards_loader(adapter_name, cognitive_card_ids=card_ids, status="active")
                    chunks = chunks_loader(adapter_name, chunk_ids=chunk_ids, evidence_ids=evidence_ids)

                    if include_neighbors:
                        needs_parent_communities = any(_is_card_id(target_id) or _is_chunk_id(target_id) for target_id in target_ids)
                        needs_chunk_neighbors = any(_is_card_id(target_id) or _is_chunk_id(target_id) for target_id in target_ids)
                        active_communities = [item for item in communities if item.status == "active"]
                        active_cards = [item for item in cards if item.status == "active"]
                        community_card_ids = _ordered_unique(
                            str(item)
                            for community in active_communities
                            for item in (community.metrics or {}).get("cognitive_card_ids") or []
                            if item
                        )[:limit]
                        card_chunk_ids = _ordered_unique(
                            chunk_id
                            for card in active_cards
                            for chunk_id in [*card.chunk_ids, card.primary_chunk_id]
                            if chunk_id
                        )
                        card_evidence_ids = _ordered_unique(card.evidence_id for card in active_cards if card.evidence_id)
                        neighbor_chunk_ids: list[str] = []
                        chunk_related_cards: list[CognitiveCard] = []
                        if needs_chunk_neighbors:
                            neighbor_chunk_ids = _ordered_unique(
                                chunk_id
                                for chunk in chunks
                                for chunk_id in [chunk.previous_chunk_id or "", chunk.chunk_id, chunk.next_chunk_id or ""]
                                if chunk_id
                            )
                            chunk_related_cards = cards_by_chunk_loader(
                                adapter_name,
                                chunk_ids=_ordered_unique([*chunk_ids, *neighbor_chunk_ids])[:limit],
                                evidence_ids=evidence_ids,
                                status="active",
                            )
                        cards = _dedupe_cards(
                            [
                                *cards,
                                *cards_loader(
                                    adapter_name,
                                    cognitive_card_ids=_ordered_unique([*card_ids, *community_card_ids]),
                                    status="active",
                                ),
                                *chunk_related_cards,
                            ]
                        )
                        card_ids = _ordered_unique([card.cognitive_card_id for card in cards])
                        if needs_parent_communities:
                            communities = _dedupe_communities(
                                [
                                    *communities,
                                    *communities_by_card_loader(adapter_name, cognitive_card_ids=card_ids),
                                ]
                            )
                        chunks = _dedupe_chunks(
                            [
                                *chunks,
                                *(
                                    chunks_loader(
                                        adapter_name,
                                        chunk_ids=_ordered_unique(
                                            [
                                                *chunk_ids,
                                                *card_chunk_ids,
                                                *neighbor_chunk_ids,
                                            ]
                                        )[: max(limit * 3, limit)],
                                        evidence_ids=_ordered_unique([*evidence_ids, *card_evidence_ids]),
                                    )
                                    if needs_chunk_neighbors
                                    else []
                                ),
                            ]
                        )

                    indexes = self._build_indexes_from_rows(communities, cards, chunks)
                    langfuse_update_span(
                        output={
                            "communities": len(indexes.communities_by_id),
                            "cards": len(indexes.cards_by_id),
                            "chunks": len(indexes.chunks_by_id),
                            "fallback_full_load": False,
                        },
                        status_message="completed",
                    )
                    return indexes
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise

    def _merge_indexes(self, left: _Indexes, right: _Indexes) -> _Indexes:
        communities = {
            **left.communities_by_id,
            **right.communities_by_id,
        }
        cards = {
            **left.cards_by_id,
            **right.cards_by_id,
        }
        chunks = {
            **left.chunks_by_id,
            **right.chunks_by_id,
        }
        return self._build_indexes_from_rows(list(communities.values()), list(cards.values()), list(chunks.values()))

    def _build_indexes_from_rows(
        self,
        communities: list[GraphIndexCommunity],
        cards: list[CognitiveCard],
        chunks: list[EvidenceChunk],
    ) -> _Indexes:
        communities_by_id = {item.community_id: item for item in communities if item.status == "active"}
        cards_by_id = {item.cognitive_card_id: item for item in cards if item.status == "active"}
        chunks_by_id = {item.chunk_id: item for item in chunks}
        card_ids_by_community_id: dict[str, list[str]] = {}
        community_ids_by_card_id: dict[str, list[str]] = {}
        for community in communities_by_id.values():
            card_ids = [str(item) for item in (community.metrics or {}).get("cognitive_card_ids") or [] if item]
            card_ids_by_community_id[community.community_id] = _ordered_unique(card_ids)
            for card_id in card_ids:
                community_ids_by_card_id.setdefault(card_id, []).append(community.community_id)
        chunk_ids_by_card_id = {
            card.cognitive_card_id: _ordered_unique([*card.chunk_ids, card.primary_chunk_id])
            for card in cards_by_id.values()
        }
        source_refs_by_evidence_id = {
            chunk.evidence_id: _source_ref_from_chunk(chunk)
            for chunk in chunks
        }
        return _Indexes(
            communities_by_id=communities_by_id,
            cards_by_id=cards_by_id,
            chunks_by_id=chunks_by_id,
            card_ids_by_community_id=card_ids_by_community_id,
            community_ids_by_card_id=community_ids_by_card_id,
            chunk_ids_by_card_id=chunk_ids_by_card_id,
            source_refs_by_evidence_id=source_refs_by_evidence_id,
        )

    def _options_from_search_request(self, request: AgentSearchRequest) -> RetrievalOptions:
        candidate_limit = request.candidate_limit or max(request.limit * 5, 50)
        candidate_limit = min(max(candidate_limit, request.limit), 300)
        time_start = request.time_range.start if request.time_range else None
        time_end = request.time_range.end if request.time_range else None
        return RetrievalOptions(
            adapter_name=request.adapter_name,
            target=request.target,
            limit=request.limit,
            keyword_limit=0,
            semantic_hybrid_limit=candidate_limit,
            graph_limit=0,
            wiki_limit=0,
            evidence_limit=request.limit,
            max_hits=request.limit,
            max_chars=request.max_chars,
            semantic_time_start=time_start,
            semantic_time_end=time_end,
        )

    def _candidate_pool(self, hits: list[RetrievalHit], indexes: _Indexes) -> list[RetrievalHit]:
        return _dedupe_hits_by_id([hit for hit in hits if _layer_for_hit(hit, indexes) != "unknown"])

    async def _rank_candidates(
        self,
        hits: list[RetrievalHit],
        query: str,
        sort: AgentRetrievalSort,
        indexes: _Indexes,
        focus_aspects: list[str] | None = None,
    ) -> list[RetrievalHit]:
        query_intent = _infer_query_intent(query, sort=sort, focus_aspects=focus_aspects or [])
        reranked = await self._rerank_candidates(query, hits, indexes)
        query_terms = _query_anchor_terms(query)
        scored = [
            (
                self._sort_score(
                    hit,
                    sort,
                    indexes,
                    query_intent=query_intent,
                    query_terms=query_terms,
                ),
                hit.hit_id,
                hit,
            )
            for hit in reranked
        ]
        self._last_fusion_diagnostics = {
            "query_intent": query_intent.name,
            "component_weights": query_intent.component_weights,
            "layer_weights": query_intent.layer_weights,
            "query_anchor_count": len(query_terms),
            "calibration_budget": AGENT_STRUCTURAL_CALIBRATION_BUDGET,
            "unreranked_calibration_budget": AGENT_UNRERANKED_CALIBRATION_BUDGET,
        }
        return [
            _hit_with_agent_score(hit, score)
            for score, _, hit in sorted(scored, key=lambda item: (-item[0], item[1]))
        ]

    async def _rerank_candidates(
        self,
        query: str,
        hits: list[RetrievalHit],
        indexes: _Indexes,
    ) -> list[RetrievalHit]:
        self._last_rerank_diagnostics = {"enabled": self.reranker_client is not None, "input_count": len(hits)}
        if self.reranker_client is None or not hits:
            return hits
        max_documents = min(
            AGENT_RERANK_MAX_DOCUMENTS,
            int(getattr(self.reranker_client, "max_documents", len(hits)) or len(hits)),
        )
        rerank_hits = _balanced_rerank_window(hits, max_documents=max(1, max_documents))
        documents = [_rerank_document(hit, indexes) for hit in rerank_hits]
        with profile_span("agent_retrieval.rerank", candidates=len(rerank_hits)):
            response = await self.reranker_client.rerank(
                query=query,
                documents=documents,
                top_n=len(rerank_hits),
            )
        ranked: list[RetrievalHit] = []
        seen: set[int] = set()
        for item in response.results:
            index = int(item.index)
            if index < 0 or index >= len(rerank_hits) or index in seen:
                continue
            seen.add(index)
            hit = rerank_hits[index]
            raw_scores = dict(hit.raw_scores or {})
            raw_scores.setdefault("semantic_score", float(hit.score or 0.0))
            raw_scores["reranker"] = float(item.relevance_score)
            ranked.append(
                hit.model_copy(
                    update={
                        "score": float(item.relevance_score),
                        "source_channels": _ordered_unique([*(hit.source_channels or [hit.source]), "reranker"]),
                        "raw_scores": raw_scores,
                    }
                )
            )
        ranked.extend(hit for index, hit in enumerate(rerank_hits) if index not in seen)
        reranked_ids = {hit.hit_id for hit in rerank_hits}
        ranked.extend(hit for hit in hits if hit.hit_id not in reranked_ids)
        self._last_rerank_diagnostics = {
            "enabled": True,
            "input_count": len(hits),
            "reranked_count": len(rerank_hits),
            "returned_count": len(ranked),
            "model": getattr(response, "model", ""),
            "latency_ms": getattr(response, "latency_ms", None),
        }
        return ranked

    def _sort_score(
        self,
        hit: RetrievalHit,
        sort: AgentRetrievalSort,
        indexes: _Indexes,
        *,
        query_intent: _QueryIntent,
        query_terms: list[str],
    ) -> float:
        base = float(hit.score or 0.0)
        structural = _structural_calibration_score(
            hit,
            indexes,
            query_intent=query_intent,
            query_terms=query_terms,
        )
        if "reranker" in (hit.raw_scores or {}):
            budget = AGENT_STRUCTURAL_CALIBRATION_BUDGET
        else:
            budget = AGENT_UNRERANKED_CALIBRATION_BUDGET
        if base < 0:
            budget *= 0.35
        return base + budget * structural

    def _select_coverage(
        self,
        hits: list[RetrievalHit],
        sort: AgentRetrievalSort,
        *,
        indexes: _Indexes,
        limit: int,
    ) -> list[RetrievalHit]:
        ranked_hits = hits
        hits = _trim_weak_tail(hits, limit=limit)
        if sort != "diversity":
            return _ensure_layer_coverage(hits[:limit], ranked_hits, indexes=indexes, limit=limit)
        selected: list[RetrievalHit] = []
        seen_communities: set[str] = set()
        seen_sources: set[str] = set()
        deferred: list[RetrievalHit] = []
        for hit in hits:
            communities = set(_hit_community_refs(hit))
            sources = set(hit.evidence_refs)
            if communities.intersection(seen_communities) or sources.intersection(seen_sources):
                deferred.append(hit)
            else:
                selected.append(hit)
                seen_communities.update(communities)
                seen_sources.update(sources)
            if len(selected) >= limit:
                return _ensure_layer_coverage(selected, ranked_hits, indexes=indexes, limit=limit)
        return _ensure_layer_coverage([*selected, *deferred][:limit], ranked_hits, indexes=indexes, limit=limit)

    async def _hits_by_ids(self, target_ids: list[str], adapter_name: str, target: str) -> list[RetrievalHit]:
        if not target_ids:
            return []
        options = RetrievalOptions(adapter_name=adapter_name, target=target, semantic_hybrid_limit=max(len(target_ids), 1))
        if getattr(self.semantic_retriever, "enabled", False):
            hits = await self.semantic_retriever.get_by_ids(target_ids, options)
            if hits:
                return hits
        return []

    async def _neighbor_hits_for_ids(
        self,
        target_ids: list[str],
        adapter_name: str,
        target: str,
        indexes: _Indexes,
        *,
        limit: int,
    ) -> list[RetrievalHit]:
        neighbor_ids: list[str] = []
        for target_id in target_ids:
            if target_id in indexes.communities_by_id:
                neighbor_ids.extend(indexes.card_ids_by_community_id.get(target_id, [])[:limit])
            elif target_id in indexes.cards_by_id:
                card = indexes.cards_by_id[target_id]
                neighbor_ids.extend(indexes.community_ids_by_card_id.get(target_id, [])[:limit])
                neighbor_ids.extend(_neighbor_chunk_ids(card.primary_chunk_id, indexes, limit=limit))
            elif target_id in indexes.chunks_by_id:
                neighbor_ids.extend(_neighbor_chunk_ids(target_id, indexes, limit=limit))
        return await self._hits_by_ids(_ordered_unique(neighbor_ids)[:limit], adapter_name, target)

    def _expand_target_ids(
        self,
        target_id: str,
        direction: str,
        indexes: _Indexes,
        *,
        limit: int,
    ) -> list[str]:
        if target_id in indexes.communities_by_id:
            community = indexes.communities_by_id[target_id]
            if direction == "supporting_chunks":
                return []
            if direction == "supporting_cards":
                return indexes.card_ids_by_community_id.get(target_id, [])[:limit]
            return indexes.card_ids_by_community_id.get(target_id, [])[:limit]
        if target_id in indexes.cards_by_id:
            card = indexes.cards_by_id[target_id]
            if direction == "neighbors":
                return _neighbor_chunk_ids(card.primary_chunk_id, indexes, limit=limit)
            return _ordered_unique([*indexes.community_ids_by_card_id.get(target_id, []), *card.chunk_ids, card.primary_chunk_id])[:limit]
        if target_id in indexes.chunks_by_id:
            chunk = indexes.chunks_by_id[target_id]
            if direction == "neighbors":
                return _neighbor_chunk_ids(target_id, indexes, limit=limit)
            related_cards = [
                card_id
                for card_id, chunk_ids in indexes.chunk_ids_by_card_id.items()
                if target_id in chunk_ids
            ]
            return _ordered_unique([*(related_cards[:limit]), chunk.previous_chunk_id or "", chunk.next_chunk_id or ""])[:limit]
        return [target_id]

    def _packages_from_hits(self, hits: list[RetrievalHit], indexes: _Indexes) -> list[RetrievalEvidencePackage]:
        return [self._package_from_hit(hit, indexes) for hit in hits]

    def _package_from_hit(self, hit: RetrievalHit, indexes: _Indexes) -> RetrievalEvidencePackage:
        layer = _layer_for_hit(hit, indexes)
        community_refs = _hit_community_refs(hit)
        card_refs = _hit_card_refs(hit, indexes)
        if layer == "community":
            chunk_refs = []
        else:
            chunk_refs = _ordered_unique([*hit.chunk_refs, *_chunk_refs_for_hit(hit, indexes)])
        evidence_refs = _ordered_unique(hit.evidence_refs)
        source_refs = _ordered_unique(indexes.source_refs_by_evidence_id.get(item, "") for item in evidence_refs)
        return RetrievalEvidencePackage(
            result_id=hit.hit_id,
            layer=layer,
            title=hit.title,
            snippet=hit.snippet,
            score=float(hit.score or 0.0),
            why_relevant=_why_relevant(hit, layer, evidence_refs=evidence_refs, chunk_refs=chunk_refs),
            evidence_refs=evidence_refs,
            chunk_refs=chunk_refs,
            cognitive_card_refs=card_refs,
            community_refs=community_refs,
            source_refs=[item for item in source_refs if item],
            expandable={
                "open": [hit.hit_id],
                "expand": _ordered_unique([hit.hit_id, *community_refs, *card_refs, *chunk_refs])[:12],
            },
            metadata={**_package_metadata(hit, indexes), "raw_scores": dict(hit.raw_scores or {})},
        )

    def _apply_budget(
        self,
        packages: list[RetrievalEvidencePackage],
        max_chars: int,
    ) -> list[RetrievalEvidencePackage]:
        result: list[RetrievalEvidencePackage] = []
        used = 0
        for package in packages:
            remaining = max_chars - used
            if remaining <= 0:
                break
            snippet = package.snippet
            if len(snippet) > max(200, remaining):
                snippet = snippet[: max(200, remaining)] + "..."
            used += len(package.title) + len(snippet)
            result.append(package.model_copy(update={"snippet": snippet}))
        return result

    def _decision_context(
        self,
        *,
        query: str,
        session_id: str | None,
        mode: str,
        request_payload: dict[str, Any],
        packages: list[RetrievalEvidencePackage],
        indexes: _Indexes,
        focus_aspects: list[str],
        trace: dict[str, Any],
    ) -> RetrievalDecisionContext:
        coverage = _coverage_summary(packages, indexes)
        diagnostics = _quality_diagnostics(packages, coverage, focus_aspects)
        hints = _available_operations(packages, coverage, diagnostics, focus_aspects)
        record_trace = _AGENT_SESSION_LEDGER.record(session_id, packages)
        trace = {
            **trace,
            "session_dedup": {
                **(trace.get("session_dedup") if isinstance(trace.get("session_dedup"), dict) else {}),
                "record": record_trace,
            },
        }
        return RetrievalDecisionContext(
            query=query,
            session_id=session_id,
            mode=mode,
            request=request_payload,
            evidence_package=packages,
            coverage_summary=coverage,
            quality_diagnostics=diagnostics,
            available_operations=hints,
            trace=trace,
        )


def _layer_for_hit(hit: RetrievalHit, indexes: _Indexes) -> AgentRetrievalLayer:
    if hit.hit_id in indexes.communities_by_id:
        return "community"
    if hit.hit_id in indexes.cards_by_id or hit.hit_type == "cognitive_card":
        return "cognitive_card"
    if hit.hit_id in indexes.chunks_by_id or hit.hit_type == "evidence":
        return "evidence_chunk"
    if "milvus.community" in hit.matched_fields:
        return "community"
    return "unknown"


def _hit_community_refs(hit: RetrievalHit) -> list[str]:
    if hit.hit_id.startswith("kgc:") or hit.hit_id.startswith("kg_community:"):
        return [hit.hit_id]
    return []


def _hit_card_refs(hit: RetrievalHit, indexes: _Indexes) -> list[str]:
    if hit.hit_id in indexes.cards_by_id:
        return [hit.hit_id]
    if hit.hit_type == "cognitive_card":
        return [hit.hit_id]
    return []


def _chunk_refs_for_hit(hit: RetrievalHit, indexes: _Indexes) -> list[str]:
    if hit.hit_id in indexes.chunks_by_id:
        return [hit.hit_id]
    if hit.hit_id in indexes.communities_by_id:
        return []
    if hit.hit_id in indexes.cards_by_id:
        return indexes.cards_by_id[hit.hit_id].chunk_ids
    return []


def _why_relevant(
    hit: RetrievalHit,
    layer: str,
    *,
    evidence_refs: list[str],
    chunk_refs: list[str],
) -> list[str]:
    reasons = [f"命中 {layer} 层语义索引"]
    if hit.matched_fields:
        reasons.append("匹配字段: " + ", ".join(hit.matched_fields[:4]))
    if evidence_refs:
        reasons.append(f"可追溯到 {len(set(evidence_refs))} 条 evidence")
    if chunk_refs:
        reasons.append(f"包含 {len(set(chunk_refs))} 个 chunk 引用")
    return reasons


def _package_metadata(hit: RetrievalHit, indexes: _Indexes) -> dict[str, Any]:
    if hit.hit_id in indexes.communities_by_id:
        community = indexes.communities_by_id[hit.hit_id]
        metrics = dict(community.metrics or {})
        return {
            "projection": community.projection,
            "level": community.level,
            "maturity": metrics.get("maturity"),
            "source_count": len(set(metrics.get("source_ids") or [])),
            "cognitive_card_count": len(set(metrics.get("cognitive_card_ids") or [])),
            "chunk_count": len(set(community.chunk_ids)),
        }
    if hit.hit_id in indexes.cards_by_id:
        card = indexes.cards_by_id[hit.hit_id]
        return {
            "source_type": card.source_type,
            "source_id": card.source_id,
            "chunk_index": card.chunk_index,
            "title_candidates": card.title_candidates,
            "topic_intent_count": len(card.topic_intents),
        }
    if hit.hit_id in indexes.chunks_by_id:
        chunk = indexes.chunks_by_id[hit.hit_id]
        return {
            "evidence_id": chunk.evidence_id,
            "chunk_index": chunk.chunk_index,
            "previous_chunk_id": chunk.previous_chunk_id,
            "next_chunk_id": chunk.next_chunk_id,
            "published_at": (chunk.payload or {}).get("published_at"),
        }
    return {}


def _coverage_summary(packages: list[RetrievalEvidencePackage], indexes: _Indexes) -> CoverageSummary:
    topics: list[str] = []
    risks: list[str] = []
    impact_targets: list[str] = []
    actors: list[str] = []
    published_values: list[str] = []
    for package in packages:
        for card_id in package.cognitive_card_refs:
            card = indexes.cards_by_id.get(card_id)
            if card is None:
                continue
            for value in [
                (card.payload or {}).get("published_at"),
                (card.payload or {}).get("source_published_at"),
                (card.payload or {}).get("event_time"),
            ]:
                if value:
                    published_values.append(str(value))
            topics.extend(card.title_candidates)
            for intent in card.topic_intents:
                topics.extend(_as_list(intent.get("parent_themes")))
                topics.extend(_as_list(intent.get("broad_topics")))
                risks.extend(_as_list(intent.get("risk_type")))
                impact_targets.extend(_as_list(intent.get("impact_target")))
                actors.extend(_as_list(intent.get("actors")))
            for signal in card.risk_signals:
                risks.extend(_as_list(signal.get("risk_type")))
            for signal in card.local_impact_signals:
                impact_targets.extend(_as_list(signal.get("local_impact_target")))
            actor_signals = card.actor_signals or {}
            for key in ("actors", "companies", "industries", "regions", "policies", "commodities"):
                actors.extend(_as_list(actor_signals.get(key)))
        for community_id in package.community_refs:
            community = indexes.communities_by_id.get(community_id)
            if community:
                topics.append(community.title)
                metrics = community.metrics or {}
                published_values.extend(
                    str(value)
                    for value in [
                        metrics.get("earliest_source_published_at"),
                        metrics.get("latest_source_published_at"),
                        metrics.get("event_time_start"),
                        metrics.get("event_time_end"),
                    ]
                    if value
                )
    chunk_ids = {item for package in packages for item in package.chunk_refs}
    evidence_ids = {item for package in packages for item in package.evidence_refs}
    card_ids = {item for package in packages for item in package.cognitive_card_refs}
    community_ids = {item for package in packages for item in package.community_refs}
    published_values.extend(
        str(indexes.chunks_by_id[chunk_id].payload.get("published_at") or "")
        for chunk_id in chunk_ids
        if chunk_id in indexes.chunks_by_id and indexes.chunks_by_id[chunk_id].payload.get("published_at")
    )
    return CoverageSummary(
        topics=_top_values(topics, limit=16),
        risks=_top_values(risks, limit=12),
        impact_targets=_top_values(impact_targets, limit=12),
        actors=_top_values(actors, limit=12),
        communities=_top_values(
            [indexes.communities_by_id[item].title for item in community_ids if item in indexes.communities_by_id],
            limit=12,
        ),
        evidence_count=len(evidence_ids),
        chunk_count=len(chunk_ids),
        cognitive_card_count=len(card_ids),
        community_count=len(community_ids),
        time_range={
            "min_published_at": min(published_values) if published_values else None,
            "max_published_at": max(published_values) if published_values else None,
        },
    )


def _quality_diagnostics(
    packages: list[RetrievalEvidencePackage],
    coverage: CoverageSummary,
    focus_aspects: list[str],
) -> QualityDiagnostics:
    layer_counts = Counter(package.layer for package in packages)
    community_counter = Counter(ref for package in packages for ref in package.community_refs)
    source_counter = Counter(ref for package in packages for ref in package.source_refs)
    top_score = max((package.score for package in packages), default=0.0)
    single_community_ratio = _max_ratio(community_counter, len(packages))
    single_source_ratio = _max_ratio(source_counter, len(packages))
    covered_aspects = _covered_aspects(coverage)
    missing_focus = [aspect for aspect in focus_aspects if aspect not in covered_aspects]
    return QualityDiagnostics(
        relevance={
            "top_score": round(top_score, 4),
            "result_count": len(packages),
            "has_results": bool(packages),
        },
        coverage={
            "covered_aspects": sorted(covered_aspects),
            "missing_explicit_focus_aspects": missing_focus,
            "topic_count": len(coverage.topics),
            "risk_count": len(coverage.risks),
            "impact_target_count": len(coverage.impact_targets),
            "actor_count": len(coverage.actors),
        },
        evidence_sufficiency={
            "evidence_count": coverage.evidence_count,
            "chunk_count": coverage.chunk_count,
            "has_raw_evidence": layer_counts.get("evidence_chunk", 0) > 0 or coverage.chunk_count > 0,
            "status": "sufficient" if coverage.evidence_count >= 2 or coverage.chunk_count >= 2 else "thin",
        },
        diversity={
            "layer_counts": dict(layer_counts),
            "community_count": coverage.community_count,
            "source_count": len(source_counter),
            "single_community_ratio": round(single_community_ratio, 3),
            "single_source_ratio": round(single_source_ratio, 3),
            "status": "concentrated" if single_community_ratio >= 0.75 or single_source_ratio >= 0.75 else "mixed",
        },
        information_redundancy={
            "status": "homogeneous" if single_community_ratio >= 0.75 or single_source_ratio >= 0.75 else "acceptable",
            "reason": "去重后结果仍集中在单一 community/source" if single_community_ratio >= 0.75 or single_source_ratio >= 0.75 else "",
        },
        conflict={
            "detected": _has_direction_conflict(packages),
            "method": "impact_direction signal comparison",
        },
        freshness={
            "time_range": coverage.time_range,
            "has_time_signal": bool(coverage.time_range.get("min_published_at") or coverage.time_range.get("max_published_at")),
        },
        expandability={
            "expandable_count": sum(1 for package in packages if package.expandable.get("expand")),
            "has_expandable_context": any(package.expandable.get("expand") for package in packages),
        },
    )


def _available_operations(
    packages: list[RetrievalEvidencePackage],
    coverage: CoverageSummary,
    diagnostics: QualityDiagnostics,
    focus_aspects: list[str],
) -> list[RetrievalAvailableOperation]:
    if not packages:
        return [
            RetrievalAvailableOperation(
                action="refine_query",
                availability_reason="当前没有命中结果；可用更宽关键词、不同时间范围或更上层主题再次检索。",
            )
        ]
    operations: list[RetrievalAvailableOperation] = []
    first = packages[0]
    operations.append(
        RetrievalAvailableOperation(
            action="open_result",
            availability_reason="最高相关命中可以打开，以获取完整可读上下文和 refs。",
            target_ids=[first.result_id],
        )
    )
    if diagnostics.evidence_sufficiency.get("status") == "thin":
        operations.append(
            RetrievalAvailableOperation(
                action="expand_context",
                availability_reason="原始 evidence/chunk 支撑偏少；可展开支撑材料或邻接 chunk。",
                target_ids=first.expandable.get("expand", [])[:5],
            )
        )
    if diagnostics.diversity.get("status") == "concentrated":
        operations.append(
            RetrievalAvailableOperation(
                action="refine_query",
                availability_reason="结果去重后仍集中在单一 community/source；可用更明确的行业、风险、主体或时间约束再次检索。",
                query_template="在当前主题下补充未覆盖的风险、影响对象或相关主体",
            )
        )
    for aspect in diagnostics.coverage.get("missing_explicit_focus_aspects") or []:
        operations.append(
            RetrievalAvailableOperation(
                action="refine_uncovered_observed_aspect",
                aspect=str(aspect),
                availability_reason=f"Agent 显式关注 {aspect}，但当前结果没有覆盖该侧面；可按该侧面再次检索。",
                query_template=f"{aspect} {', '.join(coverage.topics[:3])}",
            )
        )
    return operations[:6]


def _hit_with_agent_score(hit: RetrievalHit, score: float) -> RetrievalHit:
    raw_scores = dict(hit.raw_scores or {})
    raw_scores.setdefault("semantic_score", float(hit.score or 0.0))
    raw_scores["agent_fused_score"] = round(float(score or 0.0), 6)
    return hit.model_copy(update={"score": raw_scores["agent_fused_score"], "raw_scores": raw_scores})


def _apply_weak_result_guard(
    hits: list[RetrievalHit],
    *,
    enabled: bool,
) -> tuple[list[RetrievalHit], dict[str, Any]]:
    if not enabled:
        return hits, {"enabled": False}
    if not hits:
        return hits, {
            "enabled": True,
            "applied": False,
            "input_count": 0,
            "reason": "no_candidates_after_dedup",
            "min_top_score": AGENT_REFINE_MIN_TOP_SCORE,
        }
    top_score = max(float(hit.score or 0.0) for hit in hits)
    if top_score < AGENT_REFINE_MIN_TOP_SCORE:
        return [], {
            "enabled": True,
            "applied": True,
            "input_count": len(hits),
            "removed_count": len(hits),
            "top_score": round(top_score, 6),
            "min_top_score": AGENT_REFINE_MIN_TOP_SCORE,
            "reason": "top_score_below_refine_floor",
        }
    return hits, {
        "enabled": True,
        "applied": False,
        "input_count": len(hits),
        "top_score": round(top_score, 6),
        "min_top_score": AGENT_REFINE_MIN_TOP_SCORE,
    }


def _infer_query_intent(
    query: str,
    *,
    sort: AgentRetrievalSort,
    focus_aspects: list[str],
) -> _QueryIntent:
    focus = {str(item).strip().lower() for item in focus_aspects if str(item).strip()}
    normalized = _normalize_query_text(query)
    if sort == "freshness" or "time" in focus or any(term in normalized for term in ("最近", "近期", "最新", "今天", "昨日", "当前")):
        return _query_intent_profile("time_sensitive")
    if "risk" in focus:
        return _query_intent_profile("risk_search")
    if any(term in normalized for term in ("原文", "证据", "哪条", "哪篇", "引用", "出处", "具体数字", "公告")):
        return _query_intent_profile("evidence_lookup")
    if "actor" in focus:
        return _query_intent_profile("actor_search")
    if "topic" in focus or "impact" in focus:
        return _query_intent_profile("macro_theme")
    if sort == "evidence_strength":
        return _query_intent_profile("macro_theme")
    return _query_intent_profile("balanced")


def _query_intent_profile(name: str) -> _QueryIntent:
    profiles: dict[str, _QueryIntent] = {
        "macro_theme": _QueryIntent(
            name="macro_theme",
            layer_weights={"community": 1.0, "cognitive_card": 0.82, "evidence_chunk": 0.58, "unknown": 0.0},
            component_weights={"layer": 0.34, "field": 0.30, "evidence": 0.26, "freshness": 0.10},
        ),
        "risk_search": _QueryIntent(
            name="risk_search",
            layer_weights={"community": 0.92, "cognitive_card": 1.0, "evidence_chunk": 0.66, "unknown": 0.0},
            component_weights={"layer": 0.30, "field": 0.34, "evidence": 0.22, "freshness": 0.14},
        ),
        "actor_search": _QueryIntent(
            name="actor_search",
            layer_weights={"community": 0.66, "cognitive_card": 1.0, "evidence_chunk": 0.82, "unknown": 0.0},
            component_weights={"layer": 0.28, "field": 0.38, "evidence": 0.20, "freshness": 0.14},
        ),
        "evidence_lookup": _QueryIntent(
            name="evidence_lookup",
            layer_weights={"community": 0.45, "cognitive_card": 0.80, "evidence_chunk": 1.0, "unknown": 0.0},
            component_weights={"layer": 0.34, "field": 0.36, "evidence": 0.18, "freshness": 0.12},
        ),
        "time_sensitive": _QueryIntent(
            name="time_sensitive",
            layer_weights={"community": 0.82, "cognitive_card": 0.84, "evidence_chunk": 1.0, "unknown": 0.0},
            component_weights={"layer": 0.26, "field": 0.28, "evidence": 0.18, "freshness": 0.28},
        ),
        "balanced": _QueryIntent(
            name="balanced",
            layer_weights={"community": 0.86, "cognitive_card": 0.92, "evidence_chunk": 0.82, "unknown": 0.0},
            component_weights={"layer": 0.30, "field": 0.34, "evidence": 0.22, "freshness": 0.14},
        ),
    }
    return profiles.get(name, profiles["balanced"])


def _structural_calibration_score(
    hit: RetrievalHit,
    indexes: _Indexes,
    *,
    query_intent: _QueryIntent,
    query_terms: list[str],
) -> float:
    layer = _layer_for_hit(hit, indexes)
    layer_score = query_intent.layer_weights.get(layer, 0.0)
    field_score = _field_anchor_score(hit, indexes, query_terms=query_terms)
    evidence_score = min(1.0, _evidence_strength_boost(hit, indexes) / 0.3)
    freshness_score = min(1.0, _freshness_boost(hit, indexes) / 0.2)
    weights = query_intent.component_weights
    score = (
        layer_score * weights.get("layer", 0.0)
        + field_score * weights.get("field", 0.0)
        + evidence_score * weights.get("evidence", 0.0)
        + freshness_score * weights.get("freshness", 0.0)
    )
    return max(0.0, min(1.0, score))


def _field_anchor_score(hit: RetrievalHit, indexes: _Indexes, *, query_terms: list[str]) -> float:
    if not query_terms:
        return 0.0
    groups = _field_anchor_groups(hit, indexes)
    if not groups:
        return 0.0
    weighted_score = 0.0
    total_weight = 0.0
    for weight, values in groups:
        text = _normalize_query_text(" ".join(_as_list(values)))
        if not text:
            continue
        total_weight += weight
        matched = sum(1 for term in query_terms if term and term in text)
        if matched:
            weighted_score += weight * min(1.0, matched / max(1, min(len(query_terms), 5)))
    if total_weight <= 0:
        return 0.0
    return max(0.0, min(1.0, weighted_score / total_weight))


def _field_anchor_groups(hit: RetrievalHit, indexes: _Indexes) -> list[tuple[float, Any]]:
    if hit.hit_id in indexes.communities_by_id:
        community = indexes.communities_by_id[hit.hit_id]
        metrics = community.metrics or {}
        return [
            (1.0, [community.title, metrics.get("scope"), *(metrics.get("canonical_labels") or [])]),
            (0.75, [*(metrics.get("topic_tags") or []), *(metrics.get("risk_tags") or [])]),
            (0.45, [community.summary, *(metrics.get("source_titles") or [])[:8]]),
        ]
    if hit.hit_id in indexes.cards_by_id:
        card = indexes.cards_by_id[hit.hit_id]
        parent_themes: list[str] = []
        broad_topics: list[str] = []
        risk_types: list[str] = []
        impact_targets: list[str] = []
        actors: list[str] = []
        for intent in card.topic_intents:
            parent_themes.extend(_as_list(intent.get("parent_themes")))
            broad_topics.extend(_as_list(intent.get("broad_topics")))
            risk_types.extend(_as_list(intent.get("risk_type")))
            impact_targets.extend(_as_list(intent.get("impact_target")))
            actors.extend(_as_list(intent.get("actors")))
        return [
            (1.0, [*card.title_candidates, *parent_themes]),
            (0.75, [*broad_topics, *risk_types, *impact_targets, *actors]),
            (0.45, [card.summary, *card.supporting_text]),
        ]
    if hit.hit_id in indexes.chunks_by_id:
        chunk = indexes.chunks_by_id[hit.hit_id]
        chunk_content = "" if (chunk.payload or {}).get("content_deferred_to_milvus") else chunk.content
        payload = chunk.payload or {}
        return [
            (0.85, [payload.get("title"), payload.get("summary")]),
            (0.50, [chunk_content]),
        ]
    return [
        (0.70, [hit.title]),
        (0.35, [hit.snippet, *hit.matched_terms]),
    ]


def _query_anchor_terms(query: str) -> list[str]:
    text = _normalize_query_text(query)
    text = re.sub(r"(最近|近期|最新|当前|今天|昨日|昨天|有哪些|有什么|什么|如何|怎么|关键|变化)", " ", text)
    text = re.sub(r"(以及|或者|还是|和|与|及)", " ", text)
    text = re.sub(r"[、，。？?：:/|\\]+", " ", text)
    terms: list[str] = []
    for item in re.findall(r"[a-z0-9_.-]+|[\u4e00-\u9fff]{2,}", text):
        item = item.strip()
        if len(item) >= 2:
            terms.append(item)
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", item):
            terms.extend(item[index : index + 2] for index in range(0, len(item) - 1))
    return _ordered_unique(terms)[:32]


def _normalize_query_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _balanced_rerank_window(hits: list[RetrievalHit], *, max_documents: int) -> list[RetrievalHit]:
    if len(hits) <= max_documents:
        return hits
    communities = [hit for hit in hits if _is_community_hit(hit)]
    cards = [hit for hit in hits if _is_card_hit(hit)]
    chunks = [hit for hit in hits if _is_evidence_hit(hit)]
    others = [
        hit
        for hit in hits
        if not _is_community_hit(hit) and not _is_card_hit(hit) and not _is_evidence_hit(hit)
    ]
    selected = _ordered_unique_hits(
        [
            *communities[: min(8, max_documents)],
            *cards[: min(12, max_documents)],
            *chunks[: max(0, max_documents - min(8, len(communities)) - min(12, len(cards)))],
        ]
    )
    if len(selected) < max_documents:
        selected = _ordered_unique_hits([*selected, *chunks, *others, *hits])[:max_documents]
    return selected[:max_documents]


def _ordered_unique_hits(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    result: list[RetrievalHit] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.hit_id in seen:
            continue
        seen.add(hit.hit_id)
        result.append(hit)
    return result


def _trim_weak_tail(hits: list[RetrievalHit], *, limit: int) -> list[RetrievalHit]:
    if len(hits) <= 3:
        return hits
    top_score = max(float(hit.score or 0.0) for hit in hits)
    if top_score <= 0:
        return hits[:limit]
    floor = max(0.02, top_score * 0.12)
    kept = [hit for hit in hits if float(hit.score or 0.0) >= floor]
    if len(kept) >= 2:
        return kept
    return hits[: min(max(2, len(kept)), limit)]


def _ensure_layer_coverage(
    selected: list[RetrievalHit],
    ranked_hits: list[RetrievalHit],
    *,
    indexes: _Indexes,
    limit: int,
) -> list[RetrievalHit]:
    result = list(selected)
    result = _ensure_layer_hit(
        result,
        ranked_hits,
        indexes=indexes,
        limit=limit,
        predicate=_is_community_hit,
        require_support_for_negative=False,
    )
    result = _ensure_layer_hit(
        result,
        ranked_hits,
        indexes=indexes,
        limit=limit,
        predicate=_is_card_hit,
        require_support_for_negative=True,
    )
    result = _ensure_layer_hit(
        result,
        ranked_hits,
        indexes=indexes,
        limit=limit,
        predicate=_is_evidence_hit,
        require_support_for_negative=True,
    )
    return result[:limit]


def _ensure_layer_hit(
    selected: list[RetrievalHit],
    ranked_hits: list[RetrievalHit],
    *,
    indexes: _Indexes,
    limit: int,
    predicate,
    require_support_for_negative: bool,
) -> list[RetrievalHit]:
    if len(selected) >= limit or any(predicate(hit) for hit in selected):
        return selected
    if not ranked_hits:
        return selected
    support_scope = _support_scope_for_hits(selected, indexes)
    positive_floor = _positive_backfill_floor(selected)
    for hit in ranked_hits:
        if not predicate(hit):
            continue
        if float(hit.score or 0.0) >= 0 and float(hit.score or 0.0) < positive_floor:
            continue
        if not require_support_for_negative:
            return _dedupe_hits_by_id([*selected, hit])[:limit]
        if float(hit.score or 0.0) >= 0 or _hit_supported_by_scope(hit, support_scope, indexes):
            return _dedupe_hits_by_id([*selected, hit])[:limit]
    return selected


def _positive_backfill_floor(selected: list[RetrievalHit]) -> float:
    if not selected:
        return 0.0
    top_score = max(float(hit.score or 0.0) for hit in selected)
    if top_score <= 0:
        return 0.0
    return max(0.02, top_score * 0.08)


def _support_scope_for_hits(hits: list[RetrievalHit], indexes: _Indexes) -> dict[str, set[str]]:
    community_ids: set[str] = set()
    card_ids: set[str] = set()
    chunk_ids: set[str] = set()
    evidence_ids: set[str] = set()
    for hit in hits:
        if hit.hit_id in indexes.communities_by_id:
            community_ids.add(hit.hit_id)
            community = indexes.communities_by_id[hit.hit_id]
            card_ids.update(indexes.card_ids_by_community_id.get(hit.hit_id, []))
            chunk_ids.update(community.chunk_ids)
            evidence_ids.update(community.evidence_ids)
        if hit.hit_id in indexes.cards_by_id:
            card_ids.add(hit.hit_id)
            card = indexes.cards_by_id[hit.hit_id]
            chunk_ids.update(_ordered_unique([*card.chunk_ids, card.primary_chunk_id]))
            evidence_ids.add(card.evidence_id)
            community_ids.update(indexes.community_ids_by_card_id.get(hit.hit_id, []))
        if hit.hit_id in indexes.chunks_by_id:
            chunk_ids.add(hit.hit_id)
            evidence_ids.add(indexes.chunks_by_id[hit.hit_id].evidence_id)
        chunk_ids.update(hit.chunk_refs)
        evidence_ids.update(hit.evidence_refs)
    return {
        "community_ids": community_ids,
        "card_ids": card_ids,
        "chunk_ids": chunk_ids,
        "evidence_ids": evidence_ids,
    }


def _hit_supported_by_scope(hit: RetrievalHit, support_scope: dict[str, set[str]], indexes: _Indexes) -> bool:
    if hit.hit_id in support_scope["card_ids"] or hit.hit_id in support_scope["chunk_ids"]:
        return True
    if set(hit.chunk_refs).intersection(support_scope["chunk_ids"]):
        return True
    if set(hit.evidence_refs).intersection(support_scope["evidence_ids"]):
        return True
    if hit.hit_id in indexes.cards_by_id:
        card = indexes.cards_by_id[hit.hit_id]
        if card.evidence_id in support_scope["evidence_ids"]:
            return True
        if set(_ordered_unique([*card.chunk_ids, card.primary_chunk_id])).intersection(support_scope["chunk_ids"]):
            return True
    if hit.hit_id in indexes.chunks_by_id:
        chunk = indexes.chunks_by_id[hit.hit_id]
        return chunk.evidence_id in support_scope["evidence_ids"]
    return False


def _index_refs_from_hits(hits: list[RetrievalHit]) -> tuple[list[str], list[str], list[str], list[str]]:
    community_ids: list[str] = []
    card_ids: list[str] = []
    chunk_ids: list[str] = []
    evidence_ids: list[str] = []
    for hit in hits:
        if _is_community_hit(hit):
            community_ids.append(hit.hit_id)
        if _is_card_hit(hit):
            card_ids.append(hit.hit_id)
        if _is_evidence_hit(hit):
            chunk_ids.append(hit.hit_id)
        if _is_evidence_hit(hit):
            chunk_ids.extend(hit.chunk_refs)
            evidence_ids.extend(hit.evidence_refs)
    return (
        _ordered_unique(community_ids),
        _ordered_unique(card_ids),
        _ordered_unique(chunk_ids),
        _ordered_unique(evidence_ids),
    )


def _is_community_id(value: str) -> bool:
    return str(value or "").startswith(("kgc:", "kg_community:"))


def _is_card_id(value: str) -> bool:
    return str(value or "").startswith(("kg_cognitive_card:", "kg_card:cognitive:"))


def _is_chunk_id(value: str) -> bool:
    return str(value or "").startswith(("kg_chunk:", "chunk:"))


def _is_community_hit(hit: RetrievalHit) -> bool:
    return _is_community_id(hit.hit_id) or "milvus.community" in hit.matched_fields


def _is_card_hit(hit: RetrievalHit) -> bool:
    return hit.hit_type == "cognitive_card" or _is_card_id(hit.hit_id)


def _is_evidence_hit(hit: RetrievalHit) -> bool:
    return hit.hit_type == "evidence" or _is_chunk_id(hit.hit_id)


def _rerank_document(hit: RetrievalHit, indexes: _Indexes) -> str:
    parts = [hit.title, hit.snippet]
    if hit.hit_id in indexes.communities_by_id:
        community = indexes.communities_by_id[hit.hit_id]
        parts.extend([community.title, community.summary])
        metrics = community.metrics or {}
        parts.extend(str(item) for item in (metrics.get("absorbed_subtopics") or [])[:12])
        parts.extend(str(item) for item in (metrics.get("source_titles") or [])[:8])
    if hit.hit_id in indexes.cards_by_id:
        parts.extend(_card_search_text_parts(indexes.cards_by_id[hit.hit_id]))
    for card_id in _hit_card_refs(hit, indexes):
        card = indexes.cards_by_id.get(card_id)
        if card:
            parts.extend(_card_search_text_parts(card))
    for chunk_id in _ordered_unique([hit.hit_id, *hit.chunk_refs]):
        chunk = indexes.chunks_by_id.get(chunk_id)
        if chunk:
            if not (chunk.payload or {}).get("content_deferred_to_milvus"):
                parts.append(chunk.content)
            parts.append(str((chunk.payload or {}).get("title") or ""))
    return "\n".join(_ordered_unique(str(item).strip() for item in parts if str(item).strip()))[:4000]


def _card_search_text_parts(card: CognitiveCard) -> list[str]:
    parts: list[str] = [card.summary, *card.title_candidates, *card.supporting_text]
    for intent in card.topic_intents:
        for key in (
            "raw_theme",
            "title_candidate",
            "summary",
            "parent_themes",
            "broad_topics",
            "mid_topics",
            "specific_topics",
            "driver",
            "impact_target",
            "risk_type",
            "event_thread",
            "event_action",
            "actors",
        ):
            parts.extend(_as_list(intent.get(key)))
    actor_signals = card.actor_signals or {}
    for key in ("actors", "companies", "industries", "regions", "policies", "commodities"):
        parts.extend(_as_list(actor_signals.get(key)))
    return parts


def _freshness_boost(hit: RetrievalHit, indexes: _Indexes) -> float:
    published = _published_at_for_hit(hit, indexes)
    if published is None:
        return 0.0
    age_days = max((datetime.now(timezone.utc) - published).days, 0)
    if age_days <= 1:
        return 0.2
    if age_days <= 7:
        return 0.12
    if age_days <= 30:
        return 0.06
    return 0.0


def _evidence_strength_boost(hit: RetrievalHit, indexes: _Indexes) -> float:
    if hit.hit_id in indexes.communities_by_id:
        community = indexes.communities_by_id[hit.hit_id]
        source_count = len(set((community.metrics or {}).get("source_ids") or community.evidence_ids))
        card_count = len(set((community.metrics or {}).get("cognitive_card_ids") or []))
        return min(0.3, source_count * 0.04 + card_count * 0.01)
    if hit.hit_id in indexes.cards_by_id:
        return min(0.15, len(indexes.cards_by_id[hit.hit_id].topic_intents) * 0.03)
    if hit.evidence_refs:
        return 0.05
    return 0.0


def _published_at_for_hit(hit: RetrievalHit, indexes: _Indexes) -> datetime | None:
    chunk_ids = _ordered_unique([hit.hit_id, *hit.chunk_refs])
    values = [
        _parse_datetime(indexes.chunks_by_id[chunk_id].payload.get("published_at"))
        for chunk_id in chunk_ids
        if chunk_id in indexes.chunks_by_id
    ]
    values = [item for item in values if item is not None]
    return max(values) if values else None


def _neighbor_chunk_ids(chunk_id: str, indexes: _Indexes, *, limit: int) -> list[str]:
    chunk = indexes.chunks_by_id.get(chunk_id)
    if chunk is None:
        return []
    same_evidence = [
        item.chunk_id
        for item in sorted(indexes.chunks_by_id.values(), key=lambda value: (value.evidence_id, value.chunk_index, value.chunk_id))
        if item.evidence_id == chunk.evidence_id
    ]
    result = [chunk.previous_chunk_id or "", chunk.chunk_id, chunk.next_chunk_id or ""]
    if len(result) < limit:
        result.extend(same_evidence)
    return _ordered_unique(item for item in result if item)[:limit]


def _source_ref_from_chunk(chunk: EvidenceChunk) -> str:
    payload = chunk.payload or {}
    source_type = str(payload.get("source_type") or "")
    source_id = str(payload.get("source_id") or "")
    return f"{source_type}:{source_id}" if source_type and source_id else ""


def _refined_query(query: str, refinement: str, previous_context: dict[str, Any]) -> str:
    parts = [query.strip()]
    if refinement.strip():
        parts.append(refinement.strip())
    coverage = previous_context.get("coverage_summary") if isinstance(previous_context, dict) else None
    if isinstance(coverage, dict):
        missing = coverage.get("missing_explicit_focus_aspects") or []
        if missing:
            parts.append("补充侧面: " + " ".join(str(item) for item in missing[:5]))
    return " ".join(part for part in parts if part)


def _previous_context_refs(previous_context: dict[str, Any]) -> list[str]:
    packages = previous_context.get("evidence_package") if isinstance(previous_context, dict) else []
    if not isinstance(packages, list):
        return []
    return _ordered_unique(str(item.get("result_id") or "") for item in packages if isinstance(item, dict))[:20]


def _covered_aspects(coverage: CoverageSummary) -> set[str]:
    result: set[str] = set()
    if coverage.topics:
        result.add("topic")
    if coverage.risks:
        result.add("risk")
    if coverage.impact_targets:
        result.add("impact")
    if coverage.actors:
        result.add("actor")
    if coverage.time_range.get("min_published_at"):
        result.add("time")
    return result


def _has_direction_conflict(packages: list[RetrievalEvidencePackage]) -> bool:
    directions = []
    for package in packages:
        value = package.metadata.get("impact_direction")
        if value:
            directions.append(str(value))
    meaningful = {item for item in directions if item in {"positive", "negative"}}
    return len(meaningful) > 1


def _max_ratio(counter: Counter, denominator: int) -> float:
    if not counter or denominator <= 0:
        return 0.0
    return max(counter.values()) / denominator


def _top_values(values: list[str], *, limit: int) -> list[str]:
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    return [item for item, _ in Counter(cleaned).most_common(limit)]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _dedupe_hits_by_id(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    result: dict[str, RetrievalHit] = {}
    for hit in hits:
        existing = result.get(hit.hit_id)
        if existing is None or float(hit.score or 0.0) > float(existing.score or 0.0):
            result[hit.hit_id] = hit
    return sorted(result.values(), key=lambda item: (-float(item.score or 0.0), item.hit_id))


def _dedupe_hits_preserve_order(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    result: list[RetrievalHit] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.hit_id in seen:
            continue
        seen.add(hit.hit_id)
        result.append(hit)
    return result


def _dedupe_communities(items: list[GraphIndexCommunity]) -> list[GraphIndexCommunity]:
    result: dict[str, GraphIndexCommunity] = {}
    for item in items:
        result.setdefault(item.community_id, item)
    return list(result.values())


def _dedupe_cards(items: list[CognitiveCard]) -> list[CognitiveCard]:
    result: dict[str, CognitiveCard] = {}
    for item in items:
        result.setdefault(item.cognitive_card_id, item)
    return list(result.values())


def _dedupe_chunks(items: list[EvidenceChunk]) -> list[EvidenceChunk]:
    result: dict[str, EvidenceChunk] = {}
    for item in items:
        result.setdefault(item.chunk_id, item)
    return list(result.values())


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
