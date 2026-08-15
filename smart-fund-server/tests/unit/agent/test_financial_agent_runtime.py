from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.run_config import CallModelData, ModelInputData
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from src.application.agents.financial_research.agent import (
    _bind_research_draft,
    _bind_forecast_calibration_fields,
    _decode_provider_proposal,
    _merge_submission_objects,
    _normalize_provider_proposal,
    _prune_unopened_citations,
    _run_evidence_reopen_enabled,
    _remove_unsupported_directional_forecasts,
    _relevant_plan_references,
    _validated_proposal_is_final,
    _validate_forecast_calibration,
    checkpoint_research_working_memory,
    create_financial_research_agent,
    run_evidence_reopen,
    submit_investment_view_revision,
    submit_research_conclusion,
)
from src.application.agents.financial_research.audit import (
    _market_claim_date_error,
    _normalized_reference,
    _opened_exact_market_record,
    _validate_citation,
    collect_opened_evidence,
    validate_research_result,
)
from src.application.agents.financial_research.context import (
    AgentRunContext,
    ToolInvocation,
)
from src.application.agents.financial_research.context_compactor import (
    checkpoint_hot_evidence_refs,
    validate_context_checkpoint,
)
from src.application.agents.financial_research.instructions import (
    build_run_input,
    load_financial_research_instructions,
)
from src.application.agents.financial_research.model_settings import (
    research_model_settings,
)
from src.infrastructure.agent_runtime.observability import (
    _llm_trace_output,
)
from src.application.agents.financial_research.outcome_evaluator import (
    ResearchOutcomeEvaluator,
)
from src.application.agents.financial_research.research_context import (
    ResearchContextBuilder,
)
from src.application.agents.financial_research.runtime import (
    FinancialAgentRuntime,
    _apply_research_budget_guard,
    _collect_semantic_audit_references,
    _checkpoint_source_projection,
    _compaction_threshold_ratio,
    _compacted_research_notebook_input,
    _decode_notebook_result,
    _expand_evidence_aliases,
    _estimate_input_tokens,
    _estimate_reasoning_surface_tokens,
    _is_research_proposal_output,
    _research_surface_input,
    _reasoning_surface_is_pressured,
    _raise_on_tool_error,
    _project_notebook_result,
    _recent_research_working_notes,
    _semantic_reference_matches,
    _semantic_evidence_excerpt,
    _select_surface_replacement,
    _surface_hot_evidence_items,
    _surface_evidence_index,
)
from src.application.agents.financial_research.schemas import (
    ActiveViewSnapshot,
    CompetingHypothesis,
    CurrentResearchReportProposal,
    EvidenceCitation,
    EvidenceGap,
    EvidencePlanItem,
    Forecast,
    MarketStateFrame,
    OutcomeObservation,
    ResearchContextPack,
    ResearchClaim,
    ResearchMemoryItem,
    ResearchReportDraft,
    ResearchRunStatus,
    ResearchTaskMode,
    ResearchTriggerEnvelope,
)
from src.application.services.market_evidence_locator import (
    MarketEvidenceIdentity,
    encode_market_evidence_locator,
)
from src.infrastructure.agent_runtime.config import AgentSettings
from src.infrastructure.agent_runtime.mcp import (
    _attach_calculation_evidence,
    _compact_market_evidence,
    _decode_tool_result_object,
    _read_call_key,
    create_mcp_server,
    financial_tool_filter,
    research_ledger_missing_requirements,
)
from src.interfaces.mcp.projection import project_tool_result


from src.infrastructure.persistence.models.agent_research import (
    AgentCurrentResearchReport,
    AgentInvestmentViewRevision,
    AgentResearchForecast,
    AgentResearchOutcomeEvaluation,
)
from src.infrastructure.persistence.repositories.agent_research_repository import (
    AgentResearchRepository,
)
from src.interfaces.cli.main import cli


CUTOFF = datetime(2026, 8, 9, 6, 0, tzinfo=UTC)


def test_historical_analogue_projection_keeps_leakage_and_sensitivity() -> None:
    projected = project_tool_result("market_historical_analogue_open", {
        "subject_id": "ths:concept:886033",
        "statistics": {"median_return_pct": 9.9},
        "robustness": {
            "leakage_controls": {
                "point_in_time_features_only": True,
                "future_bars_excluded_from_signal": True,
                "non_overlapping_forward_windows": True,
                "minimum_signal_gap_bars": 6,
                "temporal_split": "chronological_older_vs_newer_half",
            },
            "threshold_sensitivity": [
                {"match_distance_threshold": 1.75, "sample_count": 12},
                {"match_distance_threshold": 2.5, "sample_count": 18},
            ],
            "strict_statistics": {"median_return_pct": 1.2},
            "trimmed_one_each_tail_statistics": {"median_return_pct": 1.1},
            "temporal_holdout": {
                "development_statistics": {
                    "median_return_pct": 1.0,
                    "positive_share": 0.6,
                },
                "holdout_statistics": {
                    "median_return_pct": -0.5,
                    "positive_share": 0.4,
                },
                "median_direction_consistent": False,
                "validation_status": "direction_shift",
            },
        },
    })

    assert projected["robustness"]["leakage_safe"] is True
    assert len(projected["robustness"]["threshold_sensitivity"]) == 2
    assert projected["full_sample_distribution"]["median_return_pct"] == 9.9
    assert "temporal_holdout" not in projected["robustness"]
    assert projected["calibration_readout"]["absolute_return"] == {
        "development_median_return_pct": 1.0,
        "holdout_median_return_pct": -0.5,
        "development_positive_share": 0.6,
        "holdout_positive_share": 0.4,
        "median_direction_consistent": False,
        "validation_status": "direction_shift",
    }
    assert "strict_statistics" not in projected["robustness"]
    assert projected["distribution_stability_readout"][
        "strict_vs_full_direction_conflict"
    ] is False


def test_market_frame_projection_keeps_capabilities_without_dashboard_metadata() -> None:
    projected = project_tool_result("market_frame_open", {
        "market": "cn",
        "market_session": "closed",
        "trade_date": "2026-08-15",
        "dimensions": [{
            "dimension": "sector_style",
            "as_of": "2026-08-15 15:00",
            "subject_count": 1269,
            "trade_dates": ["2026-08-15", "2026-08-14", "2026-08-13", "2026-08-12"],
            "trade_dates_truncated": True,
            "freshness": {"realtime": 287, "fetch_time": 752},
            "data_types": [
                {"data_type": "sector_quote", "subject_count": 265},
                {"data_type": "sector_flow", "subject_count": 228},
            ],
            "drilldown_handle": "market-dimension:sector_style:long-timestamp",
            "next_operation": "market_dimension_open",
        }],
    })

    assert projected["dimensions"] == [{
        "dimension": "sector_style",
        "latest_fact_time": "2026-08-15 15:00",
        "latest_trade_dates": ["2026-08-15", "2026-08-14", "2026-08-13"],
        "available_data_types": ["sector_quote", "sector_flow"],
        "more_trade_dates_available": True,
    }]
    assert "freshness" not in json.dumps(projected)
    assert "drilldown_handle" not in json.dumps(projected)


def test_sector_comparison_projection_hoists_identity_and_drops_empty_anchors() -> None:
    projected = project_tool_result("market_sector_compare_open", {
        "candidate_count": 1,
        "candidates": [{
            "provider_sector_code": "886033",
            "found": True,
            "latest_signals": [{
                "data_type": "ths_sector_ranking",
                "subject_id": "ths_native:concept:886033:concept_change",
                "provider_sector_code": "886033",
                "sector_name": "共封装光学(CPO)",
                "sector_type": "concept",
                "metric": "change",
                "metric_value": 2.93,
                "trade_date": "2026-08-15",
                "freshness_status": "fetch_time",
                "provider": "ths_native",
                "evidence_locator": "market_ref:M1",
            }],
            "history": [{"anchors": [{
                "trade_date": "2026-08-01",
                "data": {"provider_sector_code": "886033"},
                "evidence_locator": "market_ref:M2",
            }]}],
        }],
        "next_operations": ["market_evidence_open"],
    })

    candidate = projected["candidates"][0]
    assert candidate["provider_sector_code"] == "886033"
    assert candidate["latest_signals"] == [{
        "metric": "change",
        "metric_value": 2.93,
        "trade_date": "2026-08-15",
        "evidence_locator": "market_ref:M1",
    }]
    assert "history" not in candidate
    assert all("provider" not in item for item in candidate["latest_signals"])


def test_natural_language_cannot_end_research_run() -> None:
    assert hasattr(FinancialAgentRuntime, "prepare_and_run")
    assert _is_research_proposal_output("现在打开证据账本。") is False
    assert _is_research_proposal_output('{"status":"updated"}') is False


def test_submission_repair_merges_omitted_fields_and_honours_explicit_deletion() -> None:
    previous = {
        "report_summary": "original",
        "view_revisions": [{"view_id": "v1"}],
        "market_structure": {"breadth": "narrow", "volume": "weak"},
    }
    repaired = _merge_submission_objects(previous, {
        "report_summary": "repaired",
        "market_structure": {"breadth": "broad"},
        "view_revisions": [],
    })

    assert repaired == {
        "report_summary": "repaired",
        "view_revisions": [],
        "market_structure": {"breadth": "broad", "volume": "weak"},
    }


def test_submission_repair_merges_partial_view_revision_by_position() -> None:
    previous = {
        "view_revisions": [
            {
                "view_id": "v1",
                "title": "original",
                "claims": [{"claim_id": "c1"}],
                "confidence": {"overall": "low"},
            }
        ]
    }

    repaired = _merge_submission_objects(
        previous,
        {"view_revisions": [{"title": "repaired"}]},
    )

    assert repaired["view_revisions"] == [
        {
            "view_id": "v1",
            "title": "repaired",
            "claims": [{"claim_id": "c1"}],
            "confidence": {"overall": "low"},
        }
    ]


def test_glm53_role_settings_use_supported_reasoning_effort() -> None:
    main = research_model_settings(
        model="glm-5.3",
        reasoning_effort="max",
        parallel_tool_calls=True,
        tool_choice="required",
        max_tokens=48_000,
    )
    compactor = research_model_settings(
        model="glm-5.3",
        reasoning_effort="low",
        parallel_tool_calls=False,
    )

    assert main.extra_body == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "max",
    }
    assert main.reasoning is not None
    assert main.reasoning.summary == "auto"
    assert main.max_tokens == 48_000
    assert compactor.extra_body == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
    }
    assert compactor.reasoning is not None
    assert compactor.reasoning.summary == "auto"


def test_glm53_research_agent_uses_low_effort_for_multi_turn_work() -> None:
    agent = create_financial_research_agent(model="glm-5.3", mcp_server=None)

    assert agent.model_settings.extra_body == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "low",
    }
    assert agent.model_settings.max_tokens == 48_000


def test_non_glm53_role_settings_keep_provider_reasoning_defaults() -> None:
    settings = research_model_settings(
        model="glm-5.2",
        reasoning_effort="max",
        parallel_tool_calls=True,
    )

    assert settings.extra_body == {"thinking": {"type": "disabled"}}
    assert settings.reasoning is None


def test_agent_langfuse_output_keeps_responses_reasoning_summary() -> None:
    output = _llm_trace_output(
        [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "最终结论"}],
            },
            {
                "type": "reasoning",
                "summary": [
                    {
                        "type": "summary_text",
                        "text": "先比较支持证据，再检查反证。",
                    }
                ],
                "encrypted_content": "opaque-provider-state",
            },
        ]
    )

    assert output["assistant_text"] == "最终结论"
    assert output["reasoning_summaries"] == [
        "先比较支持证据，再检查反证。"
    ]
    assert "opaque-provider-state" not in json.dumps(output, ensure_ascii=False)


def test_agent_langfuse_output_keeps_native_reasoning_text_content() -> None:
    output = _llm_trace_output(
        [
            {
                "type": "reasoning",
                "summary": [],
                "content": [
                    {"type": "reasoning_text", "text": "先筛选候选，再核验证据。"}
                ],
                "encrypted_content": None,
            }
        ]
    )

    assert output["reasoning_summaries"] == ["先筛选候选，再核验证据。"]


