from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.interfaces.mcp import relation_graph
from src.interfaces.mcp.projection import project_tool_result


class _FakeContext:
    request_id = "request:1"
    request_context = SimpleNamespace(
        request=SimpleNamespace(headers={"mcp-session-id": "session:1"})
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


class _FakeMarketService:
    def __init__(self) -> None:
        self.instruments: list[dict] = []

    async def add_instruments(self, instruments):
        self.instruments = instruments
        return {
            "operation": "market_watchlist_add",
            "items": [{"code": "sh600036", "status": "created"}],
        }


def test_mcp_exposes_graph_and_provider_neutral_external_tools() -> None:
    tools = relation_graph.mcp._tool_manager.list_tools()

    assert [tool.name for tool in tools] == [
        "kg_relation_graph_search",
        "kg_card_expand",
        "kg_card_open",
        "kg_edge_open",
        "kg_community_expand",
        "kg_community_open",
        "market_watchlist_add",
        "market_watchlist_list",
        "market_watchlist_update",
        "market_instrument_open",
        "market_instrument_history",
        "external_web_search",
        "external_web_read",
        "external_repo_search",
        "external_repo_structure",
        "external_repo_read",
        "external_content_read",
    ]
    search_schema = tools[0].parameters
    assert "context" not in search_schema["properties"]
    assert search_schema["required"] == ["query"]
    for tool in tools[:6]:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
    for tool in (tools[6], tools[8]):
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
    for tool in (tools[7], tools[9], tools[10]):
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.openWorldHint is False
    for tool in tools[11:16]:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.openWorldHint is True
    assert tools[16].annotations is not None
    assert tools[16].annotations.openWorldHint is False


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
async def test_static_bearer_token_verifier() -> None:
    verifier = relation_graph._StaticBearerTokenVerifier("expected")

    accepted = await verifier.verify_token("expected")
    rejected = await verifier.verify_token("wrong")

    assert accepted is not None
    assert accepted.client_id == "smart-fund-agent"
    assert accepted.scopes == ["graph:read"]
    assert rejected is None


@pytest.mark.asyncio
async def test_market_watchlist_add_uses_typed_tool_input(monkeypatch) -> None:
    service = _FakeMarketService()
    monkeypatch.setattr(relation_graph, "_market_service", lambda: service)

    result = await relation_graph.market_watchlist_add(
        [
            relation_graph.MarketWatchlistAddItem(
                code="600036",
                type="stock",
                name="招商银行",
                reason="验证银行板块事件影响",
            )
        ],
        _FakeContext(),
    )

    assert service.instruments == [
        {
            "code": "600036",
            "type": "stock",
            "name": "招商银行",
            "reason": "验证银行板块事件影响",
            "interval": 1800,
            "target_days": 10,
        }
    ]
    assert result["items"][0]["status"] == "created"


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
