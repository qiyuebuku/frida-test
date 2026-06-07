"""Tests for recorded retrieval trace replay."""

from __future__ import annotations

import pytest

from src.domain.knowledge.enums import EvidenceType
from src.domain.knowledge.retrieval import (
    HybridRetrievalRuntime,
    RetrievalHit,
    RetrievalOptions,
    RetrievalStep,
    RetrievalTrace,
    SemanticHybridRetriever,
)
from src.domain.knowledge.retrieval_tools import RetrievalToolCall, RetrievalToolRegistry
from src.domain.knowledge.retrieval_trace_replay import replay_retrieval_trace
from src.domain.knowledge.schemas import CompiledEvidence, EvidenceChunk


class _Repo:
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news:1",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="ft_news:1",
        content="宁德时代受海外产能事件影响。",
        version="v1",
    )

    def list_nodes(self, adapter_name: str):
        return []

    def list_edges(self, adapter_name: str):
        return []

    def get_evidence(self, evidence_id: str):
        return self.evidence if evidence_id == self.evidence.evidence_id else None

    def list_evidence_chunks(self, adapter_name: str):
        return [
            EvidenceChunk(
                chunk_id="kg_chunk:semantic:1",
                adapter_name="financial",
                evidence_id=self.evidence.evidence_id,
                content=self.evidence.content,
            )
        ]

    def get_node(self, node_id: str):
        return None

    def get_edge(self, edge_id: str):
        return None


class _SemanticHybrid(SemanticHybridRetriever):
    enabled = True
    backend_name = "milvus"

    async def search(self, query: str, options: RetrievalOptions):
        return [
            RetrievalHit(
                hit_id="kg_chunk:semantic:1",
                hit_type="semantic_hybrid",
                title="semantic hit",
                snippet="宁德时代 海外产能",
                source="semantic_hybrid",
                evidence_refs=["kg_ev:financial:news:1"],
            )
        ]


@pytest.mark.asyncio
async def test_replay_retrieval_trace_reexecutes_recorded_tool_calls() -> None:
    search_call = RetrievalToolCall(tool="search", query="宁德时代")
    open_call = RetrievalToolCall(tool="open", evidence_ids=["kg_ev:financial:news:1"])
    recorded_trace = RetrievalTrace(
        mode="agentic_arag",
        agentic_enabled=True,
        steps=[
            RetrievalStep(
                tool="search",
                input=search_call.model_dump(mode="json"),
                output_refs=["kg_chunk:semantic:1"],
                hit_count=1,
            ),
            RetrievalStep(
                tool="open",
                input=open_call.model_dump(mode="json"),
                output_refs=["kg_chunk:semantic:1"],
                hit_count=1,
            ),
        ],
    )

    result = await replay_retrieval_trace(
        query="宁德时代",
        recorded_trace=recorded_trace,
        registry=RetrievalToolRegistry(
            HybridRetrievalRuntime(_Repo(), semantic_retriever=_SemanticHybrid()),
            RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5),
        ),
    )

    assert result.trace.mode == "agentic_arag_trace_replay"
    assert result.trace.milvus_enabled is True
    assert result.trace.channels_used == ["search", "open"]
    assert result.evidence_refs == ["kg_ev:financial:news:1"]
    assert result.mismatches == []


@pytest.mark.asyncio
async def test_replay_retrieval_trace_reports_output_mismatch() -> None:
    recorded_trace = RetrievalTrace(
        mode="agentic_arag",
        steps=[
            RetrievalStep(
                tool="search",
                input=RetrievalToolCall(
                    tool="search",
                    query="宁德时代",
                ).model_dump(mode="json"),
                output_refs=["kg_chunk:old"],
                hit_count=1,
            )
        ],
    )

    result = await replay_retrieval_trace(
        query="宁德时代",
        recorded_trace=recorded_trace,
        registry=RetrievalToolRegistry(
            HybridRetrievalRuntime(_Repo(), semantic_retriever=_SemanticHybrid()),
            RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=5),
        ),
    )

    assert len(result.mismatches) == 1
    assert result.mismatches[0].expected_output_refs == ["kg_chunk:old"]
    assert "kg_chunk:semantic:1" in result.mismatches[0].actual_output_refs
    assert result.trace.warnings == ["trace replay output mismatch: 1 step(s)"]
