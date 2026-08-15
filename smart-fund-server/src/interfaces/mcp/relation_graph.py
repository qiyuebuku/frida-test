"""Streamable HTTP MCP interface for verified relation-graph retrieval."""

from __future__ import annotations

import asyncio
import hmac
from datetime import UTC, date, datetime
from typing import Any, Literal

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from src.application.services.agent_market_query_service import (
    AgentMarketQueryService,
)
from src.application.services.agent_research_state_query_service import (
    AgentResearchStateQueryService,
)
from src.application.services.agent_run_prepare_service import (
    AgentRunPrepareService,
)
from src.application.services.agent_research_commit_service import (
    AgentResearchCommitService,
)
from src.application.services.external_research_service import (
    create_external_research_service,
)
from src.application.services.market_tracking_service import (
    create_market_tracking_service,
)
from src.application.services.realtime_instrument_research_service import (
    RealtimeInstrumentResearchService,
)
from src.application.services.market_observability_service import (
    MarketObservabilityService,
)
from src.application.services.market_evidence_locator import (
    MarketEvidenceIdentity,
    encode_market_evidence_locator,
)
from src.application.services.relation_graph_agent_retrieval_service import (
    create_relation_graph_agent_retrieval_service,
)
from src.infrastructure.config import settings
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_flush,
    langfuse_propagation_context,
)
from src.infrastructure.agent_runtime.run_authorization import (
    RunAuthorizationClaims,
    verify_run_authorization,
)
from src.infrastructure.persistence.repositories.agent_mcp_audit_repository import (
    AgentMcpAuditRepository,
)
from src.interfaces.mcp.projection import project_tool_result

_READ_SCOPE = "agent:read"
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
_INTERNAL_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
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
            "Read-only financial Agent gateway with a cutoff-aware market "
            "catalogue, market frame, verified relation-graph reads, tracked "
            "instrument facts, and provider-neutral external research. "
            "Graph search finds possible Card entry points; "
            "graph open returns evidence. Market tools inspect tracked stocks, "
            "funds, ETFs, indices, sectors, and persisted market domains. "
            "Sector market tools read persisted THS board facts and clearly "
            "separated provider-derived rotation signals without calling the "
            "upstream App in the Agent request path. "
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
_SECTOR_OBSERVABILITY_SERVICE = MarketObservabilityService()


