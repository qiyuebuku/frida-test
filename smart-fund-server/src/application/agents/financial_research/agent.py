"""OpenAI Agents SDK definition of the Research Agent role."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import timedelta

from agents import (
    Agent,
    FunctionTool,
    ModelSettings,
    RunContextWrapper,
    ToolsToFinalOutputResult,
)
from src.application.agents.financial_research.audit import (
    prune_unopened_evidence_plan_references,
    validate_research_result,
)
from src.application.agents.financial_research.context import AgentRunContext
from src.application.agents.financial_research.instructions import (
    load_financial_research_instructions,
)
from src.application.agents.financial_research.schemas import (
    CurrentResearchReportProposal,
    InvestmentViewResearchDraft,
    ResearchConclusionDraft,
    ResearchReportDraft,
    ResearchRunStatus,
)
from src.application.services.market_evidence_locator import (
    decode_market_evidence_locator,
)


async def _invoke_research_conclusion(
    wrapper: RunContextWrapper[AgentRunContext],
    raw_arguments: str,
) -> str:
    """Validate and bind one concise, non-updating research conclusion."""

    try:
        proposal = ResearchConclusionDraft.model_validate(
            _bind_task_inputs(_decode_provider_proposal(raw_arguments), wrapper)
        )
        return _validate_and_serialize_draft(
            wrapper,
            proposal.to_research_report_draft(),
        )
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return (
            "Research Proposal 校验失败，请根据错误修正全部字段后重新调用"
            f" submit_research_conclusion：{error}"
        )


async def _invoke_investment_view_revision(
    wrapper: RunContextWrapper[AgentRunContext],
    raw_arguments: str,
) -> str:
    """Validate and bind one full investment-view revision."""

    try:
        proposal = InvestmentViewResearchDraft.model_validate(
            _bind_task_inputs(_decode_provider_proposal(raw_arguments), wrapper)
        )
        return _validate_and_serialize_draft(
            wrapper,
            proposal.to_research_report_draft(),
        )
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return (
            "Investment View Revision 校验失败，请按错误修正后重新调用"
            f" submit_investment_view_revision：{error}"
        )


def _decode_provider_proposal(raw_arguments: str) -> object:
    """Decode the flat contract and tolerate the retired nested wrapper."""

    arguments = json.loads(raw_arguments)
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    if "proposal" not in arguments:
        return arguments
    proposal = arguments["proposal"]
    return json.loads(proposal) if isinstance(proposal, str) else proposal


def _bind_task_inputs(value: object, wrapper: RunContextWrapper[AgentRunContext]) -> object:
    """Bind fields that describe runtime facts rather than research judgment."""

    if not isinstance(value, dict):
        return value
    context_pack = wrapper.context.research_context
    if context_pack is None:
        return value
    normalized = dict(value)
    normalized["research_question"] = context_pack.research_question
    forecast_start = context_pack.trigger.cutoff_at + timedelta(minutes=1)
    for revision in normalized.get("view_revisions") or []:
        if not isinstance(revision, dict):
            continue
        for forecast in revision.get("forecasts") or []:
            if isinstance(forecast, dict):
                forecast["evaluation_start_at"] = forecast_start
    citation_entries = _collect_model_citation_entries(normalized)
    for plan in normalized.get("evidence_plan") or []:
        if not isinstance(plan, dict):
            continue
        plan["opened_references"] = (
            _relevant_plan_references(plan, citation_entries)
            if plan.get("status") == "completed"
            else []
        )
    attempted_tools = list(
        dict.fromkeys(
            invocation.name
            for invocation in wrapper.context.tool_invocations
            if not invocation.name.startswith("submit_")
        )
    )[:20]
    for gap in normalized.get("evidence_gaps") or []:
        if isinstance(gap, dict):
            gap["attempted_tools"] = attempted_tools
    for index, requirement in enumerate(
        normalized.get("observation_requirements") or [],
        start=1,
    ):
        if isinstance(requirement, dict):
            requirement["requirement_id"] = f"observation-{index:03d}"
    return normalized


def _collect_model_citation_entries(value: object) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []

    def visit(item: object, parent_text: str = "") -> None:
        if isinstance(item, list):
            for child in item:
                visit(child, parent_text)
            return
        if not isinstance(item, dict):
            return
        current_text = str(
            item.get("statement")
            or item.get("effect")
            or item.get("thesis")
            or parent_text
            or ""
        )
        if "reference" in item and "support" in item:
            reference = str(item.get("reference") or "")
            if reference and all(reference != existing[1] for existing in entries):
                entries.append((current_text, reference))
        for child in item.values():
            visit(child, current_text)

    visit(value)
    return entries


def _relevant_plan_references(
    plan: dict,
    entries: list[tuple[str, str]],
) -> list[str]:
    """Project a compact evidence index from the Proposal's actual citations."""

    plan_text = " ".join(
        str(plan.get(field) or "")
        for field in ("question", "required_evidence")
    )
    plan_terms = _evidence_match_terms(plan_text)
    ranked: list[tuple[int, int, str]] = []
    for order, (claim_text, reference) in enumerate(entries):
        score = len(plan_terms.intersection(_evidence_match_terms(claim_text)))
        ranked.append((score, -order, reference))
    relevant = [
        reference
        for score, _, reference in sorted(ranked, reverse=True)
        if score > 0
    ][:8]
    # A completed plan needs a provenance pointer even when its prose uses no
    # shared object token. Keep one representative citation, not the entire
    # evidence ledger.
    if not relevant and entries:
        relevant = [entries[0][1]]
    return relevant


