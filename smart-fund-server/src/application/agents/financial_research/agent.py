"""OpenAI Agents SDK definition of the Research Agent role."""

from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from datetime import timedelta

from agents import (
    Agent,
    FunctionTool,
    RunContextWrapper,
    ToolsToFinalOutputResult,
)
from src.application.agents.financial_research.audit import (
    collect_opened_evidence,
    prune_unopened_evidence_plan_references,
    validate_research_result,
)
from src.application.agents.financial_research.quality_evaluator import (
    evaluate_research_quality,
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
from src.application.agents.financial_research.model_settings import (
    research_model_settings,
)
from src.application.services.market_evidence_locator import (
    LOCATOR_PREFIX,
    decode_market_evidence_locator,
)
from src.infrastructure.agent_runtime.mcp import (
    RESEARCH_READ_TOOLS,
    _decode_tool_result_object,
    research_ledger_missing_requirements,
)

logger = logging.getLogger(__name__)


async def _checkpoint_research_working_memory(
    wrapper: RunContextWrapper[AgentRunContext],
    raw_arguments: str,
) -> str:
    """Replace the current run-local research state with a compact checkpoint."""

    try:
        payload = json.loads(raw_arguments)
    except json.JSONDecodeError as error:
        return f"工作记忆检查点不是有效 JSON：{error}"
    if not isinstance(payload, dict):
        return "工作记忆检查点必须是 JSON 对象"
    wrapper.context.working_memory_revision += 1
    wrapper.context.working_memory = deepcopy(payload)
    comparison_gaps = [
        item
        for item in research_ledger_missing_requirements(wrapper.context)
        if item.startswith("打开比较变化所需的基线记录级证据")
    ]
    return json.dumps(
        {
            "saved": True,
            "revision": wrapper.context.working_memory_revision,
            "instruction": "检查点已替换；其中判断不是事实证据，引用仍须使用正式证据定位符。",
            "unresolved_evidence_requirements": comparison_gaps,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _working_memory_checkpoint_enabled(
    wrapper: RunContextWrapper[AgentRunContext],
    _agent,
) -> bool:
    return True


checkpoint_research_working_memory = FunctionTool(
    name="checkpoint_research_working_memory",
    description=(
        "可选地保存本次研究的结构化工作状态。只在候选很多、需要显式整理计划时"
        "使用；Runtime 上下文压缩不依赖它。它不保存事实，也不能作为证据。"
    ),
    params_json_schema={
        "type": "object",
        "properties": {
            "research_goal": {"type": "string"},
            "candidate_hypotheses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "statement": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["active", "weakened", "rejected", "unresolved"],
                        },
                        "reason": {"type": "string"},
                        "evidence_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["id", "statement", "status", "reason", "evidence_refs"],
                    "additionalProperties": False,
                },
            },
            "answered_questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "answer": {"type": "string"},
                        "evidence_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["question", "answer", "evidence_refs"],
                    "additionalProperties": False,
                },
            },
            "remaining_questions": {"type": "array", "items": {"type": "string"}},
            "discarded_paths": {"type": "array", "items": {"type": "string"}},
            "next_step": {"type": "string"},
        },
        "required": [
            "research_goal",
            "candidate_hypotheses",
            "answered_questions",
            "remaining_questions",
            "discarded_paths",
            "next_step",
        ],
        "additionalProperties": False,
    },
    on_invoke_tool=_checkpoint_research_working_memory,
    is_enabled=_working_memory_checkpoint_enabled,
    strict_json_schema=True,
)


async def _run_evidence_reopen(
    wrapper: RunContextWrapper[AgentRunContext],
    raw_arguments: str,
) -> str:
    """Reopen one immutable run-local tool result after transcript compaction."""

    try:
        arguments = json.loads(raw_arguments)
        order = int(arguments["order"])
        path = str(arguments.get("path") or "").strip("./")
        offset = max(int(arguments.get("offset_chars") or 0), 0)
        limit = min(max(int(arguments.get("max_chars") or 8_000), 500), 12_000)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        return json.dumps({"available": False, "error": str(error)}, ensure_ascii=False)
    invocations = wrapper.context.tool_invocations
    if order < 1 or order > len(invocations):
        return json.dumps({
            "available": False,
            "error": f"order must be between 1 and {len(invocations)}",
        }, ensure_ascii=False)
    invocation = invocations[order - 1]
    if (
        invocation.finished_at is None
        or invocation.result is None
        or invocation.name.startswith("submit_")
        or invocation.name in {"agent_evidence_ledger_open", "run_evidence_reopen"}
    ):
        return json.dumps({
            "available": False,
            "order": order,
            "error": "该 order 不是可恢复的已完成读取结果",
        }, ensure_ascii=False)
    value = _decode_tool_result_object(invocation.result)
    if path:
        for segment in path.replace("/", ".").split("."):
            if not segment:
                continue
            try:
                value = value[int(segment)] if isinstance(value, list) else value[segment]
            except (IndexError, KeyError, TypeError, ValueError):
                return json.dumps({
                    "available": False,
                    "order": order,
                    "tool": invocation.name,
                    "path": path,
                    "error": f"path segment not found: {segment}",
                }, ensure_ascii=False)
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    chunk = serialized[offset : offset + limit]
    return json.dumps({
        "available": True,
        "order": order,
        "tool": invocation.name,
        "arguments": invocation.arguments,
        "path": path or None,
        "offset_chars": offset,
        "next_offset_chars": offset + len(chunk),
        "has_more": offset + len(chunk) < len(serialized),
        "total_chars": len(serialized),
        "content": chunk,
        "authority": "本次运行原始工具结果的只读恢复；可作为该原调用的事实证据。",
    }, ensure_ascii=False, separators=(",", ":"))


