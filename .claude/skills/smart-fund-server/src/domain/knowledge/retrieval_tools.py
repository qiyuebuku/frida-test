"""Tool registry for plan-based and agentic knowledge retrieval."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from src.domain.knowledge.retrieval import (
    GraphDirection,
    HybridRetrievalRuntime,
    RetrievalHit,
    RetrievalOptions,
    RetrievalStep,
)
from src.domain.knowledge.schemas import KnowledgeBaseModel

RetrievalToolName = Literal[
    "entity_resolve",
    "semantic_hybrid_search",
    "graph_search",
    "wiki_search",
    "chunk_read",
]


class RetrievalToolCall(KnowledgeBaseModel):
    tool: RetrievalToolName
    query: str | None = None
    seed_node_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    depth: int | None = None
    limit: int | None = None
    direction: GraphDirection | None = None
    relation_filters: list[str] = Field(default_factory=list)
    time_start: datetime | None = None
    time_end: datetime | None = None

    @model_validator(mode="after")
    def _has_required_input(self) -> "RetrievalToolCall":
        if self.tool in {"entity_resolve", "semantic_hybrid_search", "wiki_search"}:
            if not self.query or not self.query.strip():
                raise ValueError(f"{self.tool} requires query")
        if self.tool == "graph_search" and not self.seed_node_ids:
            raise ValueError("graph_search requires seed_node_ids")
        if self.tool == "chunk_read" and not self.evidence_ids:
            raise ValueError("chunk_read requires evidence_ids")
        return self


class RetrievalToolResult(KnowledgeBaseModel):
    tool: RetrievalToolName
    hits: list[RetrievalHit] = Field(default_factory=list)
    step: RetrievalStep


class RetrievalToolRegistry:
    """Whitelisted retrieval tools exposed to planners and future A-RAG agents."""

    available_tools: tuple[RetrievalToolName, ...] = (
        "entity_resolve",
        "semantic_hybrid_search",
        "graph_search",
        "wiki_search",
        "chunk_read",
    )

    def __init__(self, runtime: HybridRetrievalRuntime, options: RetrievalOptions):
        self.runtime = runtime
        self.options = options

    async def execute(self, call: RetrievalToolCall) -> RetrievalToolResult:
        if call.tool == "entity_resolve":
            hits = self.runtime.entity_resolve(
                call.query or "",
                self.options,
                limit=call.limit,
            )
        elif call.tool == "semantic_hybrid_search":
            hits = await self.runtime.semantic_hybrid_search(
                call.query or "",
                self.options,
                limit=call.limit,
            )
        elif call.tool == "graph_search":
            hits = self.runtime.graph_search(
                call.seed_node_ids,
                self.options,
                depth=call.depth,
                limit=call.limit,
                direction=call.direction,
                relation_filters=call.relation_filters,
                time_start=call.time_start,
                time_end=call.time_end,
            )
        elif call.tool == "wiki_search":
            hits = self.runtime.wiki_search(
                call.query or "",
                self.options,
                limit=call.limit,
            )
        elif call.tool == "chunk_read":
            hits = self.runtime.chunk_read(
                call.evidence_ids,
                self.options,
                limit=call.limit,
            )
        else:  # pragma: no cover - Literal and pydantic validation make this unreachable.
            raise ValueError(f"unsupported retrieval tool: {call.tool}")
        return RetrievalToolResult(
            tool=call.tool,
            hits=hits,
            step=RetrievalStep(
                tool=call.tool,
                input=call.model_dump(mode="json"),
                output_refs=[hit.hit_id for hit in hits],
                hit_count=len(hits),
            ),
        )