@mcp.tool(
    description=(
        "Internal orchestrator contract that prepares the bounded Research "
        "Context Pack. It is authorized per run but excluded from the model "
        "tool projection."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_run_prepare(
    trigger_payload: dict[str, Any],
    context: Context,
    research_question: str = "",
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "research_run_prepare")
    called_at = datetime.now(UTC)
    result = await asyncio.to_thread(
        _run_prepare_service().prepare_research,
        trigger_payload=trigger_payload,
        signed_cutoff_at=claims.cutoff_at,
        research_question=research_question,
    )
    await _record_tool_result(
        claims=claims,
        tool_name="research_run_prepare",
        called_at=called_at,
        result=result,
    )
    return result


@mcp.tool(
    description=(
        "Internal deterministic proposal commit contract. It validates the "
        "signed run and server evidence ledger and is excluded from the model "
        "tool projection."
    ),
    annotations=_INTERNAL_WRITE_ANNOTATIONS,
)
async def research_proposal_commit(
    proposal_payload: dict[str, Any],
    publish: bool,
    context: Context,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "research_proposal_commit")
    return await asyncio.to_thread(
        _research_commit_service().commit,
        claims=claims,
        proposal_payload=proposal_payload,
        publish=publish,
    )


@mcp.tool(
    description=(
        "Internal deterministic contract that stores an independent semantic "
        "quality evaluation after the Research run has completed."
    ),
    annotations=_INTERNAL_WRITE_ANNOTATIONS,
)
async def research_semantic_evaluation_commit(
    evaluation_payload: dict[str, Any],
    context: Context,
) -> dict[str, Any]:
    claims = _require_run_authorization(
        context, "research_semantic_evaluation_commit"
    )
    return await asyncio.to_thread(
        _research_commit_service().commit_semantic_evaluation,
        claims=claims,
        evaluation_payload=evaluation_payload,
    )


@mcp.tool(
    description=(
        "Internal orchestrator contract that closes a failed run without "
        "publishing business state. It is excluded from the model projection."
    ),
    annotations=_INTERNAL_WRITE_ANNOTATIONS,
)
async def research_run_abort(
    error_type: str,
    error_message: str,
    context: Context,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "research_run_abort")
    repository = AgentMcpAuditRepository(
        target=settings.SMART_FUND_MCP_TARGET
    )
    await asyncio.to_thread(
        repository.complete_run,
        claims=claims,
        status="failed",
        checkpoint={
            "error_type": str(error_type)[:180],
            "error_message": str(error_message)[:2000],
        },
    )
    return {
        "operation": "research_run_abort",
        "status": "failed",
        "run_id": claims.run_id,
    }


async def _call(
    *,
    tool_name: str,
    operation: str,
    context: Context,
    awaitable,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, tool_name)
    called_at = datetime.now(UTC)
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
            async with asyncio.timeout(60):
                raw_result = await awaitable
            result = project_tool_result(tool_name, raw_result)
            await _record_tool_result(
                claims=claims,
                tool_name=tool_name,
                called_at=called_at,
                result=result,
            )
            return result
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
    claims = _require_run_authorization(context, tool_name)
    if claims.run_mode == "replay":
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise ToolError(
            "external open-world tools are disabled during historical replay"
        )
    called_at = datetime.now(UTC)
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
            result = await awaitable
            await _record_tool_result(
                claims=claims,
                tool_name=tool_name,
                called_at=called_at,
                result=result,
            )
            return result
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
    claims = _require_run_authorization(context, tool_name)
    called_at = datetime.now(UTC)
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
            result = await awaitable
            await _record_tool_result(
                claims=claims,
                tool_name=tool_name,
                called_at=called_at,
                result=result,
            )
            return result
    except (ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(f"{tool_name} failed: {exc}") from exc
    finally:
        langfuse_flush()


async def _call_agent_market(
    *,
    tool_name: str,
    context: Context,
    function,
    **kwargs,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, tool_name)
    # Replay is frozen. A live run resolves "latest" at every tool call instead
    # of freezing all reads at the run start time.
    kwargs["cutoff_at"] = _data_read_time(claims)
    called_at = datetime.now(UTC)
    session_id = _mcp_session_id(context)
    try:
        with langfuse_propagation_context(
            trace_name=f"agent.market.{tool_name}",
            session_id=session_id,
            tags=["agent-tool", "mcp", "agent-market", tool_name],
            metadata={
                "operation": tool_name,
                "transport": "streamable-http",
                "read_path": "database",
            },
        ):
            result = await asyncio.to_thread(function, **kwargs)
            await _record_tool_result(
                claims=claims,
                tool_name=tool_name,
                called_at=called_at,
                result=result,
            )
            return result
    except (ValueError, RuntimeError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(f"{tool_name} failed: {exc}") from exc
    finally:
        langfuse_flush()


@mcp.tool(
    description=(
        "Open the Research Agent data catalogue at an explicit decision "
        "cutoff. Returns all persisted domains, coverage, latest fact time, "
        "availability, bounded group handles, and the next drilldown tools. "
        "Use this to discover what data exists; it never calls a WebUI API."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_data_catalog_open(
    context: Context,
) -> dict[str, Any]:
    result = await _call_agent_market(
        tool_name="research_data_catalog_open",
        context=context,
        function=_agent_market_query_service().data_catalog,
    )
    return _compact_research_data_catalog(result)


@mcp.tool(
    description=(
        "Open a compact latest-available market starting frame. "
        "Returns coverage, market session, trade dates, freshness, quality "
        "issues, and drilldown handles by dimension; it never dumps the WebUI "
        "dashboard payload."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_frame_open(
    context: Context,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="market_frame_open",
        context=context,
        function=_agent_market_query_service().market_frame,
    )


@mcp.tool(
    description=(
        "Open one bounded, baseline-aware market change brief for a common "
        "research focus. With focus=overall it provides a bounded map of every "
        "collected market dimension; narrower focuses provide their relevant dimensions. "
        "It combines the market frame with material changes, quality blockers, "
        "and stable evidence locators. Prefer this over manually opening many "
        "dimensions for a routine review; it does not form an investment view."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_change_brief_open(
    focus: Literal[
        "overall",
        "risk_appetite",
        "liquidity",
        "sector_rotation",
        "data_quality",
    ],
    context: Context,
    per_dimension_limit: int = 1,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="market_change_brief_open",
        context=context,
        function=_agent_market_query_service().market_change_brief,
        focus=focus,
        per_dimension_limit=per_dimension_limit,
    )


@mcp.tool(
    description=(
        "Open a compact pre-open cross-market prior covering overnight global/US/HK/FX, "
        "futures and commodities, gold, ETFs, rates and bonds, liquidity, and sentiment. "
        "Use it to form conditional A-share opening scenarios and decide what deserves "
        "deeper evidence or history; it is not a claim that today's A-share move occurred."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_premarket_context_open(
    context: Context,
    limit_per_dimension: int = 2,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="market_premarket_context_open",
        context=context,
        function=_agent_market_query_service().premarket_context,
        limit_per_dimension=limit_per_dimension,
    )


@mcp.tool(
    description=(
        "Drill into one market-frame dimension using the latest available facts. "
        "Returns a bounded list of latest database facts with stable evidence "
        "locators. Use the frame first, then open only relevant dimensions."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_dimension_open(
    dimension: Literal[
        "a_share_market",
        "stock_activity",
        "sector_style",
        "flow_liquidity",
        "sentiment",
        "valuation_rates_bonds",
        "etf_fund",
        "futures_commodities",
        "gold",
        "global_us_hk_fx",
        "instrument_tracking",
        "other_market",
    ],
    context: Context,
    limit: int = 8,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="market_dimension_open",
        context=context,
        function=_agent_market_query_service().market_dimension,
        dimension=dimension,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Open one complete Research market topic after the catalogue or frame. "
        "Returns bounded latest snapshot facts plus related persisted domains "
        "and group handles. Topics cover every currently collected market and "
        "research-data family."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_topic_open(
    topic: Literal[
        "a_share",
        "stock",
        "sector",
        "etf",
        "futures_commodities",
        "gold",
        "global_us_hk_fx",
        "fund",
        "flow_liquidity",
        "sentiment",
        "macro_valuation",
        "news_research",
        "data_health",
    ],
    context: Context,
    limit: int = 8,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="market_topic_open",
        context=context,
        function=_agent_market_query_service().market_topic,
        topic=topic,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Open one persisted collection domain using the latest available facts. "
        "Supports bounded paging and optional group or text filtering. Use the "
        "research data catalogue to choose a domain."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_domain_open(
    domain: str,
    context: Context,
    group: str = "",
    query: str = "",
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="market_domain_open",
        context=context,
        function=_agent_market_query_service().market_domain,
        domain=domain,
        group=group or None,
        query=query or None,
        limit=limit,
        offset=offset,
    )


@mcp.tool(
    description=(
        "Open one exact persisted market-evidence locator under the signed run "
        "cutoff. Optionally open up to twelve explicit fields; each returned "
        "field receives its own stable locator for formal citation."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_evidence_open(
    locator: str,
    context: Context,
    fields: list[str] | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="market_evidence_open",
        context=context,
        function=_agent_market_query_service().market_evidence,
        locator=locator,
        fields=fields or [],
        max_chars=max_chars,
    )


@mcp.tool(
    description=(
        "Open the latest authoritative Research report that already existed "
        "at the signed run cutoff."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_current_report_open(context: Context) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="research_current_report_open",
        context=context,
        function=_research_state_query_service().current_report,
    )


@mcp.tool(
    description=(
        "List a bounded set of Research investment views as they existed at "
        "the signed run cutoff."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_view_list(
    context: Context,
    statuses: list[Literal["active", "challenged"]] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="research_view_list",
        context=context,
        function=_research_state_query_service().list_views,
        statuses=statuses or ["active", "challenged"],
        limit=limit,
    )


@mcp.tool(
    description=(
        "Open one Research view and its claims, hypotheses, forecasts, "
        "invalidation conditions, and cited evidence."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_view_open(
    view_id: str,
    context: Context,
    revision_id: str = "",
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="research_view_open",
        context=context,
        function=_research_state_query_service().open_view,
        view_id=view_id,
        revision_id=revision_id,
    )


@mcp.tool(
    description=(
        "Search only promoted, unexpired Research experience memory under "
        "the signed cutoff. Memory is guidance, never market fact evidence."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def role_memory_search(
    context: Context,
    query: str = "",
    subject_id: str = "",
    market_regime: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="role_memory_search",
        context=context,
        function=_research_state_query_service().search_memories,
        query=query,
        subject_id=subject_id,
        market_regime=market_regime,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Open one promoted Research memory with applicability, "
        "counterexamples, evidence references, validity, and version."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def role_memory_open(
    memory_id: str,
    context: Context,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="role_memory_open",
        context=context,
        function=_research_state_query_service().open_memory,
        memory_id=memory_id,
    )


@mcp.tool(
    description=(
        "Open bounded original decision/outcome cases linked to one promoted "
        "Research memory."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def role_memory_case_open(
    memory_id: str,
    context: Context,
    limit: int = 20,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="role_memory_case_open",
        context=context,
        function=_research_state_query_service().open_memory_cases,
        memory_id=memory_id,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Search evaluated outcomes of formal Research forecasts under the "
        "signed cutoff."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def role_outcome_search(
    context: Context,
    subject_id: str = "",
    status: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="role_outcome_search",
        context=context,
        function=_research_state_query_service().search_outcomes,
        subject_id=subject_id,
        status=status,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Open one exact Research forecast outcome, actual observation, "
        "evidence, and decomposed evaluation."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def role_outcome_open(
    evaluation_id: str,
    context: Context,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="role_outcome_open",
        context=context,
        function=_research_state_query_service().open_outcome,
        evaluation_id=evaluation_id,
    )


@mcp.tool(
    description=(
        "List bounded historical Research quality evaluations under the "
        "signed cutoff, including initial and outcome-adjusted scores."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_quality_list(
    context: Context,
    passed: bool | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="research_quality_list",
        context=context,
        function=_research_state_query_service().list_quality,
        passed=passed,
        limit=limit,
    )


@mcp.tool(
    description=(
        "Open one exact Research quality evaluation with ten dimension "
        "scores, hard failures, actions, tool coverage, and outcome feedback."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_quality_open(
    quality_ref: str,
    context: Context,
) -> dict[str, Any]:
    return await _call_agent_market(
        tool_name="research_quality_open",
        context=context,
        function=_research_state_query_service().open_quality,
        quality_ref=quality_ref,
    )


@mcp.tool(
    description=(
        "Open the current Research account/position exposure summary. Returns "
        "an explicit unavailable state until a broker projection is connected."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_exposure_summary_open(context: Context) -> dict[str, Any]:
    claims = _require_run_authorization(
        context, "research_exposure_summary_open"
    )
    return await _call_agent_market(
        tool_name="research_exposure_summary_open",
        context=context,
        function=_research_state_query_service().exposure_unavailable,
        operation="research_exposure_summary_open",
        account_ids=claims.account_ids,
    )


@mcp.tool(
    description=(
        "Open one Research-visible position. Returns unavailable rather than "
        "inventing holdings until the broker projection is connected."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_position_open(
    instrument_id: str,
    context: Context,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "research_position_open")
    return await _call_agent_market(
        tool_name="research_position_open",
        context=context,
        function=_research_state_query_service().exposure_unavailable,
        operation="research_position_open",
        account_ids=claims.account_ids,
        instrument_id=instrument_id,
    )


@mcp.tool(
    description=(
        "Open position performance and view linkage. Returns unavailable until "
        "authoritative broker performance facts are connected."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def research_position_performance_open(
    instrument_id: str,
    context: Context,
) -> dict[str, Any]:
    claims = _require_run_authorization(
        context, "research_position_performance_open"
    )
    return await _call_agent_market(
        tool_name="research_position_performance_open",
        context=context,
        function=_research_state_query_service().exposure_unavailable,
        operation="research_position_performance_open",
        account_ids=claims.account_ids,
        instrument_id=instrument_id,
    )


@mcp.tool(
    description=(
        "Open server-owned state for this signed Agent run, including status, "
        "budget, checkpoint, and observed tool-call count."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def agent_run_state_open(context: Context) -> dict[str, Any]:
    claims = _require_run_authorization(context, "agent_run_state_open")
    called_at = datetime.now(UTC)
    repository = AgentMcpAuditRepository(
        target=settings.SMART_FUND_MCP_TARGET
    )
    result = await asyncio.to_thread(
        repository.open_run_state,
        claims=claims,
    )
    await _record_tool_result(
        claims=claims,
        tool_name="agent_run_state_open",
        called_at=called_at,
        result=result,
    )
    return result


@mcp.tool(
    description=(
        "Open the server-owned ledger of evidence actually opened during this "
        "signed run. The model cannot add ledger entries itself."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def agent_evidence_ledger_open(
    context: Context,
    limit: int = 100,
) -> dict[str, Any]:
    claims = _require_run_authorization(
        context, "agent_evidence_ledger_open"
    )
    called_at = datetime.now(UTC)
    repository = AgentMcpAuditRepository(
        target=settings.SMART_FUND_MCP_TARGET
    )
    result = await asyncio.to_thread(
        repository.open_evidence_ledger,
        claims=claims,
        limit=limit,
    )
    await _record_tool_result(
        claims=claims,
        tool_name="agent_evidence_ledger_open",
        called_at=called_at,
        result=result,
    )
    return _compact_evidence_ledger(result)


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
    claims = _require_run_authorization(context, "kg_relation_graph_search")
    parsed_start = _parse_datetime(time_start)
    parsed_end = _parse_datetime(time_end)
    read_time = _data_read_time(claims)
    if claims.run_mode == "replay" and parsed_start is not None and parsed_start > read_time:
        raise ToolError("time_start cannot be later than the signed run cutoff")
    if claims.run_mode == "replay" and parsed_end is not None and parsed_end > read_time:
        raise ToolError("time_end cannot be later than the signed run cutoff")
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
            time_start=parsed_start,
            time_end=parsed_end or read_time,
            cutoff_at=read_time,
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
    claims = _require_run_authorization(context, "kg_card_expand")
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
            cutoff_at=_data_read_time(claims),
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
    claims = _require_run_authorization(context, "kg_card_open")
    service = _service()
    return await _call(
        tool_name="kg_card_open",
        operation="card_open",
        context=context,
        awaitable=service.open_cards(
            card_ids=card_ids,
            adapter_name=settings.SMART_FUND_MCP_ADAPTER_NAME,
            incident_edge_limit=incident_edge_limit,
            cutoff_at=_data_read_time(claims),
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
    claims = _require_run_authorization(context, "kg_edge_open")
    service = _service()
    return await _call(
        tool_name="kg_edge_open",
        operation="edge_open",
        context=context,
        awaitable=service.open_edges(
            edge_ids=edge_ids,
            adapter_name=settings.SMART_FUND_MCP_ADAPTER_NAME,
            cutoff_at=_data_read_time(claims),
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
    claims = _require_run_authorization(context, "kg_community_expand")
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
            cutoff_at=_data_read_time(claims),
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
    claims = _require_run_authorization(context, "kg_community_open")
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
            cutoff_at=_data_read_time(claims),
        ),
    )


@mcp.tool(
    description=(
        "Read the latest persisted sector-market overview. Returns bounded "
        "hot boards, rankings, fund flows, rotation, opportunity, prosperity, "
        "and commodity-linkage signals. It never triggers an upstream call."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_sector_overview(
    context: Context,
    limit_per_group: int = 3,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "market_sector_overview")
    return await _call_market(
        tool_name="market_sector_overview",
        operation="sector_overview",
        context=context,
        awaitable=_read_sector_overview(
            max(1, min(limit_per_group, 5)),
            cutoff_at=_data_read_time(claims),
        ),
    )


@mcp.tool(
    description=(
        "Read one persisted THS sector ranking with explicit data type, "
        "metric, and sector classification. Use this after the overview when "
        "a longer bounded ranking is needed."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_sector_rankings(
    data_type: Literal[
        "ths_sector_hot",
        "ths_sector_ranking",
        "ths_sector_flow",
        "ths_sector_rotation",
        "ths_industry_opportunity",
        "ths_sector_prosperity",
        "ths_sector_commodity_linkage",
    ],
    context: Context,
    metric: str | None = None,
    sector_type: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "market_sector_rankings")
    return await _call_market(
        tool_name="market_sector_rankings",
        operation="sector_rankings",
        context=context,
        awaitable=_read_sector_rankings(
            data_type=data_type,
            metric=metric,
            sector_type=sector_type,
            limit=max(1, min(limit, 30)),
            offset=max(0, offset),
            cutoff_at=_data_read_time(claims),
        ),
    )


@mcp.tool(
    description=(
        "Open one persisted THS sector by provider code. Returns compact "
        "latest facts and a bounded history; it does not call the upstream App."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_sector_open(
    provider_sector_code: str,
    context: Context,
    sector_type: str | None = None,
    history_limit: int = 5,
    constituent_limit: int = 5,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "market_sector_open")
    return await _call_market(
        tool_name="market_sector_open",
        operation="sector_open",
        context=context,
        awaitable=_read_sector_detail(
            provider_sector_code=provider_sector_code,
            sector_type=sector_type,
            history_limit=max(1, min(history_limit, 30)),
            constituent_limit=max(0, min(constituent_limit, 20)),
            cutoff_at=_data_read_time(claims),
        ),
    )


@mcp.tool(
    description=(
        "Compare two to four candidate sectors in one bounded read. Returns "
        "standardized latest signals, multi-date history anchors, constituent "
        "breadth and exact evidence locators. Prefer this after the market map "
        "when deciding which candidate deserves deeper research."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_sector_compare_open(
    provider_sector_codes: list[str],
    context: Context,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "market_sector_compare_open")
    codes = list(dict.fromkeys(str(item).strip() for item in provider_sector_codes))
    if not 2 <= len(codes) <= 4 or any(not item for item in codes):
        raise ToolError("provider_sector_codes must contain 2 to 4 unique codes")
    return await _call_market(
        tool_name="market_sector_compare_open",
        operation="sector_compare_open",
        context=context,
        awaitable=_read_sector_comparison(codes, cutoff_at=_data_read_time(claims)),
    )


@mcp.tool(
    description=(
        "Open the latest persisted facts for at most eight tracked instruments. "
        "This database tool supports monitoring and event-trigger context; use "
        "market_instrument_realtime_open for ad-hoc live exploration."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_instrument_open(
    codes: list[str],
    context: Context,
    data_types: list[str] | None = None,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "market_instrument_open")
    service = _market_service()
    return await _call_market(
        tool_name="market_instrument_open",
        operation="instrument_open",
        context=context,
        awaitable=service.open_instruments(
            codes=codes,
            data_types=data_types,
            cutoff_at=_data_read_time(claims),
            allow_refresh=False,
        ),
    )


@mcp.tool(
    description=(
        "Directly query the latest THS upstream data for one to five ad-hoc A-share "
        "stocks or ETFs. No watchlist membership and no database read is used. Select "
        "at most four fields such as quote, nav_trend, holdings, manager, performance, "
        "scale, announcements, or news. Read only the modules needed by the current "
        "hypothesis. Live upstream tools are disabled during replay."
    ),
    annotations=_EXTERNAL_READ_ONLY_ANNOTATIONS,
)
async def market_instrument_realtime_open(
    codes: list[str],
    context: Context,
    instrument_type: Literal["auto", "stock", "etf"] = "auto",
    fields: list[Literal[
        "quote", "identity", "fund_overview", "realtime_trend", "nav_trend",
        "performance", "holdings", "asset_allocation", "style", "scale",
        "holders", "manager", "manager_profile", "trade_rules", "technical",
        "announcements", "news",
    ]] | None = None,
    period: Literal["month", "year", "nowyear"] = "month",
    item_limit: int = 20,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "market_instrument_realtime_open")
    if claims.run_mode == "replay":
        raise ToolError("historical replay cannot call live THS upstream data")
    return await _call_market(
        tool_name="market_instrument_realtime_open",
        operation="instrument_realtime_open",
        context=context,
        awaitable=_realtime_instrument_service().open(
            codes=codes,
            instrument_type=instrument_type,
            fields=fields,
            period=period,
            item_limit=item_limit,
        ),
    )


@mcp.tool(
    description=(
        "Compare two to four ETF expressions through direct THS upstream reads. "
        "Returns identity, liquidity, tracking index, performance, drawdown, top "
        "holdings and pairwise holding overlap without loading every fund module."
    ),
    annotations=_EXTERNAL_READ_ONLY_ANNOTATIONS,
)
async def market_expression_compare_open(
    codes: list[str],
    context: Context,
    item_limit: int = 10,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "market_expression_compare_open")
    if claims.run_mode == "replay":
        raise ToolError("historical replay cannot call live THS upstream data")
    return await _call_market(
        tool_name="market_expression_compare_open",
        operation="expression_compare_open",
        context=context,
        awaitable=_realtime_instrument_service().compare_expressions(
            codes=codes,
            item_limit=max(3, min(item_limit, 20)),
        ),
    )


@mcp.tool(
    description=(
        "Read one persisted historical market series, up to 120 rows. Use "
        "data_type=ths_index_daily with code=cn:index:000001 (or a native "
        "index code), and data_type=ths_sector_daily with "
        "code=ths:industry:881xxx / ths:concept:886xxx. Use "
        "northbound_turnover only as turnover, never directional net flow. "
        "For a trend, persistence or new-high claim, request a window long "
        "enough for that exact claim; otherwise explicitly limit the claim "
        "to the opened interval. THS K-line volume is provider-native raw "
        "volume with an unconfirmed unit, so compare direction or ratios but "
        "do not label it shares/lots/手. Historical rows are not live quotes."
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
    claims = _require_run_authorization(context, "market_instrument_history")
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
            cutoff_at=_data_read_time(claims),
        ),
    )


@mcp.tool(
    description=(
        "Calculate deterministic 20/60/120-bar technical position, recent "
        "confirmed swing points, drawdown and optional benchmark-relative "
        "strength from persisted K-lines. Use this instead of asking the model "
        "to choose a convenient high, low or trend window."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_technical_state_open(
    code: str,
    data_type: str,
    context: Context,
    benchmark_code: str = "",
    benchmark_data_type: str = "",
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "market_technical_state_open")
    return await _call_market(
        tool_name="market_technical_state_open",
        operation="technical_state_open",
        context=context,
        awaitable=_market_service().instrument_technical_state(
            code=code,
            data_type=data_type,
            benchmark_code=benchmark_code,
            benchmark_data_type=benchmark_data_type,
            cutoff_at=_data_read_time(claims),
        ),
    )


@mcp.tool(
    description=(
        "Find historically similar technical states and calculate subsequent "
        "absolute and benchmark-relative return distributions. The program "
        "returns an explicit insufficient-sample warning and never fabricates "
        "a probability."
    ),
    annotations=_READ_ONLY_ANNOTATIONS,
)
async def market_historical_analogue_open(
    code: str,
    data_type: str,
    context: Context,
    benchmark_code: str = "",
    benchmark_data_type: str = "",
    forward_window: int = 3,
    min_samples: int = 8,
    match_distance_threshold: float = 2.5,
    search_limit: int = 500,
) -> dict[str, Any]:
    claims = _require_run_authorization(context, "market_historical_analogue_open")
    return await _call_market(
        tool_name="market_historical_analogue_open",
        operation="historical_analogue_open",
        context=context,
        awaitable=_market_service().instrument_historical_analogues(
            code=code,
            data_type=data_type,
            benchmark_code=benchmark_code,
            benchmark_data_type=benchmark_data_type,
            forward_window=max(1, min(forward_window, 20)),
            min_samples=max(3, min(min_samples, 30)),
            match_distance_threshold=max(
                1.0, min(float(match_distance_threshold), 8.0)
            ),
            search_limit=max(60, min(search_limit, 1000)),
            cutoff_at=_data_read_time(claims),
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


def _realtime_instrument_service() -> RealtimeInstrumentResearchService:
    return RealtimeInstrumentResearchService()


def _agent_market_query_service() -> AgentMarketQueryService:
    return AgentMarketQueryService()


def _research_state_query_service() -> AgentResearchStateQueryService:
    return AgentResearchStateQueryService(
        target=settings.SMART_FUND_MCP_TARGET
    )


def _run_prepare_service() -> AgentRunPrepareService:
    return AgentRunPrepareService(target=settings.SMART_FUND_MCP_TARGET)


def _research_commit_service() -> AgentResearchCommitService:
    return AgentResearchCommitService(target=settings.SMART_FUND_MCP_TARGET)


def _sector_observability_service() -> MarketObservabilityService:
    return _SECTOR_OBSERVABILITY_SERVICE


async def _read_sector_overview(
    limit_per_group: int,
    *,
    cutoff_at: datetime,
) -> dict[str, Any]:
    result = await asyncio.to_thread(
        _sector_observability_service().sector_overview,
        limit_per_group=limit_per_group,
        cutoff_at=cutoff_at,
    )
    return {
        "operation": "sector_overview",
        "generated_at": result.get("generated_at"),
        "fact_highlights": _sector_highlights(
            result.get("facts"),
            max_items=12,
        ),
        "provider_signal_highlights": _sector_highlights(
            result.get("provider_signals"),
            max_items=12,
        ),
        "freshness": result.get("freshness"),
        "total": result.get("total"),
        "upstream_requested": False,
        "next_operations": ["market_sector_rankings", "market_sector_open"],
    }


async def _read_sector_rankings(**kwargs) -> dict[str, Any]:
    result = await asyncio.to_thread(
        _sector_observability_service().sector_ranking,
        **kwargs,
    )
    return {
        **{key: result.get(key) for key in (
            "data_type", "metric", "sector_type", "total", "offset", "limit"
        )},
        "items": [_compact_sector_row(item) for item in result.get("items") or []],
        "upstream_requested": False,
        "next_operations": ["market_sector_open"],
    }


async def _read_sector_detail(**kwargs) -> dict[str, Any]:
    constituent_limit = int(kwargs.pop("constituent_limit", 20))
    result = await asyncio.to_thread(
        _sector_observability_service().sector_detail,
        **kwargs,
    )
    series = []
    for group in (result.get("series") or [])[:3]:
        series.append(
            {
                "data_type": group.get("data_type"),
                "subject_id": group.get("subject_id"),
                "items": [
                    _compact_sector_history_item(item)
                    for item in (group.get("items") or [])[:3]
                ],
            }
        )
    constituents = [
        item for item in (result.get("constituents") or [])
        if isinstance(item, dict)
    ]
    ranked_constituents = sorted(
        constituents,
        key=lambda item: float(item.get("change_pct") or 0),
        reverse=True,
    )

    def compact_constituent(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item.get(key)
            for key in (
                "security_code", "security_name", "latest", "change_pct",
                "speed_pct", "turnover_rate",
            )
            if item.get(key) is not None
        }

    return {
        "operation": "sector_open",
        "provider_sector_code": result.get("provider_sector_code"),
        "sector_type": result.get("sector_type"),
        "found": result.get("found"),
        "latest": [
            _compact_sector_row(item)
            for item in (result.get("latest") or [])[:8]
        ],
        "series": series,
        "representative_etf": result.get("representative_etf"),
        "constituent_count": result.get("constituent_count"),
        "top_gainers": [
            compact_constituent(item)
            for item in ranked_constituents[:constituent_limit]
        ],
        "top_losers": [
            compact_constituent(item)
            for item in reversed(ranked_constituents[-constituent_limit:])
        ],
        "constituent_order": "change_pct_descending",
        "constituents_truncated": (
            len(constituents) > constituent_limit
        ),
        "upstream_requested": False,
    }


async def _read_sector_comparison(
    codes: list[str],
    *,
    cutoff_at: datetime,
) -> dict[str, Any]:
    details = await asyncio.gather(
        *[
            asyncio.to_thread(
                _sector_observability_service().sector_detail,
                provider_sector_code=code,
                history_limit=20,
                cutoff_at=cutoff_at,
            )
            for code in codes
        ]
    )
    candidates = []
    for detail in details:
        constituent_evidence = detail.get("constituent_evidence") or {}
        breadth_trade_date = (
            constituent_evidence.get("trade_date")
            or constituent_evidence.get("source_date")
            or constituent_evidence.get("observed_at")
            or constituent_evidence.get("bucket_at")
        )
        comparison_trade_date = (
            str(breadth_trade_date)[:10]
            if breadth_trade_date is not None else None
        )
        latest_rows = _prefer_specific_sector_type(detail.get("latest") or [])
        if comparison_trade_date:
            # This is a cross-sectional comparison, not a last-known-value
            # join. A missing current metric must not be filled by an older
            # observation because that makes stale moves look contemporaneous.
            latest_rows = [
                item
                for item in latest_rows
                if _sector_row_trade_date(item) == comparison_trade_date
            ]
        constituents = detail.get("constituents") or []
        changes = [
            float(item["change_pct"])
            for item in constituents
            if isinstance(item, dict)
            and isinstance(item.get("change_pct"), (int, float))
        ]
        history = []
        ranked_series = sorted(
            detail.get("series") or [],
            key=lambda series: len(
                {
                    str(item.get("trade_date") or "")
                    for item in (series.get("items") or [])
                    if item.get("trade_date")
                }
            ),
            reverse=True,
        )
        for series in ranked_series[:1]:
            items = series.get("items") or []
            by_date: dict[str, dict[str, Any]] = {}
            for item in items:
                date_key = str(item.get("trade_date") or item.get("bucket_at") or "")[:10]
                if date_key:
                    by_date[date_key] = item
            dated_items = list(by_date.values())
            anchors = list(
                dict.fromkeys([0, len(dated_items) // 2, len(dated_items) - 1])
            )
            history.append(
                {
                    "data_type": series.get("data_type"),
                    "subject_id": series.get("subject_id"),
                    "anchors": [
                        _compact_sector_history_item(dated_items[index])
                        for index in anchors
                        if 0 <= index < len(dated_items)
                    ],
                }
            )
        candidates.append(
            {
                "provider_sector_code": detail.get("provider_sector_code"),
                "found": detail.get("found"),
                "latest_signals": _distinct_sector_signals(
                    latest_rows,
                    limit=5,
                ),
                "history": history,
                "constituent_breadth": {
                    "trade_date": (
                        comparison_trade_date
                    ),
                    "count": detail.get("constituent_count"),
                    "advancers": sum(value > 0 for value in changes),
                    "decliners": sum(value < 0 for value in changes),
                    "unchanged": sum(value == 0 for value in changes),
                    "average_change_pct": (
                        round(sum(changes) / len(changes), 2) if changes else None
                    ),
                    "evidence_locator": _sector_row_locator(constituent_evidence),
                },
            }
        )
    return {
        "operation": "sector_compare_open",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "upstream_requested": False,
        "next_operations": ["market_evidence_open", "market_sector_open"],
    }


def _compact_research_data_catalog(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the catalogue useful as a map without dumping its whole inventory."""

    domains = []
    for item in result.get("domains") or []:
        groups = item.get("groups") or []
        domains.append(
            {
                key: item.get(key)
                for key in (
                    "domain",
                    "title",
                    "status",
                    "record_count",
                    "latest_at",
                    "entry_tool",
                    "unavailable_reason",
                )
                if item.get(key) is not None
            }
            | {
                "group_count": len(groups),
                "top_groups": [
                    {
                        key: group.get(key)
                        for key in ("name", "count")
                        if group.get(key) is not None
                    }
                    for group in groups[:6]
                ],
                "groups_truncated": len(groups) > 6,
            }
        )
    return {
        key: result.get(key)
        for key in (
            "operation",
            "status",
            "as_of",
            "read_path",
            "domain_count",
            "available_domain_count",
            "snapshot_type_mapping",
        )
        if result.get(key) is not None
    } | {
        "domains": domains,
        "evidence_domains": result.get("evidence_domains") or [],
        "research_topics": [
            {
                "topic": item.get("topic"),
                "entry_tool": item.get("entry_tool"),
            }
            for item in (result.get("research_topics") or [])
        ],
        "next_operations": result.get("next_operations") or [],
        "note": "需要某领域完整分组时再调用 market_domain_open 下钻。",
    }


def _compact_evidence_ledger(result: dict[str, Any]) -> dict[str, Any]:
    """Expose copyable evidence aliases, not audit implementation metadata."""

    entries = []
    for item in result.get("entries") or []:
        references = list(item.get("evidence_refs") or [])
        # external_web_read records both the source URL (provenance/navigation)
        # and the immutable content handle.  Only the latter is a copyable
        # proposal citation, so do not invite a deterministic audit failure.
        if any(str(ref).startswith("external_content:") for ref in references):
            references = [
                ref
                for ref in references
                if not str(ref).startswith(("http://", "https://"))
            ]
        if references:
            entries.append(
                {
                    "tool_name": item.get("tool_name"),
                    "evidence_refs": references,
                }
            )
    return {
        "run_ref": "current",
        "entries": entries,
        "entry_count": len(entries),
    }


def _prefer_specific_sector_type(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep one concrete sector identity and discard ambiguous ``all`` rows."""

    specific_types = {
        str(item.get("sector_type") or "")
        for item in items
        if str(item.get("sector_type") or "") not in {"", "all"}
    }
    if not specific_types:
        return items
    preferred = next(
        (
            str(item.get("sector_type"))
            for item in items
            if item.get("data_type") in {"ths_sector_hot", "ths_sector_flow"}
            and str(item.get("sector_type") or "") in specific_types
        ),
        sorted(specific_types)[0],
    )
    return [
        item
        for item in items
        if str(item.get("sector_type") or "") in {"", preferred}
    ]


def _distinct_sector_signals(
    items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (
            str(item.get("data_type") or ""),
            str(item.get("metric") or item.get("opportunity_category") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(_compact_sector_row(item))
        if len(selected) >= limit:
            break
    return selected


def _sector_row_trade_date(item: dict[str, Any]) -> str | None:
    value = (
        item.get("trade_date")
        or item.get("source_date")
        or item.get("observed_at")
        or item.get("bucket_at")
        or item.get("fact_time")
    )
    return str(value)[:10] if value is not None else None


def _compact_sector_tree(value: Any) -> Any:
    if isinstance(value, list):
        return [_compact_sector_tree(item) for item in value[:5]]
    if isinstance(value, dict):
        if any(
            key in value
            for key in (
                "provider_sector_code",
                "sector_name",
                "metric_value",
                "heat_score",
                "prosperity_score",
            )
        ):
            return _compact_sector_row(value)
        return {
            key: _compact_sector_tree(item)
            for key, item in list(value.items())[:12]
        }
    return value


def _sector_highlights(value: Any, *, max_items: int) -> list[dict[str, Any]]:
    """Flatten a large dashboard tree into bounded navigational highlights."""
    result: list[dict[str, Any]] = []

    def visit(item: Any, path: list[str]) -> None:
        if len(result) >= max_items:
            return
        if isinstance(item, list):
            for child in item:
                visit(child, path)
                if len(result) >= max_items:
                    break
            return
        if not isinstance(item, dict):
            return
        if any(
            key in item
            for key in (
                "provider_sector_code",
                "sector_name",
                "metric_value",
                "heat_score",
                "prosperity_score",
            )
        ):
            result.append(
                {
                    "group_path": "/".join(path),
                    **_compact_sector_row(item),
                }
            )
            return
        for key, child in item.items():
            visit(child, [*path, str(key)])
            if len(result) >= max_items:
                break

    visit(value, [])
    return result


def _compact_sector_row(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    fields = (
        "provider_sector_code",
        "sector_name",
        "sector_type",
        "rank",
        "metric",
        "metric_value",
        "change_pct",
        "speed_pct",
        "volume_ratio",
        "limit_up_count",
        "heat_rank",
        "heat_score",
        "representative_etf_code",
        "representative_etf_name",
        "main_net_inflow",
        "source_date",
        "opportunity_category",
        "prosperity_score",
        "prosperity_percentile",
        "indicator",
        "trade_date",
        "observed_at",
        "freshness_status",
        "provider",
    )
    compacted = {
        key: _compact_sector_tree(item[key])
        for key in fields
        if item.get(key) is not None
    }
    locator = _sector_row_locator(item)
    if locator is not None:
        compacted["evidence_locator"] = locator
    return compacted


def _sector_row_locator(item: dict[str, Any]) -> str | None:
    snapshot_id = item.get("id")
    if snapshot_id is None:
        return None
    fact_time = _canonical_sector_fact_time(
        item.get("observed_at")
        or item.get("bucket_at")
        or item.get("trade_date")
    )
    trade_date = item.get("source_date") or item.get("trade_date")
    return encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity={
                "id": snapshot_id,
                **(
                    {"trade_date": str(trade_date)[:10]}
                    if trade_date is not None else {}
                ),
            },
            data_type=str(item.get("data_type") or "") or None,
            subject_id=str(item.get("subject_id") or "") or None,
            provider=str(item.get("provider") or "") or None,
            fact_time=fact_time,
            version=str(item.get("payload_hash") or "") or None,
        )
    )


def _canonical_sector_fact_time(value: Any) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value.isoformat()
    elif isinstance(value, str) and value.strip():
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                return date.fromisoformat(normalized[:10]).isoformat()
            except ValueError:
                return normalized
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _compact_sector_history_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    data = item.get("data") or {}
    compacted = {
        **{
            key: item[key]
            for key in ("trade_date", "observed_at", "bucket_at", "freshness_status")
            if item.get(key) is not None
        },
        "data": _compact_sector_row(data),
    }
    locator = _sector_row_locator(item)
    if locator is not None:
        compacted["evidence_locator"] = locator
    return compacted


def _parse_datetime(value: str) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(f"invalid ISO-8601 datetime: {value}") from exc


def _require_run_authorization(
    context: Context,
    tool_name: str,
) -> RunAuthorizationClaims:
    request = context.request_context.request
    headers = getattr(request, "headers", {}) if request is not None else {}
    token = str(headers.get("x-smart-fund-run-authorization") or "")
    try:
        return verify_run_authorization(
            token,
            secret=settings.SMART_FUND_MCP_BEARER_TOKEN,
            tool_name=tool_name,
            expected_role="research",
            expected_task="research_review",
        )
    except ValueError as exc:
        raise ToolError(f"run authorization rejected: {exc}") from exc


def _data_read_time(claims: RunAuthorizationClaims) -> datetime:
    return claims.cutoff_at if claims.run_mode == "replay" else datetime.now(UTC)


async def _record_tool_result(
    *,
    claims: RunAuthorizationClaims,
    tool_name: str,
    called_at: datetime,
    result: dict[str, Any],
) -> None:
    repository = AgentMcpAuditRepository(
        target=settings.SMART_FUND_MCP_TARGET
    )
    await asyncio.to_thread(
        repository.record_result,
        claims=claims,
        tool_name=tool_name,
        called_at=called_at,
        result=result,
    )


def _mcp_session_id(context: Context) -> str:
    request = context.request_context.request
    if request is not None and hasattr(request, "headers"):
        session_id = request.headers.get("mcp-session-id")
        if session_id:
            return f"mcp:{session_id}"
    return f"mcp-request:{context.request_id}"
