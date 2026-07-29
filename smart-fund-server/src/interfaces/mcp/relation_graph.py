"""Streamable HTTP MCP interface for verified relation-graph retrieval."""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any, Literal

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from src.application.services.external_research_service import (
    create_external_research_service,
)
from src.application.services.market_tracking_service import (
    create_market_tracking_service,
)
from src.application.services.relation_graph_agent_retrieval_service import (
    create_relation_graph_agent_retrieval_service,
)
from src.infrastructure.config import settings
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_flush,
    langfuse_propagation_context,
)
from src.interfaces.mcp.projection import project_tool_result

_READ_SCOPE = "graph:read"
_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_EXTERNAL_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
_IDEMPOTENT_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


class MarketWatchlistAddItem(BaseModel):
    code: str
    type: Literal["stock", "fund", "etf", "index"]
    name: str = ""
    reason: str
    interval: int = Field(default=1800, ge=300)
    target_days: int = Field(default=10, ge=1)


class MarketWatchlistUpdateItem(BaseModel):
    code: str
    enabled: bool | None = None
    name: str | None = None
    type: Literal["stock", "fund", "etf", "index"] | None = None
    reason: str | None = None
    interval: int | None = Field(default=None, ge=300)
    target_days: int | None = Field(default=None, ge=1)


class _StaticBearerTokenVerifier:
    """Validate the deployment-managed static MCP bearer token."""

    def __init__(self, expected_token: str) -> None:
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="smart-fund-agent",
            scopes=[_READ_SCOPE],
        )


def _create_mcp_server() -> FastMCP:
    token = settings.SMART_FUND_MCP_BEARER_TOKEN
    auth: AuthSettings | None = None
    verifier: _StaticBearerTokenVerifier | None = None
    if token:
        auth = AuthSettings(
            issuer_url=settings.SMART_FUND_MCP_PUBLIC_URL,
            resource_server_url=None,
            required_scopes=[_READ_SCOPE],
        )
        verifier = _StaticBearerTokenVerifier(token)
    return FastMCP(
        "smart-fund-graph",
        instructions=(
            "Financial research gateway with verified relation-graph reads, "
            "continuous market tracking controls, and provider-neutral "
            "external research. Graph search finds possible Card entry points; "
            "graph open returns evidence. Market watchlist tools add, disable, "
            "inspect, and read tracked stocks, funds, ETFs, and indices. "
            "External search discovers public sources, external read opens "
            "them, and large content is read incrementally through content "
            "handles."
        ),
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        log_level=settings.SMART_FUND_MCP_LOG_LEVEL,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        auth=auth,
        token_verifier=verifier,
    )


mcp = _create_mcp_server()