@pytest.mark.asyncio
async def test_working_memory_checkpoint_replaces_run_local_state() -> None:
    context = _context()
    payload = {
        "research_goal": "比较两个候选",
        "candidate_hypotheses": [],
        "answered_questions": [],
        "remaining_questions": ["哪个候选历史表现更稳健"],
        "discarded_paths": ["不再研究无关板块"],
        "next_step": "查询两个候选的历史类比",
    }

    result = await checkpoint_research_working_memory.on_invoke_tool(
        SimpleNamespace(context=context), json.dumps(payload, ensure_ascii=False)
    )

    assert json.loads(result)["saved"] is True
    assert context.working_memory == payload
    assert context.working_memory_revision == 1


def test_compacted_notebook_retains_structured_working_memory() -> None:
    memory = {
        "research_goal": "形成观点",
        "candidate_hypotheses": [],
        "answered_questions": [],
        "remaining_questions": ["补直接反证"],
        "discarded_paths": ["黄金"],
        "next_step": "打开反方原文",
    }

    compacted = _compacted_research_notebook_input(
        original_input=[{"role": "user", "content": "{}"}],
        invocations=[],
        working_memory=memory,
        working_memory_revision=3,
    )
    notebook = json.loads(compacted[1]["content"])["research_notebook"]

    assert notebook["working_memory"]["revision"] == 3
    assert notebook["working_memory"]["state"] == memory


def test_context_checkpoint_requires_plain_markdown_sections() -> None:
    checkpoint = "\n".join(
        f"## {section}\n- 无"
        for section in (
            "研究目标与用户意图", "当前判断", "候选假设",
            "已削弱或排除的解释", "已完成验证", "关键证据索引",
            "最强反证索引", "热证据原文保留", "未解决问题",
            "当前工作", "下一步", "关键限制",
        )
    )
    assert validate_context_checkpoint(checkpoint) == checkpoint


def test_surface_replacement_keeps_exact_tail_and_balances_tool_pair() -> None:
    raw = [
        {"role": "user", "content": "任务"},
        {"type": "function_call", "call_id": "c1", "name": "open"},
        {"type": "function_call_output", "call_id": "c1", "output": "x" * 400},
        {"role": "assistant", "content": "下一步"},
    ]
    selected = _select_surface_replacement(
        raw_input=raw,
        checkpoint=None,
        shadowed_item_count=0,
        generation=0,
        retain_tokens=30,
    )
    assert selected is not None
    source, retained, shadowed_count = selected
    assert source[-2:] == raw[1:3]
    assert retained == raw[3:]
    assert shadowed_count == 3


def test_surface_replacement_keeps_reasoning_that_introduces_retained_tool_turn() -> None:
    raw = [
        {"role": "user", "content": "任务"},
        {"type": "reasoning", "summary": [{"text": "先打开证据"}]},
        {"type": "function_call", "call_id": "c1", "name": "open"},
        {"type": "function_call_output", "call_id": "c1", "output": "x" * 400},
    ]

    selected = _select_surface_replacement(
        raw_input=raw,
        checkpoint=None,
        shadowed_item_count=0,
        generation=0,
        retain_tokens=120,
    )

    assert selected is not None
    source, retained, shadowed_count = selected
    assert source == raw[:1]
    assert retained == raw[1:]
    assert shadowed_count == 1


def test_reasoning_surface_pressure_requires_both_size_and_dominance() -> None:
    context = _context()
    reasoning_item = {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "分析" * 20_000}],
    }
    reasoning_tokens = _estimate_reasoning_surface_tokens([reasoning_item])

    assert reasoning_tokens >= 18_000
    assert _reasoning_surface_is_pressured(
        context=context,
        total_tokens=reasoning_tokens * 2,
        reasoning_tokens=reasoning_tokens,
    ) is True
    assert _reasoning_surface_is_pressured(
        context=context,
        total_tokens=reasoning_tokens * 5,
        reasoning_tokens=reasoning_tokens,
    ) is False


def test_reasoning_surface_pressure_is_disabled_after_evidence_ledger() -> None:
    context = _context()
    context.tool_invocations.append(
        _finished_invocation("agent_evidence_ledger_open", 1)
    )

    assert _reasoning_surface_is_pressured(
        context=context,
        total_tokens=40_000,
        reasoning_tokens=20_000,
    ) is False


def test_checkpoint_source_projection_omits_reasoning_but_keeps_exact_facts() -> None:
    source = [
        {"role": "user", "content": "研究目标"},
        {"type": "reasoning", "summary": [{"text": "旧推理"}]},
        {"type": "function_call", "call_id": "c1", "name": "open"},
        {"type": "function_call_output", "call_id": "c1", "output": "事实"},
        {"role": "assistant", "content": "下一步核验反证"},
    ]

    projected = _checkpoint_source_projection(source)

    assert projected == [source[0], *source[2:]]
    assert source[1]["summary"][0]["text"] == "旧推理"


def test_surface_projection_merges_checkpoint_with_unshadowed_history() -> None:
    raw = [{"role": "user", "content": str(index)} for index in range(4)]
    surface = _research_surface_input(
        raw_input=raw,
        checkpoint="## 旧检查点",
        hot_items=[],
        shadowed_item_count=2,
        generation=1,
        transient=[{"role": "user", "content": "提醒"}],
    )
    assert "旧检查点" in surface[0]["content"]
    assert surface[1:3] == raw[2:]
    assert surface[-1]["content"] == "提醒"


def test_checkpoint_selects_at_most_six_immediate_hot_evidence_refs() -> None:
    checkpoint = """## 热证据原文保留
- run_evidence:E14
- run_evidence:E23
- run_evidence:E14
## 未解决问题
- 无"""

    assert checkpoint_hot_evidence_refs(checkpoint) == [
        "run_evidence:E14",
        "run_evidence:E23",
    ]


def test_surface_keeps_checkpoint_selected_hot_call_result_pairs() -> None:
    raw = [
        {"role": "user", "content": "任务"},
        {"type": "function_call", "call_id": "cold-call", "name": "open"},
        {"type": "function_call_output", "call_id": "cold-call", "output": "关键事实"},
        {"type": "function_call", "call_id": "tail-call", "name": "ledger"},
        {"type": "function_call_output", "call_id": "tail-call", "output": "账本"},
    ]
    invocations = [
        ToolInvocation(name="market_sector_compare_open", call_id="cold-call"),
        ToolInvocation(name="agent_evidence_ledger_open", call_id="tail-call"),
    ]

    hot = _surface_hot_evidence_items(
        raw_input=raw,
        invocations=invocations,
        evidence_refs=["run_evidence:E1", "run_evidence:E2"],
        retained_items=raw[3:],
    )
    surface = _research_surface_input(
        raw_input=raw,
        checkpoint="## 检查点",
        hot_items=hot,
        shadowed_item_count=3,
        generation=1,
        transient=[],
    )

    assert hot == raw[1:3]
    assert surface[1:3] == raw[1:3]
    assert surface[3:] == raw[3:]


def test_compaction_threshold_reserves_runway_after_first_replacement() -> None:
    context = _context()
    initial = _compaction_threshold_ratio(context)

    context.surface_generation = 1

    assert initial < 0.90
    assert _compaction_threshold_ratio(context) == min(initial, 0.42)


def test_compaction_threshold_protects_post_ledger_submission() -> None:
    context = _context()
    context.surface_generation = 1
    context.tool_invocations.append(
        _finished_invocation("agent_evidence_ledger_open", 1)
    )

    assert _compaction_threshold_ratio(context) == 0.95


def test_compacted_notebook_keeps_current_market_results_with_many_analogues() -> None:
    invocations = []
    for index in range(8):
        invocations.append(ToolInvocation(
            name="market_historical_analogue_open",
            call_id=f"history-{index}",
            arguments={"code": f"candidate-{index}"},
            result=json.dumps({"subject_id": f"candidate-{index}", "payload": "h" * 4_000}),
            finished_at=CUTOFF,
        ))
    invocations.extend([
        ToolInvocation(
            name="market_sector_open",
            call_id=f"sector-{index}",
            arguments={"provider_sector_code": str(881100 + index)},
            result=json.dumps({"subject_id": f"sector-{index}", "payload": "m" * 8_000}),
            finished_at=CUTOFF,
        )
        for index in range(3)
    ])

    compacted = _compacted_research_notebook_input(
        original_input=[{"role": "user", "content": "{}"}],
        invocations=invocations,
    )
    notebook = json.loads(compacted[1]["content"])["research_notebook"]
    retained_tools = [item["tool"] for item in notebook["retained_results"]]

    assert retained_tools.count("market_historical_analogue_open") == 8
    assert retained_tools.count("market_sector_open") == 3


def test_compacted_notebook_retains_global_overview_before_repeated_analogues() -> None:
    invocations = [ToolInvocation(
        name="market_global_overview_open",
        call_id="global",
        arguments={},
        result=json.dumps({
            "us_indices": [{"symbol": "IXIC", "change_pct": 0.81}],
            "us_breadth": {"advancing": 6798, "declining": 3618},
            "evidence": ["market_ref:US1"],
        }),
        finished_at=CUTOFF,
    )]
    invocations.extend(
        ToolInvocation(
            name="market_historical_analogue_open",
            call_id=f"history-{index}",
            arguments={"code": f"candidate-{index}"},
            result=json.dumps({
                "subject_id": f"candidate-{index}",
                "payload": "h" * 9_000,
            }),
            finished_at=CUTOFF,
        )
        for index in range(12)
    )

    compacted = _compacted_research_notebook_input(
        original_input=[{"role": "user", "content": "{}"}],
        invocations=invocations,
    )
    notebook = json.loads(compacted[1]["content"])["research_notebook"]
    global_result = next(
        item for item in notebook["retained_results"]
        if item["tool"] == "market_global_overview_open"
    )

    assert global_result["result"]["us_indices"][0]["symbol"] == "IXIC"
    assert global_result["result"]["evidence"] == ["market_ref:US1"]


@pytest.mark.asyncio
async def test_run_evidence_reopen_restores_folded_result_by_order_and_path() -> None:
    context = _context()
    context.tool_invocations = [ToolInvocation(
        name="market_global_overview_open",
        call_id="global",
        arguments={},
        result=json.dumps({
            "us_indices": [
                {"symbol": "DJI", "change_pct": 0.4},
                {"symbol": "IXIC", "change_pct": 0.81},
            ],
        }),
        finished_at=CUTOFF,
    )]

    result = await run_evidence_reopen.on_invoke_tool(
        SimpleNamespace(context=context),
        json.dumps({"order": 1, "path": "us_indices.1"}),
    )
    payload = json.loads(result)

    assert payload["available"] is True
    assert payload["tool"] == "market_global_overview_open"
    assert json.loads(payload["content"]) == {"symbol": "IXIC", "change_pct": 0.81}


def test_run_evidence_reopen_remains_enabled_after_evidence_ledger() -> None:
    context = _context()
    context.notebook_compacted = True
    context.tool_invocations.append(ToolInvocation(
        name="agent_evidence_ledger_open",
        call_id="ledger",
        arguments={},
        result="{}",
        finished_at=CUTOFF,
    ))

    assert _run_evidence_reopen_enabled(SimpleNamespace(context=context), None)


def test_only_market_tool_aliases_are_authoritative_opened_evidence() -> None:
    context = _context()
    locator = encode_market_evidence_locator(MarketEvidenceIdentity(
        kind="snapshot",
        domain="market_snapshot",
        data_type="ths_us_market_module",
        subject_id="indices_stream",
        provider="ths_native_stream",
        identity={"id": 1},
    ))
    context.evidence_aliases["market_ref:M1"] = locator

    opened = collect_opened_evidence(context)

    assert "market_ref:M1" not in opened["market"]
    context.opened_market_aliases.add("market_ref:M1")
    opened = collect_opened_evidence(context)

    assert "market_ref:M1" in opened["market"]
    assert _normalized_reference(locator) in opened["market"]


