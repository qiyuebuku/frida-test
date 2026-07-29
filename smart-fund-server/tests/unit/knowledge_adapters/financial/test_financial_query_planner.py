"""Tests for financial retrieval query planning."""

from __future__ import annotations

from src.domain.knowledge_adapters.financial.query_planner import FinancialQueryPlanner


def test_planner_parses_entity_impact_query() -> None:
    plan = FinancialQueryPlanner().plan("宁德时代 300750 最近受哪些事件影响")

    assert plan.intent == "impact_events_for_entity"
    assert plan.direction == "incoming"
    assert plan.time_range.preset == "recent"
    assert plan.time_range.days == 30
    assert "affects" in plan.relation_filters
    assert {"text": "300750", "type_hint": "stock"} in [
        entity.model_dump() for entity in plan.entities
    ]
    assert [step.tool for step in plan.steps] == ["search"]


def test_planner_parses_topic_targets_query_without_keyword_tool() -> None:
    plan = FinancialQueryPlanner().plan("并购重组对哪些行业有影响")

    assert plan.intent == "affected_targets_for_topic"
    assert plan.direction == "outgoing"
    assert "benefits_from" in plan.relation_filters
    assert [step.tool for step in plan.steps] == ["search"]


def test_planner_parses_path_explanation_query() -> None:
    plan = FinancialQueryPlanner().plan("某政策通过什么链条影响新能源车")

    assert plan.intent == "path_explanation"
    assert plan.direction == "path"
    assert "belongs_to" in plan.relation_filters
    assert "holds" in plan.relation_filters


def test_planner_parses_macro_beneficiary_query() -> None:
    plan = FinancialQueryPlanner().plan("低利率环境利好哪些方向")

    assert plan.intent == "macro_beneficiaries"
    assert plan.direction == "outgoing"
    assert [step.tool for step in plan.steps] == ["search"]
