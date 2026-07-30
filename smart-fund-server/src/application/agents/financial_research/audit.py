"""Service-side validation of Agent evidence references."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from src.application.agents.financial_research.context import AgentRunContext, ToolInvocation
from src.application.agents.financial_research.schemas import EvidenceCitation, FinancialResearchResult


_CARD_PATTERN = re.compile(r"kg_cognitive_card:[A-Za-z0-9:_-]+")
_EDGE_PATTERN = re.compile(r"kg_card_relation:[A-Za-z0-9:_-]+")
_COMMUNITY_PATTERN = re.compile(r"kgc:[A-Za-z0-9:_-]+")
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")

_CARD_EVIDENCE_TOOLS = {"kg_card_open", "kg_edge_open"}
_EDGE_EVIDENCE_TOOLS = {"kg_edge_open"}
_COMMUNITY_EVIDENCE_TOOLS = {"kg_community_open"}
_EXTERNAL_EVIDENCE_TOOLS = {"external_web_read", "external_content_read"}
_MARKET_EVIDENCE_TOOLS = {
    "market_instrument_open",
    "market_instrument_history",
    "market_watchlist_list",
}


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _tool_text(invocations: Iterable[ToolInvocation], names: set[str]) -> str:
    return "\n".join(
        _stringify(item.result)
        for item in invocations
        if item.name in names and item.result is not None
    )


def collect_opened_evidence(context: AgentRunContext) -> dict[str, set[str]]:
    invocations = context.tool_invocations
    card_text = _tool_text(invocations, _CARD_EVIDENCE_TOOLS)
    edge_text = _tool_text(invocations, _EDGE_EVIDENCE_TOOLS)
    community_text = _tool_text(invocations, _COMMUNITY_EVIDENCE_TOOLS)
    external_text = _tool_text(invocations, _EXTERNAL_EVIDENCE_TOOLS)
    market_calls = {
        item.name for item in invocations if item.name in _MARKET_EVIDENCE_TOOLS
    }
    return {
        "card": set(_CARD_PATTERN.findall(card_text)),
        "edge": set(_EDGE_PATTERN.findall(edge_text)),
        "community": set(_COMMUNITY_PATTERN.findall(community_text)),
        "external": set(_URL_PATTERN.findall(external_text)),
        "market": market_calls,
    }


def _validate_citation(
    citation: EvidenceCitation,
    opened: dict[str, set[str]],
) -> str | None:
    if citation.kind in {"card", "edge", "community"}:
        if citation.reference not in opened[citation.kind]:
            return (
                f"{citation.kind} evidence was not opened in this run: "
                f"{citation.reference}"
            )
        return None
    if citation.kind == "external":
        if citation.reference not in opened["external"]:
            return f"external source was not opened in this run: {citation.reference}"
        return None
    if citation.kind == "market" and not opened["market"]:
        return "market evidence was cited without calling a market read tool"
    return None


def validate_research_result(
    result: FinancialResearchResult,
    context: AgentRunContext,
) -> None:
    if result.task_mode != context.task_mode:
        raise ValueError(
            f"Agent returned task_mode={result.task_mode.value}, "
            f"expected {context.task_mode.value}"
        )

    opened = collect_opened_evidence(context)
    errors = [
        error
        for citation in result.evidence
        if (error := _validate_citation(citation, opened)) is not None
    ]
    if errors:
        raise ValueError("Agent evidence audit failed: " + "; ".join(errors))
