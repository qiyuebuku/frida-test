from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.interfaces.mcp import relation_graph
from src.interfaces.mcp.projection import project_tool_result
from src.infrastructure.agent_runtime.mcp import RESEARCH_READ_TOOLS
from src.infrastructure.agent_runtime.run_authorization import (
    issue_run_authorization,
)


TEST_SECRET = "unit-test-mcp-secret"


@pytest.mark.asyncio
async def test_sector_comparison_returns_bounded_pairwise_constituent_overlap(
    monkeypatch,
) -> None:
    memberships = {
        "886033": ["000001", "000002", "000003"],
        "885556": ["000002", "000003", "000004"],
    }

    def sector_detail(**kwargs):
        code = kwargs["provider_sector_code"]
        members = memberships[code]
        return {
            "provider_sector_code": code,
            "found": True,
            "latest": [],
            "series": [],
            "constituents": [
                {"security_code": member, "change_pct": 1.0}
                for member in members
            ],
            "constituent_count": len(members),
            "constituent_evidence": {
                "id": 1 if code == "886033" else 2,
                "data_type": "ths_sector_constituents",
                "subject_id": f"ths_native:concept:{code}",
                "provider": "ths_native",
                "trade_date": "2026-08-14",
            },
        }

    monkeypatch.setattr(
        relation_graph,
        "_sector_observability_service",
        lambda: SimpleNamespace(sector_detail=sector_detail),
    )

    result = await relation_graph._read_sector_comparison(
        ["886033", "885556"],
        cutoff_at=datetime(2026, 8, 15, tzinfo=UTC),
    )

    overlap = result["pairwise_constituent_overlap"][0]
    assert overlap["shared_count"] == 2
    assert overlap["left_overlap_pct"] == pytest.approx(66.67)
    assert overlap["right_overlap_pct"] == pytest.approx(66.67)
    assert overlap["jaccard_pct"] == 50.0
    assert len(overlap["evidence_locators"]) == 2


@pytest.mark.asyncio
async def test_sector_comparison_does_not_fill_current_cells_with_stale_signals(
    monkeypatch,
) -> None:
    service = SimpleNamespace(
        sector_detail=lambda **_kwargs: {
            "provider_sector_code": "885517",
            "found": True,
            "latest": [
                {
                    "data_type": "ths_sector_ranking",
                    "metric": "change_pct",
                    "trade_date": "2026-08-03",
                    "change_pct": 1.25,
                },
                {
                    "data_type": "ths_sector_flow",
                    "metric": "main_net_inflow",
                    "trade_date": "2026-08-14",
                    "main_net_inflow": -3.2,
                },
            ],
            "series": [],
            "constituents": [{"change_pct": -0.5}],
            "constituent_evidence": {"trade_date": "2026-08-14"},
        }
    )
    monkeypatch.setattr(
        relation_graph,
        "_sector_observability_service",
        lambda: service,
    )

    result = await relation_graph._read_sector_comparison(
        ["885517", "885517-copy"],
        cutoff_at=datetime(2026, 8, 15, tzinfo=UTC),
    )

    assert all(
        signal.get("trade_date") == "2026-08-14"
        for candidate in result["candidates"]
        for signal in candidate["latest_signals"]
    )
    assert all(
        signal.get("metric") != "change_pct"
        for candidate in result["candidates"]
        for signal in candidate["latest_signals"]
    )


def test_compact_evidence_ledger_prefers_external_content_handle() -> None:
    result = relation_graph._compact_evidence_ledger(
        {
            "entries": [
                {
                    "tool_name": "external_web_read",
                    "evidence_refs": [
                        "https://example.com/article",
                        "external_content:article-1",
                    ],
                }
            ]
        }
    )

    assert result["entries"][0]["evidence_refs"] == [
        "external_content:article-1"
    ]