async def _call(
    *,
    tool_name: str,
    operation: str,
    context: Context,
    awaitable,
) -> dict[str, Any]:
    session_id = _mcp_session_id(context)
    try:
        with langfuse_propagation_context(
            trace_name=f"kg.relation_graph_agent.{operation}",
            session_id=session_id,
            tags=["kg", "agent-tool", "mcp", "relation-graph", operation],
            metadata={
                "operation": operation,
                "transport": "streamable-http",
                "adapter_name": settings.SMART_FUND_MCP_ADAPTER_NAME,
                "target": settings.SMART_FUND_MCP_TARGET,
            },
        ):
            result = await awaitable
            return project_tool_result(tool_name, result)
    except (ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(f"{tool_name} failed: {exc}") from exc
    finally:
        langfuse_flush()


async def _call_external(
    *,
    tool_name: str,
    operation: str,
    context: Context,
    awaitable,
) -> dict[str, Any]:
    session_id = _mcp_session_id(context)
    try:
        with langfuse_propagation_context(
            trace_name=f"external.research.{operation}",
            session_id=session_id,
            tags=["agent-tool", "mcp", "external-research", operation],
            metadata={
                "operation": operation,
                "transport": "streamable-http",
            },
        ):
            return await awaitable
    except (ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(f"{tool_name} failed: {exc}") from exc
    finally:
        langfuse_flush()


async def _call_market(
    *,
    tool_name: str,
    operation: str,
    context: Context,
    awaitable,
) -> dict[str, Any]:
    session_id = _mcp_session_id(context)
    try:
        with langfuse_propagation_context(
            trace_name=f"market.tracking.{operation}",
            session_id=session_id,
            tags=["agent-tool", "mcp", "market-tracking", operation],
            metadata={
                "operation": operation,
                "transport": "streamable-http",
                "target": settings.SMART_FUND_MCP_TARGET,
            },
        ):
            return await awaitable
    except (ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(f"{tool_name} failed: {exc}") from exc
    finally:
        langfuse_flush()


@mcp.tool(
    description=(
        "Search atomic financial Cards by meaning. A search hit is only an "
        "entry point and does not prove a relationship. Use this for ingested "
        "graph knowledge and verified relation research; do not use it as a "
        "substitute for public web search or explicitly requested latest news."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def kg_relation_graph_search(
    query: str,
    context: Context,
    seed_limit: int = 6,
    candidate_limit: int = 20,
    time_start: str = "",
    time_end: str = "",
) -> dict[str, Any]:
    service = _service()
    return await _call(
        tool_name="kg_relation_graph_search",
        operation="search",
        context=context,
        awaitable=service.search(
            query=query,
            adapter_name=settings.SMART_FUND_MCP_ADAPTER_NAME,
            seed_limit=seed_limit,
            candidate_limit=candidate_limit,
            time_start=_parse_datetime(time_start),
            time_end=_parse_datetime(time_end),
        ),
    )


@mcp.tool(
    description=(
        "Expand Cards through verified active Edges. Use returned Edge IDs "
        "with kg_edge_open before asserting a relationship."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def kg_card_expand(
    card_ids: list[str],
    context: Context,
    hop_limit: int = 1,
    node_limit: int = 8,
    edge_limit: int = 8,
    relation_kinds: list[str] | None = None,
    decision_classes: list[Literal["observed", "inferred"]] | None = None,
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    service = _service()
    return await _call(
        tool_name="kg_card_expand",
        operation="card_expand",
        context=context,
        awaitable=service.expand_cards(
            card_ids=card_ids,
            adapter_name=settings.SMART_FUND_MCP_ADAPTER_NAME,
            hop_limit=hop_limit,
            node_limit=node_limit,
            edge_limit=edge_limit,
            relation_kinds=relation_kinds or [],
            decision_classes=decision_classes or ["observed", "inferred"],
            min_confidence=min_confidence,
        ),
    )


@mcp.tool(
    description=(
        "Open at most four Cards by exact ID to read atomic summaries, focus "
        "evidence, sources, publication times, and incident Edge handles."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def kg_card_open(
    card_ids: list[str],
    context: Context,
    incident_edge_limit: int = 20,
) -> dict[str, Any]:
    if len(card_ids) > 4:
        raise ToolError("kg_card_open accepts at most 4 card_ids per call")
    service = _service()
    return await _call(
        tool_name="kg_card_open",
        operation="card_open",
        context=context,
        awaitable=service.open_cards(
            card_ids=card_ids,
            adapter_name=settings.SMART_FUND_MCP_ADAPTER_NAME,
            incident_edge_limit=incident_edge_limit,
        ),
    )


@mcp.tool(
    description=(
        "Open at most three verified Edges by exact ID. Use this before "
        "asserting causality, confirmation, contradiction, progression, or "
        "market co-movement."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def kg_edge_open(
    edge_ids: list[str],
    context: Context,
) -> dict[str, Any]:
    if len(edge_ids) > 3:
        raise ToolError("kg_edge_open accepts at most 3 edge_ids per call")
    service = _service()
    return await _call(
        tool_name="kg_edge_open",
        operation="edge_open",
        context=context,
        awaitable=service.open_edges(
            edge_ids=edge_ids,
            adapter_name=settings.SMART_FUND_MCP_ADAPTER_NAME,
        ),
    )


@mcp.tool(
    description=(
        "Expand Communities through active cross-community relations. This "
        "navigates event clusters; it does not prove individual facts."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def kg_community_expand(
    community_ids: list[str],
    context: Context,
    hop_limit: int = 1,
    community_limit: int = 12,
    relation_limit: int = 20,
    relation_kinds: list[str] | None = None,
) -> dict[str, Any]:
    service = _service()
    return await _call(
        tool_name="kg_community_expand",
        operation="community_expand",
        context=context,
        awaitable=service.expand_communities(
            community_ids=community_ids,
            adapter_name=settings.SMART_FUND_MCP_ADAPTER_NAME,
            hop_limit=hop_limit,
            community_limit=community_limit,
            relation_limit=relation_limit,
            relation_kinds=relation_kinds or [],
        ),
    )


@mcp.tool(
    description=(
        "Open Communities by exact ID to inspect member Card summaries and "
        "internal Edge handles. Open Cards or Edges before citing evidence."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def kg_community_open(
    community_ids: list[str],
    context: Context,
    member_limit: int = 20,
    edge_limit: int = 30,
) -> dict[str, Any]:
    service = _service()
    return await _call(
        tool_name="kg_community_open",
        operation="community_open",
        context=context,
        awaitable=service.open_communities(
            community_ids=community_ids,
            adapter_name=settings.SMART_FUND_MCP_ADAPTER_NAME,
            member_limit=member_limit,
            edge_limit=edge_limit,
        ),
    )


@mcp.tool(
    description=(
        "Add or reactivate stocks, funds, ETFs, or mainland indices for "
        "continuous collection. Each item must contain code, type, and a "
        "specific tracking reason. Newly activated instruments are collected "
        "immediately; repeated calls are idempotent."
    ),
    annotations=_IDEMPOTENT_WRITE_ANNOTATIONS,
)
async def market_watchlist_add(
    instruments: list[MarketWatchlistAddItem],
    context: Context,
) -> dict[str, Any]:
    service = _market_service()
    return await _call_market(
        tool_name="market_watchlist_add",
        operation="watchlist_add",
        context=context,
        awaitable=service.add_instruments(
            [instrument.model_dump() for instrument in instruments]
        ),
    )


@mcp.tool(
    description=(
        "List continuously tracked instruments with collection checkpoints, "
        "freshness, failures, source, and tracking reason."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_watchlist_list(
    context: Context,
    enabled_only: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    service = _market_service()
    return await _call_market(
        tool_name="market_watchlist_list",
        operation="watchlist_list",
        context=context,
        awaitable=service.list_watchlist(
            enabled_only=enabled_only,
            limit=limit,
        ),
    )


@mcp.tool(
    description=(
        "Update tracked instruments in batch. Use enabled=false to stop future "
        "collection without deleting historical market data, or enabled=true "
        "to reactivate and immediately collect."
    ),
    annotations=_IDEMPOTENT_WRITE_ANNOTATIONS,
)
async def market_watchlist_update(
    updates: list[MarketWatchlistUpdateItem],
    context: Context,
) -> dict[str, Any]:
    service = _market_service()
    return await _call_market(
        tool_name="market_watchlist_update",
        operation="watchlist_update",
        context=context,
        awaitable=service.update_watchlist(
            [update.model_dump(exclude_none=True) for update in updates]
        ),
    )


@mcp.tool(
    description=(
        "Open the latest collected market snapshot for at most eight tracked "
        "instruments. Defaults to compact decision-relevant dimensions and "
        "also returns collection freshness and failure state."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_instrument_open(
    codes: list[str],
    context: Context,
    data_types: list[str] | None = None,
) -> dict[str, Any]:
    service = _market_service()
    return await _call_market(
        tool_name="market_instrument_open",
        operation="instrument_open",
        context=context,
        awaitable=service.open_instruments(
            codes=codes,
            data_types=data_types,
        ),
    )


@mcp.tool(
    description=(
        "Read one collected time series for a tracked instrument, such as "
        "nav, kline, stock_flow, valuation, quote, or performance."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_instrument_history(
    code: str,
    data_type: str,
    context: Context,
    date_start: str = "",
    date_end: str = "",
    limit: int = 120,
) -> dict[str, Any]:
    service = _market_service()
    return await _call_market(
        tool_name="market_instrument_history",
        operation="instrument_history",
        context=context,
        awaitable=service.instrument_history(
            code=code,
            data_type=data_type,
            date_start=date_start,
            date_end=date_end,
            limit=limit,
        ),
    )


@mcp.tool(
    description=(
        "Search the public web through the configured Smart Fund provider. "
        "Use this for current/latest public information and for topics outside "
        "the financial graph. For financial research it supplements, rather "
        "than replaces, Smart Fund graph retrieval. Results are discovery "
        "leads, not verified financial graph evidence."
    ),
    annotations=_EXTERNAL_READ_ONLY_ANNOTATIONS,
)
async def external_web_search(
    query: str,
    context: Context,
    domain: str = "",
    recency: Literal[
        "oneDay",
        "oneWeek",
        "oneMonth",
        "oneYear",
        "noLimit",
    ] = "noLimit",
    content_size: Literal["medium", "high"] = "medium",
    location: Literal["cn", "us"] = "cn",
    limit: int = 10,
) -> dict[str, Any]:
    service = _external_service()
    return await _call_external(
        tool_name="external_web_search",
        operation="web_search",
        context=context,
        awaitable=service.search_web(
            query=query,
            domain=domain,
            recency=recency,
            content_size=content_size,
            location=location,
            limit=limit,
        ),
    )


@mcp.tool(
    description=(
        "Read one public HTTP(S) page through the configured provider. "
        "Returns a preview and a content handle instead of flooding context."
    ),
    annotations=_EXTERNAL_READ_ONLY_ANNOTATIONS,
)
async def external_web_read(
    url: str,
    context: Context,
    no_cache: bool = False,
) -> dict[str, Any]:
    service = _external_service()
    return await _call_external(
        tool_name="external_web_read",
        operation="web_read",
        context=context,
        awaitable=service.read_web(url=url, no_cache=no_cache),
    )


@mcp.tool(
    description=(
        "Search documentation, issues, commits, and code knowledge for one "
        "public GitHub repository identified as owner/repo."
    ),
    annotations=_EXTERNAL_READ_ONLY_ANNOTATIONS,
)
async def external_repo_search(
    repository: str,
    query: str,
    context: Context,
    language: Literal["zh", "en"] = "zh",
) -> dict[str, Any]:
    service = _external_service()
    return await _call_external(
        tool_name="external_repo_search",
        operation="repo_search",
        context=context,
        awaitable=service.search_repository(
            repository=repository,
            query=query,
            language=language,
        ),
    )


@mcp.tool(
    description=(
        "List the structure of one directory in a public GitHub repository. "
        "Use owner/repo and a repository-relative directory."
    ),
    annotations=_EXTERNAL_READ_ONLY_ANNOTATIONS,
)
async def external_repo_structure(
    repository: str,
    context: Context,
    directory: str = "/",
) -> dict[str, Any]:
    service = _external_service()
    return await _call_external(
        tool_name="external_repo_structure",
        operation="repo_structure",
        context=context,
        awaitable=service.get_repository_structure(
            repository=repository,
            directory=directory,
        ),
    )


@mcp.tool(
    description=(
        "Read one file from a public GitHub repository. Returns a preview and "
        "a content handle for incremental reading."
    ),
    annotations=_EXTERNAL_READ_ONLY_ANNOTATIONS,
)
async def external_repo_read(
    repository: str,
    path: str,
    context: Context,
) -> dict[str, Any]:
    service = _external_service()
    return await _call_external(
        tool_name="external_repo_read",
        operation="repo_read",
        context=context,
        awaitable=service.read_repository_file(
            repository=repository,
            path=path,
        ),
    )


@mcp.tool(
    description=(
        "Read a bounded character range from a content handle returned by "
        "external_web_read or an external_repo_* tool."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def external_content_read(
    content_handle: str,
    context: Context,
    offset: int = 0,
    max_chars: int = 12000,
) -> dict[str, Any]:
    service = _external_service()
    return await _call_external(
        tool_name="external_content_read",
        operation="content_read",
        context=context,
        awaitable=service.read_content(
            handle=content_handle,
            offset=offset,
            max_chars=max_chars,
        ),
    )


relation_graph_mcp_app = mcp.streamable_http_app()


def _service():
    return create_relation_graph_agent_retrieval_service(
        target=settings.SMART_FUND_MCP_TARGET,
    )


def _external_service():
    return create_external_research_service()


def _market_service():
    return create_market_tracking_service()


def _parse_datetime(value: str) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(f"invalid ISO-8601 datetime: {value}") from exc


def _mcp_session_id(context: Context) -> str:
    request = context.request_context.request
    if request is not None and hasattr(request, "headers"):
        session_id = request.headers.get("mcp-session-id")
        if session_id:
            return f"mcp:{session_id}"
    return f"mcp-request:{context.request_id}"