def test_submit_boundary_prunes_citations_not_opened_in_current_run() -> None:
    context = _context()
    context.tool_invocations = [
        ToolInvocation(
            name="kg_card_open",
            call_id="card",
            arguments={"card_id": "kg_cognitive_card:opened"},
            result=json.dumps({"card_id": "kg_cognitive_card:opened"}),
            finished_at=CUTOFF,
        )
    ]
    proposal = _no_change_report()
    proposal.claims = [ResearchClaim(
        claim_id="claim-1",
        claim_type="source_claim",
        epistemic_status="supported",
        statement="已打开来源陈述了一项事实。",
        thesis_effect="context",
        confidence="medium",
        evidence=[
            EvidenceCitation(
                citation_id="citation-opened",
                kind="card",
                reference="kg_cognitive_card:opened",
                support="supports",
            ),
            EvidenceCitation(
                citation_id="citation-stale",
                kind="card",
                reference="kg_cognitive_card:stale",
                support="context",
            ),
        ],
    )]

    pruned = _prune_unopened_citations(proposal, context)
    references = [
        citation.reference
        for claim in pruned.claims
        for citation in claim.evidence
    ]

    assert references == []
    assert pruned.claims == []


def test_submit_boundary_drops_observed_fact_when_its_only_locator_is_unopened() -> None:
    context = _context()
    proposal = _no_change_report()
    proposal.claims = [ResearchClaim(
        claim_id="claim-unopened-market",
        claim_type="observed_fact",
        epistemic_status="supported",
        statement="模型使用了带虚构后缀的行情定位符。",
        thesis_effect="context",
        confidence="medium",
        evidence=[EvidenceCitation(
            citation_id="citation-unopened-market",
            kind="market",
            reference="market_ref:M30_result",
            support="supports",
        )],
    )]

    pruned = _prune_unopened_citations(proposal, context)

    assert pruned.claims == []


def test_notebook_decoder_unwraps_agents_sdk_text_envelope() -> None:
    payload = {
        "subject_id": "ths:concept:886033",
        "calibration_status": "calibrated",
        "sample_count": 30,
    }
    envelope = {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
        ]
    }

    assert _decode_notebook_result(json.dumps(envelope)) == payload


def test_submit_tool_uses_flat_proposal_schema() -> None:
    assert "proposal" not in submit_investment_view_revision.params_json_schema["properties"]
    assert "view_revisions" in submit_investment_view_revision.params_json_schema["properties"]
    nested = submit_investment_view_revision.params_json_schema["$defs"][
        "InvestmentViewRevisionProposal"
    ]
    assert "hypotheses" not in nested["properties"]
    assert "evidence_plan" not in nested["properties"]
    citation = submit_investment_view_revision.params_json_schema["$defs"][
        "EvidenceCitation"
    ]
    assert not {
        "citation_id",
        "kind",
        "observed_at",
        "as_of",
    }.intersection(citation["properties"])
    assert "research_question" not in (
        submit_investment_view_revision.params_json_schema["properties"]
    )
    plan = submit_investment_view_revision.params_json_schema["$defs"][
        "EvidencePlanItem"
    ]
    assert "opened_references" not in plan["properties"]
    gap = submit_investment_view_revision.params_json_schema["$defs"]["EvidenceGap"]
    assert "attempted_tools" not in gap["properties"]
    forecast = submit_investment_view_revision.params_json_schema["$defs"]["Forecast"]
    assert "evaluation_start_at" not in forecast["properties"]
    requirement = submit_investment_view_revision.params_json_schema["$defs"][
        "ObservationRequirement"
    ]
    assert "requirement_id" not in requirement["properties"]
    assert "budget_exhausted" not in gap["properties"]["reason"]["enum"]


def test_submit_boundary_normalizes_non_judgmental_provider_shape_noise() -> None:
    evidence = [
        {"reference": f"market_ref:M{index}", "support": "supports"}
        for index in range(13)
    ]
    proposal = {
        "view_revisions": [{
            "confidence": {
                "overall": "medium_low",
                "evidence_quality": "moderate",
                "independent_confirmation": "medium-high",
                "counterevidence_resilience": "very_low",
                "timing_clarity": "very_high",
            },
            "mechanism_chain": [{"evidence": evidence}],
        }],
    }

    normalized = _normalize_provider_proposal(proposal)
    revision = normalized["view_revisions"][0]

    assert revision["confidence"] == {
        "overall": "low",
        "evidence_quality": "medium",
        "independent_confirmation": "medium_high",
        "counterevidence_resilience": "low",
        "timing_clarity": "high",
    }
    assert len(revision["mechanism_chain"][0]["evidence"]) == 12
    assert len(evidence) == 13


def test_submit_boundary_repairs_program_derivable_roles_and_claim_semantics() -> None:
    normalized = _normalize_provider_proposal({
        "hypotheses": [
            {"hypothesis_id": "h_main"},
            {"hypothesis_id": "h_data_missing"},
        ],
        "claims": [{
            "claim_id": "aggregate",
            "claim_type": "observed_fact",
            "evidence": [{"reference": "market_ref:M1", "support": "context"}],
        }],
    })

    assert normalized["hypotheses"][0]["role"] == "primary"
    assert normalized["hypotheses"][1]["role"] == "data_quality"
    assert normalized["claims"][0]["claim_type"] == "inference"


def test_submit_boundary_normalizes_instrument_expression_evidence_layers() -> None:
    normalized = _normalize_provider_proposal({
        "evidence_plan": [
            {"plan_item_id": "p1", "layer": "expression"},
            {"plan_item_id": "p2", "layer": "ETF"},
            {"plan_item_id": "p3", "layer": "history"},
        ],
    })

    assert [item["layer"] for item in normalized["evidence_plan"]] == [
        "object",
        "object",
        "history",
    ]


def test_submit_boundary_removes_only_mixed_unknown_hypothesis_ids() -> None:
    normalized = _normalize_provider_proposal({
        "hypotheses": [
            {"hypothesis_id": "h-main"},
            {"hypothesis_id": "h-alt"},
        ],
        "evidence_plan": [
            {"hypothesis_ids": ["h-main", "h-invented"]},
            {"hypothesis_ids": ["h-invented-only"]},
        ],
    })

    assert normalized["evidence_plan"][0]["hypothesis_ids"] == ["h-main"]
    assert normalized["evidence_plan"][1]["hypothesis_ids"] == [
        "h-invented-only"
    ]


def test_submit_boundary_maps_ordinal_hypothesis_aliases_by_role() -> None:
    normalized = _normalize_provider_proposal({
        "hypotheses": [
            {"hypothesis_id": "h-mainline", "role": "primary"},
            {"hypothesis_id": "h-rotation", "role": "alternative"},
            {"hypothesis_id": "h-calendar", "role": "data_quality"},
        ],
        "evidence_plan": [
            {"hypothesis_ids": ["H1", "H2", "H3"]},
        ],
    })

    assert normalized["evidence_plan"][0]["hypothesis_ids"] == [
        "h-mainline",
        "h-rotation",
        "h-calendar",
    ]


def test_submit_boundary_normalizes_provider_hypothesis_status_aliases() -> None:
    normalized = _normalize_provider_proposal({
        "hypotheses": [
            {"hypothesis_id": "h_main", "status": "unresolved"},
            {"hypothesis_id": "h_alt", "status": "pending"},
        ],
        "view_revisions": [{
            "hypotheses": [
                {"hypothesis_id": "h_data_missing", "status": "unknown"},
            ],
        }],
    })

    assert normalized["hypotheses"][0]["status"] == "inconclusive"
    assert normalized["hypotheses"][1]["status"] == "unverified"
    assert normalized["view_revisions"][0]["hypotheses"][0]["status"] == "inconclusive"


def test_submit_boundary_marks_omitted_market_structure_dimensions_unverified() -> None:
    normalized = _normalize_provider_proposal({
        "view_revisions": [{
            "market_structure": {
                "breadth": "跌多涨少",
                "pricing_state": "partially_priced",
            },
        }],
    })
    market_structure = normalized["view_revisions"][0]["market_structure"]

    assert market_structure["breadth"] == "跌多涨少"
    assert market_structure["pricing_state"] == "partially_priced"
    assert market_structure["volume_liquidity_confirmation"] == (
        "本轮未形成可验证的成交量或流动性确认结论。"
    )
    assert all(market_structure[field] for field in (
        "leadership_concentration",
        "crowding_and_reversal_risk",
        "persistence_assessment",
    ))


def test_submit_boundary_normalizes_expired_view_event_alias() -> None:
    normalized = _normalize_provider_proposal({
        "view_revisions": [{
            "event": "expire",
            "status": "expired",
        }],
    })

    assert normalized["view_revisions"][0] == {
        "event": "invalidate",
        "status": "expired",
    }


def test_submit_boundary_removes_empty_analogue_pseudo_citations() -> None:
    normalized = _normalize_provider_proposal({
        "claims": [{
            "claim_id": "empty",
            "claim_type": "observed_fact",
            "evidence": [{
                "kind": "external",
                "reference": "market_historical_analogue_open:ths:concept:886033:empty",
                "support": "supports",
            }],
        }],
    })

    assert normalized["claims"][0]["evidence"] == []
    assert normalized["claims"][0]["claim_type"] == "inference"


def test_semantic_audit_collects_citations_beyond_top_level_claims() -> None:
    references = _collect_semantic_audit_references({
        "claims": [{
            "evidence": [{
                "reference": "market:v1:claim",
                "support": "supports",
            }],
        }],
        "view_revisions": [{
            "mechanism_chain": [{
                "evidence": [{
                    "reference": "https://example.com/mechanism",
                    "support": "context_only",
                }],
            }],
            "forecasts": [{
                "evidence": [{
                    "reference": "market:v1:forecast",
                    "support": "supports",
                }],
            }],
        }],
    })

    assert references == {
        "market:v1:claim",
        "https://example.com/mechanism",
        "market:v1:forecast",
    }
    assert _semantic_reference_matches(
        "market:v1:canonical",
        "market_ref:M50",
        '{"evidence_locator":"market_ref:M50"}',
    )


def test_notebook_projection_retains_bounded_external_source_substance() -> None:
    projected = _project_notebook_result(
        "external_web_read",
        {
            "provider": "zhipu",
            "title": "CPO量产观察",
            "url": "https://example.com/cpo",
            "content_handle": "external_content:1",
            "preview": "关键反证" * 2_000,
            "media_type": "text/markdown",
        },
    )

    assert projected["source_excerpt"].startswith("关键反证")
    assert len(projected["source_excerpt"]) == 4_500
    assert projected["source_excerpt_truncated"] is True
    assert "media_type" not in projected


def test_notebook_projection_keeps_analogue_aggregates_for_parallel_candidates() -> None:
    samples = [{"signal_date": f"2026-01-{day:02d}"} for day in range(1, 11)]
    source_result = project_tool_result(
        "market_historical_analogue_open",
        {
            "subject_id": "ths:concept:886033",
            "sample_count": 30,
            "calibration_status": "calibrated",
            "statistics": {"positive_share": 0.4333, "median_return_pct": -0.69},
            "robustness": {
                "strict_statistics": {"median_return_pct": 0.25},
                "temporal_holdout": {
                    "development_statistics": {"median_return_pct": 0.5},
                    "holdout_statistics": {"median_return_pct": -0.7},
                    "median_direction_consistent": False,
                },
            },
            "samples": samples,
            "evidence_locators": ["market_ref:M1", "market_ref:M2"],
            "analysis_evidence_locator": "market_ref:M3",
        },
    )
    projected = _project_notebook_result(
        "market_historical_analogue_open",
        source_result,
    )

    assert projected["full_sample_distribution"]["median_return_pct"] == -0.69
    assert "robustness" not in projected or "temporal_holdout" not in projected["robustness"]
    assert projected["calibration_readout"]["absolute_return"][
        "holdout_median_return_pct"
    ] == -0.7
    assert projected["distribution_stability_readout"][
        "strict_vs_full_direction_conflict"
    ] is True
    assert projected["analysis_evidence_locator"] == "market_ref:M3"
    assert "evidence_locators" not in projected
    assert "sample_examples" not in projected
    assert "samples" not in projected


def test_analogue_result_receives_stable_calculation_evidence_locator() -> None:
    payload = {
        "subject_id": "ths:concept:886033",
        "data_type": "ths_sector_daily",
        "signal_definition": {"return_5_bars_pct": 3.0},
        "forward_window_bars": 3,
        "sample_count": 30,
        "statistics": {"median_return_pct": -0.69},
        "robustness": {"temporal_holdout": {"median_direction_consistent": False}},
    }
    first = _attach_calculation_evidence(payload, "market_historical_analogue_open")
    second = _attach_calculation_evidence(payload, "market_historical_analogue_open")

    assert first["analysis_evidence_locator"].startswith("market:v1:")
    assert first["analysis_evidence_locator"] == second["analysis_evidence_locator"]
    assert "analysis_evidence_locator" not in payload