@pytest.mark.asyncio
async def test_sector_detail_samples_latest_row_per_trade_date(monkeypatch) -> None:
    service = SimpleNamespace(
        sector_detail=lambda **_kwargs: {
            "provider_sector_code": "886033",
            "found": True,
            "latest": [],
            "series": [{
                "data_type": "ths_sector_flow",
                "subject_id": "ths_native:concept:886033",
                "items": [
                    {"id": 1, "trade_date": "2026-08-12", "bucket_at": "2026-08-12T07:00:00Z", "data_type": "ths_sector_flow", "subject_id": "ths_native:concept:886033", "data": {"main_net_inflow": 134.53}},
                    {"id": 2, "trade_date": "2026-08-13", "bucket_at": "2026-08-13T06:49:00Z", "data_type": "ths_sector_flow", "subject_id": "ths_native:concept:886033", "data": {"main_net_inflow": 8.53}},
                    {"id": 3, "trade_date": "2026-08-13", "bucket_at": "2026-08-13T07:00:00Z", "data_type": "ths_sector_flow", "subject_id": "ths_native:concept:886033", "data": {"main_net_inflow": -15.93}},
                ],
            }],
            "constituents": [],
        },
    )
    monkeypatch.setattr(
        relation_graph,
        "_sector_observability_service",
        lambda: service,
    )

    result = await relation_graph._read_sector_detail(
        provider_sector_code="886033",
        history_limit=5,
        constituent_limit=0,
    )

    items = result["series"][0]["items"]
    assert [item["trade_date"] for item in items] == ["2026-08-12", "2026-08-13"]
    assert items[0]["data"]["main_net_inflow"] == 134.53
    assert items[1]["data"]["main_net_inflow"] == -15.93
    assert items[0]["data_type"] == "ths_sector_flow"


def _run_authorization() -> str:
    now = datetime.now(UTC)
    return issue_run_authorization(
        secret=TEST_SECRET,
        run_id="run:test",
        role="research",
        task="research_review",
        cutoff_at=now - timedelta(seconds=1),
        tools=RESEARCH_READ_TOOLS,
        run_mode="debug",
        ttl_seconds=3600,
        now=now,
    )


class _FakeContext:
    request_id = "request:1"
    request_context = SimpleNamespace(
        request=SimpleNamespace(
            headers={
                "mcp-session-id": "session:1",
                "x-smart-fund-run-authorization": _run_authorization(),
            }
        )
    )


@pytest.fixture(autouse=True)
def _mcp_run_authorization_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        relation_graph.settings,
        "SMART_FUND_MCP_BEARER_TOKEN",
        TEST_SECRET,
    )

    async def no_op_audit(**_kwargs):
        return None

    monkeypatch.setattr(
        relation_graph,
        "_record_tool_result",
        no_op_audit,
    )


class _FakeService:
    def __init__(self) -> None:
        self.search_kwargs: dict = {}

    async def search(self, **kwargs):
        self.search_kwargs = kwargs
        return {
            "operation": "search",
            "query": kwargs["query"],
            "cards": [
                {
                    "card_id": "card:1",
                    "fact_id": "fact:1",
                    "summary": "存储芯片价格上涨。",
                    "focus_evidence": "search 阶段不返回原文。",
                    "source_id": "news:1",
                    "retrieval": {"retrieval_rank": 1},
                }
            ],
            "communities": [],
            "diagnostics": {"seed_count": 1},
        }


class _FakeSectorObservabilityService:
    def sector_overview(self, *, limit_per_group: int, cutoff_at=None):
        assert cutoff_at is not None
        assert limit_per_group == 2
        return {
            "generated_at": "2026-08-02T00:00:00+00:00",
            "facts": {
                "hot": {
                    "concept": [
                        {
                            "provider_sector_code": "885001",
                            "sector_name": "AI应用",
                            "heat_score": 23541,
                            "interval_data": {"values": [1, 2, 3]},
                        }
                    ]
                }
            },
            "provider_signals": {},
            "freshness": {"latest_bucket_at": "2026-08-02T00:00:00+00:00"},
            "total": 1,
        }


