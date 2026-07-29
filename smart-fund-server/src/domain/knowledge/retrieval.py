"""Hybrid retrieval runtime for generated knowledge context."""

from __future__ import annotations

import json
import logging
import re
from collections import deque
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceStatus, NodeStatus
from src.domain.knowledge.repositories import KnowledgeRepository
from src.domain.knowledge.retrieval_anchor import build_guarded_query_anchor
from src.domain.knowledge.retrieval_judge import (
    CandidateJudgement,
    filter_hits_by_judgement,
    judge_hits,
    retrieval_quality_metrics,
)
from src.domain.knowledge.retrieval_profile import profile_span
from src.domain.knowledge.schemas import (
    CompiledEdge,
    CompiledEvidence,
    CompiledNode,
    EvidenceChunk,
    KnowledgeBaseModel,
)

logger = logging.getLogger(__name__)

HitType = Literal["node", "edge", "path", "evidence", "cognitive_card", "semantic_hybrid"]
GraphDirection = Literal["incoming", "outgoing", "undirected", "path"]


class RetrievalHit(KnowledgeBaseModel):
    hit_id: str
    hit_type: HitType
    title: str
    snippet: str
    score: float = 0.0
    source: str
    source_channels: list[str] = Field(default_factory=list)
    channel_ranks: dict[str, int] = Field(default_factory=dict)
    raw_scores: dict[str, float] = Field(default_factory=dict)
    node_refs: list[str] = Field(default_factory=list)
    edge_refs: list[str] = Field(default_factory=list)
    chunk_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    path_node_refs: list[str] = Field(default_factory=list)
    path_edge_refs: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    matched_fields: list[str] = Field(default_factory=list)
    confidence: ConfidenceLabel | None = None
    consumption_scope: str = "context"


class RetrievalStep(KnowledgeBaseModel):
    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    output_refs: list[str] = Field(default_factory=list)
    hit_count: int = 0
    warning: str | None = None


