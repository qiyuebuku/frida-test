"""Tests for retrieval trace metadata."""

from __future__ import annotations

import pytest

from src.domain.knowledge.enums import NodeStatus
from src.domain.knowledge.retrieval import (
    HybridRetrievalRuntime,
    RetrievalOptions,
    SemanticHybridRetriever,
)
from src.domain.knowledge.schemas import CompiledNode


class _Repo:
    def list_nodes(self, adapter_name: str):
        return [
            CompiledNode(
                node_id="kg:financial:concept:ma",
                adapter_name=adapter_name,
                node_type="concept",
                canonical_name="并购重组",
                status=NodeStatus.CANDIDATE,
                version="v1",
            )
        ]

    def list_edges(self, adapter_name: str):
        return []

    def search_wiki_pages(self, adapter_name: str, query: str, limit: int = 20):
        return []

    def get_evidence(self, evidence_id: str):
        return None

    def get_node(self, node_id: str):
        return self.list_nodes("financial")[0]

    def get_edge(self, edge_id: str):
        return None


class _EnabledSemanticHybrid(SemanticHybridRetriever):
    enabled = True
    backend_name = "milvus"

    async def search(self, query: str, options: RetrievalOptions):
        return []


@pytest.mark.asyncio
async def test_semantic_hybrid_search_requires_enabled_retriever() -> None:
    with pytest.raises(RuntimeError, match="Milvus semantic_hybrid_search is required"):
        await HybridRetrievalRuntime(_Repo()).build_answer_context_async(
            "并购重组对哪些行业有影响",
            RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5),
        )


@pytest.mark.asyncio
async def test_trace_reports_enabled_milvus_channel() -> None:
    context = await HybridRetrievalRuntime(
        _Repo(),
        semantic_retriever=_EnabledSemanticHybrid(),
    ).build_answer_context_async(
        "并购重组对哪些行业有影响",
        RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5),
    )

    assert context.trace.semantic_enabled is True
    assert context.trace.milvus_enabled is True
    assert "semantic_hybrid_search" in context.trace.channels_enabled
