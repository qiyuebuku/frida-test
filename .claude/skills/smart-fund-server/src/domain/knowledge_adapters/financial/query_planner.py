"""Rule-first retrieval planner for financial KG queries."""

from __future__ import annotations

import re

from src.domain.knowledge.retrieval_plan import (
    PlannedEntity,
    PlannedTimeRange,
    RelationDirection,
    RetrievalIntent,
    RetrievalPlan,
)
from src.domain.knowledge.retrieval_tools import RetrievalToolCall

_STOCK_CODE_RE = re.compile(r"(?<!\d)(?:[036]\d{5})(?!\d)")
_FUND_CODE_RE = re.compile(r"(?<!\d)(?:[0-9]{6})(?!\d)")


class FinancialQueryPlanner:
    """Small deterministic planner used for baseline/fallback and Agent hints."""

    def plan(self, query: str) -> RetrievalPlan:
        normalized = query.strip()
        entities = _extract_entities(normalized)
        intent = _intent(normalized)
        direction = _direction(intent, normalized)
        relation_filters = _relation_filters(intent)
        time_range = _time_range(normalized)
        return RetrievalPlan(
            intent=intent,
            entities=entities,
            time_range=time_range,
            relation_filters=relation_filters,
            direction=direction,
            steps=_steps(normalized, intent, direction, entities),
        )


def _extract_entities(query: str) -> list[PlannedEntity]:
    entities: list[PlannedEntity] = []
    for code in _STOCK_CODE_RE.findall(query):
        entities.append(PlannedEntity(text=code, type_hint="stock"))

    # Keep explicit Chinese entity spans simple in the first version: the resolver
    # will do canonical/alias matching against KG nodes.
    cleaned = _STOCK_CODE_RE.sub(" ", query)
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}", cleaned):
        if token in {"最近", "近期", "哪些", "什么", "影响", "事件", "行业", "方向"}:
            continue
        entities.append(PlannedEntity(text=token, type_hint=None))
    return _dedupe_entities(entities)


def _intent(query: str) -> RetrievalIntent:
    if any(term in query for term in ("低利率", "利率环境", "降息")) and any(
        term in query for term in ("利好", "受益")
    ):
        return "macro_beneficiaries"
    if any(term in query for term in ("受哪些事件影响", "受什么事件影响", "被哪些事件影响")):
        return "impact_events_for_entity"
    if any(
        term in query
        for term in (
            "影响哪些行业",
            "影响哪些方向",
            "利好哪些方向",
            "哪些行业有影响",
            "哪些方向有影响",
            "对哪些行业有影响",
            "对哪些方向有影响",
        )
    ):
        return "affected_targets_for_topic"
    if "通过什么链条" in query or "传导" in query or "路径" in query:
        return "path_explanation"
    if any(term in query for term in ("黄金", "原油", "资产")) and "影响" in query:
        return "asset_transmission"
    if any(term in query for term in ("总结", "怎么看", "主要逻辑")):
        return "research_summary"
    return "general"


def _direction(intent: RetrievalIntent, query: str) -> RelationDirection:
    if intent == "impact_events_for_entity":
        return "incoming"
    if intent in {"affected_targets_for_topic", "macro_beneficiaries"}:
        return "outgoing"
    if intent in {"asset_transmission", "path_explanation"}:
        return "path"
    if "受" in query and "影响" in query:
        return "incoming"
    if "影响" in query:
        return "outgoing"
    return "undirected"


def _relation_filters(intent: RetrievalIntent) -> list[str]:
    if intent == "impact_events_for_entity":
        return ["affects", "benefits_from", "hurt_by", "mentions"]
    if intent in {"affected_targets_for_topic", "macro_beneficiaries", "asset_transmission"}:
        return ["affects", "benefits_from", "hurt_by", "related_to", "mentions"]
    if intent == "path_explanation":
        return ["affects", "belongs_to", "related_to", "mentions", "holds"]
    return ["mentions", "related_to", "affects"]


def _time_range(query: str) -> PlannedTimeRange:
    if any(term in query for term in ("最近", "近期", "近来")):
        return PlannedTimeRange(preset="recent", days=30)
    if "本周" in query:
        return PlannedTimeRange(preset="this_week", days=7)
    if "本月" in query:
        return PlannedTimeRange(preset="this_month", days=31)
    return PlannedTimeRange()


def _steps(
    query: str,
    intent: RetrievalIntent,
    direction: RelationDirection,
    entities: list[PlannedEntity],
) -> list[RetrievalToolCall]:
    steps: list[RetrievalToolCall] = []
    if entities:
        steps.append(RetrievalToolCall(tool="entity_resolve", query=query))
    if direction in {"incoming", "outgoing", "path"} and entities:
        # seed_node_ids are filled after entity_resolve by execute_plan.
        pass
    steps.append(RetrievalToolCall(tool="semantic_hybrid_search", query=query))
    if intent in {"research_summary", "general", "affected_targets_for_topic"}:
        steps.append(RetrievalToolCall(tool="wiki_search", query=query))
    # chunk_read is filled after retrieval channels return evidence refs.
    return steps


def _dedupe_entities(entities: list[PlannedEntity]) -> list[PlannedEntity]:
    result: list[PlannedEntity] = []
    seen: set[tuple[str, str | None]] = set()
    for entity in entities:
        key = (entity.text, entity.type_hint)
        if key in seen:
            continue
        seen.add(key)
        result.append(entity)
    return result