def test_compaction_preserves_recent_non_evidentiary_working_plan() -> None:
    notes = _recent_research_working_notes([
        {"role": "user", "content": "研究问题"},
        {"role": "assistant", "text": "先比较CPO与半导体。"},
        {"type": "tool_call", "tool": "market_sector_open"},
        {"role": "assistant", "text": "CPO历史反证更强；只剩来源核验。"},
    ])

    assert [item["text"] for item in notes] == [
        "先比较CPO与半导体。",
        "CPO历史反证更强；只剩来源核验。",
    ]
    assert all(item["type"] == "non_evidentiary_working_note" for item in notes)


def test_finalizer_selects_relevant_plan_references_instead_of_copying_ledger() -> None:
    references = _relevant_plan_references(
        {
            "question": "CPO概念886033的120日趋势是否持续？",
            "required_evidence": "CPO板块历史K线",
        },
        [
            ("黄金ETF 518880短期上涨", "market_ref:M1"),
            ("CPO概念886033近120日累计上涨", "market_ref:M2"),
            ("半导体行业881121资金净流入", "market_ref:M3"),
        ],
    )

    assert references[0] == "market_ref:M2"
    assert "market_ref:M1" not in references


def test_mcp_http_request_uses_tool_timeout_not_connect_timeout() -> None:
    settings = AgentSettings.from_mapping(
        {
            "SMART_FUND_AGENT_MCP_CONNECT_TIMEOUT": "15",
            "SMART_FUND_AGENT_MCP_TOOL_TIMEOUT": "120",
        }
    )

    server = create_mcp_server(settings)

    assert server.params["timeout"] == 120.0
    assert server.client_session_timeout_seconds == 120.0
    assert server.params["sse_read_timeout"] == 1800.0


def test_exact_market_evidence_requires_a_returned_locator_not_only_tool_name() -> None:
    context = AgentRunContext(
        run_id="run-exact",
        session_id="session-exact",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
        tool_invocations=[
            ToolInvocation(
                name="market_evidence_open",
                call_id="missing",
                result='{"status":"unavailable"}',
            )
        ],
    )
    assert _opened_exact_market_record(context) is False

    context.tool_invocations.append(
        ToolInvocation(
            name="market_sector_compare_open",
            call_id="opened",
            result='{"evidence_locator":"market_ref:M7"}',
        )
    )
    assert _opened_exact_market_record(context) is True


def test_market_history_projection_keeps_bars_and_adds_window_statistics() -> None:
    projected = project_tool_result("market_instrument_history", {
        "code": "cn:index:000001",
        "data_type": "ths_index_daily",
        "items": [
            {"trade_date": f"2026-08-{day:02d}", "data": {
                "open": day, "high": day + 1, "low": day - 1,
                "close": day, "volume": day * 100,
            }}
            for day in range(12, 7, -1)
        ],
        "series_semantics": {"volume_field": "unknown"},
    })

    assert projected["bar_count"] == 5
    assert projected["bars"][0] == ["2026-08-12", 12, 13, 11, 12, 1200]
    assert projected["window_statistics"]["return_5_bars_pct"] == 50
    assert projected["window_statistics"]["close_high_5_bars"] == ["2026-08-12", 12]
    assert projected["window_statistics"]["drawdown_from_close_high_5_bars_pct"] == 0
    assert projected["window_statistics"]["intraday_high_5_bars"] == ["2026-08-12", 13]
    assert projected["window_statistics"][
        "drawdown_from_intraday_high_5_bars_pct"
    ] == -7.6923
    assert projected["window_statistics"]["up_transitions_within_5_bars"] == 4
    assert projected["bars_truncated"] is False
    assert projected["series_semantics"] == {"volume_field": "unknown"}


def _settings_values() -> dict[str, str]:
    return {
        "SMART_FUND_MCP_URL": "http://127.0.0.1:8900/mcp",
        "SMART_FUND_MCP_BEARER_TOKEN": "test-token",
        "SMART_FUND_AGENT_LLM_BASE_URL": "http://127.0.0.1:13000/v1",
        "SMART_FUND_AGENT_LLM_API_KEY": "test-key",
        "SMART_FUND_AGENT_MODEL": "glm-5.2",
        "SMART_FUND_AGENT_SESSION_DB": "data/test-agent.sqlite3",
        "SMART_FUND_AGENT_LANGFUSE_ENABLED": "false",
    }


def test_agent_langfuse_project_overrides_server_project() -> None:
    values = {
        **_settings_values(),
        "SMART_FUND_AGENT_LANGFUSE_ENABLED": "true",
        "SMART_FUND_AGENT_LANGFUSE_PUBLIC_KEY": "pk-agent",
        "SMART_FUND_AGENT_LANGFUSE_SECRET_KEY": "sk-agent",
        "SMART_FUND_AGENT_LANGFUSE_BASE_URL": "http://agent-langfuse:3001/",
        "LANGFUSE_PUBLIC_KEY": "pk-server",
        "LANGFUSE_SECRET_KEY": "sk-server",
        "LANGFUSE_BASE_URL": "http://server-langfuse:3001",
    }

    resolved = AgentSettings.from_mapping(values)

    assert resolved.langfuse_public_key == "pk-agent"
    assert resolved.langfuse_secret_key == "sk-agent"
    assert resolved.langfuse_base_url == "http://agent-langfuse:3001"


def _hypotheses() -> list[CompetingHypothesis]:
    return [
        CompetingHypothesis(
            hypothesis_id="h-primary",
            statement="产业事实形成独立行情",
            role="primary",
            expected_observations=["板块相对强度上升"],
            refuting_observations=["仅权重股上涨"],
        ),
        CompetingHypothesis(
            hypothesis_id="h-alt",
            statement="只是全市场风险偏好上升",
            role="alternative",
            expected_observations=["多数行业同步上涨"],
            refuting_observations=["板块独立跑赢"],
        ),
        CompetingHypothesis(
            hypothesis_id="h-data",
            statement="旧快照混入造成表面强势",
            role="data_quality",
            expected_observations=["数据日期不一致"],
            refuting_observations=["记录时间一致"],
        ),
    ]


def _plan(*, opened: list[str] | None = None) -> EvidencePlanItem:
    return EvidencePlanItem(
        plan_item_id="plan-1",
        hypothesis_ids=["h-primary", "h-alt", "h-data"],
        question="是否为板块独立行情",
        required_evidence="板块相对强度、市场宽度和数据时间",
        layer="dimension",
        status="completed",
        opened_references=opened or ["market:sector:semiconductor:20260809T0600Z"],
    )


def _context_pack(
    *, current_report_revision_id: str | None = "report-rev-0"
) -> ResearchContextPack:
    trigger = ResearchTriggerEnvelope(
        trigger_id="trigger-1",
        trigger_slot="intraday",
        source="schedule",
        reason="盘中研究检查点",
        cutoff_at=CUTOFF,
        run_mode="shadow",
    )
    frame = MarketStateFrame(
        frame_id="frame-1",
        cutoff_at=CUTOFF,
        trade_date=date(2026, 8, 9),
        market_session="continuous",
        overview="市场概览",
    )
    return ResearchContextBuilder().build(
        trigger=trigger,
        market_state=frame,
        current_report_revision_id=current_report_revision_id,
        active_views=[],
        memory_items=[],
        research_question="半导体强势是否独立于市场 Beta？",
    )


def _context() -> AgentRunContext:
    return AgentRunContext(
        run_id="run-1",
        session_id="session-1",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
        research_context=_context_pack(),
    )


def _finished_invocation(name: str, index: int) -> ToolInvocation:
    return ToolInvocation(
        name=name,
        call_id=f"call-{index}",
        arguments={"index": index},
        result='{"reference":"market_ref:M1","value":1}',
        finished_at=CUTOFF,
    )


def test_research_budget_guard_keeps_early_exploration_unchanged() -> None:
    context = _context()
    context.tool_invocations = [
        _finished_invocation("market_change_brief_open", index)
        for index in range(17)
    ]
    model_data = ModelInputData(input=[], instructions="研究指令")
    call_data = SimpleNamespace(model_data=model_data, context=context)

    assert _apply_research_budget_guard(call_data) is model_data


def test_research_budget_guard_requests_convergence_after_broad_coverage() -> None:
    context = _context()
    names = [
        "market_change_brief_open",
        "market_sector_compare_open",
        "market_evidence_open",
        "market_instrument_history",
        *(["market_dimension_open"] * 14),
    ]
    context.tool_invocations = [
        _finished_invocation(name, index)
        for index, name in enumerate(names)
    ]
    model_data = ModelInputData(
        input=[{"role": "user", "content": "原始任务"}],
        instructions="研究指令",
    )
    call_data = SimpleNamespace(model_data=model_data, context=context)

    filtered = _apply_research_budget_guard(call_data)

    assert filtered is not model_data
    assert filtered.input[:-1] == model_data.input
    assert "不要开启新主题" in filtered.input[-1]["content"]
    assert "role_memory_search" in filtered.input[-1]["content"]
    assert "当前仍有以下确定性证据完整性问题" in filtered.input[-1]["content"]
    assert "本轮已经完成的工具能力" in filtered.input[-1]["content"]
    assert "market_change_brief_open" in filtered.input[-1]["content"]
    assert "最多还可调用" not in filtered.input[-1]["content"]
    assert "不是打开证据账本的固定前置条件" in filtered.input[-1]["content"]
    assert "不得为了满足流程补查来源" in filtered.input[-1]["content"]
    assert filtered.instructions == "研究指令"


def test_ledger_does_not_require_narrative_source_for_market_only_research() -> None:
    context = _context()
    context.tool_invocations = [
        _finished_invocation("market_change_brief_open", index)
        for index in range(12)
    ]
    context.tool_invocations.extend([
        _finished_invocation("market_instrument_history", 20),
        _finished_invocation("market_evidence_open", 21),
        _finished_invocation("role_memory_search", 22),
    ])

    missing = research_ledger_missing_requirements(context)

    assert not any(
        token in item
        for item in missing
        for token in ("Card", "Edge", "外部原文", "转载")
    )
    assert not any("12次" in item for item in missing)
    assert not any("三个板块" in item for item in missing)
    assert not any("两个不同前瞻窗口" in item for item in missing)
    assert not any("可交易ETF表达" in item for item in missing)


def test_research_budget_guard_requires_opening_selected_memory_after_search() -> None:
    context = _context()
    context.tool_invocations = [
        _finished_invocation("market_change_brief_open", 1),
        _finished_invocation("market_sector_compare_open", 2),
        _finished_invocation("market_evidence_open", 3),
        _finished_invocation("market_instrument_history", 4),
        *[
            _finished_invocation("market_dimension_open", index)
            for index in range(5, 19)
        ],
        _finished_invocation("role_memory_search", 20),
    ]
    model_data = ModelInputData(
        input=[{"role": "user", "content": "原始任务"}],
        instructions="研究指令",
    )
    call_data = SimpleNamespace(model_data=model_data, context=context)

    filtered = _apply_research_budget_guard(call_data)

    reminder = filtered.input[-1]["content"]
    assert "搜索结果只是导航摘要" in reminder
    assert "最多选择两条最相关记忆" in reminder
    assert "role_memory_open" in reminder
    assert "不要为了流程强行打开" in reminder


def test_research_budget_guard_adds_structural_audit_after_ledger() -> None:
    context = _context()
    context.tool_invocations = [
        _finished_invocation("market_change_brief_open", 1),
        _finished_invocation("agent_evidence_ledger_open", 2),
    ]
    model_data = ModelInputData(input=[], instructions="研究指令")
    call_data = SimpleNamespace(model_data=model_data, context=context)

    filtered = _apply_research_budget_guard(call_data)

    assert len(filtered.input) == 1
    reminder = filtered.input[-1]["content"]
    assert "研究阶段现在结束" in reminder
    assert "不要再调用任何读取工具" in reminder
    assert "不要在隐藏推理中枚举 Claim" in reminder
    assert "直接调用提交工具" in reminder
    assert "observed_fact" in reminder
    assert "若提交返回错误，只修正错误指出的字段" in reminder
    assert "禁止使用‘由…转为’" in reminder
    assert "盘中高点与收盘高点不得互换" in reminder
    assert "单日记录不得写连续性" in reminder
    assert "精确监测位必须能在账本定位" in reminder
    assert "适用期限不得超过实际检验窗口" in reminder