class RetrievalTrace(KnowledgeBaseModel):
    mode: str = "deterministic_plan"
    channels_enabled: list[str] = Field(default_factory=list)
    channels_used: list[str] = Field(default_factory=list)
    semantic_enabled: bool = False
    milvus_enabled: bool = False
    planner_enabled: bool = False
    steps: list[RetrievalStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    query_anchor: dict[str, Any] = Field(default_factory=dict)
    routing_decision: dict[str, Any] = Field(default_factory=dict)
    candidate_judgements: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_metrics: dict[str, Any] = Field(default_factory=dict)
    working_set: dict[str, Any] = Field(default_factory=dict)
    controller_decisions: list[dict[str, Any]] = Field(default_factory=list)


class RetrievalOptions(KnowledgeBaseModel):
    adapter_name: str
    target: str = "prod"
    limit: int = Field(default=20, ge=1)
    keyword_limit: int = Field(default=10, ge=0)
    semantic_hybrid_limit: int = Field(default=0, ge=0)
    graph_limit: int = Field(default=10, ge=0)
    graph_direction: GraphDirection = "undirected"
    relation_filters: list[str] = Field(default_factory=list)
    graph_time_start: datetime | None = None
    graph_time_end: datetime | None = None
    semantic_time_start: datetime | None = None
    semantic_time_end: datetime | None = None
    wiki_limit: int = Field(default=10, ge=0)
    evidence_limit: int = Field(default=10, ge=0)
    graph_depth: int = Field(default=1, ge=1, le=3)
    max_hits: int = Field(default=12, ge=1)
    max_chars: int = Field(default=4000, ge=200)


class ParsedRetrievalQuery(KnowledgeBaseModel):
    raw_query: str
    vector_query: str
    strong_identifiers: list[str] = Field(default_factory=list)
    has_strong_identifiers: bool = False


class BudgetUsage(KnowledgeBaseModel):
    max_chars: int
    used_chars: int
    truncated: bool = False


class AnswerContext(KnowledgeBaseModel):
    query: str
    intent: str = "general"
    hits: list[RetrievalHit] = Field(default_factory=list)
    matched_nodes: list[CompiledNode] = Field(default_factory=list)
    matched_edges: list[CompiledEdge] = Field(default_factory=list)
    evidence_chunks: list[RetrievalHit] = Field(default_factory=list)
    low_confidence_items: list[RetrievalHit] = Field(default_factory=list)
    budget_usage: BudgetUsage
    trace: RetrievalTrace = Field(default_factory=RetrievalTrace)


class SemanticHybridRetriever:
    enabled: bool = False
    backend_name: str = "none"

    async def search(self, query: str, options: RetrievalOptions) -> list[RetrievalHit]:
        return []

    async def get_by_ids(self, target_ids: list[str], options: RetrievalOptions) -> list[RetrievalHit]:
        return []


class HybridRetrievalRuntime:
    def __init__(
        self,
        repository: KnowledgeRepository,
        semantic_retriever: SemanticHybridRetriever | None = None,
    ):
        self.repository = repository
        self.semantic_retriever = semantic_retriever or SemanticHybridRetriever()

    def entity_resolve(
        self,
        query: str,
        options: RetrievalOptions,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        terms = _terms(query)
        if not terms:
            return []
        hits: list[RetrievalHit] = []
        for node in self.repository.list_nodes(options.adapter_name):
            haystack = _node_text(node)
            matched = sum(1 for term in terms if term in haystack)
            if matched:
                hits.append(_node_hit(node, score=float(matched), source="entity_resolve"))
        return sorted(hits, key=lambda hit: (-hit.score, hit.hit_id))[: limit or options.keyword_limit]

    def keyword_search(
        self,
        query: str,
        options: RetrievalOptions,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        return self.pg_deterministic_search(query, options, limit=limit)

    def pg_deterministic_search(
        self,
        query: str,
        options: RetrievalOptions,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        """Search canonical KG facts directly from PG-backed facts.

        This replaces retrieval-document keyword search as the deterministic
        first-stage entry. It is intentionally broad: exact identifiers, codes,
        source ids, node names, aliases, edge context, and evidence text can all
        produce candidates. Semantic understanding stays in the vector branch.
        """

        max_hits = limit or options.keyword_limit
        if max_hits <= 0:
            return []
        parsed = parse_retrieval_query(query)
        terms = _terms(query)
        identifiers = [item.lower() for item in parsed.strong_identifiers]
        if not terms and not identifiers:
            return []

        node_by_id = {
            node.node_id: node
            for node in self.repository.list_nodes(options.adapter_name)
            if node.status in _RETRIEVABLE_NODE_STATUSES
        }
        evidence_by_id = {
            evidence.evidence_id: evidence
            for evidence in _list_evidence(self.repository, options.adapter_name)
            if evidence.status == EvidenceStatus.ACTIVE
        }
        chunks_by_evidence = _chunks_by_evidence(self.repository, options.adapter_name)
        hits: list[RetrievalHit] = []

        for node in node_by_id.values():
            score, matched_terms, matched_fields = _deterministic_match(
                query_terms=terms,
                identifiers=identifiers,
                field_values={
                    "node_id": [node.node_id],
                    "canonical_name": [node.canonical_name],
                    "aliases": node.aliases,
                    "external_ids": list(node.external_ids.values()),
                    "properties": [_json_text(node.properties)],
                    "node_type": [node.node_type],
                },
                field_weights={
                    "node_id": 30.0,
                    "canonical_name": 8.0,
                    "aliases": 8.0,
                    "external_ids": 25.0,
                    "node_type": 2.0,
                    "properties": 0.8,
                },
                identifier_weights={
                    "node_id": 35.0,
                    "external_ids": 35.0,
                    "canonical_name": 20.0,
                    "aliases": 20.0,
                },
            )
            if score <= 0:
                continue
            hits.append(
                _node_hit(node, score=score, source="pg_deterministic").model_copy(
                    update={
                        "matched_terms": matched_terms,
                        "matched_fields": matched_fields,
                    }
                )
            )

        for evidence in evidence_by_id.values():
            score, matched_terms, matched_fields = _deterministic_match(
                query_terms=terms,
                identifiers=identifiers,
                field_values={
                    "evidence_id": [evidence.evidence_id],
                    "source_ref": [f"{evidence.source_type}:{evidence.source_id}"],
                    "source_id": [evidence.source_id],
                    "content": [_evidence_text(evidence)],
                    "payload": [_json_text(evidence.payload)],
                },
                field_weights={
                    "evidence_id": 30.0,
                    "source_ref": 30.0,
                    "source_id": 25.0,
                    "content": 0.35,
                    "payload": 0.2,
                },
                identifier_weights={
                    "evidence_id": 40.0,
                    "source_ref": 40.0,
                    "source_id": 35.0,
                },
            )
            if score <= 0:
                continue
            hits.append(
                _evidence_hit(evidence, source="pg_deterministic").model_copy(
                    update={
                        "score": score,
                        "matched_terms": matched_terms,
                        "matched_fields": matched_fields,
                    }
                )
            )

        list_edges = getattr(self.repository, "list_edges", None)
        edges = list_edges(options.adapter_name) if callable(list_edges) else []
        for edge in edges:
            if edge.status not in _RETRIEVABLE_EDGE_STATUSES:
                continue
            score, matched_terms, matched_fields = _deterministic_match(
                query_terms=terms,
                identifiers=identifiers,
                field_values={
                    "edge_id": [edge.edge_id],
                    "relation_type": [edge.relation_type],
                    "source_node": [_node_text(node_by_id[edge.source_node_id])]
                    if edge.source_node_id in node_by_id
                    else [edge.source_node_id],
                    "target_node": [_node_text(node_by_id[edge.target_node_id])]
                    if edge.target_node_id in node_by_id
                    else [edge.target_node_id],
                    "evidence": [
                        _evidence_text(evidence_by_id[evidence_id])
                        for evidence_id in edge.evidence_ids
                        if evidence_id in evidence_by_id
                    ],
                    "properties": [_json_text(edge.properties)],
                },
                field_weights={
                    "edge_id": 30.0,
                    "relation_type": 2.0,
                    "source_node": 5.0,
                    "target_node": 5.0,
                    "evidence": 0.25,
                    "properties": 0.5,
                },
                identifier_weights={
                    "edge_id": 40.0,
                    "source_node": 25.0,
                    "target_node": 25.0,
                    "evidence": 8.0,
                },
            )
            if score <= 0:
                continue
            score += _edge_relation_intent_score(edge, query=query, query_terms=terms)
            score += min(max(float(edge.confidence_score or 0.0), 0.0), 1.0)
            hits.append(
                _edge_hit(edge, source="pg_deterministic").model_copy(
                    update={
                        "score": max(score, float(edge.confidence_score or 0.0)),
                        "title": _edge_title(edge, node_by_id=node_by_id),
                        "snippet": _edge_snippet(
                            edge,
                            node_by_id=node_by_id,
                            evidence_by_id=evidence_by_id,
                            chunks_by_evidence=chunks_by_evidence,
                        ),
                        "matched_terms": matched_terms,
                        "matched_fields": matched_fields,
                    }
                )
            )

        return sorted(hits, key=lambda hit: (-hit.score, hit.hit_id))[:max_hits]

    async def semantic_hybrid_search(
        self,
        query: str,
        options: RetrievalOptions,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        if (limit if limit is not None else options.semantic_hybrid_limit) <= 0:
            return []
        if not getattr(self.semantic_retriever, "enabled", False):
            raise RuntimeError("Milvus semantic_hybrid_search is required but unavailable")
        with profile_span("semantic_hybrid.retriever_search", query=query):
            hits = await self.semantic_retriever.search(query, options)
        with profile_span("semantic_hybrid.active_evidence_filter", raw_hits=len(hits)):
            return self._active_evidence_hits(hits, options.adapter_name)[: limit or options.semantic_hybrid_limit]

    def graph_search(
        self,
        seed_nodes: list[str],
        options: RetrievalOptions,
        depth: int | None = None,
        limit: int | None = None,
        direction: GraphDirection | None = None,
        relation_filters: list[str] | None = None,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
    ) -> list[RetrievalHit]:
        max_depth = depth or options.graph_depth
        max_hits = limit or options.graph_limit
        graph_direction = direction or options.graph_direction
        allowed_relations = set(relation_filters or options.relation_filters)
        graph_time_start = time_start or options.graph_time_start
        graph_time_end = time_end or options.graph_time_end
        if not seed_nodes or max_hits <= 0:
            return []

        edges = self.repository.list_edges(options.adapter_name)
        if graph_direction == "path":
            return _graph_path_hits(
                seed_nodes=seed_nodes,
                edges=edges,
                max_depth=max_depth,
                max_hits=max_hits,
                allowed_relations=allowed_relations,
                time_start=graph_time_start,
                time_end=graph_time_end,
            )

        edges_by_node: dict[str, list[CompiledEdge]] = {}
        for edge in edges:
            if edge.status not in _RETRIEVABLE_EDGE_STATUSES:
                continue
            if allowed_relations and edge.relation_type not in allowed_relations:
                continue
            if not _edge_overlaps_time_window(
                edge,
                start=graph_time_start,
                end=graph_time_end,
            ):
                continue
            if graph_direction in {"outgoing", "undirected", "path"}:
                edges_by_node.setdefault(edge.source_node_id, []).append(edge)
            if graph_direction in {"incoming", "undirected", "path"}:
                edges_by_node.setdefault(edge.target_node_id, []).append(edge)

        hits: list[RetrievalHit] = []
        seen_edges: set[str] = set()
        queue = deque((node_id, 0) for node_id in seed_nodes)
        seen_nodes = set(seed_nodes)
        while queue and len(hits) < max_hits:
            node_id, distance = queue.popleft()
            if distance >= max_depth:
                continue
            for edge in sorted(edges_by_node.get(node_id, []), key=lambda item: item.edge_id):
                if edge.edge_id not in seen_edges:
                    hits.append(_edge_hit(edge, source="graph"))
                    seen_edges.add(edge.edge_id)
                next_node_id = _next_graph_node(edge, node_id, graph_direction)
                if next_node_id is None:
                    continue
                if next_node_id not in seen_nodes:
                    seen_nodes.add(next_node_id)
                    queue.append((next_node_id, distance + 1))
                if len(hits) >= max_hits:
                    break
        return hits

    def chunk_read(
        self,
        evidence_ids: list[str],
        options: RetrievalOptions,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        chunks_by_evidence = _chunks_by_evidence(self.repository, options.adapter_name)
        for evidence_id in evidence_ids:
            chunks = chunks_by_evidence.get(evidence_id) or []
            if chunks:
                for chunk in chunks:
                    hits.append(_evidence_chunk_hit(chunk, repository=self.repository))
                    if len(hits) >= (limit or options.evidence_limit):
                        return hits
                continue
            evidence = self.repository.get_evidence(evidence_id)
            if evidence is not None:
                hits.append(_evidence_hit(evidence, source="chunk"))
                if len(hits) >= (limit or options.evidence_limit):
                    return hits
        return hits

    def build_answer_context(self, query: str, options: RetrievalOptions) -> AnswerContext:
        anchor = build_guarded_query_anchor(
            query,
            known_nodes=self.repository.list_nodes(options.adapter_name),
        )
        entity_raw = self.entity_resolve(query, options)
        entity_judgements = judge_hits(anchor, entity_raw)
        entity_hits = filter_hits_by_judgement(entity_raw, entity_judgements)
        keyword_raw = self.keyword_search(query, options)
        seed_nodes = _ordered_unique(node_id for hit in [*entity_hits, *keyword_raw] for node_id in hit.node_refs)
        graph_raw = self.graph_search(seed_nodes, options)
        semantic_hits: list[RetrievalHit] = []
        pre_evidence_hits = [*entity_hits, *keyword_raw, *graph_raw, *semantic_hits]
        pre_judgements = judge_hits(anchor, pre_evidence_hits)
        judged_pre_hits = filter_hits_by_judgement(pre_evidence_hits, pre_judgements)
        expandable_pre_hits = _expandable_hits(pre_evidence_hits, pre_judgements)
        keyword_hits = [hit for hit in judged_pre_hits if hit.source == "keyword"]
        graph_hits = [hit for hit in expandable_pre_hits if hit.source == "graph"]
        evidence_ids = _ordered_unique(
            evidence_id
            for hit in expandable_pre_hits
            for evidence_id in hit.evidence_refs
        )
        evidence_raw = _inherit_evidence_scores(
            self.chunk_read(evidence_ids, options),
            expandable_pre_hits,
        )
        evidence_judgements = _keep_parent_backed_evidence_judgements(
            judge_hits(anchor, evidence_raw)
        )
        evidence_hits = filter_hits_by_judgement(
            evidence_raw,
            evidence_judgements,
            include_weak=False,
        )
        _log_retrieval_channels(
            query=query,
            options=options,
            entity_hits=entity_hits,
            keyword_hits=keyword_hits,
            graph_hits=graph_hits,
            semantic_hits=semantic_hits,
            evidence_hits=evidence_hits,
        )
        fused = reciprocal_rank_fusion(
            [entity_hits, keyword_hits, graph_hits, semantic_hits, evidence_hits]
        )
        all_judgements = [*entity_judgements, *pre_judgements, *evidence_judgements]
        metrics = retrieval_quality_metrics(
            anchor=anchor,
            hits=[*judged_pre_hits, *evidence_hits],
            judgements=all_judgements,
        )
        trace = _trace_for(
            options=options,
            entity_hits=entity_hits,
            keyword_hits=keyword_hits,
            graph_hits=graph_hits,
            semantic_hits=semantic_hits,
            evidence_hits=evidence_hits,
            semantic_retriever=self.semantic_retriever,
        )
        trace = _trace_with_judgement(
            trace,
            anchor=anchor,
            judgements=all_judgements,
            metrics=metrics,
        )
        if options.semantic_hybrid_limit > 0:
            trace.warnings.append("semantic_hybrid_search requires async build_answer_context_async")
        selected, usage = apply_context_budget(dedupe_hits(fused), options)
        selected = _filter_selected_hits_by_anchor(
            selected,
            anchor,
            self.repository,
            options.adapter_name,
        )
        low_confidence = [
            hit
            for hit in selected
            if hit.confidence in {ConfidenceLabel.AMBIGUOUS, ConfidenceLabel.REJECTED}
        ]
        hard_hits = [hit for hit in selected if hit not in low_confidence]
        matched_edges = self._load_edges(hard_hits, options.adapter_name, anchor=anchor)
        _log_selected_hits(query=query, options=options, trace=trace, hits=hard_hits)
        return AnswerContext(
            query=query,
            hits=hard_hits,
            matched_nodes=self._load_nodes(hard_hits, matched_edges),
            matched_edges=matched_edges,
            evidence_chunks=[hit for hit in hard_hits if hit.hit_type == "evidence"],
            low_confidence_items=low_confidence,
            budget_usage=usage,
            trace=trace,
        )

    async def build_answer_context_async(
        self,
        query: str,
        options: RetrievalOptions,
    ) -> AnswerContext:
        anchor = build_guarded_query_anchor(
            query,
            known_nodes=self.repository.list_nodes(options.adapter_name),
        )
        entity_raw = self.entity_resolve(query, options)
        entity_judgements = judge_hits(anchor, entity_raw)
        entity_hits = filter_hits_by_judgement(entity_raw, entity_judgements)
        keyword_raw = self.keyword_search(query, options)
        seed_nodes = _ordered_unique(node_id for hit in [*entity_hits, *keyword_raw] for node_id in hit.node_refs)
        graph_raw = self.graph_search(seed_nodes, options)
        semantic_raw = await self.semantic_hybrid_search(query, options)
        pre_evidence_hits = [*entity_hits, *keyword_raw, *graph_raw, *semantic_raw]
        pre_judgements = judge_hits(anchor, pre_evidence_hits)
        judged_pre_hits = filter_hits_by_judgement(pre_evidence_hits, pre_judgements)
        expandable_pre_hits = _expandable_hits(pre_evidence_hits, pre_judgements)
        keyword_hits = [hit for hit in judged_pre_hits if hit.source == "keyword"]
        graph_hits = [hit for hit in expandable_pre_hits if hit.source == "graph"]
        semantic_hits = [hit for hit in judged_pre_hits if hit.source == "semantic_hybrid"]
        evidence_ids = _ordered_unique(
            evidence_id
            for hit in expandable_pre_hits
            for evidence_id in hit.evidence_refs
        )
        evidence_raw = _inherit_evidence_scores(
            self.chunk_read(evidence_ids, options),
            expandable_pre_hits,
        )
        evidence_judgements = _keep_parent_backed_evidence_judgements(
            judge_hits(anchor, evidence_raw)
        )
        evidence_hits = filter_hits_by_judgement(
            evidence_raw,
            evidence_judgements,
            include_weak=False,
        )
        _log_retrieval_channels(
            query=query,
            options=options,
            entity_hits=entity_hits,
            keyword_hits=keyword_hits,
            graph_hits=graph_hits,
            semantic_hits=semantic_hits,
            evidence_hits=evidence_hits,
        )
        fused = reciprocal_rank_fusion(
            [entity_hits, keyword_hits, semantic_hits, graph_hits, evidence_hits]
        )
        all_judgements = [*entity_judgements, *pre_judgements, *evidence_judgements]
        metrics = retrieval_quality_metrics(
            anchor=anchor,
            hits=[*judged_pre_hits, *evidence_hits],
            judgements=all_judgements,
        )
        trace = _trace_for(
            options=options,
            entity_hits=entity_hits,
            keyword_hits=keyword_hits,
            graph_hits=graph_hits,
            semantic_hits=semantic_hits,
            evidence_hits=evidence_hits,
            semantic_retriever=self.semantic_retriever,
        )
        trace = _trace_with_judgement(
            trace,
            anchor=anchor,
            judgements=all_judgements,
            metrics=metrics,
        )
        selected, usage = apply_context_budget(dedupe_hits(fused), options)
        selected = _filter_selected_hits_by_anchor(
            selected,
            anchor,
            self.repository,
            options.adapter_name,
        )
        low_confidence = [
            hit
            for hit in selected
            if hit.confidence in {ConfidenceLabel.AMBIGUOUS, ConfidenceLabel.REJECTED}
        ]
        hard_hits = [hit for hit in selected if hit not in low_confidence]
        matched_edges = self._load_edges(hard_hits, options.adapter_name, anchor=anchor)
        _log_selected_hits(query=query, options=options, trace=trace, hits=hard_hits)
        return AnswerContext(
            query=query,
            hits=hard_hits,
            matched_nodes=self._load_nodes(hard_hits, matched_edges),
            matched_edges=matched_edges,
            evidence_chunks=[hit for hit in hard_hits if hit.hit_type == "evidence"],
            low_confidence_items=low_confidence,
            budget_usage=usage,
            trace=trace,
        )

    def build_answer_context_from_hits(
        self,
        query: str,
        hits: list[RetrievalHit],
        options: RetrievalOptions,
        trace: RetrievalTrace,
        *,
        apply_judgement: bool = True,
    ) -> AnswerContext:
        with profile_span("answer_context.build_from_hits", mode=trace.mode, hits=len(hits)):
            with profile_span("answer_context.anchor", adapter=options.adapter_name):
                known_nodes = self.repository.list_nodes(options.adapter_name)
                node_by_id = {node.node_id: node for node in known_nodes}
                anchor = build_guarded_query_anchor(
                    query,
                    known_nodes=known_nodes,
                )
            with profile_span("answer_context.preload_edges", adapter=options.adapter_name):
                all_edges = self.repository.list_edges(options.adapter_name)
            with profile_span("answer_context.preload_evidence", adapter=options.adapter_name):
                list_evidence = getattr(self.repository, "list_evidence", None)
                evidence_by_id = (
                    {
                        evidence.evidence_id: evidence
                        for evidence in list_evidence(options.adapter_name)
                    }
                    if callable(list_evidence)
                    else {}
                )
            with profile_span("answer_context.judgement_reuse", hits=len(hits), enabled=apply_judgement):
                if apply_judgement:
                    judgements, should_update_trace_judgements = _context_judgements_from_trace_or_default(
                        trace,
                        anchor=anchor,
                        hits=hits,
                    )
                    judged_hits = filter_hits_by_judgement(hits, judgements)
                else:
                    judgements = []
                    should_update_trace_judgements = False
                    judged_hits = hits
            with profile_span("answer_context.metrics", hits=len(judged_hits), judgements=len(judgements)):
                metrics = retrieval_quality_metrics(
                    anchor=anchor,
                    hits=judged_hits,
                    judgements=judgements,
                )
                trace = _trace_with_judgement(
                    trace,
                    anchor=anchor,
                    judgements=judgements,
                    metrics=metrics,
                    update_candidate_judgements=should_update_trace_judgements,
                )
            with profile_span("answer_context.budget", hits=len(judged_hits)):
                selected, usage = apply_context_budget(dedupe_hits(judged_hits), options)
            with profile_span("answer_context.anchor_filter", selected=len(selected)):
                selected = _filter_selected_hits_by_anchor(
                    selected,
                    anchor,
                    self.repository,
                    options.adapter_name,
                    node_by_id=node_by_id,
                    evidence_by_id=evidence_by_id,
                    all_edges=all_edges,
                )
            low_confidence = [
                hit
                for hit in selected
                if hit.confidence in {ConfidenceLabel.AMBIGUOUS, ConfidenceLabel.REJECTED}
            ]
            hard_hits = [hit for hit in selected if hit not in low_confidence]
            with profile_span("answer_context.load_edges", hits=len(hard_hits)):
                matched_edges = self._load_edges(
                    hard_hits,
                    options.adapter_name,
                    anchor=anchor,
                    all_edges=all_edges,
                    node_by_id=node_by_id,
                    evidence_by_id=evidence_by_id,
                )
            with profile_span("answer_context.load_nodes", hits=len(hard_hits), edges=len(matched_edges)):
                matched_nodes = self._load_nodes(hard_hits, matched_edges, node_by_id=node_by_id)
            _log_selected_hits(query=query, options=options, trace=trace, hits=hard_hits)
            return AnswerContext(
                query=query,
                hits=hard_hits,
                matched_nodes=matched_nodes,
                matched_edges=matched_edges,
                evidence_chunks=[hit for hit in hard_hits if hit.hit_type == "evidence"],
                low_confidence_items=low_confidence,
                budget_usage=usage,
                trace=trace,
            )

    def _load_nodes(
        self,
        hits: list[RetrievalHit],
        edges: list[CompiledEdge] | None = None,
        *,
        node_by_id: dict[str, CompiledNode] | None = None,
    ) -> list[CompiledNode]:
        node_ids = _ordered_unique(
            [
                *(node_id for hit in hits for node_id in hit.node_refs),
                *(edge.source_node_id for edge in edges or []),
                *(edge.target_node_id for edge in edges or []),
            ]
        )
        if node_by_id is not None:
            return [node for node_id in node_ids if (node := node_by_id.get(node_id))]
        return [node for node_id in node_ids if (node := self.repository.get_node(node_id))]

    def _load_edges(
        self,
        hits: list[RetrievalHit],
        adapter_name: str,
        *,
        anchor=None,
        all_edges: list[CompiledEdge] | None = None,
        node_by_id: dict[str, CompiledNode] | None = None,
        evidence_by_id: dict[str, CompiledEvidence] | None = None,
    ) -> list[CompiledEdge]:
        evidence_ids = set(evidence_id for hit in hits for evidence_id in hit.evidence_refs)
        edges = all_edges if all_edges is not None else self.repository.list_edges(adapter_name)
        edge_by_id = {edge.edge_id: edge for edge in edges}
        edge_ids = _ordered_unique(
            [
                *(edge_id for hit in hits for edge_id in hit.edge_refs),
                *(
                    edge.edge_id
                    for edge in edges
                    if evidence_ids.intersection(edge.evidence_ids)
                ),
            ]
        )
        return [
            edge
            for edge_id in edge_ids
            if (edge := edge_by_id.get(edge_id) or self.repository.get_edge(edge_id))
            and edge.status in _RETRIEVABLE_EDGE_STATUSES
            and _edge_matches_anchor(
                edge,
                anchor,
                self.repository,
                node_by_id=node_by_id,
                evidence_by_id=evidence_by_id,
            )
        ]

    def _active_evidence_hits(self, hits: list[RetrievalHit], adapter_name: str) -> list[RetrievalHit]:
        evidence_refs = {
            evidence_id
            for hit in hits
            for evidence_id in hit.evidence_refs
        }
        active_evidence_ids: set[str] = set()
        if evidence_refs:
            try:
                active_evidence_ids = {
                    evidence.evidence_id
                    for evidence in self.repository.list_evidence(adapter_name)
                    if evidence.evidence_id in evidence_refs
                }
                active_evidence_ids.update(
                    evidence_id
                    for evidence_id in evidence_refs - active_evidence_ids
                    if self.repository.get_evidence(evidence_id) is not None
                )
            except AttributeError:
                active_evidence_ids = {
                    evidence_id
                    for evidence_id in evidence_refs
                    if self.repository.get_evidence(evidence_id) is not None
                }
        filtered: list[RetrievalHit] = []
        for hit in hits:
            if not hit.evidence_refs:
                filtered.append(hit)
                continue
            active_refs = [evidence_id for evidence_id in hit.evidence_refs if evidence_id in active_evidence_ids]
            if not active_refs:
                continue
            filtered.append(hit.model_copy(update={"evidence_refs": active_refs}))
        return filtered


def reciprocal_rank_fusion(hit_lists: list[list[RetrievalHit]], k: int = 60) -> list[RetrievalHit]:
    by_id: dict[str, RetrievalHit] = {}
    scores: dict[str, float] = {}
    for hits in hit_lists:
        for rank, hit in enumerate(hits, start=1):
            by_id.setdefault(hit.hit_id, hit)
            scores[hit.hit_id] = scores.get(hit.hit_id, 0.0) + 1.0 / (k + rank)
    fused = [hit.model_copy(update={"score": scores[hit_id]}) for hit_id, hit in by_id.items()]
    return sorted(fused, key=lambda hit: (-hit.score, hit.hit_id))


def dedupe_hits(hits: list[RetrievalHit]) -> list[RetrievalHit]:
    deduped: dict[str, RetrievalHit] = {}
    source_positions: dict[str, int] = {}
    for hit in hits:
        source = hit.source or "unknown"
        source_positions[source] = source_positions.get(source, 0) + 1
        normalized_hit = _with_channel_metadata(hit, rank=source_positions[source])
        key = _canonical_key(hit)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = normalized_hit
        else:
            deduped[key] = _merge_duplicate_hit(existing, normalized_hit)
    return sorted(deduped.values(), key=lambda hit: (-hit.score, hit.hit_id))


def _with_channel_metadata(hit: RetrievalHit, *, rank: int) -> RetrievalHit:
    source = hit.source or "unknown"
    return hit.model_copy(
        update={
            "source_channels": _ordered_unique([*(hit.source_channels or []), source]),
            "channel_ranks": {source: rank, **(hit.channel_ranks or {})},
            "raw_scores": {source: float(hit.score or 0.0), **(hit.raw_scores or {})},
        }
    )


def _merge_duplicate_hit(existing: RetrievalHit, incoming: RetrievalHit) -> RetrievalHit:
    preferred = incoming if float(incoming.score or 0.0) > float(existing.score or 0.0) else existing
    channel_ranks = dict(existing.channel_ranks or {})
    for channel, rank in (incoming.channel_ranks or {}).items():
        channel_ranks[channel] = min(int(channel_ranks.get(channel, rank)), int(rank))
    raw_scores = dict(existing.raw_scores or {})
    for channel, score in (incoming.raw_scores or {}).items():
        raw_scores[channel] = max(float(raw_scores.get(channel, score)), float(score))
    return preferred.model_copy(
        update={
            "source_channels": _ordered_unique(
                [*(existing.source_channels or [existing.source]), *(incoming.source_channels or [incoming.source])]
            ),
            "channel_ranks": channel_ranks,
            "raw_scores": raw_scores,
            "node_refs": _ordered_unique([*existing.node_refs, *incoming.node_refs]),
            "edge_refs": _ordered_unique([*existing.edge_refs, *incoming.edge_refs]),
            "evidence_refs": _ordered_unique([*existing.evidence_refs, *incoming.evidence_refs]),
            "matched_terms": _ordered_unique([*existing.matched_terms, *incoming.matched_terms]),
            "matched_fields": _ordered_unique([*existing.matched_fields, *incoming.matched_fields]),
        }
    )


_RETRIEVABLE_NODE_STATUSES = {NodeStatus.ACTIVE, NodeStatus.CANDIDATE, NodeStatus.AMBIGUOUS}
_RETRIEVABLE_EDGE_STATUSES = {EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE}


def _expandable_hits(
    hits: list[RetrievalHit],
    judgements: list[CandidateJudgement],
) -> list[RetrievalHit]:
    judgement_by_id = {item.candidate_id: item for item in judgements}
    return [
        hit
        for hit in hits
        if (judgement := judgement_by_id.get(hit.hit_id)) is None
        or judgement.decision == "keep"
        or judgement.can_expand_graph
    ]


def _filter_selected_hits_by_anchor(
    hits: list[RetrievalHit],
    anchor,
    repository: KnowledgeRepository,
    adapter_name: str,
    *,
    node_by_id: dict[str, CompiledNode] | None = None,
    evidence_by_id: dict[str, CompiledEvidence] | None = None,
    all_edges: list[CompiledEdge] | None = None,
) -> list[RetrievalHit]:
    if not _has_strong_identity_constraints(anchor):
        return hits
    edges_by_evidence: dict[str, list[CompiledEdge]] = {}
    for edge in (all_edges if all_edges is not None else repository.list_edges(adapter_name)):
        for evidence_id in edge.evidence_ids:
            edges_by_evidence.setdefault(evidence_id, []).append(edge)
    return [
        hit
        for hit in hits
        if _hit_matches_anchor_context(
            hit,
            anchor,
            repository,
            edges_by_evidence,
            node_by_id=node_by_id,
            evidence_by_id=evidence_by_id,
        )
    ]


def _hit_matches_anchor_context(
    hit: RetrievalHit,
    anchor,
    repository: KnowledgeRepository,
    edges_by_evidence: dict[str, list[CompiledEdge]],
    *,
    node_by_id: dict[str, CompiledNode] | None = None,
    evidence_by_id: dict[str, CompiledEvidence] | None = None,
) -> bool:
    text_parts = [_retrieval_hit_text(hit)]
    for node_id in hit.node_refs:
        if node := (node_by_id.get(node_id) if node_by_id is not None else repository.get_node(node_id)):
            text_parts.append(_node_text(node))
    for evidence_id in hit.evidence_refs:
        if evidence := (
            evidence_by_id.get(evidence_id)
            if evidence_by_id is not None
            else repository.get_evidence(evidence_id)
        ):
            text_parts.append(_evidence_text(evidence))
        for edge in edges_by_evidence.get(evidence_id, []):
            text_parts.append(
                _edge_context_text(
                    edge,
                    repository,
                    node_by_id=node_by_id,
                    evidence_by_id=evidence_by_id,
                )
            )
    for edge_id in hit.edge_refs:
        if edge := repository.get_edge(edge_id):
            text_parts.append(
                _edge_context_text(
                    edge,
                    repository,
                    node_by_id=node_by_id,
                    evidence_by_id=evidence_by_id,
                )
            )
    text = "\n".join(text_parts).lower()
    return _strong_identity_match(anchor, text)


def _retrieval_hit_text(hit: RetrievalHit) -> str:
    return "\n".join(
        [
            hit.hit_id,
            hit.title,
            hit.snippet,
            hit.source,
            " ".join(hit.node_refs),
            " ".join(hit.edge_refs),
            " ".join(hit.evidence_refs),
        ]
    )


def _edge_matches_anchor(
    edge: CompiledEdge,
    anchor,
    repository: KnowledgeRepository,
    *,
    node_by_id: dict[str, CompiledNode] | None = None,
    evidence_by_id: dict[str, CompiledEvidence] | None = None,
) -> bool:
    if anchor is None or not _has_strong_identity_constraints(anchor):
        return True
    return _strong_identity_match(
        anchor,
        _edge_context_text(
            edge,
            repository,
            node_by_id=node_by_id,
            evidence_by_id=evidence_by_id,
        ).lower(),
    )


def _has_strong_identity_constraints(anchor) -> bool:
    return bool(
        anchor
        and any(
            constraint.must_preserve
            and constraint.constraint_type
            in {"source_id", "evidence_id", "instrument_code", "exact_entity"}
            for constraint in anchor.guard_constraints
        )
    )


def _strong_identity_match(anchor, text: str) -> bool:
    evidence_refs = set(re.findall(r"kg_ev:[A-Za-z0-9_.:-]+", text))
    for constraint in anchor.guard_constraints:
        if not constraint.must_preserve:
            continue
        value = constraint.value.lower()
        if constraint.constraint_type == "evidence_id" and value in evidence_refs:
            return True
        if constraint.constraint_type == "source_id" and value in text:
            return True
        if constraint.constraint_type in {"instrument_code", "exact_entity"} and value in text:
            return True
    return False


def _edge_context_text(
    edge: CompiledEdge,
    repository: KnowledgeRepository,
    *,
    node_by_id: dict[str, CompiledNode] | None = None,
    evidence_by_id: dict[str, CompiledEvidence] | None = None,
) -> str:
    parts = [
        edge.edge_id,
        edge.relation_type,
        edge.source_node_id,
        edge.target_node_id,
        " ".join(edge.evidence_ids),
    ]
    for node_id in [edge.source_node_id, edge.target_node_id]:
        if node := (node_by_id.get(node_id) if node_by_id is not None else repository.get_node(node_id)):
            parts.append(_node_text(node))
    for evidence_id in edge.evidence_ids:
        if evidence := (
            evidence_by_id.get(evidence_id)
            if evidence_by_id is not None
            else repository.get_evidence(evidence_id)
        ):
            parts.append(_evidence_text(evidence))
    return "\n".join(parts)


def _evidence_text(evidence: CompiledEvidence) -> str:
    return "\n".join(
        [
            evidence.evidence_id,
            evidence.source_type,
            evidence.source_id,
            evidence.content,
            json.dumps(evidence.payload, ensure_ascii=False, sort_keys=True),
        ]
    )


def _inherit_evidence_scores(
    evidence_hits: list[RetrievalHit],
    parent_hits: list[RetrievalHit],
) -> list[RetrievalHit]:
    score_by_evidence_id: dict[str, float] = {}
    for parent in parent_hits:
        for evidence_id in parent.evidence_refs:
            score_by_evidence_id[evidence_id] = max(
                score_by_evidence_id.get(evidence_id, 0.0),
                parent.score,
            )
    return [
        hit.model_copy(
            update={
                "score": score_by_evidence_id.get(hit.evidence_refs[0], hit.score) + 1e-9
                if hit.evidence_refs
                else hit.score
            }
        )
        for hit in evidence_hits
    ]


def _log_retrieval_channels(
    *,
    query: str,
    options: RetrievalOptions,
    entity_hits: list[RetrievalHit],
    keyword_hits: list[RetrievalHit],
    graph_hits: list[RetrievalHit],
    semantic_hits: list[RetrievalHit],
    evidence_hits: list[RetrievalHit],
) -> None:
    if not logger.isEnabledFor(logging.INFO):
        return
    channels = {
        "entity_resolve": entity_hits,
        "keyword_search": keyword_hits,
        "semantic_hybrid_search": semantic_hits,
        "graph_search": graph_hits,
        "chunk_read": evidence_hits,
    }
    logger.info(
        "[kg_retrieval] adapter=%s mode=runtime query=%r channels=%s evidence_refs=%s",
        options.adapter_name,
        _clip(query, 160),
        {
            name: {
                "hits": len(hits),
                "sample": _hit_debug_sample(hits),
            }
            for name, hits in channels.items()
        },
        _ordered_unique(
            evidence_id
            for hits in channels.values()
            for hit in hits
            for evidence_id in hit.evidence_refs
        )[:10],
    )


def _log_selected_hits(
    *,
    query: str,
    options: RetrievalOptions,
    trace: RetrievalTrace,
    hits: list[RetrievalHit],
) -> None:
    if not logger.isEnabledFor(logging.INFO):
        return
    logger.info(
        "[kg_retrieval] adapter=%s mode=%s query=%r selected_hits=%d channels_used=%s sample=%s",
        options.adapter_name,
        trace.mode,
        _clip(query, 160),
        len(hits),
        trace.channels_used,
        _hit_debug_sample(hits),
    )


def _hit_debug_sample(hits: list[RetrievalHit], limit: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "id": hit.hit_id,
            "type": hit.hit_type,
            "title": _clip(hit.title, 80),
            "score": round(hit.score, 4),
            "nodes": hit.node_refs[:3],
            "edges": hit.edge_refs[:3],
            "evidence": hit.evidence_refs[:3],
        }
        for hit in hits[:limit]
    ]


def apply_context_budget(
    hits: list[RetrievalHit],
    options: RetrievalOptions,
) -> tuple[list[RetrievalHit], BudgetUsage]:
    selected: list[RetrievalHit] = []
    used = 0
    truncated = False
    for hit in hits:
        if len(selected) >= options.max_hits:
            truncated = True
            break
        cost = len(hit.title) + len(hit.snippet)
        if selected and used + cost > options.max_chars:
            truncated = True
            break
        selected.append(hit)
        used += cost
    return selected, BudgetUsage(max_chars=options.max_chars, used_chars=used, truncated=truncated)


def _trace_for(
    *,
    options: RetrievalOptions,
    entity_hits: list[RetrievalHit],
    keyword_hits: list[RetrievalHit],
    graph_hits: list[RetrievalHit],
    semantic_hits: list[RetrievalHit],
    evidence_hits: list[RetrievalHit],
    semantic_retriever: SemanticHybridRetriever,
) -> RetrievalTrace:
    steps = [
        _step("entity_resolve", entity_hits, {"adapter_name": options.adapter_name}),
        _step("keyword_search", keyword_hits, {"adapter_name": options.adapter_name}),
        _step("semantic_hybrid_search", semantic_hits, {"adapter_name": options.adapter_name}),
        _step(
            "graph_search",
            graph_hits,
            {"adapter_name": options.adapter_name, "graph_depth": options.graph_depth},
        ),
        _step("chunk_read", evidence_hits, {"adapter_name": options.adapter_name}),
    ]
    warnings: list[str] = []
    semantic_enabled = bool(getattr(semantic_retriever, "enabled", False))
    backend_name = str(getattr(semantic_retriever, "backend_name", "none"))
    return RetrievalTrace(
        mode="deterministic_plan",
        channels_enabled=[
            "entity_resolve",
            "keyword_search",
            *(
                ["semantic_hybrid_search"]
                if semantic_enabled
                else []
            ),
            "graph_search",
            "chunk_read",
        ],
        channels_used=[
            step.tool
            for step in steps
            if step.hit_count > 0
        ],
        semantic_enabled=semantic_enabled,
        milvus_enabled=semantic_enabled and backend_name == "milvus",
        planner_enabled=False,
        steps=steps,
        warnings=warnings,
    )


def _trace_with_judgement(
    trace: RetrievalTrace,
    *,
    anchor,
    judgements,
    metrics,
    update_candidate_judgements: bool = True,
) -> RetrievalTrace:
    update = {
        "query_anchor": anchor.model_dump(mode="json"),
        "retrieval_metrics": metrics.model_dump(mode="json"),
    }
    if update_candidate_judgements:
        update["candidate_judgements"] = [
            item.model_dump(mode="json") for item in judgements
        ]
    return trace.model_copy(update=update)


def _context_judgements_from_trace_or_default(
    trace: RetrievalTrace,
    *,
    anchor,
    hits,
) -> tuple[list[CandidateJudgement], bool]:
    if trace.candidate_judgements:
        judgements: list[CandidateJudgement] = []
        for item in trace.candidate_judgements:
            try:
                judgements.append(CandidateJudgement.model_validate(item))
            except Exception:  # pragma: no cover - defensive trace compatibility
                continue
        if judgements:
            return judgements, False
    return _keep_parent_backed_evidence_judgements(judge_hits(anchor, hits)), True


def _keep_parent_backed_evidence_judgements(judgements):
    """Keep evidence chunks that were read from already-kept parent candidates."""
    return [
        item.model_copy(
            update={
                "decision": "weak_keep",
                "can_expand_graph": False,
                "reason": "parent_candidate_kept",
            }
        )
        if item.decision == "drop"
        else item
        for item in judgements
    ]


def _step(tool: str, hits: list[RetrievalHit], input_: dict[str, Any]) -> RetrievalStep:
    return RetrievalStep(
        tool=tool,
        input=input_,
        output_refs=[hit.hit_id for hit in hits],
        hit_count=len(hits),
    )


_STRONG_IDENTIFIER_RE = re.compile(
    r"kg_ev:[^\s,，;；]+|kg_edge:[^\s,，;；]+|kg:[^\s,，;；]+|"
    r"ft_news:\d+|[A-Z]{1,6}:\d{2,8}|US:[A-Z.]{1,8}|\b\d{6}\b"
)


def parse_retrieval_query(query: str) -> ParsedRetrievalQuery:
    raw_query = (query or "").strip()
    identifiers = _ordered_unique(match.group(0) for match in _STRONG_IDENTIFIER_RE.finditer(raw_query))
    vector_query = _STRONG_IDENTIFIER_RE.sub(" ", raw_query)
    vector_query = re.sub(r"\s+", " ", vector_query).strip()
    if not vector_query:
        vector_query = raw_query
    return ParsedRetrievalQuery(
        raw_query=raw_query,
        vector_query=vector_query,
        strong_identifiers=identifiers,
        has_strong_identifiers=bool(identifiers),
    )


def _deterministic_match(
    *,
    query_terms: list[str],
    identifiers: list[str],
    field_values: dict[str, list[str]],
    field_weights: dict[str, float] | None = None,
    identifier_weights: dict[str, float] | None = None,
) -> tuple[float, list[str], list[str]]:
    score = 0.0
    matched_terms: list[str] = []
    matched_fields: list[str] = []
    field_weights = field_weights or {}
    identifier_weights = identifier_weights or {}
    normalized_fields = {
        field: [str(value).lower() for value in values if str(value).strip()]
        for field, values in field_values.items()
    }
    for identifier in identifiers:
        best_field = ""
        best_weight = 0.0
        for field, values in normalized_fields.items():
            if any(identifier == value or identifier in value for value in values):
                weight = float(identifier_weights.get(field, 25.0))
                if weight > best_weight:
                    best_field = field
                    best_weight = weight
        if best_field:
            score += best_weight
            matched_terms.append(identifier)
            matched_fields.append(best_field)
    for term in query_terms:
        best_field = ""
        best_weight = 0.0
        for field, values in normalized_fields.items():
            if any(term in value for value in values):
                weight = float(field_weights.get(field, 1.0))
                if weight > best_weight:
                    best_field = field
                    best_weight = weight
        if best_field:
            score += best_weight
            matched_terms.append(term)
            matched_fields.append(best_field)
    return score, _ordered_unique(matched_terms), _ordered_unique(matched_fields)


def _edge_relation_intent_score(edge: CompiledEdge, *, query: str, query_terms: list[str]) -> float:
    """Score an edge's relation type against obvious query intent.

    This is a deterministic recall prior, not a final relevance judgement.
    It keeps broad evidence matches from flattening all edges to the same score.
    """

    relation_type = edge.relation_type
    query_text = query.lower()
    terms = set(query_terms)
    involvement_terms = {
        "涉及",
        "主体",
        "行业",
        "资产",
        "提及",
        "相关",
        "哪些",
        "涉及哪",
        "哪些主",
        "些主体",
    }
    impact_terms = {
        "影响",
        "资产影响",
        "受影响",
        "风险",
        "利好",
        "利空",
        "受益",
        "负面",
        "正面",
        "拖累",
    }
    has_involvement_intent = any(term in query_text or term in terms for term in involvement_terms)
    has_impact_intent = any(term in query_text or term in terms for term in impact_terms)

    score = 0.0
    if has_involvement_intent:
        score += {
            "mentions": 6.0,
            "belongs_to": 4.0,
            "related_to": 2.0,
            "holds": 2.0,
            "affects": 1.5,
            "benefits_from": 1.5,
            "hurt_by": 1.5,
        }.get(relation_type, 0.5)
    if has_impact_intent:
        score += {
            "affects": 8.0,
            "benefits_from": 7.0,
            "hurt_by": 7.0,
            "causal_hint": 5.0,
            "holds": 3.0,
            "related_to": 2.0,
            "mentions": 1.0,
        }.get(relation_type, 0.5)
    if not has_involvement_intent and not has_impact_intent:
        score += {
            "affects": 2.0,
            "benefits_from": 2.0,
            "hurt_by": 2.0,
            "mentions": 1.5,
            "related_to": 1.0,
        }.get(relation_type, 0.5)
    direction = str(edge.properties.get("direction") or "").lower()
    if direction in {"positive", "negative"} and has_impact_intent:
        score += 1.0
    return score


def _list_evidence(repository: KnowledgeRepository, adapter_name: str) -> list[CompiledEvidence]:
    list_evidence = getattr(repository, "list_evidence", None)
    if list_evidence is None:
        return []
    return list_evidence(adapter_name)


def _chunks_by_evidence(repository: KnowledgeRepository, adapter_name: str) -> dict[str, list[EvidenceChunk]]:
    list_chunks = getattr(repository, "list_evidence_chunks", None)
    if list_chunks is None:
        return {}
    result: dict[str, list[EvidenceChunk]] = {}
    for chunk in list_chunks(adapter_name):
        result.setdefault(chunk.evidence_id, []).append(chunk)
    for chunks in result.values():
        chunks.sort(key=lambda item: item.chunk_id)
    return result


def _json_text(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _node_hit(node: CompiledNode, *, score: float, source: str) -> RetrievalHit:
    return RetrievalHit(
        hit_id=node.node_id,
        hit_type="node",
        title=node.canonical_name,
        snippet=_clip(_node_snippet(node), 500),
        score=score,
        source=source,
        node_refs=[node.node_id],
    )


def _edge_hit(edge: CompiledEdge, *, source: str) -> RetrievalHit:
    return RetrievalHit(
        hit_id=edge.edge_id,
        hit_type="edge",
        title=edge.relation_type,
        snippet=f"{edge.source_node_id} -> {edge.target_node_id}",
        score=edge.confidence_score,
        source=source,
        node_refs=[edge.source_node_id, edge.target_node_id],
        edge_refs=[edge.edge_id],
        evidence_refs=edge.evidence_ids,
        confidence=edge.confidence_label,
        consumption_scope="context" if edge.status == EdgeStatus.ACTIVE else "review",
    )


def _edge_title(edge: CompiledEdge, *, node_by_id: dict[str, CompiledNode]) -> str:
    source = node_by_id.get(edge.source_node_id)
    target = node_by_id.get(edge.target_node_id)
    source_name = source.canonical_name if source is not None else edge.source_node_id
    target_name = target.canonical_name if target is not None else edge.target_node_id
    return f"{source_name} {edge.relation_type} {target_name}"


def _edge_snippet(
    edge: CompiledEdge,
    *,
    node_by_id: dict[str, CompiledNode],
    evidence_by_id: dict[str, CompiledEvidence],
    chunks_by_evidence: dict[str, list[EvidenceChunk]] | None = None,
) -> str:
    source = node_by_id.get(edge.source_node_id)
    target = node_by_id.get(edge.target_node_id)
    source_name = source.canonical_name if source is not None else edge.source_node_id
    target_name = target.canonical_name if target is not None else edge.target_node_id
    source_type = source.node_type if source is not None else "unknown"
    target_type = target.node_type if target is not None else "unknown"
    relation_line = (
        f"关系事实: {source_name}（{source_type}） "
        f"--{edge.relation_type}--> {target_name}（{target_type}）"
    )
    focus_line = f"关系焦点: {target_name}；焦点类型: {_node_role_label(target_type)}"
    property_summary = _edge_property_summary(edge.properties)
    evidence_parts: list[str] = []
    for evidence_id in edge.evidence_ids[:1]:
        chunks = (chunks_by_evidence or {}).get(evidence_id) or []
        if chunks:
            evidence_parts.append(_edge_chunk_summary(chunks[0]))
        elif evidence_id in evidence_by_id:
            evidence_parts.append(_edge_evidence_summary(evidence_by_id[evidence_id]))
    parts = [relation_line, focus_line, property_summary, *evidence_parts]
    return _clip("\n".join(part for part in parts if part), 800)


def _node_snippet(node: CompiledNode) -> str:
    parts = [
        f"节点事实: {node.canonical_name}",
        f"节点类型: {node.node_type}",
    ]
    aliases = _ordered_unique([item for item in [*node.aliases, *node.external_ids.values()] if item])
    if aliases:
        parts.append(f"别名/标识: {', '.join(aliases[:8])}")
    readable_properties = _readable_properties(node.properties)
    if readable_properties:
        parts.append(f"属性摘要: {readable_properties}")
    return "；".join(parts)


def _readable_properties(properties: dict[str, Any]) -> str:
    values: list[str] = []
    for key, value in sorted((properties or {}).items()):
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list)):
            text = _json_text(value)
        else:
            text = str(value)
        values.append(f"{key}={text}")
        if len(values) >= 6:
            break
    return "；".join(values)


def _node_role_label(node_type: str) -> str:
    return node_type or "未知"


def _edge_property_summary(properties: dict[str, Any]) -> str:
    if not properties:
        return ""
    values: list[str] = []
    for key in ["direction", "reason", "summary", "impact", "stance"]:
        value = properties.get(key)
        if value in (None, "", [], {}):
            continue
        values.append(f"{key}={value}")
    if not values:
        return ""
    return f"关系属性: {'；'.join(values[:4])}"


def _edge_evidence_summary(evidence: CompiledEvidence) -> str:
    text = " ".join(str(evidence.content or "").split())
    if not text:
        text = " ".join(_evidence_text(evidence).split())
    return f"证据摘要: {_clip(text, 220)}"


def _edge_chunk_summary(chunk: EvidenceChunk) -> str:
    text = " ".join(str(chunk.content or "").split())
    return f"证据分片: {_clip(text, 220)}"


def _path_hit(
    *,
    path_nodes: list[str],
    path_edges: list[CompiledEdge],
) -> RetrievalHit:
    path_edge_ids = [edge.edge_id for edge in path_edges]
    return RetrievalHit(
        hit_id="kg_path:" + ":".join(path_edge_ids),
        hit_type="path",
        title=f"path depth {len(path_edges)}",
        snippet=_path_snippet(path_nodes, path_edges),
        score=sum(edge.confidence_score for edge in path_edges) / max(len(path_edges), 1),
        source="graph",
        node_refs=path_nodes,
        edge_refs=path_edge_ids,
        evidence_refs=_ordered_unique(
            evidence_id for edge in path_edges for evidence_id in edge.evidence_ids
        ),
        path_node_refs=path_nodes,
        path_edge_refs=path_edge_ids,
        confidence=(
            ConfidenceLabel.EXTRACTED
            if any(edge.confidence_label == ConfidenceLabel.EXTRACTED for edge in path_edges)
            else path_edges[-1].confidence_label
        ),
        consumption_scope=(
            "context"
            if all(edge.status == EdgeStatus.ACTIVE for edge in path_edges)
            else "review"
        ),
    )


def _graph_path_hits(
    *,
    seed_nodes: list[str],
    edges: list[CompiledEdge],
    max_depth: int,
    max_hits: int,
    allowed_relations: set[str],
    time_start: datetime | None,
    time_end: datetime | None,
) -> list[RetrievalHit]:
    edges_by_node: dict[str, list[CompiledEdge]] = {}
    for edge in edges:
        if edge.status not in _RETRIEVABLE_EDGE_STATUSES:
            continue
        if allowed_relations and edge.relation_type not in allowed_relations:
            continue
        if not _edge_overlaps_time_window(edge, start=time_start, end=time_end):
            continue
        edges_by_node.setdefault(edge.source_node_id, []).append(edge)
        edges_by_node.setdefault(edge.target_node_id, []).append(edge)

    hits: list[RetrievalHit] = []
    seen_paths: set[tuple[str, ...]] = set()
    queue = deque((node_id, 0, [node_id], []) for node_id in seed_nodes)
    while queue and len(hits) < max_hits:
        node_id, distance, path_nodes, path_edges = queue.popleft()
        if distance >= max_depth:
            continue
        for edge in sorted(edges_by_node.get(node_id, []), key=lambda item: item.edge_id):
            next_node_id = _next_graph_node(edge, node_id, "undirected")
            if next_node_id is None or next_node_id in path_nodes:
                continue
            next_path_nodes = [*path_nodes, next_node_id]
            next_path_edges = [*path_edges, edge]
            path_key = tuple(edge.edge_id for edge in next_path_edges)
            if path_key not in seen_paths:
                hits.append(_path_hit(path_nodes=next_path_nodes, path_edges=next_path_edges))
                seen_paths.add(path_key)
            if len(hits) >= max_hits:
                break
            queue.append((next_node_id, distance + 1, next_path_nodes, next_path_edges))
    return hits


def _path_snippet(path_nodes: list[str], path_edges: list[CompiledEdge]) -> str:
    parts: list[str] = [path_nodes[0]]
    for index, edge in enumerate(path_edges):
        parts.append(f"-[{edge.relation_type}]-")
        parts.append(path_nodes[index + 1])
    return " ".join(parts)


def _next_graph_node(
    edge: CompiledEdge,
    node_id: str,
    direction: GraphDirection,
) -> str | None:
    if direction == "outgoing":
        return edge.target_node_id if edge.source_node_id == node_id else None
    if direction == "incoming":
        return edge.source_node_id if edge.target_node_id == node_id else None
    if edge.source_node_id == node_id:
        return edge.target_node_id
    if edge.target_node_id == node_id:
        return edge.source_node_id
    return None


def _edge_overlaps_time_window(
    edge: CompiledEdge,
    *,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if start is None and end is None:
        return True
    if edge.valid_from is not None and end is not None and edge.valid_from > end:
        return False
    if edge.valid_to is not None and start is not None and edge.valid_to < start:
        return False
    return True


def _evidence_hit(evidence: CompiledEvidence, *, source: str) -> RetrievalHit:
    return RetrievalHit(
        hit_id=evidence.evidence_id,
        hit_type="evidence",
        title=f"{evidence.source_type}:{evidence.source_id}",
        snippet=_clip(_evidence_text(evidence), 800),
        score=1.0,
        source=source,
        evidence_refs=[evidence.evidence_id],
    )


def _evidence_chunk_hit(chunk: EvidenceChunk, *, repository: KnowledgeRepository) -> RetrievalHit:
    evidence = repository.get_evidence(chunk.evidence_id)
    title = f"evidence:{chunk.evidence_id}"
    if evidence is not None:
        title = f"{evidence.source_type}:{evidence.source_id}"
    return RetrievalHit(
        hit_id=chunk.chunk_id,
        hit_type="evidence",
        title=title,
        snippet=_clip(chunk.content, 800),
        score=1.0,
        source="chunk",
        evidence_refs=[chunk.evidence_id],
        matched_fields=["kg_evidence_chunks.manifest", "kg_evidence.content"],
    )


def _canonical_key(hit: RetrievalHit) -> str:
    if hit.hit_type == "edge" and hit.edge_refs:
        return f"edge:{hit.edge_refs[0]}"
    if hit.hit_type in {"evidence", "semantic_hybrid"} and hit.hit_id.startswith("kg_chunk:"):
        return f"chunk:{hit.hit_id}"
    if hit.hit_type in {"evidence", "semantic_hybrid"} and hit.evidence_refs:
        return f"evidence:{hit.evidence_refs[0]}"
    if hit.hit_type == "node" and hit.node_refs:
        return f"node:{hit.node_refs[0]}"
    return f"{hit.hit_type}:{hit.hit_id}"


def _terms(query: str) -> list[str]:
    raw_terms = [item.lower() for item in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", query) if item.strip()]
    terms: list[str] = []
    for term in raw_terms:
        if _is_cjk(term):
            terms.extend(_cjk_terms(term))
        else:
            terms.append(term)
    return _ordered_unique(term for term in terms if len(term) >= 2)


def _is_cjk(value: str) -> bool:
    return bool(value) and all("\u4e00" <= char <= "\u9fff" for char in value)


def _cjk_terms(value: str) -> list[str]:
    if len(value) <= 4:
        return [value]
    result: list[str] = []
    # Prefer longer terms first so domain phrases such as "并购重组" rank above
    # generic bigrams such as "哪些".
    for size in (4, 3, 2):
        result.extend(value[idx : idx + size] for idx in range(0, len(value) - size + 1))
    return result


def _node_text(node: CompiledNode) -> str:
    payload = {
        "name": node.canonical_name,
        "aliases": node.aliases,
        "type": node.node_type,
        "properties": node.properties,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()


def _evidence_text(evidence: CompiledEvidence) -> str:
    if evidence.content and evidence.content.strip():
        return evidence.content
    return json.dumps(evidence.payload, ensure_ascii=False, sort_keys=True)


def _clip(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
