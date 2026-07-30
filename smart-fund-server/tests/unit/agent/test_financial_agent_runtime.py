from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.application.agents.financial_research.audit import (
    collect_opened_evidence,
    validate_research_result,
)
from src.application.agents.financial_research.agent import submit_financial_research
from src.application.agents.financial_research.context import (
    AgentRunContext,
    ToolInvocation,
)
from src.application.agents.financial_research.instructions import (
    build_run_input,
    load_financial_research_instructions,
)
from src.application.agents.financial_research.schemas import (
    EvidenceCitation,
    FinancialResearchResult,
    ResearchTaskMode,
)
from src.infrastructure.agent_runtime.config import AgentSettings
from src.infrastructure.agent_runtime.mcp import financial_tool_filter
from src.interfaces.cli.main import cli


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


def _context(*, allow_writes: bool = False) -> AgentRunContext:
    return AgentRunContext(
        run_id="run-1",
        session_id="session-1",
        task_mode=ResearchTaskMode.RESEARCH,
        allow_writes=allow_writes,
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


def test_financial_tool_filter_requires_explicit_write_permission() -> None:
    read_tool = SimpleNamespace(name="kg_card_open")
    write_tool = SimpleNamespace(name="market_watchlist_add")
    unknown_tool = SimpleNamespace(name="database_query")

    read_context = SimpleNamespace(
        run_context=SimpleNamespace(context=_context())
    )
    write_context = SimpleNamespace(
        run_context=SimpleNamespace(context=_context(allow_writes=True))
    )

    assert financial_tool_filter(read_context, read_tool) is True
    assert financial_tool_filter(read_context, write_tool) is False
    assert financial_tool_filter(write_context, write_tool) is True
    assert financial_tool_filter(write_context, unknown_tool) is False


def test_evidence_audit_accepts_only_opened_card_and_edge() -> None:
    context = _context()
    context.tool_invocations.extend(
        [
            ToolInvocation(
                name="kg_card_open",
                call_id="card-call",
                result={
                    "card_id": "kg_cognitive_card:card-1",
                    "summary": "事实摘要",
                },
            ),
            ToolInvocation(
                name="kg_edge_open",
                call_id="edge-call",
                result={
                    "edge_id": "kg_card_relation:edge-1",
                    "source_card_id": "kg_cognitive_card:card-1",
                },
            ),
        ]
    )
    result = FinancialResearchResult(
        task_mode=ResearchTaskMode.RESEARCH,
        conclusion="结论",
        confidence="medium_high",
        evidence=[
            EvidenceCitation(
                kind="card",
                reference="kg_cognitive_card:card-1",
                claim="支持事实",
                support="observed",
            ),
            EvidenceCitation(
                kind="edge",
                reference="kg_card_relation:edge-1",
                claim="支持关系",
                support="inferred",
            ),
        ],
    )

    opened = collect_opened_evidence(context)
    validate_research_result(result, context)

    assert opened["card"] == {"kg_cognitive_card:card-1"}
    assert opened["edge"] == {"kg_card_relation:edge-1"}


def test_evidence_audit_rejects_unopened_reference() -> None:
    result = FinancialResearchResult(
        task_mode=ResearchTaskMode.RESEARCH,
        conclusion="结论",
        confidence="low",
        evidence=[
            EvidenceCitation(
                kind="card",
                reference="kg_cognitive_card:not-opened",
                claim="未打开的事实",
                support="observed",
            )
        ],
    )

    with pytest.raises(ValueError, match="was not opened"):
        validate_research_result(result, _context())


def test_prompt_and_run_input_preserve_runtime_policy() -> None:
    instructions = load_financial_research_instructions()
    run_input = build_run_input(
        prompt="分析市场",
        task_mode=ResearchTaskMode.MARKET_BRIEFING,
        run_id="run-1",
        now=SimpleNamespace(isoformat=lambda: "2026-07-30T00:00:00+00:00"),
        allow_writes=False,
    )

    assert "submit_financial_research" in instructions
    assert "task_mode: market_briefing" in run_input
    assert "本次运行只读" in run_input
    assert "分析市场" in run_input


def test_submit_tool_exposes_confidence_as_strict_enum() -> None:
    confidence_schema = submit_financial_research.params_json_schema["properties"][
        "confidence"
    ]
    enum_schema = submit_financial_research.params_json_schema["$defs"][
        "ConfidenceLevel"
    ]

    assert confidence_schema == {"$ref": "#/$defs/ConfidenceLevel"}
    assert enum_schema["enum"] == ["low", "medium", "medium_high", "high"]


def test_server_cli_registers_agent_group() -> None:
    assert "agent" in cli.commands