def _evidence_match_terms(text: str) -> set[str]:
    normalized = text.casefold()
    terms = set(re.findall(r"[a-z]{2,}[a-z0-9_-]*|[0-9]{3,}", normalized))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]+", normalized)
    for run in chinese_runs:
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def _without_model_citation_times(schema: dict) -> dict:
    schema = deepcopy(schema)
    citation = schema.get("$defs", {}).get("EvidenceCitation", {})
    properties = citation.get("properties", {})
    for field in ("citation_id", "kind", "observed_at", "as_of"):
        properties.pop(field, None)
    citation["required"] = [
        item
        for item in citation.get("required", [])
        if item not in {"citation_id", "kind", "observed_at", "as_of"}
    ]
    schema.get("properties", {}).pop("research_question", None)
    schema["required"] = [
        item for item in schema.get("required", []) if item != "research_question"
    ]
    plan = schema.get("$defs", {}).get("EvidencePlanItem", {})
    plan.get("properties", {}).pop("opened_references", None)
    plan["required"] = [
        item for item in plan.get("required", []) if item != "opened_references"
    ]
    gap = schema.get("$defs", {}).get("EvidenceGap", {})
    gap.get("properties", {}).pop("attempted_tools", None)
    gap["required"] = [
        item for item in gap.get("required", []) if item != "attempted_tools"
    ]
    reason = gap.get("properties", {}).get("reason", {})
    if "enum" in reason:
        reason["enum"] = [
            item for item in reason["enum"] if item != "budget_exhausted"
        ]
    requirement = schema.get("$defs", {}).get("ObservationRequirement", {})
    requirement.get("properties", {}).pop("requirement_id", None)
    requirement["required"] = [
        item
        for item in requirement.get("required", [])
        if item != "requirement_id"
    ]
    forecast = schema.get("$defs", {}).get("Forecast", {})
    forecast.get("properties", {}).pop("evaluation_start_at", None)
    forecast["required"] = [
        item
        for item in forecast.get("required", [])
        if item != "evaluation_start_at"
    ]
    return schema


submit_research_conclusion = FunctionTool(
    name="submit_research_conclusion",
    description=(
        "Finish a review without changing an existing investment view. no_change "
        "means active views were reviewed and remain valid; when no active view "
        "exists, do not use it merely because the market is pre-open or a threshold "
        "scanner is quiet. Also supports blocked, insufficient_evidence, or incomplete."
    ),
    params_json_schema=_without_model_citation_times(
        ResearchConclusionDraft.model_json_schema()
    ),
    on_invoke_tool=_invoke_research_conclusion,
    strict_json_schema=True,
)


def _investment_revision_tool_schema() -> dict:
    """Remove report-level structures duplicated inside the sole revision."""

    schema = deepcopy(InvestmentViewResearchDraft.model_json_schema())
    revision = schema.get("$defs", {}).get("InvestmentViewRevisionProposal", {})
    properties = revision.get("properties", {})
    properties.pop("hypotheses", None)
    properties.pop("evidence_plan", None)
    revision["required"] = [
        item
        for item in revision.get("required", [])
        if item not in {"hypotheses", "evidence_plan"}
    ]
    return _without_model_citation_times(schema)

submit_investment_view_revision = FunctionTool(
    name="submit_investment_view_revision",
    description=(
        "Finish a review by creating or revising an investment view. Use only "
        "after the full evidence, counterevidence, mechanism, market structure, "
        "forecast, and decision-boundary requirements are satisfied."
    ),
    params_json_schema=_investment_revision_tool_schema(),
    on_invoke_tool=_invoke_investment_view_revision,
    strict_json_schema=True,
)


def _validate_and_serialize_draft(
    wrapper: RunContextWrapper[AgentRunContext],
    proposal: ResearchReportDraft,
) -> str:
    """Apply deterministic identity, schema, and evidence-integrity checks."""

    context_pack = wrapper.context.research_context
    if context_pack is None:
        raise ValueError("Research Context Pack is missing")
    formal_proposal = _bind_research_draft(
        proposal,
        context=wrapper.context,
    )
    formal_proposal = prune_unopened_evidence_plan_references(
        formal_proposal,
        wrapper.context,
    )
    # Validate evidence while the submit tool can still return actionable
    # errors to the model.  Running this only after the tool had finalized the
    # Agent would turn correctable citation mistakes into a failed run.
    validate_research_result(formal_proposal, wrapper.context)
    return formal_proposal.model_dump_json()


