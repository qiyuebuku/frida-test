"""Financial source contracts and reference helpers."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from src.domain.knowledge.enums import InputType


SOURCE_INPUT_TYPES = {
    "stock_basics": InputType.STRUCTURED_RECORD,
    "industry_components": InputType.STRUCTURED_RECORD,
    "concept_components": InputType.STRUCTURED_RECORD,
    "fund_holdings": InputType.STRUCTURED_RECORD,
    "news_articles": InputType.SEMI_STRUCTURED_RECORD,
    "policy_news": InputType.SEMI_STRUCTURED_RECORD,
    "l1_events": InputType.EVENT_RECORD,
    "derived_signal": InputType.DERIVED_SIGNAL,
    "feedback_records": InputType.FEEDBACK_RECORD,
}

REQUIRED_FIELDS = {
    "stock_basics": {"code", "name", "exchange"},
    "industry_components": {"taxonomy", "component_name", "member_stock_code", "member_stock_exchange"},
    "concept_components": {"taxonomy", "component_name", "member_stock_code", "member_stock_exchange"},
    "fund_holdings": {"fund_code", "stock_code", "stock_exchange", "report_date"},
    "news_articles": {"source_id", "published_at"},
    "policy_news": {"source_id", "published_at"},
    "l1_events": {"event_id", "event_type", "event_time"},
    "derived_signal": {"target_ref", "signal_type", "observed_at", "value"},
    "feedback_records": {"target_type", "target_ref", "action", "reason"},
}


def validate_source_payload(source_type: str, payload: dict[str, Any]) -> None:
    required = REQUIRED_FIELDS.get(source_type)
    if required is None:
        raise ValueError(f"unsupported financial source_type: {source_type}")
    missing = sorted(field for field in required if field not in payload or _is_missing(payload[field]))
    if missing:
        raise ValueError(f"missing required fields for {source_type}: {missing}")


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _is_missing(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, float) and not math.isfinite(value):
        return True
    return False


def stock_key(exchange: str, code: str) -> str:
    return f"{exchange}:{code}"


def typed_ref(node_type: str, stable_key: str) -> str:
    return f"{node_type}:{stable_key}"


def entity_stable_key(entity: dict[str, Any]) -> str:
    node_type = entity["type"]
    if node_type == "stock":
        return stock_key(entity["exchange"], entity["code"])
    if node_type == "fund":
        return str(entity["fund_code"])
    if node_type in {"industry", "concept"}:
        return f"{entity.get('taxonomy', 'default')}:{entity.get('code') or entity['name']}"
    if node_type == "macro_indicator":
        return str(entity.get("indicator_code") or entity.get("code") or entity["name"])
    if node_type == "policy":
        return str(entity.get("document_id") or entity.get("source_id") or entity.get("name") or "unknown")
    if node_type == "event":
        return str(entity.get("event_id") or entity.get("source_id") or entity.get("name") or "unknown")
    return str(entity.get("id") or entity["name"])