class _FakeAgentMarketQueryService:
    def __init__(self) -> None:
        self.cutoff_at = None

    def data_catalog(self, *, cutoff_at):
        self.cutoff_at = cutoff_at
        return {
            "operation": "research_data_catalog_open",
            "status": "available",
            "cutoff_at": cutoff_at,
            "domains": [],
        }


def test_mcp_exposes_graph_and_provider_neutral_external_tools() -> None:
    tools = relation_graph.mcp._tool_manager.list_tools()

    assert [tool.name for tool in tools] == [
        "research_run_prepare",
        "research_proposal_commit",
        "research_semantic_evaluation_commit",
        "research_run_abort",
        "research_data_catalog_open",
        "market_frame_open",
        "market_change_brief_open",
        "market_premarket_context_open",
        "market_global_overview_open",
        "market_dimension_open",
        "market_topic_open",
        "market_domain_open",
        "market_evidence_open",
        "research_current_report_open",
        "research_view_list",
        "research_view_open",
        "role_memory_search",
        "role_memory_open",
        "role_memory_case_open",
        "role_outcome_search",
        "role_outcome_open",
        "research_quality_list",
        "research_quality_open",
        "research_exposure_summary_open",
        "research_position_open",
        "research_position_performance_open",
        "agent_run_state_open",
        "agent_evidence_ledger_open",
        "kg_relation_graph_search",
        "kg_card_expand",
        "kg_card_open",
        "kg_edge_open",
        "kg_community_expand",
        "kg_community_open",
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
        "external_web_search",
        "external_web_read",
        "external_repo_search",
        "external_repo_structure",
        "external_repo_read",
        "external_content_read",
    ]
    assert "research_run_prepare" not in RESEARCH_READ_TOOLS
    assert "research_proposal_commit" not in RESEARCH_READ_TOOLS
    assert "research_semantic_evaluation_commit" not in RESEARCH_READ_TOOLS
    assert "research_run_abort" not in RESEARCH_READ_TOOLS
    search_schema = next(
        tool.parameters
        for tool in tools
        if tool.name == "kg_relation_graph_search"
    )
    assert "context" not in search_schema["properties"]
    assert search_schema["required"] == ["query"]
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is (
            tool.name
                not in {
                    "research_proposal_commit",
                    "research_semantic_evaluation_commit",
                    "research_run_abort",
                }
        )
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
    open_world_tools = {
        "market_instrument_realtime_open",
        "market_expression_compare_open",
        "external_web_search",
        "external_web_read",
        "external_repo_search",
        "external_repo_structure",
        "external_repo_read",
    }
    for tool in tools:
        assert tool.annotations.openWorldHint is (tool.name in open_world_tools)


@pytest.mark.asyncio
async def test_mcp_search_calls_application_service_directly(
    monkeypatch,
) -> None:
    service = _FakeService()
    monkeypatch.setattr(relation_graph, "_service", lambda: service)
    monkeypatch.setattr(
        relation_graph.settings,
        "SMART_FUND_MCP_ADAPTER_NAME",
        "financial",
    )

    result = await relation_graph.kg_relation_graph_search(
        "存储芯片涨价",
        _FakeContext(),
        seed_limit=5,
        candidate_limit=18,
    )

    assert service.search_kwargs["adapter_name"] == "financial"
    assert service.search_kwargs["seed_limit"] == 5
    assert service.search_kwargs["candidate_limit"] == 18
    assert result == {
        "operation": "search",
        "query": "存储芯片涨价",
        "cards": [
            {
                "card_id": "card:1",
                "fact_id": "fact:1",
                "summary": "存储芯片价格上涨。",
                "source_id": "news:1",
            }
        ],
    }


@pytest.mark.asyncio
async def test_research_catalog_calls_agent_market_service_with_aware_cutoff(
    monkeypatch,
) -> None:
    service = _FakeAgentMarketQueryService()
    monkeypatch.setattr(
        relation_graph,
        "_agent_market_query_service",
        lambda: service,
    )

    result = await relation_graph.research_data_catalog_open(_FakeContext())

    assert service.cutoff_at.tzinfo is not None
    assert "operation" not in result
    assert result["domains"] == []


