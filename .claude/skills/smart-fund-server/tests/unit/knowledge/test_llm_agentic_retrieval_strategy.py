"""Tests for LLM-backed agentic retrieval strategy parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from src.application.services import llm_agentic_retrieval_strategy as strategy_module
from src.application.services.llm_agentic_retrieval_strategy import LLMAgenticRetrievalStrategy
from src.domain.knowledge.agentic_retrieval import AgenticRetrievalConstraints, RetrievalWorkingSet
from src.domain.knowledge.retrieval_anchor import build_guarded_query_anchor


@dataclass
class _Response:
    text: str
    structured_output: dict | None


class _LLM:
    def __init__(self) -> None:
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        assert request.metadata["task"] == "kg_retrieval_controller"
        return _Response(
            text="",
            structured_output={
                "next_tool": "search",
                "reason": "需要先召回候选",
                "query_rewrites": ["宁德时代", "300750 事件影响"],
                "search_plan": {
                    "answer_targets": ["宁德时代近期事件"],
                    "negative_boundaries": ["无关海外军事新闻"],
                    "expected_evidence": ["事件影响证据"],
                    "relation_intents": ["impact"],
                    "stop_condition": "找到影响宁德时代的事件证据",
                },
                "expected_gain": "evidence_coverage",
                "confidence": 0.8,
            },
        )


@pytest.mark.asyncio
async def test_llm_agentic_strategy_returns_tool_call(monkeypatch) -> None:
    llm = _LLM()
    monkeypatch.setattr(strategy_module, "get_llm_gateway_service", lambda: llm)
    monkeypatch.setattr(
        strategy_module,
        "resolve_kg_llm_model",
        lambda task: "deepseek-v4-pro",
    )

    decision = await LLMAgenticRetrievalStrategy().next_decision(
        query="宁德时代最近受哪些事件影响",
        working_set=RetrievalWorkingSet(
            query_anchor=build_guarded_query_anchor("宁德时代最近受哪些事件影响")
        ),
        observations=[],
        constraints=AgenticRetrievalConstraints(),
    )

    assert decision.next_tool == "search"
    assert decision.query_rewrites == ["宁德时代", "300750 事件影响"]
    assert decision.search_plan.answer_targets == ["宁德时代近期事件"]
    assert decision.search_plan.negative_boundaries == ["无关海外军事新闻"]
    assert decision.search_plan.expected_evidence == ["事件影响证据"]
    assert decision.search_plan.relation_intents == ["impact"]
    assert llm.requests[0].model == "deepseek-v4-pro"
    assert llm.requests[0].use_cache is True
    assert llm.requests[0].prompt == ""
    assert len(llm.requests[0].messages) == 3
    stable_payload = json.loads(llm.requests[0].messages[1]["content"])
    dynamic_payload = json.loads(llm.requests[0].messages[2]["content"])
    assert "current_date" in stable_payload["runtime_time"]
    assert "Do not invent numeric dates" in stable_payload["runtime_time"]["time_rewrite_policy"]
    assert stable_payload["cache_policy"]["message_shape"] == (
        "system + stable_task_context + dynamic_observation_context"
    )
    assert "scoped_search" in stable_payload["available_tools"]
    assert "working_set" in dynamic_payload


@pytest.mark.asyncio
async def test_llm_agentic_strategy_cache_can_be_disabled(monkeypatch) -> None:
    llm = _LLM()
    monkeypatch.setattr(strategy_module, "get_llm_gateway_service", lambda: llm)
    monkeypatch.setenv("KG_RETRIEVAL_LLM_USE_CACHE", "0")

    await LLMAgenticRetrievalStrategy().next_decision(
        query="宁德时代最近受哪些事件影响",
        working_set=RetrievalWorkingSet(
            query_anchor=build_guarded_query_anchor("宁德时代最近受哪些事件影响")
        ),
        observations=[],
        constraints=AgenticRetrievalConstraints(),
    )

    assert llm.requests[0].use_cache is False


def test_decision_parser_rejects_unknown_tool() -> None:
    decision = strategy_module._decision_from_payload(
        {"next_tool": "entity_resolve", "reason": "old low-level tool", "confidence": 0.7},
    )

    assert decision.next_tool == "stop"


def test_decision_parser_accepts_scoped_search_tool() -> None:
    decision = strategy_module._decision_from_payload(
        {
            "next_tool": "scoped_search",
            "reason": "局部补齐影响链",
            "target_candidate_ids": ["kg:financial:stock:300750"],
            "query_rewrites": ["海外产能 负面事件"],
            "confidence": 0.8,
        },
    )

    assert decision.next_tool == "scoped_search"
    assert decision.target_candidate_ids == ["kg:financial:stock:300750"]
    assert decision.query_rewrites == ["海外产能 负面事件"]


def test_decision_parser_removes_model_invented_years_from_query_rewrites() -> None:
    decision = strategy_module._decision_from_payload(
        {
            "next_tool": "search",
            "reason": "search recent events",
            "query_rewrites": [
                "宁德时代 300750 最新事件 影响",
                "CATL 300750 最近新闻 事件影响",
                "宁德时代 利空 利好 消息 2025",
            ],
            "confidence": 0.8,
        },
        query="宁德时代 300750 最近受哪些事件影响",
    )

    assert decision.query_rewrites == [
        "宁德时代 300750 最新事件 影响",
        "CATL 300750 最近新闻 事件影响",
        "宁德时代 利空 利好 消息",
    ]


def test_decision_parser_keeps_explicit_user_year() -> None:
    decision = strategy_module._decision_from_payload(
        {
            "next_tool": "search",
            "reason": "search explicit year",
            "query_rewrites": ["宁德时代 2025 年度事件"],
            "confidence": 0.8,
        },
        query="宁德时代 2025 年发生了什么",
    )

    assert decision.query_rewrites == ["宁德时代 2025 年度事件"]
