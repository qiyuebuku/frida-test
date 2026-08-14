"""Deterministic service-side audit for Research Agent proposals."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from src.application.agents.financial_research.context import (
    AgentRunContext,
    ToolInvocation,
)
from src.application.agents.financial_research.schemas import (
    CurrentResearchReportProposal,
    EvidenceCitation,
)
from src.application.services.market_evidence_locator import (
    LOCATOR_PREFIX,
    decode_market_evidence_locator,
    normalize_market_evidence_locator,
)


_CARD_PATTERN = re.compile(r"kg_cognitive_card:[A-Za-z0-9:_-]+")
_EDGE_PATTERN = re.compile(r"kg_card_relation:[A-Za-z0-9:_-]+")
_COMMUNITY_PATTERN = re.compile(r"kgc:[A-Za-z0-9:_-]+")
_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
_EXTERNAL_HANDLE_PATTERN = re.compile(r"external_content:[A-Za-z0-9:_-]+")
_MARKET_LOCATOR_PATTERN = re.compile(r"market:v1:[A-Za-z0-9_-]+=*")
_MARKET_ALIAS_PATTERN = re.compile(r"market_ref:M[1-9][0-9]*")
_ISO_DATE_PATTERN = re.compile(r"(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)")

# Card 必须由 kg_card_open 真正打开。Edge 结果里附带的端点 Card ID
# 只能用于导航，不等于模型已经阅读 Card 正文。
_CARD_EVIDENCE_TOOLS = {"kg_card_open"}
_EDGE_EVIDENCE_TOOLS = {"kg_edge_open"}
_COMMUNITY_EVIDENCE_TOOLS = {"kg_community_open"}
_EXTERNAL_EVIDENCE_TOOLS = {"external_web_read", "external_content_read"}
_MARKET_EVIDENCE_TOOLS = {
    "market_frame_open",
    "market_change_brief_open",
    "market_premarket_context_open",
    "market_global_overview_open",
    "market_dimension_open",
    "market_topic_open",
    "market_domain_open",
    "market_evidence_open",
    "market_sector_overview",
    "market_sector_rankings",
    "market_sector_open",
    "market_sector_compare_open",
    "market_instrument_open",
    "market_instrument_realtime_open",
    "market_expression_compare_open",
    "market_instrument_history",
    "market_technical_state_open",
    "market_historical_analogue_open",
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


def collect_opened_evidence(context: AgentRunContext) -> dict[str, object]:
    invocations = context.tool_invocations
    card_text = _tool_text(invocations, _CARD_EVIDENCE_TOOLS)
    edge_text = _tool_text(invocations, _EDGE_EVIDENCE_TOOLS)
    community_text = _tool_text(invocations, _COMMUNITY_EVIDENCE_TOOLS)
    external_text = _tool_text(invocations, _EXTERNAL_EVIDENCE_TOOLS)
    market_text = _tool_text(invocations, _MARKET_EVIDENCE_TOOLS)
    market_references = {
        _normalized_reference(reference)
        for reference in _MARKET_LOCATOR_PATTERN.findall(market_text)
    }
    market_aliases = set(_MARKET_ALIAS_PATTERN.findall(market_text))
    market_references.update(market_aliases)
    # Keep both representations.  Model-facing results contain compact aliases,
    # while runtime-owned binding writes canonical reversible locators into the
    # formal proposal.  Treating only one representation as opened makes the
    # subsequent evidence-plan pruning delete evidence that came from the same
    # successful tool result.
    market_references.update(
        _normalized_reference(context.evidence_aliases[alias])
        for alias in market_aliases
        if alias in context.evidence_aliases
        and context.evidence_aliases[alias].startswith(LOCATOR_PREFIX)
    )
    # The alias table also contains locators projected from the previous report.
    # Only aliases explicitly marked by a successful market-evidence tool are
    # authoritative in the current run, matching the server-owned audit ledger.
    for alias in context.opened_market_aliases:
        reference = context.evidence_aliases.get(alias, "")
        if not reference.startswith(LOCATOR_PREFIX):
            continue
        market_references.add(alias)
        market_references.add(_normalized_reference(reference))
    context_pack = context.research_context
    input_references: set[str] = set()
    if context_pack is not None:
        input_references.add(context_pack.market_state.frame_id)
        if context_pack.current_report_revision_id:
            input_references.add(context_pack.current_report_revision_id)
        for view in context_pack.active_views:
            input_references.update({view.view_id, view.revision_id})
        input_references.update(
            item.memory_id for item in context_pack.memory_items
        )
        input_references.update(
            alias
            for alias, reference in context.evidence_aliases.items()
            if reference in input_references
        )
    return {
        "card": set(_CARD_PATTERN.findall(card_text)),
        "edge": set(_EDGE_PATTERN.findall(edge_text)),
        "community": set(_COMMUNITY_PATTERN.findall(community_text)),
        "external": {
            *_URL_PATTERN.findall(external_text),
            *_EXTERNAL_HANDLE_PATTERN.findall(external_text),
        },
        "market": market_references,
        "market_text": market_text,
        "input": input_references,
    }


def _validate_citation(
    citation: EvidenceCitation,
    opened: dict[str, object],
    *,
    cutoff_at,
) -> str | None:
    if cutoff_at is not None and citation.as_of is not None and citation.as_of > cutoff_at:
        return f"evidence is newer than cutoff_at: {citation.citation_id}"
    if cutoff_at is not None and citation.observed_at is not None and citation.observed_at > cutoff_at:
        return f"evidence was observed after cutoff_at: {citation.citation_id}"
    if citation.kind in {"card", "edge", "community"}:
        references = opened[citation.kind]
        if not isinstance(references, set) or citation.reference not in references:
            return (
                f"{citation.kind} evidence was not opened in this run: "
                f"{citation.reference}"
            )
        return None
    if citation.kind == "external":
        if citation.reference.endswith(":result:empty"):
            return (
                "empty search sentinel is not evidence; delete this citation: "
                f"{citation.reference}"
            )
        references = opened["external"]
        if not isinstance(references, set) or citation.reference not in references:
            return f"external source was not opened in this run: {citation.reference}"
        return None
    if citation.kind == "market":
        if not (
            citation.reference.startswith(LOCATOR_PREFIX)
            or _MARKET_ALIAS_PATTERN.fullmatch(citation.reference)
        ):
            return (
                "market citation must use a canonical market:v1 locator, not "
                f"a navigation handle: {citation.reference}"
            )
        references = opened["market"]
        normalized = (
            citation.reference
            if _MARKET_ALIAS_PATTERN.fullmatch(citation.reference)
            else _normalized_reference(citation.reference)
        )
        if (
            not isinstance(references, set)
            or normalized not in references
        ):
            return (
                "market evidence locator was not present in an opened "
                f"market result: {citation.reference}"
            )
        return None
    return None


def _iter_citations(
    result: CurrentResearchReportProposal,
) -> Iterable[EvidenceCitation]:
    for claim in result.claims:
        yield from claim.evidence
    for revision in result.view_revisions:
        for claim in revision.claims:
            yield from claim.evidence
        for link in revision.mechanism_chain:
            yield from link.evidence
        if revision.market_structure is not None:
            yield from revision.market_structure.evidence


def _iter_claims(result: CurrentResearchReportProposal):
    yield from result.claims
    for revision in result.view_revisions:
        yield from revision.claims


def _market_claim_date_error(
    claim,
    context: AgentRunContext,
) -> str | None:
    """Reject exact dated market claims whose citations cover other dates.

    This deliberately validates provenance rather than market semantics. A
    model may interpret a record, but it may not cite an Aug-13 locator for an
    Aug-12 number or carry an unverified baseline out of the previous report.
    """

    claimed_dates = set(_ISO_DATE_PATTERN.findall(str(claim.statement)))
    market_citations = [item for item in claim.evidence if item.kind == "market"]
    if not claimed_dates or not market_citations:
        return None
    cited_dates: set[str] = set()
    for citation in market_citations:
        reference = context.evidence_aliases.get(
            citation.reference,
            citation.reference,
        )
        if not str(reference).startswith(LOCATOR_PREFIX):
            continue
        try:
            identity = decode_market_evidence_locator(str(reference))
        except ValueError:
            continue
        trade_date = identity.identity.get("trade_date")
        if trade_date:
            cited_dates.add(str(trade_date)[:10])
        if identity.fact_time:
            cited_dates.add(str(identity.fact_time)[:10])
    missing = sorted(claimed_dates - cited_dates)
    if missing:
        return (
            f"dated market claim {claim.claim_id} has no citation for "
            + ", ".join(missing)
            + "; remove the unverified dated fact or open its exact record"
        )
    return None


def _reference_was_opened(reference: str, opened: dict[str, object]) -> bool:
    for kind in ("card", "edge", "community", "external", "input"):
        values = opened[kind]
        if isinstance(values, set) and reference in values:
            return True
    market_text = opened["market_text"]
    if reference.startswith(LOCATOR_PREFIX):
        market_references = opened["market"]
        return (
            isinstance(market_references, set)
            and _normalized_reference(reference) in market_references
        )
    if _MARKET_ALIAS_PATTERN.fullmatch(reference):
        market_references = opened["market"]
        return isinstance(market_references, set) and reference in market_references
    return isinstance(market_text, str) and reference in market_text


def _normalized_reference(reference: str) -> str:
    if not reference.startswith(LOCATOR_PREFIX):
        return reference
    try:
        return normalize_market_evidence_locator(reference)
    except ValueError:
        return reference


def validate_research_result(
    result: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> None:
    context_pack = context.research_context
    if context_pack is None:
        raise ValueError("Research Context Pack is missing from run context")
    if result.run_id != context.run_id:
        raise ValueError("Agent returned a proposal for a different run_id")
    if result.cutoff_at != context_pack.trigger.cutoff_at:
        raise ValueError("Agent proposal cutoff does not match the trigger cutoff")
    if result.source_frame_id != context_pack.market_state.frame_id:
        raise ValueError("Agent proposal references a different Market State Frame")
    if result.base_report_revision_id != context_pack.current_report_revision_id:
        raise ValueError("Agent proposal is based on another Current Research Report")

    opened = collect_opened_evidence(context)
    citations = list(_iter_citations(result))
    citation_ids = [item.citation_id for item in citations]
    if len(citation_ids) != len(set(citation_ids)):
        raise ValueError("Agent evidence audit failed: duplicate citation_id")

    errors = [
        error
        for citation in citations
        if (
            error := _validate_citation(
                citation,
                opened,
                cutoff_at=(
                    result.cutoff_at
                    if context_pack.trigger.run_mode.value == "replay"
                    else None
                ),
            )
        )
        is not None
    ]
    errors.extend(
        error
        for claim in _iter_claims(result)
        if (error := _market_claim_date_error(claim, context)) is not None
    )
    if result.status.value == "updated" and not _opened_exact_market_record(context):
        errors.append(
            "updated view requires at least one successfully opened record-level "
            "market_evidence_open result; calling an unavailable record does not count"
        )

    plan_groups = [
        (result.hypotheses, result.evidence_plan),
        *[
            (revision.hypotheses, revision.evidence_plan)
            for revision in result.view_revisions
        ],
    ]
    for hypotheses, evidence_plan in plan_groups:
        hypothesis_ids = {item.hypothesis_id for item in hypotheses}
        for plan in evidence_plan:
            unknown = set(plan.hypothesis_ids) - hypothesis_ids
            if unknown:
                errors.append(
                    f"evidence plan {plan.plan_item_id} references unknown "
                    "hypotheses: "
                    + ", ".join(sorted(unknown))
                )
            if plan.status == "completed" and not plan.opened_references:
                errors.append(
                    "completed evidence plan has no opened references: "
                    f"{plan.plan_item_id}"
                )
            for reference in plan.opened_references:
                if not _reference_was_opened(reference, opened):
                    errors.append(
                        f"evidence plan reference was not opened: {reference}"
                    )

    revised_ids = [item.view_id for item in result.view_revisions]
    if len(revised_ids) != len(set(revised_ids)):
        errors.append("one run cannot submit multiple revisions for the same view_id")

    claim_ids = [
        *[claim.claim_id for claim in result.claims],
        *[
        claim.claim_id
        for revision in result.view_revisions
        for claim in revision.claims
        ],
    ]
    if len(claim_ids) != len(set(claim_ids)):
        errors.append("one run cannot reuse claim_id")
    forecast_ids = [
        forecast.forecast_id
        for revision in result.view_revisions
        for forecast in revision.forecasts
    ]
    if len(forecast_ids) != len(set(forecast_ids)):
        errors.append("one run cannot reuse forecast_id")
    requirement_ids = [
        item.requirement_id for item in result.observation_requirements
    ]
    if len(requirement_ids) != len(set(requirement_ids)):
        errors.append("one run cannot reuse requirement_id")

    if errors:
        raise ValueError("Agent evidence audit failed: " + "; ".join(errors))


def _opened_exact_market_record(context: AgentRunContext) -> bool:
    """A tool name alone is not evidence; require a returned stable locator."""

    for invocation in context.tool_invocations:
        if invocation.name != "market_evidence_open" or invocation.result is None:
            continue
        text = _stringify(invocation.result)
        if LOCATOR_PREFIX in text or _MARKET_ALIAS_PATTERN.search(text):
            return True
    return False


def prune_unopened_evidence_plan_references(
    result: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> CurrentResearchReportProposal:
    """Remove stale plan-index refs; claims and citations remain untouched.

    Evidence Plan is navigation metadata. An existing view can carry historical
    references that were not reopened in this run, but a new revision must not
    re-publish them as if they were reviewed. Filtering those refs is therefore
    deterministic provenance binding, not a research judgment.
    """

    opened = collect_opened_evidence(context)
    payload = result.model_dump(mode="python")
    for plan in payload.get("evidence_plan", []):
        plan["opened_references"] = [
            reference
            for reference in plan.get("opened_references", [])
            if _reference_was_opened(reference, opened)
        ]
        if plan.get("status") == "completed" and not plan["opened_references"]:
            plan["status"] = "not_needed"
    for revision in payload.get("view_revisions", []):
        for plan in revision.get("evidence_plan", []):
            plan["opened_references"] = [
                reference
                for reference in plan.get("opened_references", [])
                if _reference_was_opened(reference, opened)
            ]
            if plan.get("status") == "completed" and not plan["opened_references"]:
                plan["status"] = "not_needed"
    return CurrentResearchReportProposal.model_validate(payload)
