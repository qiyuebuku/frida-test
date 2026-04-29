"""Hybrid retrieval runtime for generated knowledge context."""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus
from src.domain.knowledge.repositories import KnowledgeRepository
from src.domain.knowledge.schemas import (
    CompiledEdge,
    CompiledEvidence,
    CompiledNode,
    KnowledgeBaseModel,
)

HitType = Literal["node", "edge", "path", "wiki", "evidence", "semantic_hybrid"]
GraphDirection = Literal["incoming", "outgoing", "undirected", "path"]


class RetrievalHit(KnowledgeBaseModel):
    hit_id: str
    hit_type: HitType
    title: str
    snippet: str
    score: float = 0.0
    source: str
    node_refs: list[str] = Field(default_factory=list)
    edge_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    path_node_refs: list[str] = Field(default_factory=list)
    path_edge_refs: list[str] = Field(default_factory=list)
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
    agentic_enabled: bool = False
    planner_enabled: bool = False
    steps: list[RetrievalStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    wiki_limit: int = Field(default=10, ge=0)
    evidence_limit: int = Field(default=10, ge=0)
    graph_depth: int = Field(default=1, ge=1, le=3)
    max_hits: int = Field(default=12, ge=1)
    max_chars: int = Field(default=4000, ge=200)


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
    wiki_pages: list[RetrievalHit] = Field(default_factory=list)
    evidence_chunks: list[RetrievalHit] = Field(default_factory=list)
    low_confidence_items: list[RetrievalHit] = Field(default_factory=list)
    budget_usage: BudgetUsage
    trace: RetrievalTrace = Field(default_factory=RetrievalTrace)


class SemanticHybridRetriever:
    enabled: bool = False
    backend_name: str = "none"

    async def search(self, query: str, options: RetrievalOptions) -> list[RetrievalHit]:
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
        """Deprecated compatibility wrapper; formal retrieval uses entity_resolve + Milvus BM25."""
        return self.entity_resolve(query, options, limit=limit)

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
        hits = await self.semantic_retriever.search(query, options)
        return hits[: limit or options.semantic_hybrid_limit]

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

    def wiki_search(
        self,
        query: str,
        options: RetrievalOptions,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        max_hits = limit or options.wiki_limit
        pages_by_id = {}
        for term in _terms(query) or [query]:
            for page in self.repository.search_wiki_pages(
                options.adapter_name,
                term,
                limit=max_hits,
            ):
                pages_by_id.setdefault(page.page_id, page)
                if len(pages_by_id) >= max_hits:
                    break
            if len(pages_by_id) >= max_hits:
                break
        pages = list(pages_by_id.values())
        return [
            RetrievalHit(
                hit_id=page.page_id,
                hit_type="wiki",
                title=page.title,
                snippet=_clip(page.summary or page.content, 500),
                score=1.0,
                source="wiki",
                node_refs=page.source_node_ids,
                edge_refs=page.source_edge_ids,
                evidence_refs=page.source_evidence_ids,
            )
            for page in pages
        ]

    def chunk_read(
        self,
        evidence_ids: list[str],
        options: RetrievalOptions,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for evidence_id in evidence_ids:
            evidence = self.repository.get_evidence(evidence_id)
            if evidence is None:
                continue
            hits.append(_evidence_hit(evidence, source="chunk"))
            if len(hits) >= (limit or options.evidence_limit):
                break
        return hits

    def build_answer_context(self, query: str, options: RetrievalOptions) -> AnswerContext:
        entity_hits = self.entity_resolve(query, options)
        seed_nodes = [node_id for hit in entity_hits for node_id in hit.node_refs]
        graph_hits = self.graph_search(seed_nodes, options)
        wiki_hits = self.wiki_search(query, options)
        semantic_hits: list[RetrievalHit] = []
        evidence_ids = _ordered_unique(
            evidence_id
            for hit in entity_hits + graph_hits + wiki_hits + semantic_hits
            for evidence_id in hit.evidence_refs
        )
        evidence_hits = _inherit_evidence_scores(
            self.chunk_read(evidence_ids, options),
            entity_hits + graph_hits + wiki_hits + semantic_hits,
        )

        fused = reciprocal_rank_fusion(
            [entity_hits, graph_hits, wiki_hits, semantic_hits, evidence_hits]
        )
        trace = _trace_for(
            options=options,
            entity_hits=entity_hits,
            graph_hits=graph_hits,
            wiki_hits=wiki_hits,
            semantic_hits=semantic_hits,
            evidence_hits=evidence_hits,
            semantic_retriever=self.semantic_retriever,
        )
        if options.semantic_hybrid_limit > 0:
            trace.warnings.append("semantic_hybrid_search requires async build_answer_context_async")
        selected, usage = apply_context_budget(dedupe_hits(fused), options)
        low_confidence = [
            hit
            for hit in selected
            if hit.confidence in {ConfidenceLabel.AMBIGUOUS, ConfidenceLabel.REJECTED}
        ]
        hard_hits = [hit for hit in selected if hit not in low_confidence]
        return AnswerContext(
            query=query,
            hits=hard_hits,
            matched_nodes=self._load_nodes(hard_hits),
            matched_edges=self._load_edges(hard_hits),
            wiki_pages=[hit for hit in hard_hits if hit.hit_type == "wiki"],
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
        entity_hits = self.entity_resolve(query, options)
        seed_nodes = [node_id for hit in entity_hits for node_id in hit.node_refs]
        graph_hits = self.graph_search(seed_nodes, options)
        wiki_hits = self.wiki_search(query, options)
        semantic_hits = await self.semantic_hybrid_search(query, options)
        evidence_ids = _ordered_unique(
            evidence_id
            for hit in entity_hits + graph_hits + wiki_hits + semantic_hits
            for evidence_id in hit.evidence_refs
        )
        evidence_hits = _inherit_evidence_scores(
            self.chunk_read(evidence_ids, options),
            entity_hits + graph_hits + wiki_hits + semantic_hits,
        )
        fused = reciprocal_rank_fusion(
            [entity_hits, semantic_hits, graph_hits, wiki_hits, evidence_hits]
        )
        trace = _trace_for(
            options=options,
            entity_hits=entity_hits,
            graph_hits=graph_hits,
            wiki_hits=wiki_hits,
            semantic_hits=semantic_hits,
            evidence_hits=evidence_hits,
            semantic_retriever=self.semantic_retriever,
        )
        selected, usage = apply_context_budget(dedupe_hits(fused), options)
        low_confidence = [
            hit
            for hit in selected
            if hit.confidence in {ConfidenceLabel.AMBIGUOUS, ConfidenceLabel.REJECTED}
        ]
        hard_hits = [hit for hit in selected if hit not in low_confidence]
        return AnswerContext(
            query=query,
            hits=hard_hits,
            matched_nodes=self._load_nodes(hard_hits),
            matched_edges=self._load_edges(hard_hits),
            wiki_pages=[hit for hit in hard_hits if hit.hit_type == "wiki"],
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
    ) -> AnswerContext:
        selected, usage = apply_context_budget(dedupe_hits(hits), options)
        low_confidence = [
            hit
            for hit in selected
            if hit.confidence in {ConfidenceLabel.AMBIGUOUS, ConfidenceLabel.REJECTED}
        ]
        hard_hits = [hit for hit in selected if hit not in low_confidence]
        return AnswerContext(
            query=query,
            hits=hard_hits,
            matched_nodes=self._load_nodes(hard_hits),
            matched_edges=self._load_edges(hard_hits),
            wiki_pages=[hit for hit in hard_hits if hit.hit_type == "wiki"],
            evidence_chunks=[hit for hit in hard_hits if hit.hit_type == "evidence"],
            low_confidence_items=low_confidence,
            budget_usage=usage,
            trace=trace,
        )

    def _load_nodes(self, hits: list[RetrievalHit]) -> list[CompiledNode]:
        node_ids = _ordered_unique(node_id for hit in hits for node_id in hit.node_refs)
        return [node for node_id in node_ids if (node := self.repository.get_node(node_id))]

    def _load_edges(self, hits: list[RetrievalHit]) -> list[CompiledEdge]:
        edge_ids = _ordered_unique(edge_id for hit in hits for edge_id in hit.edge_refs)
        return [edge for edge_id in edge_ids if (edge := self.repository.get_edge(edge_id))]


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
    for hit in hits:
        key = _canonical_key(hit)
        existing = deduped.get(key)
        if existing is None or hit.score > existing.score:
            deduped[key] = hit
    return sorted(deduped.values(), key=lambda hit: (-hit.score, hit.hit_id))


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
    graph_hits: list[RetrievalHit],
    wiki_hits: list[RetrievalHit],
    semantic_hits: list[RetrievalHit],
    evidence_hits: list[RetrievalHit],
    semantic_retriever: SemanticHybridRetriever,
) -> RetrievalTrace:
    steps = [
        _step("entity_resolve", entity_hits, {"adapter_name": options.adapter_name}),
        _step("semantic_hybrid_search", semantic_hits, {"adapter_name": options.adapter_name}),
        _step(
            "graph_search",
            graph_hits,
            {"adapter_name": options.adapter_name, "graph_depth": options.graph_depth},
        ),
        _step("wiki_search", wiki_hits, {"adapter_name": options.adapter_name}),
        _step("chunk_read", evidence_hits, {"adapter_name": options.adapter_name}),
    ]
    warnings: list[str] = []
    semantic_enabled = bool(getattr(semantic_retriever, "enabled", False))
    backend_name = str(getattr(semantic_retriever, "backend_name", "none"))
    return RetrievalTrace(
        mode="deterministic_plan",
        channels_enabled=[
            "entity_resolve",
            *(
                ["semantic_hybrid_search"]
                if semantic_enabled
                else []
            ),
            "graph_search",
            "wiki_search",
            "chunk_read",
        ],
        channels_used=[
            step.tool
            for step in steps
            if step.hit_count > 0
        ],
        semantic_enabled=semantic_enabled,
        milvus_enabled=semantic_enabled and backend_name == "milvus",
        agentic_enabled=False,
        planner_enabled=False,
        steps=steps,
        warnings=warnings,
    )


def _step(tool: str, hits: list[RetrievalHit], input_: dict[str, Any]) -> RetrievalStep:
    return RetrievalStep(
        tool=tool,
        input=input_,
        output_refs=[hit.hit_id for hit in hits],
        hit_count=len(hits),
    )


def _node_hit(node: CompiledNode, *, score: float, source: str) -> RetrievalHit:
    return RetrievalHit(
        hit_id=node.node_id,
        hit_type="node",
        title=node.canonical_name,
        snippet=_clip(_node_text(node), 500),
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


def _canonical_key(hit: RetrievalHit) -> str:
    if hit.hit_type == "edge" and hit.edge_refs:
        return f"edge:{hit.edge_refs[0]}"
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
