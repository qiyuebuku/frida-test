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
)
from src.domain.knowledge.retrieval_profile import profile_event, profile_span
from src.domain.knowledge.schemas import KnowledgeBaseModel

RetrievalToolName = Literal["search", "find", "open", "summarize", "rerank"]


class RetrievalToolCall(KnowledgeBaseModel):
    tool: RetrievalToolName
    query: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    limit: int | None = None

    @model_validator(mode="after")
    def _has_required_input(self) -> "RetrievalToolCall":
        if self.tool == "search" and not _has_text(self.query):
            raise ValueError("search requires query")
        if self.tool == "rerank" and not _has_text(self.query):
            raise ValueError("rerank requires query")
        if self.tool == "find" and (not _has_text(self.query) or not self.evidence_ids):
            raise ValueError("find requires query and evidence_ids")
        if self.tool == "open" and not self.evidence_ids and not self.candidate_ids:
            raise ValueError("open requires evidence_ids or candidate_ids")
        return self


class RetrievalToolResult(KnowledgeBaseModel):
    tool: RetrievalToolName
    hits: list[RetrievalHit] = Field(default_factory=list)
    step: RetrievalStep
    summary: str | None = None


class RetrievalToolRegistry:
    """Whitelisted high-level tools exposed to the retrieval controller."""

    available_tools: tuple[RetrievalToolName, ...] = ("search", "find", "open", "summarize")

    def __init__(self, runtime: HybridRetrievalRuntime, options: RetrievalOptions):
        self.runtime = runtime
        self.options = options
        self._last_search_diagnostics: dict[str, object] = {}

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
            elif call.tool == "find":
                hits = self._find(call.query or "", call.evidence_ids, call.limit)
                summary = None
            elif call.tool == "open":
                hits = self._open(call.evidence_ids, call.candidate_ids, call.limit)
                summary = None
            elif call.tool == "summarize":
                hits = []
                summary = "summary_requested"
            else:  # pragma: no cover - Literal and pydantic validation make this unreachable.
                raise ValueError(f"unsupported retrieval tool: {call.tool}")
        profile_event("retrieval_tool.result", tool=call.tool, hits=len(hits))
        step_input = call.model_dump(mode="json")
        if call.tool == "search" and self._last_search_diagnostics:
            step_input["search_diagnostics"] = self._last_search_diagnostics
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

    async def _search(self, query: str, limit: int | None) -> list[RetrievalHit]:
        max_hits = limit or self.options.max_hits
        lexical_limit = max(self.options.keyword_limit, max_hits)
        semantic_limit = max(self.options.semantic_hybrid_limit, max_hits)
        with profile_span("retrieval_tool.search.entity_resolve", query=query):
            entity_hits = self.runtime.entity_resolve(query, self.options, limit=self.options.keyword_limit)
        with profile_span("retrieval_tool.search.keyword_search", query=query, limit=lexical_limit):
            keyword_hits = self.runtime.keyword_search(query, self.options, limit=lexical_limit)
        seed_nodes = _ordered_unique(
            node_id
            for hit in [*entity_hits, *keyword_hits]
            for node_id in hit.node_refs
        )
        seed_nodes = _ordered_unique(
            [
                *seed_nodes,
                *_seed_nodes_from_evidence_refs(
                    self.runtime.repository,
                    self.options.adapter_name,
                    evidence_refs=[
                        evidence_id
                        for hit in keyword_hits
                        for evidence_id in hit.evidence_refs
                    ],
                ),
            ]
        )
        with profile_span("retrieval_tool.search.graph_search", seed_nodes=len(seed_nodes)):
            graph_hits = self.runtime.graph_search(
                seed_nodes,
                self.options,
                limit=self.options.graph_limit,
            )
        with profile_span("retrieval_tool.search.wiki_search", query=query):
            wiki_hits = self.runtime.wiki_search(query, self.options, limit=self.options.wiki_limit)
        semantic_hits: list[RetrievalHit] = []
        if self.options.semantic_hybrid_limit > 0 and getattr(
            self.runtime.semantic_retriever,
            "enabled",
            False,
        ):
            with profile_span(
                "retrieval_tool.search.semantic_hybrid_search",
                query=query,
                limit=semantic_limit,
            ):
                semantic_hits = await self.runtime.semantic_hybrid_search(
                    query,
                    self.options,
                    limit=semantic_limit,
                )
        raw_hits = [*entity_hits, *keyword_hits, *graph_hits, *wiki_hits, *semantic_hits]
        merged_hits = dedupe_hits(raw_hits)
        selected_hits = merged_hits[:max_hits]
        self._last_search_diagnostics = {
            "pre_dedupe_counts": {
                "entity_resolve": len(entity_hits),
                "keyword": len(keyword_hits),
                "graph": len(graph_hits),
                "wiki": len(wiki_hits),
                "semantic_hybrid": len(semantic_hits),
            },
            "post_dedupe_primary_source_counts": _source_counts(merged_hits),
            "selected_primary_source_counts": _source_counts(selected_hits),
            "merged_channel_counts": _merged_channel_counts(merged_hits),
            "selected_merged_channel_counts": _merged_channel_counts(selected_hits),
            "source_samples": _source_samples(raw_hits),
            "semantic_hybrid": {
                "enabled": bool(
                    self.options.semantic_hybrid_limit > 0
                    and getattr(self.runtime.semantic_retriever, "enabled", False)
                ),
                "configured_limit": self.options.semantic_hybrid_limit,
                "requested_limit": semantic_limit if self.options.semantic_hybrid_limit > 0 else 0,
                "returned": len(semantic_hits),
                "disabled_reason": _semantic_disabled_reason(self.runtime, self.options),
            },
            "graph_seed_nodes": len(seed_nodes),
            "max_hits": max_hits,
        }
        profile_event(
            "retrieval_tool.search.counts",
            entity=len(entity_hits),
            keyword=len(keyword_hits),
            graph=len(graph_hits),
            wiki=len(wiki_hits),
            semantic=len(semantic_hits),
            max_hits=max_hits,
        )
        return selected_hits

    def _find(self, query: str, evidence_ids: list[str], limit: int | None) -> list[RetrievalHit]:
        terms = _terms(query)
        if not terms:
            return []
        hits: list[RetrievalHit] = []
        for hit in self.runtime.chunk_read(evidence_ids, self.options, limit=limit):
            haystack = f"{hit.title}\n{hit.snippet}".lower()
            matched = sum(1 for term in terms if term in haystack)
            if matched:
                hits.append(hit.model_copy(update={"score": max(hit.score, float(matched))}))
        return sorted(hits, key=lambda item: (-item.score, item.hit_id))[: limit or self.options.evidence_limit]

    def _open(
        self,
        evidence_ids: list[str],
        candidate_ids: list[str],
        limit: int | None,
    ) -> list[RetrievalHit]:
        ids = _ordered_unique([*evidence_ids, *self._candidate_evidence_ids(candidate_ids, limit=limit)])
        if not ids:
            return []
        return self.runtime.chunk_read(ids, self.options, limit=limit)

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
            elif candidate_id.startswith("kg_wiki:"):
                for page in repository.list_wiki_pages(adapter_name):
                    if page.page_id == candidate_id:
                        evidence_ids.extend(page.source_evidence_ids)
                        break
            else:
                for edge in repository.list_edges(adapter_name):
                    if edge.source_node_id == candidate_id or edge.target_node_id == candidate_id:
                        evidence_ids.extend(edge.evidence_ids)
            evidence_ids = _ordered_unique(evidence_ids)
            if len(evidence_ids) >= max_ids:
                break
        return evidence_ids[:max_ids]


def _has_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
