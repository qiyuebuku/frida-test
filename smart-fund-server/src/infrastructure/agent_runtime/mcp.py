"""Remote Smart Fund MCP connection and least-privilege tool filtering."""
from __future__ import annotations

from typing import Any

from agents.mcp import MCPServerStreamableHttp, ToolFilterContext

from src.infrastructure.agent_runtime.config import AgentSettings
from src.application.agents.financial_research.context import AgentRunContext


READ_TOOLS = frozenset(
    {
        "kg_relation_graph_search",
        "kg_card_open",
        "kg_card_expand",
        "kg_edge_open",
        "kg_community_open",
        "kg_community_expand",
        "market_watchlist_list",
        "market_instrument_open",
        "market_instrument_history",
        "external_web_search",
        "external_web_read",
        "external_repo_search",
        "external_repo_structure",
        "external_repo_read",
        "external_content_read",
    }
)

WRITE_TOOLS = frozenset(
    {
        "market_watchlist_add",
        "market_watchlist_update",
    }
)


def financial_tool_filter(context: ToolFilterContext, tool: Any) -> bool:
    tool_name = str(getattr(tool, "name", ""))
    if tool_name in READ_TOOLS:
        return True
    run_context = context.run_context.context
    return bool(
        isinstance(run_context, AgentRunContext)
        and run_context.allow_writes
        and tool_name in WRITE_TOOLS
    )


def create_mcp_server(settings: AgentSettings) -> MCPServerStreamableHttp:
    headers = {"Authorization": f"Bearer {settings.mcp_bearer_token}"}
    return MCPServerStreamableHttp(
        name="smart-fund-server",
        params={
            "url": settings.mcp_url,
            "headers": headers,
            "timeout": settings.mcp_connect_timeout,
            "sse_read_timeout": settings.mcp_tool_timeout,
            "terminate_on_close": True,
        },
        cache_tools_list=True,
        client_session_timeout_seconds=settings.mcp_tool_timeout,
        tool_filter=financial_tool_filter,
        max_retry_attempts=2,
        retry_backoff_seconds_base=1.0,
        require_approval="never",
    )