def test_research_budget_guard_does_not_compact_long_transcript_after_ledger() -> None:
    context = _context()
    context.working_memory = {"research_goal": "最终审计"}
    context.tool_invocations = [
        _finished_invocation("market_change_brief_open", 1),
        _finished_invocation("market_evidence_open", 2),
        _finished_invocation("agent_evidence_ledger_open", 3),
    ]
    model_data = ModelInputData(
        input=[
            {"role": "user", "content": "原始任务"},
            {"role": "assistant", "content": "x" * 400_000},
        ],
        instructions="研究指令",
    )

    filtered = _apply_research_budget_guard(
        SimpleNamespace(model_data=model_data, context=context)
    )

    assert filtered.input[:-1] == model_data.input
    assert "研究阶段现在结束" in filtered.input[-1]["content"]


def test_research_budget_guard_never_drives_context_compaction() -> None:
    context = _context()
    context.working_memory = {
        "research_goal": "保持研究主线",
        "candidate_hypotheses": [],
        "answered_questions": [],
        "remaining_questions": [],
        "discarded_paths": [],
        "next_step": "继续核验",
    }
    context.tool_invocations = [
        _finished_invocation("market_change_brief_open", 1),
        _finished_invocation("market_sector_open", 2),
    ]
    model_data = ModelInputData(
        input=[
            {"role": "user", "content": "原始任务"},
            {"role": "assistant", "content": "研究" * 50_000},
        ],
        instructions="研究指令",
    )

    filtered = _apply_research_budget_guard(
        SimpleNamespace(model_data=model_data, context=context)
    )

    assert filtered is model_data


def test_research_budget_guard_does_not_compact_ordinary_30k_json_input() -> None:
    context = _context()
    model_data = ModelInputData(
        input=[{"role": "assistant", "content": "x" * 35_000}],
        instructions="研究指令",
    )

    assert _estimate_input_tokens(model_data.input) < 20_000
    assert _apply_research_budget_guard(
        SimpleNamespace(model_data=model_data, context=context)
    ) is model_data


def _no_change_report() -> CurrentResearchReportProposal:
    return CurrentResearchReportProposal(
        base_report_revision_id="report-rev-0",
        proposed_report_revision_id=None,
        run_id="run-1",
        trigger_id="trigger-1",
        trigger_slot="intraday",
        cutoff_at=CUTOFF,
        source_frame_id="frame-1",
        status="no_change",
        report_summary="市场出现波动，但证据不足以修改当前观点。",
        research_question="半导体强势是否独立于市场 Beta？",
        data_quality_assessment="数据均不晚于 cutoff，未发现关键缺失。",
        hypotheses=_hypotheses(),
        evidence_plan=[_plan()],
        counterevidence_summary="检查了权重股集中度和市场普涨解释。",
        no_change_reason="相对强度和资金确认均未越过原观点修订阈值。",
    )


def test_agent_settings_resolve_session_path_from_server_root(
    tmp_path: Path,
) -> None:
    settings = AgentSettings.from_mapping(
        _settings_values(),
        project_root=tmp_path,
    )

    settings.validate()

    assert settings.project_root == tmp_path.resolve()
    assert settings.model == "glm-5.2"
    assert settings.session_db_path == (
        tmp_path / "data/test-agent.sqlite3"
    ).resolve()
    assert settings.langfuse_configured is False


def test_financial_tool_filter_exposes_research_reads_and_never_model_writes() -> None:
    sector_tool = SimpleNamespace(name="market_sector_open")
    frame_tool = SimpleNamespace(name="market_frame_open")
    change_brief_tool = SimpleNamespace(name="market_change_brief_open")
    write_tool = SimpleNamespace(name="market_watchlist_add")
    unknown_tool = SimpleNamespace(name="database_query")
    persisted_instrument_tool = SimpleNamespace(name="market_instrument_open")
    persisted_history_tool = SimpleNamespace(name="market_instrument_history")
    realtime_instrument_tool = SimpleNamespace(name="market_instrument_realtime_open")
    context = _context()

    read_context = SimpleNamespace(
        run_context=SimpleNamespace(context=context)
    )
    assert financial_tool_filter(read_context, sector_tool) is True
    assert financial_tool_filter(read_context, frame_tool) is True
    assert financial_tool_filter(read_context, change_brief_tool) is True
    assert financial_tool_filter(read_context, write_tool) is False
    assert financial_tool_filter(read_context, unknown_tool) is False

    context.tool_invocations.append(
        ToolInvocation(name="market_change_brief_open", call_id="call-brief")
    )
    assert financial_tool_filter(read_context, sector_tool) is True
    assert financial_tool_filter(read_context, persisted_instrument_tool) is False
    assert financial_tool_filter(read_context, persisted_history_tool) is True
    assert financial_tool_filter(read_context, realtime_instrument_tool) is True

    assert financial_tool_filter(read_context, write_tool) is False


def test_financial_tool_filter_exposes_quality_feedback_before_new_research() -> None:
    context = _context()
    filter_context = SimpleNamespace(run_context=SimpleNamespace(context=context))
    quality_list = SimpleNamespace(name="research_quality_list")
    quality_open = SimpleNamespace(name="research_quality_open")

    assert financial_tool_filter(filter_context, quality_list) is True
    assert financial_tool_filter(filter_context, quality_open) is True
    context.tool_invocations.append(
        ToolInvocation(name="research_current_report_open", call_id="call-report")
    )
    assert financial_tool_filter(filter_context, quality_list) is True
    assert financial_tool_filter(filter_context, quality_open) is True
    context.tool_invocations.append(
        ToolInvocation(name="research_quality_list", call_id="call-quality-list")
    )
    assert financial_tool_filter(filter_context, quality_open) is True


def test_financial_tool_filter_keeps_reads_visible_past_legacy_run_budget() -> None:
    context = _context()
    context.research_context.trigger.max_tool_calls = 1
    context.tool_invocations.append(
        ToolInvocation(name="market_frame_open", call_id="call-1")
    )
    filter_context = SimpleNamespace(
        run_context=SimpleNamespace(context=context),
    )

    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="market_dimension_open"),
    ) is True
    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="agent_evidence_ledger_open"),
    ) is True
    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="external_web_search"),
    ) is True
    context.tool_invocations.append(
        ToolInvocation(name="agent_evidence_ledger_open", call_id="call-ledger")
    )
    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="agent_evidence_ledger_open"),
    ) is True


def test_financial_tool_filter_keeps_recovery_reads_visible_past_legacy_budget() -> None:
    context = _context()
    context.research_context.trigger.max_tool_calls = 1
    context.tool_invocations.extend(
        ToolInvocation(name="market_evidence_open", call_id=f"call-{index}")
        for index in range(1)
    )
    filter_context = SimpleNamespace(
        run_context=SimpleNamespace(context=context),
    )

    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="market_evidence_open"),
    ) is True
    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="external_web_read"),
    ) is True
    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="agent_evidence_ledger_open"),
    ) is True


def test_financial_tool_filter_opens_ledger_only_after_decision_coverage() -> None:
    context = _context()
    filter_context = SimpleNamespace(
        run_context=SimpleNamespace(context=context),
    )
    ledger = SimpleNamespace(name="agent_evidence_ledger_open")

    assert financial_tool_filter(filter_context, ledger) is True

    names = [
        "market_frame_open",
        "market_dimension_open",
        "market_sector_open",
        "market_sector_compare_open",
        "market_instrument_history",
        "market_technical_state_open",
        "market_historical_analogue_open",
        "market_evidence_open",
        "kg_card_open",
        "role_memory_search",
        "external_web_search",
        "external_web_read",
        "external_content_read",
        "kg_edge_open",
    ]
    context.tool_invocations = [
        ToolInvocation(
            name=name,
            call_id=f"call-{index}",
            result='{"status":"available"}',
            finished_at=CUTOFF,
        )
        for index, name in enumerate(names)
    ]
    for invocation in context.tool_invocations:
        if invocation.name == "market_historical_analogue_open":
            invocation.arguments = {"code": "ths:concept:886033"}
            invocation.result = '{"calibration_status":"calibrated"}'
    context.working_memory = {
        "research_goal": "比较候选并形成观点",
        "candidate_hypotheses": [],
        "answered_questions": [],
        "remaining_questions": [],
        "discarded_paths": [],
        "next_step": "打开证据账本",
    }
    context.tool_invocations.append(
        ToolInvocation(
            name="checkpoint_research_working_memory",
            call_id="call-checkpoint",
            result='{"saved":true}',
            finished_at=CUTOFF,
        )
    )

    assert financial_tool_filter(filter_context, ledger) is True


def test_ledger_waits_until_comparison_baseline_is_opened() -> None:
    context = _context()
    context.tool_invocations = [
        ToolInvocation(
            name="market_sector_compare_open",
            call_id="compare",
            result=json.dumps({
                "comparison_evidence_requirements": [{
                    "evidence_locator": "market_ref:M88",
                }],
            }),
            finished_at=CUTOFF,
        ),
    ]

    missing = research_ledger_missing_requirements(context)
    assert any("market_ref:M88" in item for item in missing)

    context.tool_invocations.append(ToolInvocation(
        name="market_evidence_open",
        call_id="open-baseline",
        result='{"evidence_locator":"market_ref:M88"}',
        finished_at=CUTOFF,
    ))
    missing = research_ledger_missing_requirements(context)
    assert not any("market_ref:M88" in item for item in missing)


def test_directional_forecast_requires_same_subject_calibration() -> None:
    context = _context()
    context.tool_invocations = [
        ToolInvocation(
            name="market_historical_analogue_open",
            call_id=f"analogue-{window}",
            arguments={"code": "ths:concept:886033", "forward_window_bars": window},
            result=json.dumps({
                "subject_id": "ths:concept:886033",
                "forward_window_bars": window,
                "sample_count": 12,
                "minimum_sample_count": 8,
                "calibration_status": "calibrated",
                "robustness": {"temporal_holdout": {
                    "median_direction_consistent": True,
                }},
            }),
            finished_at=CUTOFF,
        )
        for window in (3, 5)
    ]
    proposal = SimpleNamespace(view_revisions=[SimpleNamespace(forecasts=[Forecast(
        forecast_id="forecast-1",
        subject_id="ths:industry:881121",
        metric="3日绝对收益率（%）",
        expected_direction="up",
        expected_min_value=-1.0,
        expected_max_value=3.0,
        evaluation_start_at=CUTOFF + timedelta(minutes=1),
        evaluation_end_at=CUTOFF + timedelta(days=3),
        invalidation_condition="跌破近期低点",
    )])])

    with pytest.raises(ValueError, match="不能借用其他候选"):
        _validate_forecast_calibration(proposal, context)


def test_directional_forecast_requires_calibrated_distribution_bounds() -> None:
    context = _context()
    context.tool_invocations = [
        ToolInvocation(
            name="market_historical_analogue_open",
            call_id=f"analogue-{window}",
            arguments={"code": "ths:concept:886033", "forward_window_bars": window},
            result=json.dumps({
                "subject_id": "ths:concept:886033",
                "forward_window_bars": window,
                "sample_count": 12,
                "minimum_sample_count": 8,
                "calibration_status": "calibrated",
                "robustness": {
                    "temporal_holdout": {"median_direction_consistent": True},
                    "relative_temporal_holdout": {
                        "median_direction_consistent": True,
                    },
                },
            }),
            finished_at=CUTOFF,
        )
        for window in (3, 5)
    ]
    proposal = SimpleNamespace(view_revisions=[SimpleNamespace(forecasts=[Forecast(
        forecast_id="forecast-1",
        subject_id="ths:concept:886033",
        metric="3日绝对收益率（%）",
        expected_direction="up",
        evaluation_start_at=CUTOFF + timedelta(minutes=1),
        evaluation_end_at=CUTOFF + timedelta(days=3),
        invalidation_condition="跌破近期低点",
    )])])

    with pytest.raises(ValueError, match="历史分布"):
        _validate_forecast_calibration(proposal, context)