def _run_evidence_reopen_enabled(
    wrapper: RunContextWrapper[AgentRunContext],
    _agent,
) -> bool:
    # Recovery is harmless before compaction and must never disappear from the
    # model's tool schema as the surface changes.
    return True


run_evidence_reopen = FunctionTool(
    name="run_evidence_reopen",
    description=(
        "按上下文检查点中的 order 重新展开本次运行已经成功读取的原始工具结果。"
        "不重新请求数据；压缩后需要核对被折叠字段时使用。可用 path 只取某个字段，"
        "大结果可用 offset_chars 分页。"
    ),
    params_json_schema={
        "type": "object",
        "properties": {
            "order": {"type": "integer", "minimum": 1},
            "path": {"type": "string"},
            "offset_chars": {"type": "integer", "minimum": 0},
            "max_chars": {"type": "integer", "minimum": 500, "maximum": 12000},
        },
        "required": ["order"],
        "additionalProperties": False,
    },
    on_invoke_tool=_run_evidence_reopen,
    is_enabled=_run_evidence_reopen_enabled,
    strict_json_schema=False,
)


def _research_submission_enabled(
    wrapper: RunContextWrapper[AgentRunContext],
    _agent,
) -> bool:
    return True


async def _invoke_research_conclusion(
    wrapper: RunContextWrapper[AgentRunContext],
    raw_arguments: str,
) -> str:
    """Validate and bind one concise, non-updating research conclusion."""

    try:
        proposal = ResearchConclusionDraft.model_validate(
            _bind_task_inputs(_prepare_submission_draft(raw_arguments, wrapper), wrapper)
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
            _bind_task_inputs(_prepare_submission_draft(raw_arguments, wrapper), wrapper)
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


def _prepare_submission_draft(
    raw_arguments: str,
    wrapper: RunContextWrapper[AgentRunContext],
) -> object:
    """Merge repair turns without making the model regenerate stable fields.

    A retry is a revision of the preceding proposal, not a new report. The
    current payload wins wherever it supplies a value; omitted keys retain the
    latest model-authored value. Explicit empty lists/strings still delete a
    previous value, so semantic changes remain under model control.
    """

    current = _normalize_provider_proposal(_decode_provider_proposal(raw_arguments))
    if not isinstance(current, dict):
        return current
    previous = wrapper.context.submission_draft
    merged = _merge_submission_objects(previous, current) if previous else current
    wrapper.context.submission_draft = deepcopy(merged)
    return merged


def _merge_submission_objects(previous: object, current: object) -> object:
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return deepcopy(current)
    merged = deepcopy(previous)
    for key, value in current.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_submission_objects(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _normalize_provider_proposal(value: object) -> object:
    """Repair deterministic provider-shape noise before business validation.

    Evidence overflow and confidence spelling do not require research judgment.
    Keeping those repairs in the submit boundary avoids asking the model to
    regenerate a very large proposal and accidentally changing its conclusion.
    """

    if not isinstance(value, dict):
        return value
    normalized = deepcopy(value)
    confidence_aliases = {
        "very_low": "low",
        "medium_low": "low",
        "moderate": "medium",
        "medium-high": "medium_high",
        "very_high": "high",
    }
    hypothesis_status_aliases = {
        # Provider vocabulary noise does not change the research judgment.
        # Both spellings mean that the evidence has not resolved the
        # hypothesis, so normalize locally instead of spending another large
        # proposal-generation turn on an enum correction.
        "unresolved": "inconclusive",
        "undetermined": "inconclusive",
        "unknown": "inconclusive",
        "pending": "unverified",
    }
    evidence_layer_aliases = {
        # Expression is an investment/portfolio concept, not a separate
        # evidence depth.  At the Research boundary an ETF or instrument
        # expression is an object-level record.  Repairing this vocabulary
        # mismatch is mechanical and must not trigger regeneration of the
        # complete Proposal.
        "expression": "object",
        "instrument": "object",
        "etf": "object",
    }
    revision_event_aliases = {
        # The business state has an ``expired`` status but its immutable event
        # vocabulary uses ``invalidate``. Provider wording such as expire is a
        # protocol synonym, not a different investment judgment.
        "expire": "invalidate",
        "expired": "invalidate",
    }

    def normalize_evidence_plan(container: dict) -> None:
        for plan in container.get("evidence_plan") or []:
            if not isinstance(plan, dict):
                continue
            layer = plan.get("layer")
            if isinstance(layer, str):
                plan["layer"] = evidence_layer_aliases.get(
                    layer.strip().casefold(),
                    layer,
                )

    def remove_non_evidence_sentinels(item: object) -> None:
        if isinstance(item, list):
            for child in item:
                remove_non_evidence_sentinels(child)
            return
        if not isinstance(item, dict):
            return
        evidence = item.get("evidence")
        if isinstance(evidence, list):
            item["evidence"] = [
                citation
                for citation in evidence
                if not (
                    isinstance(citation, dict)
                    and (
                        str(citation.get("reference") or "").startswith(
                            "run_evidence:"
                        )
                        or
                        str(citation.get("reference") or "").startswith(
                            "market_historical_analogue_open:"
                        )
                        or "_analogue_empty" in str(citation.get("reference") or "")
                    )
                )
            ]
        for child in item.values():
            remove_non_evidence_sentinels(child)

    remove_non_evidence_sentinels(normalized)
    normalize_evidence_plan(normalized)
    for index, hypothesis in enumerate(normalized.get("hypotheses") or []):
        if not isinstance(hypothesis, dict):
            continue
        status = hypothesis.get("status")
        if isinstance(status, str):
            hypothesis["status"] = hypothesis_status_aliases.get(status, status)
        if hypothesis.get("role"):
            continue
        identity = str(hypothesis.get("hypothesis_id") or "").casefold()
        hypothesis["role"] = (
            "data_quality"
            if any(token in identity for token in ("data", "quality", "missing", "gap"))
            else "primary" if index == 0 else "alternative"
        )

    def repair_claims(container: dict) -> None:
        for claim in container.get("claims") or []:
            if not isinstance(claim, dict) or claim.get("claim_type") != "observed_fact":
                continue
            evidence = claim.get("evidence") or []
            supports = any(
                isinstance(item, dict) and item.get("support") == "supports"
                for item in evidence
            )
            if not supports:
                # A claim backed only by context locators is necessarily an
                # inference over those records, not a directly observed fact.
                claim["claim_type"] = "inference"

    repair_claims(normalized)
    _remove_unknown_evidence_plan_hypothesis_ids(normalized)
    for revision in normalized.get("view_revisions") or []:
        if not isinstance(revision, dict):
            continue
        event = revision.get("event")
        if isinstance(event, str):
            revision["event"] = revision_event_aliases.get(
                event.strip().casefold(),
                event,
            )
        normalize_evidence_plan(revision)
        for index, hypothesis in enumerate(revision.get("hypotheses") or []):
            if not isinstance(hypothesis, dict):
                continue
            status = hypothesis.get("status")
            if isinstance(status, str):
                hypothesis["status"] = hypothesis_status_aliases.get(status, status)
            if hypothesis.get("role"):
                continue
            identity = str(hypothesis.get("hypothesis_id") or "").casefold()
            hypothesis["role"] = (
                "data_quality"
                if any(token in identity for token in ("data", "quality", "missing", "gap"))
                else "primary" if index == 0 else "alternative"
            )
        repair_claims(revision)
        market_structure = revision.get("market_structure")
        if isinstance(market_structure, dict):
            # Missing is not evidence of a market-structure conclusion. Bind
            # explicit non-claims instead of asking the model to regenerate a
            # large Proposal merely because it omitted one descriptive slot.
            uncertainty_defaults = {
                "breadth": "本轮未形成可验证的市场宽度结论。",
                "leadership_concentration": "本轮未形成可验证的主线集中度结论。",
                "volume_liquidity_confirmation": "本轮未形成可验证的成交量或流动性确认结论。",
                "crowding_and_reversal_risk": "本轮未形成可验证的拥挤或反转风险结论。",
                "persistence_assessment": "本轮未形成可验证的持续性结论。",
            }
            for field, default in uncertainty_defaults.items():
                if not market_structure.get(field):
                    market_structure[field] = default
            if not market_structure.get("pricing_state"):
                market_structure["pricing_state"] = "unknown"
        confidence = revision.get("confidence")
        if isinstance(confidence, dict):
            for field in (
                "overall",
                "evidence_quality",
                "independent_confirmation",
                "counterevidence_resilience",
                "timing_clarity",
            ):
                level = confidence.get(field)
                if isinstance(level, str):
                    confidence[field] = confidence_aliases.get(level, level)
        for link in revision.get("mechanism_chain") or []:
            if isinstance(link, dict) and isinstance(link.get("evidence"), list):
                link["evidence"] = link["evidence"][:12]
    return normalized


def _remove_unknown_evidence_plan_hypothesis_ids(payload: dict) -> None:
    """Drop provider-invented IDs when a plan also names real hypotheses.

    This is referential-integrity normalization, not research judgment.  A plan
    containing only unknown IDs remains untouched so validation still asks the
    model to repair the missing semantic association.
    """

    valid_ids = {
        str(item.get("hypothesis_id"))
        for item in payload.get("hypotheses") or []
        if isinstance(item, dict) and item.get("hypothesis_id")
    }
    if not valid_ids:
        return
    for plan in payload.get("evidence_plan") or []:
        if not isinstance(plan, dict) or not isinstance(plan.get("hypothesis_ids"), list):
            continue
        known = [
            hypothesis_id
            for hypothesis_id in plan["hypothesis_ids"]
            if str(hypothesis_id) in valid_ids
        ]
        if known:
            plan["hypothesis_ids"] = known


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
            if (
                normalized.get("status") != ResearchRunStatus.INCOMPLETE.value
                and gap.get("reason") == "budget_exhausted"
            ):
                # The runtime, not the model, knows whether execution actually
                # exhausted its budget. A completed run must not fail because
                # the provider invented an internal run-state enum.
                gap["reason"] = "not_yet_available"
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
    direction = forecast.get("properties", {}).get("expected_direction", {})
    if "enum" in direction:
        direction["enum"] = [
            item for item in direction["enum"] if item != "range"
        ]
    # The persistence models leave room for imports and future migrations.
    # Exposing those generous maxima to the LLM encourages enormous tool
    # arguments and malformed JSON without improving the investment decision.
    root_properties = schema.get("properties", {})
    for field, maximum in {
        "hypotheses": 3,
        "evidence_plan": 8,
        "claims": 16,
        "memory_application": 6,
        "view_revisions": 2,
        "observation_requirements": 10,
        "evidence_gaps": 8,
    }.items():
        if field in root_properties:
            root_properties[field]["maxItems"] = maximum
    if "report_summary" in root_properties:
        root_properties["report_summary"]["maxLength"] = 3200
    if "data_quality_assessment" in root_properties:
        root_properties["data_quality_assessment"]["maxLength"] = 1800
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
    is_enabled=_research_submission_enabled,
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
    is_enabled=_research_submission_enabled,
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
    if not any(
        invocation.name == "agent_evidence_ledger_open"
        and invocation.finished_at is not None
        and invocation.result is not None
        for invocation in wrapper.context.tool_invocations
    ):
        missing = research_ledger_missing_requirements(wrapper.context)
        detail = "；".join(missing) if missing else "账本尚未成功打开"
        raise ValueError(
            "提交前必须打开 agent_evidence_ledger_open。当前仍需完成：" + detail
        )
    formal_proposal = _bind_research_draft(
        proposal,
        context=wrapper.context,
    )
    # Bind runtime-owned daily locators before pruning.  A completed history
    # plan intentionally arrives without model-authored ``opened_references``;
    # pruning it first would downgrade the plan to ``not_needed`` and make the
    # subsequent binding unreachable.
    formal_proposal = _bind_opened_daily_history_references(
        formal_proposal,
        wrapper.context,
    )
    formal_proposal = prune_unopened_evidence_plan_references(
        formal_proposal,
        wrapper.context,
    )
    formal_proposal = _prune_unopened_citations(
        formal_proposal,
        wrapper.context,
    )
    formal_proposal = _deduplicate_market_citations(
        formal_proposal,
        wrapper.context,
    )
    _remove_unsupported_directional_forecasts(formal_proposal, wrapper.context)
    _bind_forecast_calibration_fields(formal_proposal, wrapper.context)
    _validate_forecast_calibration(formal_proposal, wrapper.context)
    # Validate evidence while the submit tool can still return actionable
    # errors to the model.  Running this only after the tool had finalized the
    # Agent would turn correctable citation mistakes into a failed run.
    validate_research_result(formal_proposal, wrapper.context)
    opened_evidence = collect_opened_evidence(wrapper.context)
    quality = evaluate_research_quality(
        formal_proposal,
        tool_names=[item.name for item in wrapper.context.tool_invocations],
        opened_evidence_refs={
            reference
            for kind in ("card", "edge", "community", "external", "market", "input")
            for reference in (
                opened_evidence.get(kind, set())
                if isinstance(opened_evidence.get(kind), set)
                else set()
            )
        },
    )
    contract_failures = {
        "missing_market_structure_assessment",
        "primary_view_missing_own_multi_day_history",
        "missing_portfolio_decision_boundary",
    }.intersection(quality.advisory_findings)
    if contract_failures:
        actions = _forward_contract_repair_actions(
            formal_proposal,
            contract_failures=contract_failures,
        )
        raise ValueError(
            "前瞻观点契约未完成："
            + "、".join(sorted(contract_failures))
            + "；"
            + "；".join(actions)
        )
    _validate_reversal_claims_have_two_sided_evidence(formal_proposal)
    return formal_proposal.model_dump_json()


def _forward_contract_repair_actions(
    proposal: CurrentResearchReportProposal,
    *,
    contract_failures: set[str],
) -> list[str]:
    """Return exact JSON paths instead of an abstract quality-gate slogan."""

    actions: list[str] = []
    for index, revision in enumerate(proposal.view_revisions):
        if revision.status not in {"active", "challenged"} or revision.event in {
            "challenge",
            "invalidate",
        }:
            continue
        prefix = f"view_revisions[{index}]"
        if "missing_market_structure_assessment" in contract_failures:
            if revision.market_structure is None:
                actions.append(
                    prefix
                    + ".market_structure 不能为 null；填写 breadth、"
                    "leadership_concentration、volume_liquidity_confirmation、"
                    "crowding_and_reversal_risk、persistence_assessment、"
                    "pricing_state，并在 evidence 中放入至少 2 条已打开的引用"
                )
            elif len(revision.market_structure.evidence) < 2:
                actions.append(prefix + ".market_structure.evidence 至少需要 2 条已打开的引用")
        if "missing_portfolio_decision_boundary" in contract_failures:
            boundary = revision.decision_boundary
            if boundary is None:
                actions.append(
                    prefix
                    + ".decision_boundary 不能为 null；填写 portfolio_relevance、"
                    "candidate_expressions_for_portfolio_review、actions_not_supported、"
                    "sizing_constraints_for_portfolio_review、monitoring_signals"
                )
            else:
                if not boundary.actions_not_supported:
                    actions.append(prefix + ".decision_boundary.actions_not_supported 至少填写 1 项")
                if not boundary.monitoring_signals:
                    actions.append(prefix + ".decision_boundary.monitoring_signals 至少填写 1 项")
    if "primary_view_missing_own_multi_day_history" in contract_failures:
        actions.append("正式观点必须引用观点对象自身至少 3 个不同交易日的历史记录")
    return actions or ["按错误码补齐对应 view_revisions 项，不要删除已有合格字段"]


def _validate_reversal_claims_have_two_sided_evidence(
    proposal: CurrentResearchReportProposal,
) -> None:
    """Reject inherited before-values masquerading as this run's evidence."""

    for revision in proposal.view_revisions:
        # This guard protects forward research from importing an unverified
        # before-value.  A terminal state transition is historical bookkeeping
        # and is already checked through its cited invalidation facts; it must
        # not block the new active view on wording inherited from the old view.
        if revision.event in {"invalidate", "challenge"}:
            continue
        text = "\n".join(
            [revision.title, revision.thesis]
            + [claim.statement for claim in revision.claims]
            + [
                f"{link.cause} {link.mechanism} {link.effect}"
                for link in revision.mechanism_chain
            ]
        )
        if not re.search(r"由.{0,50}(转为|转成|转负|转正)|资金.{0,20}反转", text):
            continue
        dated_groups: dict[tuple[str, str], set[str]] = {}
        for citation in _revision_citations(revision):
            if not citation.reference.startswith(LOCATOR_PREFIX):
                continue
            try:
                identity = decode_market_evidence_locator(citation.reference)
            except ValueError:
                continue
            date_value = identity.identity.get("trade_date") or identity.fact_time
            if date_value:
                dated_groups.setdefault(
                    (str(identity.subject_id), str(identity.data_type)), set()
                ).add(str(date_value)[:10])
        if not any(len(dates) >= 2 for dates in dated_groups.values()):
            raise ValueError(
                f"{revision.view_id} 声称资金或强弱发生反转，但本轮引用中没有"
                "同一对象、同一数据口径的前后两个交易日证据；删除旧报告基线"
                "及‘由…转为/反转’措辞，或先重新打开历史端记录。"
            )


def _bind_opened_daily_history_references(
    proposal: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> CurrentResearchReportProposal:
    """Fill mechanical daily locators from actual history tool results.

    The model selects the subject and marks the history layer completed. Long
    reversible locators and individual bar dates are runtime metadata, just
    like citation timestamps, and should not be copied by the model after the
    final evidence-ledger compaction.
    """

    opened_evidence = collect_opened_evidence(context)
    opened = opened_evidence.get("market", set())
    if not isinstance(opened, set):
        opened = set()
    daily_by_code: dict[str, list[tuple[str, str]]] = {}

    # Prefer the successful history invocation's explicit subject argument and
    # structured window evidence.  Inferring the subject only by decoding a
    # locator is unnecessarily brittle after model-facing alias compaction.
    def collect_window_evidence(value: object) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        if isinstance(value, list):
            for child in value:
                found.extend(collect_window_evidence(child))
            return found
        if not isinstance(value, dict):
            return found
        trade_date = value.get("trade_date")
        reference = value.get("evidence_locator")
        if isinstance(trade_date, str) and isinstance(reference, str):
            found.append((trade_date[:10], reference))
        for child in value.values():
            found.extend(collect_window_evidence(child))
        return found

    for invocation in context.tool_invocations:
        if invocation.name != "market_instrument_history" or invocation.result is None:
            continue
        arguments = invocation.arguments if isinstance(invocation.arguments, dict) else {}
        data_type = str(arguments.get("data_type") or "")
        if not data_type.endswith("_daily"):
            continue
        code = str(arguments.get("code") or "")
        code_matches = re.findall(r"(?<!\d)\d{6}(?!\d)", code)
        if not code_matches:
            continue
        result = invocation.result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (TypeError, ValueError):
                continue
        for trade_date, reference in collect_window_evidence(result):
            canonical = context.evidence_aliases.get(reference, reference)
            if not canonical.startswith(LOCATOR_PREFIX):
                continue
            for code_match in code_matches:
                daily_by_code.setdefault(code_match, []).append(
                    (trade_date, canonical)
                )

    # Keep locator decoding as a fallback for legacy/raw tool results.
    for reference in opened:
        canonical = context.evidence_aliases.get(reference, reference)
        if not canonical.startswith(LOCATOR_PREFIX):
            continue
        try:
            identity = decode_market_evidence_locator(canonical)
        except ValueError:
            continue
        if not str(identity.data_type or "").endswith("_daily"):
            continue
        trade_date = identity.identity.get("trade_date") or identity.fact_time
        if not trade_date:
            continue
        for code in re.findall(r"(?<!\d)\d{6}(?!\d)", str(identity.subject_id)):
            daily_by_code.setdefault(code, []).append((str(trade_date)[:10], canonical))

    payload = proposal.model_dump(mode="python")
    for revision in payload.get("view_revisions") or []:
        if (
            revision.get("status") not in {"active", "challenged"}
            or revision.get("event") in {"challenge", "invalidate"}
        ):
            continue
        primary_text = " ".join(
            [revision.get("title", ""), revision.get("thesis", "")]
            + list(revision.get("scope") or [])
            + [
                item.get("statement", "")
                for item in revision.get("hypotheses") or []
                if item.get("role") == "primary"
            ]
        )
        codes = re.findall(r"(?<!\d)\d{6}(?!\d)", primary_text)
        candidates: list[str] = []
        seen_dates: set[str] = set()
        for code in codes:
            for trade_date, reference in sorted(daily_by_code.get(code, []), reverse=True):
                if trade_date in seen_dates:
                    continue
                seen_dates.add(trade_date)
                candidates.append(reference)
                if len(candidates) >= 5:
                    break
            if len(candidates) >= 3:
                break
        if len(candidates) < 3:
            logger.info(
                "research_history_binding_skipped run_id=%s view_id=%s codes=%s counts=%s",
                context.run_id,
                revision.get("view_id"),
                codes,
                {code: len(daily_by_code.get(code, [])) for code in codes},
            )
            continue
        primary_ids = {
            item.get("hypothesis_id")
            for item in revision.get("hypotheses") or []
            if item.get("role") == "primary"
        }
        for plan in revision.get("evidence_plan") or []:
            if (
                plan.get("layer") == "history"
                and plan.get("status") == "completed"
                and primary_ids.intersection(plan.get("hypothesis_ids") or [])
            ):
                plan["opened_references"] = list(
                    dict.fromkeys([*(plan.get("opened_references") or []), *candidates])
                )[:20]
                logger.info(
                    "research_history_binding_applied run_id=%s view_id=%s dates=%s references=%s",
                    context.run_id,
                    revision.get("view_id"),
                    sorted(seen_dates, reverse=True),
                    len(plan["opened_references"]),
                )
    return CurrentResearchReportProposal.model_validate(payload)


def _revision_citations(revision):
    for claim in revision.claims:
        yield from claim.evidence
    for link in revision.mechanism_chain:
        yield from link.evidence
    if revision.market_structure is not None:
        yield from revision.market_structure.evidence


def _prune_unopened_citations(
    proposal: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> CurrentResearchReportProposal:
    """Remove mechanically invalid citations before semantic validation.

    Large repair proposals frequently retain one stale Card or prior-report
    reference even after the model has corrected the surrounding claim. Whether
    a reference was opened in this run is deterministic runtime state, so the
    submit boundary removes it rather than asking the model to reproduce the
    entire nested Proposal. Revalidation then reports only claims that genuinely
    lost required support.
    """

    opened_by_kind = collect_opened_evidence(context)
    opened = {
        reference
        for kind in ("card", "edge", "community", "external", "market", "input")
        for reference in (
            opened_by_kind.get(kind, set())
            if isinstance(opened_by_kind.get(kind), set)
            else set()
        )
    }
    # An alias existing in the run registry only proves that Runtime can decode
    # it; it does not prove the Agent opened that record in this run.  Treating
    # every canonical alias target as opened allowed notebook/navigation
    # locators to survive pruning and fail the stricter evidence audit later.
    payload = proposal.model_dump(mode="python")
    removed: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, list):
            retained_items = []
            for item in value:
                original_evidence_count = (
                    len(item.get("evidence") or [])
                    if isinstance(item, dict)
                    else 0
                )
                visit(item)
                if (
                    isinstance(item, dict)
                    and item.get("claim_type") == "source_claim"
                    and original_evidence_count
                    and len(item.get("evidence") or []) < original_evidence_count
                ):
                    # A source claim often names several publishers in one
                    # sentence. Once any cited source was never opened, keeping
                    # the remaining fragment would preserve an unverifiable
                    # attribution. Drop the complete claim instead.
                    continue
                if (
                    isinstance(item, dict)
                    and item.get("claim_type") == "observed_fact"
                    and original_evidence_count
                    and not item.get("evidence")
                ):
                    # The model occasionally decorates a real market locator
                    # with an invented suffix (for example ``_result``). The
                    # citation is correctly pruned above; retaining the now
                    # unsupported observed fact only creates a deterministic
                    # schema failure that asks the model to repeat the entire
                    # large Proposal. Remove that claim instead. Supported
                    # claims and all auditable evidence remain unchanged.
                    continue
                retained_items.append(item)
            value[:] = retained_items
            return
        if not isinstance(value, dict):
            return
        evidence = value.get("evidence")
        if isinstance(evidence, list):
            kept = []
            for citation in evidence:
                if not isinstance(citation, dict):
                    continue
                reference = str(citation.get("reference") or "")
                canonical = context.evidence_aliases.get(reference, reference)
                if reference in opened or canonical in opened:
                    kept.append(citation)
                else:
                    removed.append(reference)
            value["evidence"] = kept
        for child in value.values():
            visit(child)

    visit(payload)
    if removed:
        logger.info(
            "research_unopened_citations_pruned run_id=%s count=%s references=%s",
            context.run_id,
            len(removed),
            sorted(set(removed))[:12],
        )
    return CurrentResearchReportProposal.model_validate(payload)


def _deduplicate_market_citations(
    proposal: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> CurrentResearchReportProposal:
    """Remove redundant fieldless locators for the same market record.

    Different field-specific locators may be required to support a compound
    fact. A fieldless locator for that exact record adds no evidence and makes
    one upstream payload look like another independent source.
    """

    payload = proposal.model_dump(mode="python")

    def record_key(reference: str) -> tuple | None:
        canonical = context.evidence_aliases.get(reference, reference)
        try:
            identity = decode_market_evidence_locator(canonical)
        except ValueError:
            return None
        return (
            identity.kind,
            identity.domain,
            json.dumps(identity.identity, sort_keys=True, default=str),
            identity.data_type,
            identity.subject_id,
            identity.provider,
            identity.fact_time,
            identity.version,
        )

    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        evidence = value.get("evidence")
        if isinstance(evidence, list):
            field_specific_keys = set()
            seen_references = set()
            for citation in evidence:
                if not isinstance(citation, dict) or citation.get("kind") != "market":
                    continue
                canonical = context.evidence_aliases.get(
                    str(citation.get("reference") or ""),
                    str(citation.get("reference") or ""),
                )
                try:
                    decoded = decode_market_evidence_locator(canonical)
                except ValueError:
                    continue
                if decoded.field:
                    field_specific_keys.add(record_key(str(citation.get("reference"))))
            compacted = []
            for citation in evidence:
                if not isinstance(citation, dict):
                    compacted.append(citation)
                    continue
                reference = str(citation.get("reference") or "")
                if reference in seen_references:
                    continue
                seen_references.add(reference)
                canonical = context.evidence_aliases.get(reference, reference)
                try:
                    decoded = decode_market_evidence_locator(canonical)
                except ValueError:
                    compacted.append(citation)
                    continue
                if not decoded.field and record_key(reference) in field_specific_keys:
                    continue
                compacted.append(citation)
            value["evidence"] = compacted
        for child in value.values():
            visit(child)

    visit(payload)
    return CurrentResearchReportProposal.model_validate(payload)


def _validate_forecast_calibration(
    proposal: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> None:
    """Require each directional forecast to use its own calibrated history.

    Historical results are trusted tool output.  The model may choose the
    research object and interpretation, but it may not transfer calibration
    from a different candidate or turn an insufficient sample into a forecast.
    """

    calibrated: dict[tuple[str, int | None], dict] = {}
    attempted: dict[str, int] = {}
    for invocation in context.tool_invocations:
        if (
            invocation.name != "market_historical_analogue_open"
            or invocation.finished_at is None
            or invocation.result is None
        ):
            continue
        result = _decode_tool_result_object(invocation.result)
        if not result:
            continue
        arguments = invocation.arguments if isinstance(invocation.arguments, dict) else {}
        subject_id = str(result.get("subject_id") or arguments.get("code") or "")
        if not subject_id:
            continue
        sample_count = int(result.get("sample_count") or 0)
        subject_key = _market_subject_equivalence_key(subject_id)
        attempted[subject_key] = max(attempted.get(subject_key, 0), sample_count)
        if result.get("calibration_status") == "calibrated":
            window = _historical_forward_window(result)
            calibration_key = (subject_key, window)
            prior = calibrated.get(calibration_key)
            if prior is None or sample_count > int(prior.get("sample_count") or 0):
                calibrated[calibration_key] = result

    for revision in proposal.view_revisions:
        for forecast in revision.forecasts:
            if forecast.expected_direction == "non_price":
                continue
            subject_key = _market_subject_equivalence_key(forecast.subject_id)
            window = _forecast_window_bars(forecast.metric)
            relative = bool(forecast.benchmark_subject_id) or any(
                term in forecast.metric for term in ("相对", "超额")
            )
            calibration = _select_forecast_calibration(
                calibrated,
                subject_key=subject_key,
                declared_window=window,
                relative=relative,
            )
            if calibration is None:
                observed = attempted.get(subject_key, 0)
                raise ValueError(
                    "Directional Forecast（方向预测）必须使用同一 subject_id（对象）"
                    "达到最低样本量且时间留出方向一致的"
                    " market_historical_analogue_open（历史类比）。"
                    f"预测对象 {forecast.subject_id!r} 当前最多只有 {observed} 个样本。"
                    "请对该对象放宽匹配阈值重查；若仍不足，改选已经校准的候选，"
                    "或删除该方向预测，不能借用其他候选的历史样本。"
                )
            stable_horizons = {
                horizon
                for (key, horizon), result in calibrated.items()
                if key == subject_key
                and horizon is not None
                and _historical_direction_is_stable(result, relative=relative)
            }
            if (
                (window is not None and window not in stable_horizons)
                or len(stable_horizons) < 2
            ):
                raise ValueError(
                    f"预测对象 {forecast.subject_id!r} 的"
                    f"{'相对' if relative else '绝对'}口径尚未在至少两个期限通过"
                    "时间留出方向一致性检查；只能保留为候选和观察要求，不能提交正式 Forecast。"
                )
            if (
                forecast.expected_min_value is None
                or forecast.expected_max_value is None
            ):
                raise ValueError(
                    f"预测对象 {forecast.subject_id!r} 已有历史分布，但方向预测缺少"
                    " expected_min_value / expected_max_value。请确认历史工具返回了"
                    " 与 metric 一致的分位区间；不要凭空估计。"
                )


def _remove_unsupported_directional_forecasts(
    proposal: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> None:
    """Drop directional forecasts that deterministic calibration cannot support.

    Choosing a thesis remains model work. Projecting an unstable historical
    distribution into a formal forecast is a mechanical validity decision and
    should not consume repeated model repair turns.
    """

    stable_windows: dict[tuple[str, bool], set[int]] = {}
    for invocation in context.tool_invocations:
        if (
            invocation.name != "market_historical_analogue_open"
            or invocation.finished_at is None
            or invocation.result is None
        ):
            continue
        result = _decode_tool_result_object(invocation.result)
        subject_id = str(result.get("subject_id") or "")
        if not subject_id or result.get("calibration_status") != "calibrated":
            continue
        window = _historical_forward_window(result)
        if window is None:
            continue
        subject_key = _market_subject_equivalence_key(subject_id)
        for relative in (False, True):
            if _historical_direction_is_stable(result, relative=relative):
                stable_windows.setdefault((subject_key, relative), set()).add(window)

    for revision in proposal.view_revisions:
        revision.forecasts = [
            forecast
            for forecast in revision.forecasts
            if forecast.expected_direction == "non_price"
            or _forecast_has_multi_window_support(forecast, stable_windows)
        ]


def _forecast_has_multi_window_support(
    forecast: Forecast,
    stable_windows: dict[tuple[str, bool], set[int]],
) -> bool:
    """Only formalize forecasts whose direction survives two horizons."""

    relative = bool(forecast.benchmark_subject_id) or any(
        term in forecast.metric for term in ("相对", "超额")
    )
    windows = stable_windows.get(
        (_market_subject_equivalence_key(forecast.subject_id), relative), set()
    )
    declared_window = _forecast_window_bars(forecast.metric)
    return len(windows) >= 2 and (
        declared_window is None or declared_window in windows
    )


def _bind_forecast_calibration_fields(
    proposal: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> None:
    """Bind empirical forecast ranges from deterministic same-object output."""

    calibrated: dict[tuple[str, int | None], dict] = {}
    for invocation in context.tool_invocations:
        if (
            invocation.name != "market_historical_analogue_open"
            or invocation.finished_at is None
            or invocation.result is None
        ):
            continue
        result = _decode_tool_result_object(invocation.result)
        if not result or result.get("calibration_status") != "calibrated":
            continue
        subject_id = str(result.get("subject_id") or "")
        subject_key = _market_subject_equivalence_key(subject_id)
        window = _historical_forward_window(result)
        calibration_key = (subject_key, window)
        prior = calibrated.get(calibration_key)
        if subject_id and (
            prior is None
            or int(result.get("sample_count") or 0)
            > int(prior.get("sample_count") or 0)
        ):
            calibrated[calibration_key] = result

    for revision in proposal.view_revisions:
        for forecast in revision.forecasts:
            if forecast.expected_direction == "non_price":
                continue
            subject_key = _market_subject_equivalence_key(forecast.subject_id)
            window = _forecast_window_bars(forecast.metric)
            relative = bool(forecast.benchmark_subject_id) or any(
                term in forecast.metric for term in ("相对", "超额")
            )
            result = _select_forecast_calibration(
                calibrated,
                subject_key=subject_key,
                declared_window=window,
                relative=relative,
            )
            statistics = result.get("statistics") if result else None
            if result is not None and not _historical_direction_is_stable(
                result, relative=relative
            ):
                continue
            if not isinstance(statistics, dict):
                continue
            lower_key = (
                "lower_quartile_relative_return_pct"
                if relative
                else "lower_quartile_return_pct"
            )
            upper_key = (
                "upper_quartile_relative_return_pct"
                if relative
                else "upper_quartile_return_pct"
            )
            lower = statistics.get(lower_key)
            upper = statistics.get(upper_key)
            if isinstance(lower, (int, float)) and isinstance(upper, (int, float)):
                forecast.expected_min_value = float(lower)
                forecast.expected_max_value = float(upper)
                # Direction is a deterministic interpretation of the bound
                # empirical interval, not prose for the model to improvise.
                # A quartile interval crossing zero is a range forecast even
                # when its median is positive or negative.
                if float(lower) > 0:
                    forecast.expected_direction = "up"
                elif float(upper) < 0:
                    forecast.expected_direction = "down"
                else:
                    forecast.expected_direction = "range"
                if "收益" in forecast.metric:
                    forecast.baseline_value = 0.0


def _historical_forward_window(result: dict) -> int | None:
    value = result.get("forward_window_bars")
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def _select_forecast_calibration(
    calibrated: dict[tuple[str, int | None], dict],
    *,
    subject_key: str,
    declared_window: int | None,
    relative: bool,
) -> dict | None:
    """Select a same-object empirical window without making the model repeat it.

    When a metric says only "future excess return" but the model supplied a
    dated evaluation window, the runtime already owns the dates and the opened
    analogue results. If at least two horizons are stable, use the longest
    stable horizon as the conservative range source. This is mechanical
    binding, not a model-authored market judgment.
    """

    if declared_window is not None:
        result = calibrated.get((subject_key, declared_window))
        if result is not None and _historical_direction_is_stable(
            result, relative=relative
        ):
            return result
        legacy_matches = [
            value
            for (key, _), value in calibrated.items()
            if key == subject_key
            and _historical_direction_is_stable(value, relative=relative)
        ]
        return legacy_matches[0] if len(legacy_matches) == 1 else None
    matches = [
        (window, value)
        for (key, window), value in calibrated.items()
        if key == subject_key
        and window is not None
        and _historical_direction_is_stable(value, relative=relative)
    ]
    if len(matches) < 2:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _forecast_window_bars(metric: str) -> int | None:
    """Read the declared trading-bar horizon from a model-authored metric."""

    match = re.search(r"(?<!\d)(\d{1,3})\s*(?:个?交易日|日|bars?|根)", metric)
    return int(match.group(1)) if match else None


def _historical_direction_is_stable(
    result: dict,
    *,
    relative: bool = False,
) -> bool:
    """Only project a directional range after temporal holdout agrees.

    A calibrated sample count is not enough: if the later holdout window flips
    the median direction, binding its quartiles into a forward forecast gives
    the model a false sense of statistical support.
    """

    robustness = result.get("robustness")
    holdout = (
        robustness.get(
            "relative_temporal_holdout" if relative else "temporal_holdout"
        )
        if isinstance(robustness, dict)
        else None
    )
    if not isinstance(holdout, dict):
        return False
    return holdout.get("median_direction_consistent") is True


def _market_subject_equivalence_key(subject_id: str) -> str:
    """Unify canonical and provider namespaces for the same market object."""

    parts = str(subject_id or "").casefold().split(":")
    if len(parts) >= 3 and parts[-2] in {"concept", "industry", "index"}:
        return f"{parts[-2]}:{parts[-1]}"
    return str(subject_id or "").casefold()


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
        model_settings=research_model_settings(
            model=model,
            # Research performs many iterative tool-selection turns. GLM-5.3
            # can spend minutes reasoning on each high-effort turn, so keep the
            # main exploratory loop on low. The isolated final evaluator also
            # uses low against its bounded audit package; deterministic checks
            # remain responsible for hard evidence invariants.
            reasoning_effort="low",
            parallel_tool_calls=True,
            tool_choice="required",
            # Total per-turn output budget: reasoning plus visible response or
            # tool arguments. This leaves ample room for a compact Proposal
            # without allowing one turn to approach GLM-5.3's 128K ceiling.
            max_tokens=48_000,
        ),
        tools=[
            run_evidence_reopen,
            submit_research_conclusion,
            submit_investment_view_revision,
        ],
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
