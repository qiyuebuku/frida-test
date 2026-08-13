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
    _decode_provider_proposal,
    _relevant_plan_references,
    _validated_proposal_is_final,
    submit_investment_view_revision,
    submit_research_conclusion,
)
from src.application.agents.financial_research.audit import (
    _opened_exact_market_record,
    _validate_citation,
    collect_opened_evidence,
    validate_research_result,
)
from src.application.agents.financial_research.context import (
    AgentRunContext,
    ToolInvocation,
)
from src.application.agents.financial_research.instructions import (
    build_run_input,
    load_financial_research_instructions,
)
from src.application.agents.financial_research.outcome_evaluator import (
    ResearchOutcomeEvaluator,
)
from src.application.agents.financial_research.research_context import (
    ResearchContextBuilder,
)
from src.application.agents.financial_research.runtime import (
    _apply_research_budget_guard,
    _expand_evidence_aliases,
    _raise_on_tool_error,
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
    _compact_market_evidence,
    _project_market_history,
    _read_call_key,
    create_mcp_server,
    financial_tool_filter,
)
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
            name="market_evidence_open",
            call_id="opened",
            result='{"evidence_locator":"market_ref:M7"}',
        )
    )
    assert _opened_exact_market_record(context) is True


def test_market_history_projection_keeps_bars_and_adds_window_statistics() -> None:
    projected = _project_market_history({
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
    assert projected["window_statistics"]["up_days_5_bars"] == 4
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
    assert "立即打开 Evidence Ledger" in filtered.input[-1]["content"]
    assert filtered.instructions == "研究指令"


def test_research_budget_guard_adds_structural_audit_after_ledger() -> None:
    context = _context()
    context.tool_invocations = [
        _finished_invocation("market_change_brief_open", 1),
        _finished_invocation("agent_evidence_ledger_open", 2),
    ]
    model_data = ModelInputData(input=[], instructions="研究指令")
    call_data = SimpleNamespace(model_data=model_data, context=context)

    filtered = _apply_research_budget_guard(call_data)

    assert len(filtered.input) == 2
    notebook = json.loads(filtered.input[0]["content"])
    assert notebook["research_notebook"]["retained_results"]
    reminder = filtered.input[-1]["content"]
    assert "observed_fact" in reminder
    assert "必须拆成 inference" in reminder
    assert "UTC 时间必须先换算为北京时间" in reminder
    assert "不能提交 no_change" in reminder
    assert "组合层可继续评估" in reminder
    assert "data_quality（数据质量）只能讨论覆盖" in reminder
    assert "同一语义" in reminder
    assert "单篇二手报道" in reminder
    assert "只有午间休市" in reminder


def test_research_budget_guard_compacts_long_transcript_after_ledger() -> None:
    context = _context()
    context.tool_invocations = [
        _finished_invocation("market_change_brief_open", 1),
        _finished_invocation("market_evidence_open", 2),
        _finished_invocation("agent_evidence_ledger_open", 3),
    ]
    model_data = ModelInputData(
        input=[
            {"role": "user", "content": "原始任务"},
            {"role": "assistant", "content": "x" * 100_000},
        ],
        instructions="研究指令",
    )

    filtered = _apply_research_budget_guard(
        SimpleNamespace(model_data=model_data, context=context)
    )

    assert len(json.dumps(filtered.input, ensure_ascii=False)) < 10_000
    assert filtered.input[0] == model_data.input[0]
    assert "research_notebook" in filtered.input[1]["content"]
    assert "x" * 1_000 not in filtered.input[1]["content"]


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
    write_tool = SimpleNamespace(name="market_watchlist_add")
    unknown_tool = SimpleNamespace(name="database_query")
    persisted_instrument_tool = SimpleNamespace(name="market_instrument_open")
    persisted_history_tool = SimpleNamespace(name="market_instrument_history")
    realtime_instrument_tool = SimpleNamespace(name="market_instrument_realtime_open")
    context = _context()

    read_context = SimpleNamespace(
        run_context=SimpleNamespace(context=context)
    )
    assert financial_tool_filter(read_context, sector_tool) is False
    assert financial_tool_filter(read_context, frame_tool) is True
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


def test_financial_tool_filter_hides_reads_after_run_budget_is_exhausted() -> None:
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
    ) is False


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
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    f'{{"evidence_locator":"{locator}",'
                    '"fact_time":"2026-08-11T11:28:07.082026Z",'
                    '"volume_ratio":0.74}'
                ),
            )
        ],
        structuredContent={
            "evidence_locator": locator,
            "fact_time": "2026-08-11T11:28:07.082026Z",
            "volume_ratio": 0.74,
        },
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
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
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
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
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
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))],
        structuredContent=payload,
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