def test_forecast_range_is_bound_from_same_object_relative_distribution() -> None:
    context = _context()
    context.tool_invocations = [
        ToolInvocation(
            name="market_historical_analogue_open",
            call_id="analogue",
            arguments={"code": "ths:concept:886033"},
            result=json.dumps({
                "subject_id": "ths:concept:886033",
                "sample_count": 12,
                "minimum_sample_count": 8,
                "calibration_status": "calibrated",
                "robustness": {
                    "temporal_holdout": {"median_direction_consistent": True},
                    "relative_temporal_holdout": {
                        "median_direction_consistent": True,
                    },
                },
                "statistics": {
                    "lower_quartile_return_pct": -2.0,
                    "upper_quartile_return_pct": 4.0,
                    "lower_quartile_relative_return_pct": -1.0,
                    "upper_quartile_relative_return_pct": 2.5,
                },
            }),
            finished_at=CUTOFF,
        )
    ]
    forecast = Forecast(
        forecast_id="forecast-1",
        subject_id="ths:concept:886033",
        metric="3日相对上证超额收益率（%）",
        benchmark_subject_id="cn:index:000001",
        expected_direction="up",
        expected_min_value=-99,
        expected_max_value=99,
        baseline_value=-3,
        evaluation_start_at=CUTOFF + timedelta(minutes=1),
        evaluation_end_at=CUTOFF + timedelta(days=3),
        invalidation_condition="相对收益转负",
    )
    proposal = SimpleNamespace(
        view_revisions=[SimpleNamespace(forecasts=[forecast])]
    )

    _bind_forecast_calibration_fields(proposal, context)

    assert forecast.expected_min_value == -1.0
    assert forecast.expected_max_value == 2.5
    assert forecast.baseline_value == 0.0
    assert forecast.expected_direction == "range"


def test_forecast_ranges_bind_to_their_own_forward_windows() -> None:
    context = _context()
    context.tool_invocations = [
        ToolInvocation(
            name="market_historical_analogue_open",
            call_id=f"analogue-{window}",
            arguments={"code": "ths:concept:886033", "forward_window_bars": window},
            result=json.dumps({
                "subject_id": "ths:concept:886033",
                "forward_window_bars": window,
                "sample_count": 30,
                "calibration_status": "calibrated",
                "robustness": {
                    "temporal_holdout": {"median_direction_consistent": True},
                    "relative_temporal_holdout": {
                        "median_direction_consistent": True,
                    },
                },
                "statistics": {
                    "lower_quartile_relative_return_pct": lower,
                    "upper_quartile_relative_return_pct": upper,
                },
            }),
            finished_at=CUTOFF,
        )
        for window, lower, upper in ((3, -0.46, 2.90), (5, 0.20, 4.23))
    ]
    forecasts = [
        Forecast(
            forecast_id=f"forecast-{window}",
            subject_id="ths:concept:886033",
            metric=f"相对上证指数{window}日累计超额收益",
            benchmark_subject_id="cn:index:000001",
            expected_direction="up",
            expected_min_value=-99,
            expected_max_value=99,
            baseline_value=-3,
            evaluation_start_at=CUTOFF + timedelta(minutes=1),
            evaluation_end_at=CUTOFF + timedelta(days=window),
            invalidation_condition="相对收益转负",
        )
        for window in (3, 5)
    ]
    proposal = SimpleNamespace(view_revisions=[SimpleNamespace(forecasts=forecasts)])

    _bind_forecast_calibration_fields(proposal, context)

    assert (forecasts[0].expected_min_value, forecasts[0].expected_max_value) == (-0.46, 2.90)
    assert (forecasts[1].expected_min_value, forecasts[1].expected_max_value) == (0.20, 4.23)
    assert forecasts[0].expected_direction == "range"
    assert forecasts[1].expected_direction == "up"


def test_generic_dated_forecast_uses_longest_stable_opened_window() -> None:
    context = _context()
    context.tool_invocations = [
        ToolInvocation(
            name="market_historical_analogue_open",
            call_id=f"analogue-{window}",
            arguments={"code": "ths:concept:886033", "forward_window_bars": window},
            result=json.dumps({
                "subject_id": "ths:concept:886033",
                "forward_window_bars": window,
                "sample_count": 30,
                "calibration_status": "calibrated",
                "robustness": {
                    "temporal_holdout": {"median_direction_consistent": True},
                    "relative_temporal_holdout": {
                        "median_direction_consistent": True,
                    },
                },
                "statistics": {
                    "lower_quartile_relative_return_pct": lower,
                    "upper_quartile_relative_return_pct": upper,
                },
            }),
            finished_at=CUTOFF,
        )
        for window, lower, upper in ((3, -0.46, 2.90), (5, 0.20, 4.23))
    ]
    forecast = Forecast(
        forecast_id="forecast-generic",
        subject_id="cn:concept:886033",
        metric="CPO概念相对上证指数累计超额收益",
        benchmark_subject_id="cn:index:000001",
        expected_direction="up",
        evaluation_start_at=CUTOFF + timedelta(minutes=1),
        evaluation_end_at=CUTOFF + timedelta(days=5),
        invalidation_condition="相对收益转负",
    )
    proposal = SimpleNamespace(
        view_revisions=[SimpleNamespace(forecasts=[forecast])]
    )

    _remove_unsupported_directional_forecasts(proposal, context)
    _bind_forecast_calibration_fields(proposal, context)
    _validate_forecast_calibration(proposal, context)

    assert proposal.view_revisions[0].forecasts == [forecast]
    assert (forecast.expected_min_value, forecast.expected_max_value) == (0.20, 4.23)
    assert forecast.expected_direction == "up"


def test_semantic_excerpt_keeps_late_cited_fact_instead_of_first_rows() -> None:
    serialized = (
        '{"facts":['
        + ','.join(
            f'{{"evidence_locator":"market_ref:M{i}","name":"row-{i}",'
            f'"change_percent":{i}}}'
            for i in range(1, 90)
        )
        + ']}'
    )

    excerpt = _semantic_evidence_excerpt(
        serialized,
        references=["canonical-m83"],
        canonical_to_alias={"canonical-m83": "market_ref:M83"},
        max_chars=1000,
    )

    assert "reference_alias=market_ref:M83" in excerpt
    assert '"name":"row-83"' in excerpt
    assert '"change_percent":83' in excerpt
    assert '"name":"row-1"' not in excerpt


def test_semantic_excerpt_keeps_table_values_that_precede_overview_locator() -> None:
    serialized = (
        '{"us_market":{"indices":['
        '{"code":"SPX","change_pct":"0.65%"},'
        '{"code":"HXC","change_pct":"-1.84%"}'
        '],"indices_evidence_locator":"market_ref:M157"}}'
    )

    excerpt = _semantic_evidence_excerpt(
        serialized,
        references=["canonical-us-indices"],
        canonical_to_alias={"canonical-us-indices": "market_ref:M157"},
    )

    assert '"code":"HXC","change_pct":"-1.84%"' in excerpt
    assert '"indices_evidence_locator":"market_ref:M157"' in excerpt


def test_semantic_excerpt_keeps_complete_bounded_analogue_result() -> None:
    serialized = json.dumps({
        "calibration_readout": {
            "absolute_return": {
                "development_median_return_pct": 1.2,
                "holdout_median_return_pct": -0.4,
            },
        },
        "analysis_evidence_locator": "market_ref:M52",
    })

    excerpt = _semantic_evidence_excerpt(
        serialized,
        references=["market:canonical:M52"],
        canonical_to_alias={"market:canonical:M52": "market_ref:M52"},
    )

    assert excerpt == serialized


def test_directional_forecast_rejects_temporally_unstable_calibration() -> None:
    context = _context()
    context.tool_invocations = [
        ToolInvocation(
            name="market_historical_analogue_open",
            call_id="analogue-unstable",
            arguments={"code": "ths:concept:886033"},
            result=json.dumps({
                "subject_id": "ths:concept:886033",
                "sample_count": 30,
                "minimum_sample_count": 8,
                "calibration_status": "calibrated",
                "robustness": {"temporal_holdout": {
                    "median_direction_consistent": False,
                }},
                "statistics": {
                    "lower_quartile_return_pct": -2.0,
                    "upper_quartile_return_pct": 3.0,
                },
            }),
            finished_at=CUTOFF,
        )
    ]
    forecast = Forecast(
        forecast_id="forecast-unstable",
        subject_id="ths:concept:886033",
        metric="3日绝对收益率（%）",
        expected_direction="up",
        expected_min_value=-2,
        expected_max_value=3,
        evaluation_start_at=CUTOFF + timedelta(minutes=1),
        evaluation_end_at=CUTOFF + timedelta(days=3),
        invalidation_condition="方向转弱",
    )
    proposal = SimpleNamespace(
        view_revisions=[SimpleNamespace(forecasts=[forecast])]
    )

    with pytest.raises(ValueError, match="时间留出方向一致"):
        _validate_forecast_calibration(proposal, context)

    _remove_unsupported_directional_forecasts(proposal, context)
    assert proposal.view_revisions[0].forecasts == []


def test_tool_result_decoder_unwraps_mcp_text_content() -> None:
    wrapped = CallToolResult(content=[TextContent(
        type="text",
        text='{"subject_id":"ths:concept:886033","sample_count":13}',
    )])

    assert _decode_tool_result_object(wrapped) == {
        "subject_id": "ths:concept:886033",
        "sample_count": 13,
    }


def test_financial_tool_filter_keeps_remote_reads_after_ledger() -> None:
    context = _context()
    context.tool_invocations = [
        ToolInvocation(name="agent_evidence_ledger_open", call_id="ledger")
    ]
    filter_context = SimpleNamespace(
        run_context=SimpleNamespace(context=context),
    )

    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="market_dimension_open"),
    ) is True


def test_financial_tool_filter_keeps_all_reads_after_submit_error() -> None:
    context = _context()
    context.research_context.trigger.max_tool_calls = 1
    context.tool_invocations = [
        ToolInvocation(name="agent_evidence_ledger_open", call_id="ledger"),
        ToolInvocation(
            name="submit_investment_view_revision",
            call_id="submit",
            result="校验失败：updated view requires market_evidence_open",
            finished_at=CUTOFF,
        ),
    ]
    filter_context = SimpleNamespace(
        run_context=SimpleNamespace(context=context),
    )

    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="market_evidence_open"),
    ) is True
    assert financial_tool_filter(
        filter_context,
        SimpleNamespace(name="market_dimension_open"),
    ) is True


def test_read_call_key_treats_reordered_arguments_as_the_same_read() -> None:
    first = _read_call_key(
        "market_dimension_open",
        {"dimension": "sentiment", "limit": 3},
    )
    second = _read_call_key(
        "market_dimension_open",
        {"limit": 3, "dimension": "sentiment"},
    )

    assert first == second
    assert first != _read_call_key(
        "market_dimension_open",
        {"dimension": "sentiment", "limit": 4},
    )


def test_market_evidence_is_compacted_for_model_and_restored_for_commit() -> None:
    context = AgentRunContext(
        run_id="run-alias",
        session_id="session-alias",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
    )
    locator = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": 123},
            version="a" * 64,
        )
    )
    source_payload = project_tool_result("market_evidence_open", {
        "evidence_locator": locator,
        "record": {
            "observed_at": "2026-08-11T11:28:07.082026Z",
            "data": {"volume_ratio": 0.74},
        },
    })
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    json.dumps(source_payload, ensure_ascii=False)
                ),
            )
        ],
        structuredContent=source_payload,
    )

    compacted = _compact_market_evidence(result, context)

    assert compacted.structuredContent == {
        "evidence_locator": "market_ref:M1",
        "fact_time": "2026-08-11 19:28",
    }
    assert "market_ref:M1" in compacted.content[0].text
    assert "2026-08-11 19:28" in compacted.content[0].text
    assert locator not in compacted.content[0].text
    assert "volume_ratio" not in compacted.content[0].text
    assert _expand_evidence_aliases(
        {"reference": "market_ref:M1"}, context.evidence_aliases
    ) == {"reference": locator}