@pytest.mark.asyncio
async def test_research_catalog_rejects_missing_run_authorization() -> None:
    context = _FakeContext()
    context.request_context = SimpleNamespace(
        request=SimpleNamespace(headers={"mcp-session-id": "session:1"})
    )
    with pytest.raises(Exception, match="authorization"):
        await relation_graph.research_data_catalog_open(context)


@pytest.mark.asyncio
async def test_static_bearer_token_verifier() -> None:
    verifier = relation_graph._StaticBearerTokenVerifier("expected")

    accepted = await verifier.verify_token("expected")
    rejected = await verifier.verify_token("wrong")

    assert accepted is not None
    assert accepted.client_id == "smart-fund-agent"
    assert accepted.scopes == ["agent:read"]
    assert rejected is None


@pytest.mark.asyncio
async def test_market_sector_overview_is_database_only_and_compact(
    monkeypatch,
) -> None:
    service = _FakeSectorObservabilityService()
    monkeypatch.setattr(
        relation_graph,
        "_sector_observability_service",
        lambda: service,
    )

    result = await relation_graph.market_sector_overview(
        _FakeContext(),
        limit_per_group=2,
    )

    assert "upstream_requested" not in result
    row = result["fact_highlights"][0]
    assert row == {
        "provider_sector_code": "885001",
        "sector_name": "AI应用",
        "heat_score": 23541,
    }


def test_edge_open_projection_keeps_relation_proof() -> None:
    result = project_tool_result(
        "kg_edge_open",
        {
            "operation": "edge_open",
            "edges": [
                {
                    "edge_id": "edge:1",
                    "source_card_id": "card:1",
                    "target_card_id": "card:2",
                    "relation_kind": "causal_influence",
                    "decision_class": "observed",
                    "basis": "原文明示。",
                    "source_evidence_refs": ["s0001"],
                    "source_card": {
                        "card_id": "card:1",
                        "summary": "原因。",
                        "focus_evidence": "原因原文。",
                        "primary_chunk_id": "chunk:1",
                    },
                    "target_card": {
                        "card_id": "card:2",
                        "summary": "结果。",
                        "focus_evidence": "结果原文。",
                        "primary_chunk_id": "chunk:2",
                    },
                }
            ],
            "diagnostics": {"ignored": True},
        },
    )

    assert result == {
        "operation": "edge_open",
        "edges": [
            {
                "edge_id": "edge:1",
                "source_card_id": "card:1",
                "target_card_id": "card:2",
                "relation_kind": "causal_influence",
                "decision_class": "observed",
                "basis": "原文明示。",
                "source_card": {
                    "card_id": "card:1",
                    "summary": "原因。",
                    "focus_evidence": "原因原文。",
                },
                "target_card": {
                    "card_id": "card:2",
                    "summary": "结果。",
                    "focus_evidence": "结果原文。",
                },
            }
        ],
    }


@pytest.mark.parametrize(
    ("tool_name", "payload"),
    [
        ("market_frame_open", {"trade_date": "2026-08-15", "dimensions": [{
            "dimension": "sector_style", "as_of": "2026-08-15 15:00",
            "trade_dates": ["2026-08-15"],
            "data_types": [{"data_type": "sector_quote", "subject_count": 2}],
        }]}),
        ("market_dimension_open", {"dimension": "flow_liquidity", "facts": [{
            "data_type": "market_capital", "subject_id": "cn:market",
            "data_preview": {"net_inflow": -10}, "evidence_locator": "M1",
        }]}),
        ("market_global_overview_open", {"us_market": {"indices": [{"code": "SPX"}]},
            "other_global_facts": [{"data_type": "forex", "subject_id": "usd_cny",
                "data_preview": {"price": 6.7}, "evidence_locator": "M2"}]}),
        ("market_evidence_open", {"evidence_locator": "M3", "record": {
            "data_type": "sector_flow", "subject_id": "886033",
            "trade_date": "2026-08-15", "data": {"main_net_inflow": 12.3},
        }}),
        ("market_instrument_history", {"code": "000001", "items": [{
            "trade_date": "2026-08-15", "data": {
                "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10,
            },
        }]}),
        ("market_historical_analogue_open", {"subject_id": "886033",
            "statistics": {"median_return_pct": 0.5}, "robustness": {}}),
        ("agent_evidence_ledger_open", {"entries": [{
            "tool_name": "market_evidence_open", "evidence_refs": ["M1", "M1"],
        }]}),
        ("research_quality_open", {"evaluation": {
            "evaluation_id": "Q1", "overall_score": 8.5,
            "semantic_evaluation": {"scores": {"decision_value": 8}},
        }}),
    ],
)
def test_agent_mcp_projection_is_idempotent(tool_name, payload) -> None:
    once = project_tool_result(tool_name, payload)
    assert project_tool_result(tool_name, once) == once


