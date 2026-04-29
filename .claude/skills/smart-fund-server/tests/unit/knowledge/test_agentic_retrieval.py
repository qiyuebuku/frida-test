"""Tests for the agentic retrieval controller."""

from __future__ import annotations

import pytest

from src.domain.knowledge.agentic_retrieval import (
    AgenticRetrievalConstraints,
    AgenticRetrievalController,
    AgenticRetrievalDecision,
)
from src.domain.knowledge.retrieval_tools import RetrievalToolCall, RetrievalToolRegistry

from tests.unit.knowledge.test_retrieval_tools import _registry


class _ScriptedStrategy:
    def __init__(self, decisions: list[AgenticRetrievalDecision]):
        self.decisions = decisions

    async def next_decision(self, *, query, observations, constraints):
        if observations:
            return AgenticRetrievalDecision(stop=True, stop_reason="evidence_sufficient")
        return self.decisions[0]


class _SemanticThenChunkStrategy:
    async def next_decision(self, *, query, observations, constraints):
        if not observations:
            return AgenticRetrievalDecision(
                tool_call=RetrievalToolCall(tool="semantic_hybrid_search", query=query)
            )
        if observations[-1].tool == "semantic_hybrid_search":
            return AgenticRetrievalDecision(
                tool_call=RetrievalToolCall(
                    tool="chunk_read",
                    evidence_ids=[
                        evidence_id
                        for hit in observations[-1].hits
                        for evidence_id in hit.evidence_refs
                    ],
                )
            )
        return AgenticRetrievalDecision(stop=True, stop_reason="evidence_sufficient")


@pytest.mark.asyncio
async def test_agentic_controller_records_tool_trace_and_evidence_refs() -> None:
    controller = AgenticRetrievalController(
        _registry(),
        _ScriptedStrategy(
            [
                AgenticRetrievalDecision(
                    tool_call=RetrievalToolCall(
                        tool="semantic_hybrid_search",
                        query="宁德时代 海外产能",
                    )
                )
            ]
        ),
    )

    result = await controller.run("宁德时代最近受哪些事件影响")

    assert result.trace.mode == "agentic_arag"
    assert result.trace.agentic_enabled is True
    assert result.trace.milvus_enabled is True
    assert result.trace.channels_used == ["semantic_hybrid_search"]
    assert result.evidence_refs == ["kg_ev:financial:news:1"]
    assert result.stop_reason == "evidence_sufficient"


@pytest.mark.asyncio
async def test_agentic_controller_stops_on_tool_call_budget() -> None:
    class _NeverStopStrategy:
        async def next_decision(self, *, query, observations, constraints):
            return AgenticRetrievalDecision(
                tool_call=RetrievalToolCall(tool="entity_resolve", query=query)
            )

    controller = AgenticRetrievalController(
        _registry(),
        _NeverStopStrategy(),
        AgenticRetrievalConstraints(max_turns=5, max_tool_calls=1),
    )

    result = await controller.run("宁德时代")

    assert len(result.trace.steps) == 1
    assert result.stop_reason == "max_tool_calls"
    assert result.trace.channels_used == ["entity_resolve"]


def test_agentic_controller_uses_registry_without_keyword_tool() -> None:
    registry = _registry()

    assert "keyword_search" not in RetrievalToolRegistry.available_tools
    assert "keyword_search" not in registry.available_tools


@pytest.mark.asyncio
async def test_agentic_controller_inherits_scores_for_agent_chunk_read() -> None:
    result = await AgenticRetrievalController(
        _registry(),
        _SemanticThenChunkStrategy(),
    ).run("宁德时代")

    evidence_hits = [hit for hit in result.hits if hit.hit_type == "evidence"]
    assert evidence_hits
    assert evidence_hits[0].score != 1.0