def test_provider_stringified_nested_proposal_is_normalized_before_validation() -> None:
    payload = _decode_provider_proposal(
        '{"proposal":"{\\"status\\":\\"no_change\\",'
        '\\"report_summary\\":\\"结论\\"}"}'
    )

    assert payload == {"status": "no_change", "report_summary": "结论"}


def test_market_change_model_view_removes_runtime_metadata_and_ambiguous_metric() -> None:
    context = AgentRunContext(
        run_id="run-compact",
        session_id="session-compact",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
    )
    payload = {
        "operation": "market_change_brief_open",
        "status": "available",
        "focus": "overall",
        "frame_id": "market-frame:long-internal-id",
        "research_state": "material_change_detected",
        "research_implication": "优先解释变化",
        "significant_change_count": 2,
        "significant_changes_truncated": False,
        "significant_changes": [
            {
                "dimension": "flow_liquidity",
                "data_type": "market_capital",
                "subject_id": "cn:a_share:market_capital",
                "metric": "net_inflow",
                "unit": "yuan",
                "direction": "up",
                "current_value": 100,
                "baseline_value": 80,
                "absolute_change": 20,
                "percent_change": 25,
                "current_as_of": "2026-08-12T03:30:00Z",
                "baseline_as_of": "2026-08-11T03:30:00Z",
                "current_evidence_locator": "market_ref:M1",
                "baseline_evidence_locator": "market_ref:M2",
            },
            {"metric": "volume_ratio", "current_value": 0.74},
        ],
        "quality_issues": [{"description": "市场维度来自多个交易日"}],
    }
    source_payload = project_tool_result("market_change_brief_open", payload)
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(source_payload))],
        structuredContent=source_payload,
    )

    compacted = _compact_market_evidence(
        result,
        context,
        tool_name="market_change_brief_open",
    )
    model_payload = json.loads(compacted.content[0].text)

    assert model_payload == compacted.structuredContent
    assert "operation" not in model_payload
    assert "frame_id" not in model_payload
    assert "research_implication" not in model_payload
    assert "quality_issues" not in model_payload
    assert len(model_payload["significant_changes"]) == 1
    change = model_payload["significant_changes"][0]
    assert "data_type" not in change
    assert "absolute_change" not in change
    assert change["current_as_of"] == "2026-08-12 11:30"
    assert change["baseline_as_of"] == "2026-08-11 11:30"


def test_sector_compare_marks_unopened_baseline_identity_explicitly() -> None:
    context = AgentRunContext(
        run_id="run-sector-compare",
        session_id="session-sector-compare",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
    )
    payload = {
        "candidates": [{
            "provider_sector_code": "886033",
            "latest_signals": [
                {
                    "trade_date": "2026-08-12",
                    "evidence_locator": "market_ref:M88",
                },
                {
                    "trade_date": "2026-08-13",
                    "main_net_inflow": -15.93,
                    "evidence_locator": "market_ref:M89",
                },
            ],
        }],
    }
    source_payload = project_tool_result("market_sector_compare_open", payload)
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(source_payload))],
        structuredContent=source_payload,
    )

    compacted = _compact_market_evidence(
        result,
        context,
        tool_name="market_sector_compare_open",
    )
    model_payload = json.loads(compacted.content[0].text)

    baseline = model_payload["candidates"][0]["latest_signals"][0]
    assert baseline["comparison_role"] == "baseline_identity_only"
    assert baseline["citation_ready"] is False
    assert baseline["required_action"] == "market_evidence_open"
    assert "comparison_evidence_requirements" not in model_payload


def test_existing_research_view_is_projected_as_decision_state_not_old_report() -> None:
    context = AgentRunContext(
        run_id="run-view",
        session_id="session-view",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
    )
    payload = {
        "view": {
            "view_id": "view-1",
            "revision_id": "rev-1",
            "title": "观点",
            "status": "active",
            "thesis": "核心论点",
            "claims": [{"statement": "旧报告长事实"}],
            "evidence_plan": [{"question": "旧证据计划"}],
            "market_structure": {
                "breadth": "宽度",
                "volume_liquidity_confirmation": "volume_ratio=0.74",
                "evidence": [{"metric": "volume_ratio", "value": 0.74}],
            },
            "forecasts": [{"forecast_id": "F1", "expected_direction": "up"}],
        },
        "next_operations": ["role_outcome_search"],
    }
    source_payload = project_tool_result("research_view_open", payload)
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(source_payload))],
        structuredContent=source_payload,
    )

    compacted = _compact_market_evidence(
        result,
        context,
        tool_name="research_view_open",
    )
    model_payload = json.loads(compacted.content[0].text)

    assert model_payload == compacted.structuredContent
    assert model_payload["view"]["thesis"] == "核心论点"
    assert model_payload["view"]["forecasts"][0]["forecast_id"] == "F1"
    assert "claims" not in model_payload["view"]
    assert "evidence_plan" not in model_payload["view"]
    assert "volume_liquidity_confirmation" not in model_payload["view"]["market_structure"]


def test_current_report_projection_keeps_state_and_drops_old_report_body() -> None:
    context = AgentRunContext(
        run_id="run-report",
        session_id="session-report",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
    )
    payload = {
        "report": {
            "report_id": "research:current",
            "status": "updated",
            "report_summary": "很长的旧报告",
            "claims": [{"statement": "旧主张"}],
            "active_views": [
                {
                    "view_id": "view-1",
                    "revision_id": "rev-1",
                    "title": "观点",
                    "status": "active",
                    "thesis": "核心论点",
                }
            ],
            "observation_requirements": [{"requirement_id": "OR1"}],
        },
        "next_operations": ["research_view_open"],
    }
    source_payload = project_tool_result("research_current_report_open", payload)
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(source_payload))],
        structuredContent=source_payload,
    )

    compacted = _compact_market_evidence(
        result,
        context,
        tool_name="research_current_report_open",
    )
    model_payload = json.loads(compacted.content[0].text)

    assert model_payload == compacted.structuredContent
    assert model_payload["report"]["active_views"][0]["thesis"] == "核心论点"
    assert "report_summary" not in model_payload["report"]
    assert "claims" not in model_payload["report"]


def test_model_facing_local_datetime_is_bound_to_china_timezone() -> None:
    forecast = Forecast(
        forecast_id="F1",
        subject_id="cn:index:000001",
        metric="相对收益",
        expected_direction="up",
        evaluation_start_at="2026-08-13 09:30",
        evaluation_end_at="2026-08-15 15:00",
        invalidation_condition="相对收益转负",
    )

    assert forecast.evaluation_start_at.isoformat() == "2026-08-13T09:30:00+08:00"
    assert forecast.evaluation_end_at.isoformat() == "2026-08-15T15:00:00+08:00"


def test_context_builder_filters_expired_memory_and_rejects_future_frame() -> None:
    pack = _context_pack()
    expired = ResearchMemoryItem(
        memory_id="memory-old",
        summary="旧经验",
        applicability="震荡市",
        counterexample="趋势市不适用",
        confidence="medium",
        expires_at=CUTOFF,
    )
    rebuilt = ResearchContextBuilder().build(
        trigger=pack.trigger,
        market_state=pack.market_state,
        current_report_revision_id=None,
        active_views=[],
        memory_items=[expired],
    )
    assert rebuilt.memory_items == []

    with pytest.raises(ValidationError, match="after cutoff_at"):
        MarketStateFrame(
            frame_id="bad-frame",
            cutoff_at=CUTOFF,
            market_session="continuous",
            overview="错误 Frame",
            dimensions=[
                {
                    "dimension": "breadth",
                    "summary": "未来数据",
                    "state": "unknown",
                    "as_of": CUTOFF + timedelta(seconds=1),
                }
            ],
        )


def test_report_status_contract_keeps_blocked_run_unpublishable() -> None:
    values = _no_change_report().model_dump()
    values.update(
        status=ResearchRunStatus.BLOCKED,
        no_change_reason=None,
        evidence_gaps=[
            EvidenceGap(
                gap_id="gap-1",
                description="关键原始公告不可达",
                reason="source_unreachable",
                impact="critical",
                confidence_impact="无法验证核心事实",
                attempted_tools=["external_web_read"],
            )
        ],
    )
    blocked = CurrentResearchReportProposal.model_validate(values)
    assert blocked.publishable is False
    assert blocked.proposed_report_revision_id is None

    values["evidence_gaps"] = []
    with pytest.raises(ValidationError, match="critical evidence gap"):
        CurrentResearchReportProposal.model_validate(values)


def test_subsequent_no_change_does_not_create_report_revision() -> None:
    report = _no_change_report()
    assert report.status == ResearchRunStatus.NO_CHANGE
    assert report.proposed_report_revision_id is None

    values = report.model_dump()
    values["proposed_report_revision_id"] = "should-not-exist"
    with pytest.raises(ValidationError, match="does not create"):
        CurrentResearchReportProposal.model_validate(values)


def test_initial_no_change_requires_baseline_report_revision() -> None:
    values = _no_change_report().model_dump()
    values.update(
        base_report_revision_id=None,
        proposed_report_revision_id="research-report-revision:run-1",
    )
    report = CurrentResearchReportProposal.model_validate(values)
    assert report.proposed_report_revision_id == "research-report-revision:run-1"

    values["proposed_report_revision_id"] = None
    with pytest.raises(ValidationError, match="baseline report revision"):
        CurrentResearchReportProposal.model_validate(values)


def test_evidence_audit_requires_exact_market_locator_from_opened_result() -> None:
    context = _context()
    locator = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": 456},
        )
    )
    context.tool_invocations.append(
        ToolInvocation(
            name="market_sector_open",
            call_id="market-call",
            result={"evidence_locator": locator, "relative_strength": 1.2},
        )
    )
    opened = collect_opened_evidence(context)
    assert locator in opened["market_text"]
    assert "frame-1" in opened["input"]

    citation = EvidenceCitation(
        citation_id="citation-1",
        kind="market",
        reference=locator,
        claim="半导体相对强度上升",
        support="supports",
        as_of=CUTOFF,
    )
    report = _no_change_report().model_copy(
        update={"evidence_plan": [_plan(opened=[locator])]}
    )
    # no_change has no new claims, so direct report audit still validates the
    # seven-step plan and run-bound identifiers.
    validate_research_result(report, context)

    assert _validate_citation(citation, opened, cutoff_at=CUTOFF) is None
    citation.reference = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": 457},
        )
    )
    assert "not present" in _validate_citation(
        citation,
        opened,
        cutoff_at=CUTOFF,
    )


def test_evidence_audit_rejects_navigation_handle_as_market_evidence() -> None:
    context = _context()
    handle = "market-dimension:sentiment:2026-08-09T06:00:00+00:00"
    context.tool_invocations.append(
        ToolInvocation(
            name="market_dimension_open",
            call_id="dimension-call",
            result={"drilldown_handle": handle},
        )
    )
    citation = EvidenceCitation(
        citation_id="citation-navigation-only",
        kind="market",
        reference=handle,
        claim="情绪维度已被查看",
        support="supports",
        as_of=CUTOFF,
    )

    error = _validate_citation(
        citation,
        collect_opened_evidence(context),
        cutoff_at=CUTOFF,
    )
    assert error is not None
    assert "navigation handle" in error


def test_dated_market_claim_requires_citation_for_each_claimed_date() -> None:
    context = _context()
    current = encode_market_evidence_locator(MarketEvidenceIdentity(
        kind="snapshot",
        domain="market_snapshot",
        identity={"id": 2, "trade_date": "2026-08-13"},
        data_type="ths_sector_flow",
    ))
    claim = SimpleNamespace(
        claim_id="flow-reversal",
        statement="2026-08-12流入134.53亿元，2026-08-13流出15.93亿元",
        evidence=[EvidenceCitation(
            citation_id="current-only",
            kind="market",
            reference=current,
            claim="当前流出",
            support="supports",
        )],
    )

    assert "2026-08-12" in _market_claim_date_error(claim, context)

    baseline = encode_market_evidence_locator(MarketEvidenceIdentity(
        kind="snapshot",
        domain="market_snapshot",
        identity={"id": 1, "trade_date": "2026-08-12"},
        data_type="ths_sector_flow",
    ))
    claim.evidence.append(EvidenceCitation(
        citation_id="baseline",
        kind="market",
        reference=baseline,
        claim="基线流入",
        support="supports",
    ))
    assert _market_claim_date_error(claim, context) is None


