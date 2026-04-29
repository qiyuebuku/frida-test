"""Tests for LLM-backed agentic retrieval strategy parsing."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.application.services import llm_agentic_retrieval_strategy as strategy_module
from src.application.services.llm_agentic_retrieval_strategy import LLMAgenticRetrievalStrategy
from src.domain.knowledge.agentic_retrieval import AgenticRetrievalConstraints


@dataclass
class _Response:
    text: str
    structured_output: dict | None


class _LLM:
    async def generate(self, request):
        assert request.metadata["task"] == "kg_agentic_retrieval"
        return _Response(
            text="",
            structured_output={
                "stop": False,
                "tool": "semantic_hybrid_search",
                "args": {"query": "宁德时代"},
            },
        )


@pytest.mark.asyncio
async def test_llm_agentic_strategy_returns_tool_call(monkeypatch) -> None:
    monkeypatch.setattr(strategy_module, "get_claude_proxy_service", lambda: _LLM())

    decision = await LLMAgenticRetrievalStrategy().next_decision(
        query="宁德时代最近受哪些事件影响",
        observations=[],
        constraints=AgenticRetrievalConstraints(),
    )

    assert decision.tool_call is not None
    assert decision.tool_call.tool == "semantic_hybrid_search"
    assert decision.tool_call.query == "宁德时代"


def test_decision_parser_defaults_query_for_query_tools() -> None:
    decision = strategy_module._decision_from_payload(
        {"stop": False, "tool": "entity_resolve", "args": {}},
        query="300750",
    )

    assert decision.tool_call is not None
    assert decision.tool_call.query == "300750"