def _bind_research_draft(
    proposal: ResearchReportDraft,
    *,
    context: AgentRunContext,
) -> CurrentResearchReportProposal:
    """Create the persistence proposal from trusted run state and model content."""

    context_pack = context.research_context
    if context_pack is None:
        raise ValueError("Research Context Pack is missing")
    if (
        proposal.status == ResearchRunStatus.NO_CHANGE
        and not context_pack.active_views
    ):
        raise ValueError(
            "no_change requires at least one existing active view to remain "
            "unchanged; this run has no active view. Create a bounded, "
            "falsifiable view when evidence supports one, or use "
            "insufficient_evidence only with a genuinely critical gap."
        )
    payload = proposal.model_dump(mode="python")
    _bind_citation_times(payload, context=context)
    _bind_future_schedule_times(
        payload,
        cutoff_at=context_pack.trigger.cutoff_at,
    )
    payload.update(
        task_mode=context.task_mode,
        report_id="research:current",
        base_report_revision_id=context_pack.current_report_revision_id,
        proposed_report_revision_id=(
            f"research-report-revision:{context.run_id}"
            if (
                proposal.status == ResearchRunStatus.UPDATED
                or (
                    proposal.status == ResearchRunStatus.NO_CHANGE
                    and context_pack.current_report_revision_id is None
                )
            )
            else None
        ),
        run_id=context.run_id,
        trigger_id=context_pack.trigger.trigger_id,
        trigger_slot=context_pack.trigger.trigger_slot,
        cutoff_at=context_pack.trigger.cutoff_at,
        source_frame_id=context_pack.market_state.frame_id,
        active_views=[item.model_dump(mode="python") for item in context_pack.active_views],
    )
    return CurrentResearchReportProposal.model_validate(payload)


def _bind_future_schedule_times(payload: dict, *, cutoff_at) -> None:
    """Normalize exact future instants while preserving the model's horizon."""

    minimum_start = cutoff_at + timedelta(minutes=1)
    for revision in payload.get("view_revisions") or []:
        if not isinstance(revision, dict):
            continue
        for forecast in revision.get("forecasts") or []:
            if not isinstance(forecast, dict):
                continue
            start = forecast.get("evaluation_start_at")
            end = forecast.get("evaluation_end_at")
            if start is None or end is None or start > cutoff_at:
                continue
            duration = end - start
            forecast["evaluation_start_at"] = minimum_start
            if end <= minimum_start:
                forecast["evaluation_end_at"] = minimum_start + (
                    duration if duration > timedelta(0) else timedelta(days=1)
                )
    for requirement in payload.get("observation_requirements") or []:
        if (
            isinstance(requirement, dict)
            and requirement.get("due_at") is not None
            and requirement["due_at"] <= cutoff_at
        ):
            requirement["due_at"] = minimum_start


def _bind_citation_times(payload: dict, *, context: AgentRunContext) -> None:
    """Bind market citation times from trusted locators, never model text."""

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("kind") == "market" and value.get("reference"):
            reference = context.evidence_aliases.get(
                str(value["reference"]),
                str(value["reference"]),
            )
            try:
                identity = decode_market_evidence_locator(reference)
            except ValueError:
                identity = None
            if identity is not None and identity.fact_time:
                value["observed_at"] = identity.fact_time
                value["as_of"] = identity.fact_time
            else:
                # Navigation aliases without a reversible locator remain
                # explicitly timeless instead of trusting model-generated time.
                value["observed_at"] = None
                value["as_of"] = None
        for child in value.values():
            visit(child)

    visit(payload)


def create_financial_research_agent(
    *,
    model: str,
    mcp_server,
) -> Agent[AgentRunContext]:
    return Agent[AgentRunContext](
        name="Research Agent｜研究智能体",
        instructions=load_financial_research_instructions(),
        model=model,
        model_settings=ModelSettings(
            parallel_tool_calls=True,
            include_usage=True,
            tool_choice="required",
        ),
        tools=[submit_research_conclusion, submit_investment_view_revision],
        mcp_servers=[mcp_server],
        tool_use_behavior=_validated_proposal_is_final,
        reset_tool_choice=False,
    )


def _validated_proposal_is_final(
    _context: RunContextWrapper[AgentRunContext],
    tool_results: list,
) -> ToolsToFinalOutputResult:
    for result in tool_results:
        if result.tool.name not in {
            "submit_research_conclusion",
            "submit_investment_view_revision",
        }:
            continue
        try:
            CurrentResearchReportProposal.model_validate_json(result.output)
        except (ValueError, TypeError):
            continue
        return ToolsToFinalOutputResult(
            is_final_output=True,
            final_output=result.output,
        )
    return ToolsToFinalOutputResult(is_final_output=False)