def test_dated_market_claim_uses_date_from_exact_opened_alias_record() -> None:
    context = _context()
    context.evidence_aliases["market_ref:M58"] = (
        "market:v1:compact-locator-without-date"
    )
    context.tool_invocations.append(ToolInvocation(
        name="market_dimension_open",
        call_id="futures",
        result={
            "facts": [{
                "evidence_locator": "market_ref:M58",
                "trade_date": "2026-08-15",
                "bucket_at": "2026-08-15 00:40",
                "data_preview": {"quotes": [{"code": "sc2609"}]},
            }],
        },
    ))
    claim = SimpleNamespace(
        claim_id="night-session",
        statement="2026-08-15 00:40夜盘原油上涨2.02%",
        evidence=[EvidenceCitation(
            citation_id="night-futures",
            kind="market",
            reference="market_ref:M58",
            claim="夜盘商品快照",
            support="supports",
        )],
    )

    assert _market_claim_date_error(claim, context) is None


def test_dated_market_claim_accepts_compact_china_time_for_utc_record() -> None:
    context = _context()
    locator = encode_market_evidence_locator(MarketEvidenceIdentity(
        kind="snapshot",
        domain="market_snapshot",
        identity={"id": 3},
        fact_time="2026-08-14T23:28:03.219000Z",
        data_type="ths_cn_market_breadth",
    ))
    context.evidence_aliases["market_ref:M1"] = locator
    context.tool_invocations.append(ToolInvocation(
        name="market_frame_open",
        call_id="frame",
        result={
            "current_evidence_locator": "market_ref:M1",
            "current_as_of": "2026-08-15 07:28",
        },
    ))
    claim = SimpleNamespace(
        claim_id="china-local-date",
        claim_type="observed_fact",
        statement="2026-08-15 07:28宽度快照显示跌多涨少",
        evidence=[EvidenceCitation(
            citation_id="breadth",
            kind="market",
            reference="market_ref:M1",
            claim="宽度快照",
            support="supports",
        )],
    )

    assert _market_claim_date_error(claim, context) is None


def test_dated_market_inference_does_not_treat_view_lifecycle_as_market_fact() -> None:
    claim = SimpleNamespace(
        claim_id="view-outcome",
        claim_type="inference",
        statement="旧观点于2026-08-12建立，窗口内累计保持正超额",
        evidence=[],
    )

    assert _market_claim_date_error(claim, _context()) is None


def test_edge_endpoint_card_id_is_not_treated_as_opened_card() -> None:
    context = _context()
    card_ref = "kg_cognitive_card:not-opened"
    context.tool_invocations.append(
        ToolInvocation(
            name="kg_edge_open",
            call_id="edge-call",
            result={
                "edge_id": "kg_card_relation:opened",
                "source_card_id": card_ref,
            },
        )
    )
    citation = EvidenceCitation(
        citation_id="citation-card-navigation-only",
        kind="card",
        reference=card_ref,
        claim="Card 内容支持该结论",
        support="supports",
        as_of=CUTOFF,
    )

    error = _validate_citation(
        citation,
        collect_opened_evidence(context),
        cutoff_at=CUTOFF,
    )
    assert error is not None
    assert "was not opened" in error


def test_evidence_audit_accepts_optional_padding_for_same_market_locator() -> None:
    locator = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={"id": 123},
        )
    )
    context = _context()
    context.tool_invocations.append(
        ToolInvocation(
            name="market_dimension_open",
            call_id="market-call",
            result={"evidence_locator": locator},
        )
    )
    citation = EvidenceCitation(
        citation_id="citation-padded",
        kind="market",
        reference=locator + "==",
        claim="同一条市场事实",
        support="supports",
        as_of=CUTOFF,
    )

    assert (
        _validate_citation(
            citation,
            collect_opened_evidence(context),
            cutoff_at=CUTOFF,
        )
        is None
    )


def test_evidence_audit_accepts_opened_external_content_handle() -> None:
    context = _context()
    handle = "external_content:opened-body-1"
    context.tool_invocations.append(
        ToolInvocation(
            name="external_web_read",
            call_id="external-call",
            result={"content_handle": handle, "title": "正式原文"},
        )
    )
    citation = EvidenceCitation(
        citation_id="external-citation",
        kind="external",
        reference=handle,
        claim="正式原文中的主张",
        support="supports",
        as_of=CUTOFF,
    )

    assert (
        _validate_citation(
            citation,
            collect_opened_evidence(context),
            cutoff_at=CUTOFF,
        )
        is None
    )


def test_prompt_and_run_input_contain_autonomous_bounded_context() -> None:
    instructions = load_financial_research_instructions()
    run_input = build_run_input(
        context_pack=_context_pack(),
    )

    assert "像成熟研究员一样循环工作" in instructions
    assert "不是按固定流水线机械调用工具" in instructions
    assert "market_historical_analogue_open" in instructions
    assert "submit_research_conclusion" in instructions
    assert "submit_investment_view_revision" in instructions
    payload = json.loads(run_input)
    assert payload["research_task"] == {
        "question": "半导体强势是否独立于市场 Beta？",
        "trigger_scene": "intraday",
        "data_time_policy": "latest_available_at_each_tool_call",
    }
    assert payload["initial_context"]["market_state"]["frame_id"] == (
        "frame_ref:F1"
    )
    assert "cutoff_at" not in payload["initial_context"]["market_state"]
    assert "trigger" not in payload["initial_context"]
    assert "research_question" not in payload["initial_context"]
    assert "+00:00" not in run_input
    assert "Z\"" not in run_input
    assert "run_id" not in run_input
    assert '"state":"unknown"' not in run_input
    assert "market-dimension:" not in run_input


def test_conclusion_tool_exposes_small_non_updating_schema() -> None:
    schema = submit_research_conclusion.params_json_schema
    properties = schema["properties"]
    assert "run_id" not in properties
    assert "source_frame_id" not in properties
    assert "view_revisions" not in properties
    status_schema = properties["status"]
    assert status_schema["enum"] == [
        "no_change",
        "blocked",
        "insufficient_evidence",
        "incomplete",
    ]

    revision_schema = submit_investment_view_revision.params_json_schema
    revision_properties = revision_schema["properties"]
    assert "view_revisions" in revision_properties
    assert "run_id" not in revision_properties


def test_server_binds_run_metadata_to_concise_non_updating_draft() -> None:
    draft = ResearchReportDraft(
        status="no_change",
        report_summary="现有证据没有越过观点修订阈值。",
        research_question="半导体强势是否独立于市场 Beta？",
        data_quality_assessment="关键行情数据可用。",
        counterevidence_summary="尚未观察到独立于市场的持续确认。",
        no_change_reason="没有足够的新事实。",
    )

    context = _context()
    context.research_context.active_views = [
        ActiveViewSnapshot(
            view_id="view-1",
            revision_id="view-rev-1",
            title="半导体相对强势",
            status="active",
            thesis="半导体可能相对宽基保持强势。",
            confidence="medium",
        )
    ]
    proposal = _bind_research_draft(draft, context=context)

    assert proposal.run_id == "run-1"
    assert proposal.trigger_id == "trigger-1"
    assert proposal.source_frame_id == "frame-1"
    assert proposal.base_report_revision_id == "report-rev-0"
    assert proposal.hypotheses == []
    assert proposal.evidence_plan == []
    assert proposal.proposed_report_revision_id is None


def test_server_rejects_no_change_when_no_active_view_exists() -> None:
    context = _context()
    context.research_context = _context_pack(current_report_revision_id=None)
    draft = ResearchReportDraft(
        status="no_change",
        report_summary="首次市场基线研究完成，暂无可发布投资观点。",
        research_question="当前市场是否存在可持续主线？",
        data_quality_assessment="关键行情可用。",
        counterevidence_summary="资金面尚未确认技术面改善。",
        no_change_reason="尚未形成达到观点发布门槛的证据链。",
    )

    with pytest.raises(ValueError, match="no_change requires"):
        _bind_research_draft(draft, context=context)


def test_invalid_submit_result_returns_to_model_for_correction() -> None:
    invalid = SimpleNamespace(
        tool=SimpleNamespace(name="submit_research_conclusion"),
        output="Research Proposal 校验失败：observed_fact requires evidence",
    )
    valid = SimpleNamespace(
        tool=SimpleNamespace(name="submit_research_conclusion"),
        output=_no_change_report().model_dump_json(),
    )

    assert _validated_proposal_is_final(None, [invalid]).is_final_output is False
    accepted = _validated_proposal_is_final(None, [valid])
    assert accepted.is_final_output is True
    assert accepted.final_output == valid.output


def test_mcp_business_error_cannot_be_reported_as_success() -> None:
    result = SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text="proposal evidence was not opened")],
    )

    with pytest.raises(
        RuntimeError,
        match="research_proposal_commit failed.*evidence was not opened",
    ):
        _raise_on_tool_error(result, "research_proposal_commit")

    _raise_on_tool_error(
        SimpleNamespace(isError=False, content=[]),
        "research_proposal_commit",
    )


def test_outcome_evaluator_uses_predeclared_window_and_baseline() -> None:
    forecast = Forecast(
        forecast_id="forecast-1",
        subject_id="sector:semiconductor",
        metric="relative_strength",
        expected_direction="up",
        benchmark_subject_id="index:all-a",
        baseline_value=100.0,
        evaluation_start_at=CUTOFF + timedelta(days=1),
        evaluation_end_at=CUTOFF + timedelta(days=4),
        invalidation_condition="相对强度跌破 95",
    )
    observation = OutcomeObservation(
        observation_id="observation-1",
        forecast_id="forecast-1",
        observed_at=CUTOFF + timedelta(days=3),
        actual_value=104.0,
        benchmark_value=1.5,
        evidence=[
            EvidenceCitation(
                citation_id="market-outcome-1",
                kind="market",
                reference="market:sector:semiconductor:outcome",
                claim="验证窗口内相对强度为 104",
                support="supports",
            )
        ],
    )

    evaluation = ResearchOutcomeEvaluator().evaluate(
        forecast=forecast,
        observation=observation,
        evaluation_id="evaluation-1",
        evaluated_at=CUTOFF + timedelta(days=3),
    )

    assert evaluation.status == "confirmed"
    assert evaluation.direction_correct is True
    assert evaluation.benchmark_outperformance is True
    assert evaluation.mechanism_assessment == "unknown"


def test_range_forecast_requires_calibrated_bounds() -> None:
    with pytest.raises(ValidationError, match="range forecast requires"):
        Forecast(
            forecast_id="forecast-range",
            subject_id="sector:semiconductor",
            metric="五日相对收益区间",
            expected_direction="range",
            evaluation_start_at=CUTOFF,
            evaluation_end_at=CUTOFF + timedelta(days=5),
            invalidation_condition="相对收益方向转负。",
        )


def test_research_orm_registers_current_revision_and_outcome_tables() -> None:
    assert AgentCurrentResearchReport.__tablename__ == (
        "agent_current_research_reports"
    )
    assert AgentInvestmentViewRevision.__tablename__ == (
        "agent_investment_view_revisions"
    )
    assert AgentResearchForecast.__tablename__ == "agent_research_forecasts"
    assert AgentResearchOutcomeEvaluation.__tablename__ == (
        "agent_research_outcome_evaluations"
    )


def test_research_ddl_contains_every_registered_agent_table() -> None:
    ddl = (Path(__file__).parents[3] / "schema/21_agent_research.sql").read_text(
        encoding="utf-8"
    )
    table_names = {
        table_name
        for table_name in AgentCurrentResearchReport.metadata.tables
        if table_name.startswith("agent_")
    }
    assert table_names
    assert all(f"CREATE TABLE IF NOT EXISTS {name}" in ddl for name in table_names)


def test_older_cutoff_cannot_replace_a_more_recent_report_check() -> None:
    proposal = _no_change_report()
    current = SimpleNamespace(
        current_revision_id="report-rev-0",
        last_checked_at=CUTOFF + timedelta(minutes=1),
    )
    with pytest.raises(ValueError, match="older cutoff"):
        AgentResearchRepository._validate_report_pointer(current, proposal)


def test_server_cli_registers_agent_group() -> None:
    assert "agent" in cli.commands