def test_agent_mcp_projection_normalizes_time_precision_and_server_metadata() -> None:
    projected = project_tool_result(
        "market_dimension_open",
        {
            "dimension": "flow_liquidity",
            "updated_at": "2026-08-15T05:02:03.123456Z",
            "facts": [
                {
                    "data_type": "market_capital",
                    "subject_id": "cn:a_share:market_capital",
                    "observed_at": "2026-08-15T05:02:03.123456Z",
                    "data_preview": {
                        "net_inflow": 192.48392811,
                        "percent_change": 216.6600001,
                    },
                    "evidence_locator": "market:v1:one",
                },
                {
                    "data_type": "market_capital",
                    "subject_id": "cn:a_share:market_capital:baseline",
                    "observed_at": "2026-08-15T05:02:03.123456Z",
                    "data_preview": {"net_inflow": -164.99000001},
                    "evidence_locator": "market:v1:two",
                },
            ],
        },
    )

    assert "updated_at" not in projected
    assert projected["facts_shared_time"] == {"fact_time": "2026-08-15 13:02"}
    assert all("fact_time" not in fact for fact in projected["facts"])
    assert projected["facts"][0]["values"] == {
        "net_inflow": 192.4839,
        "percent_change": 216.66,
    }
    assert projected["facts"][1]["values"]["net_inflow"] == -164.99


def test_agent_mcp_projection_normalizes_native_datetime_values() -> None:
    from datetime import UTC, datetime

    projected = project_tool_result(
        "market_frame_open",
        {"dimensions": [{
            "dimension": "a_share_market",
            "as_of": datetime(2026, 8, 14, 17, 41, 55, 431000, tzinfo=UTC),
            "trade_dates": [],
            "data_types": [],
        }]},
    )

    assert projected["dimensions"][0]["latest_fact_time"] == "2026-08-15 01:41"


def test_technical_state_projection_names_intraday_extremes_explicitly() -> None:
    projected = project_tool_result("market_technical_state_open", {
        "subject_id": "ths:industry:881175",
        "latest_close": 21621.915,
        "windows": {"20_bars": {
            "high": 22066.371,
            "high_trade_date": "2026-08-13",
            "low": 17000.0,
            "low_trade_date": "2026-07-20",
            "distance_to_high_pct": -2.0133,
            "return_pct": 24.19,
        }},
        "volume_confirmation": {
            "latest_volume_raw": 1200,
            "prior_20_median_volume_raw": 1000,
            "latest_to_prior_median_ratio": 1.2,
            "state": "above_prior_median",
        },
        "analysis_evidence_locator": "market:v1:technical",
        "peak_drawdown_pct": -2.0133,
    })

    window = projected["windows"]["20_bars"]
    assert window["intraday_high"] == 22066.371
    assert window["close_distance_to_intraday_high_pct"] == -2.0133
    assert "high" not in window
    assert projected["drawdown_from_intraday_peak_pct"] == -2.0133
    assert projected["volume_confirmation"]["latest_to_prior_median_ratio"] == 1.2
    assert projected["analysis_evidence_locator"] == "market:v1:technical"
