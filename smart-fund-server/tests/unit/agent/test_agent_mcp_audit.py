import json
from datetime import datetime, timezone

from src.application.agents.financial_research.audit import (
    _opened_market_reference_dates,
)
from src.application.agents.financial_research.context import (
    AgentRunContext,
    ToolInvocation,
)
from src.application.agents.financial_research.schemas import ResearchTaskMode
from src.infrastructure.persistence.repositories.agent_mcp_audit_repository import (
    _extract_opened_evidence,
)


def test_opened_market_reference_dates_parse_serialized_tool_results() -> None:
    context = AgentRunContext(
        run_id="run",
        session_id="session",
        task_mode=ResearchTaskMode.RESEARCH_REVIEW,
        tool_invocations=[
            ToolInvocation(
                name="market_global_overview_open",
                call_id="call",
                result=json.dumps(
                    {
                        "us_market": {
                            "trade_date": "2026-08-14",
                            "indices_evidence_locator": "market_ref:M1",
                        }
                    }
                ),
                finished_at=datetime.now(timezone.utc),
            )
        ],
    )

    assert _opened_market_reference_dates(context, "market_ref:M1") == {
        "2026-08-14"
    }


def test_evidence_ledger_records_only_opened_evidence_contracts() -> None:
    assert _extract_opened_evidence(
        "kg_relation_graph_search",
        {"cards": [{"card_id": "card-search-hit"}]},
    ) == []
    assert _extract_opened_evidence(
        "kg_card_open",
        {"cards": [{"card_id": "card-opened", "evidence_id": "ev-1"}]},
    ) == ["card-opened", "ev-1"]
    assert _extract_opened_evidence(
        "market_evidence_open",
        {
            "locator": "market:v1:record",
            "fields": [{"evidence_locator": "market:v1:field"}],
        },
    ) == ["market:v1:record", "market:v1:field"]
    assert _extract_opened_evidence(
        "market_global_overview_open",
        {"us_market": {"evidence_locators": [
            "market:v1:us-indices",
            "market:v1:us-breadth",
        ]}},
    ) == ["market:v1:us-indices", "market:v1:us-breadth"]
    assert _extract_opened_evidence(
        "market_frame_open",
        {
            "indices": [{
                "evidence_locator": "market:v1:frame-index",
            }],
            "breadth": {
                "evidence_locator": "market:v1:frame-breadth",
            },
            "capital": {
                "evidence_locator": "market:v1:frame-capital",
            },
        },
    ) == [
        "market:v1:frame-index",
        "market:v1:frame-breadth",
        "market:v1:frame-capital",
    ]
    assert _extract_opened_evidence(
        "market_change_brief_open",
        {
            "significant_changes": [
                {
                    "current_evidence_locator": "market:v1:current",
                    "baseline_evidence_locator": "market:v1:baseline",
                }
            ],
            "dimension_facts": [
                {"facts": [{"evidence_locator": "market:v1:brief"}]}
            ]
        },
    ) == ["market:v1:current", "market:v1:baseline", "market:v1:brief"]
    assert _extract_opened_evidence(
        "market_instrument_history",
        {"window_evidence": {"120_bars": {
            "baseline": {"evidence_locator": "market:v1:history-baseline"},
            "close_high": {"evidence_locator": "market:v1:history-high"},
        }}},
    ) == ["market:v1:history-baseline", "market:v1:history-high"]
    assert _extract_opened_evidence(
        "market_technical_state_open",
        {"windows": {"120_bars": {
            "high_evidence_locator": "market:v1:technical-high",
            "low_evidence_locator": "market:v1:technical-low",
        }}},
    ) == ["market:v1:technical-high", "market:v1:technical-low"]
    assert _extract_opened_evidence(
        "market_historical_analogue_open",
        {
            "analysis_evidence_locator": "market:v1:analogue-calculation",
            "semantic_projection": {"reference": "market:v1:nested-record"},
        },
    ) == ["market:v1:analogue-calculation", "market:v1:nested-record"]
