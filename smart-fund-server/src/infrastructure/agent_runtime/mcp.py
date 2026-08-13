"""Remote Smart Fund MCP connection and least-privilege tool filtering."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from agents.mcp import MCPServerStreamableHttp, ToolFilterContext
from mcp.types import CallToolResult, TextContent

from src.infrastructure.agent_runtime.config import AgentSettings
from src.application.agents.financial_research.context import AgentRunContext
from src.application.agents.financial_research.schemas import ResearchTaskMode


RESEARCH_READ_TOOLS = frozenset(
    {
        "research_data_catalog_open",
        "market_frame_open",
        "market_change_brief_open",
        "market_premarket_context_open",
        "market_dimension_open",
        "market_topic_open",
        "market_domain_open",
        "market_evidence_open",
        "research_current_report_open",
        "research_view_list",
        "research_view_open",
        "research_quality_list",
        "research_quality_open",
        "research_exposure_summary_open",
        "research_position_open",
        "research_position_performance_open",
        "role_memory_search",
        "role_memory_open",
        "role_memory_case_open",
        "role_outcome_search",
        "role_outcome_open",
        "agent_run_state_open",
        "agent_evidence_ledger_open",
        "kg_relation_graph_search",
        "kg_card_open",
        "kg_card_expand",
        "kg_edge_open",
        "kg_community_open",
        "kg_community_expand",
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
    }
)

_STATEFUL_READ_TOOLS = frozenset(
    {"agent_run_state_open", "agent_evidence_ledger_open"}
)

_INITIAL_RESEARCH_TOOLS = frozenset(
    {
        "research_data_catalog_open",
        "market_change_brief_open",
        "market_premarket_context_open",
        "market_frame_open",
        "market_topic_open",
        "research_current_report_open",
        "research_view_open",
        "role_memory_search",
        "role_memory_open",
        "role_outcome_search",
        "role_outcome_open",
        "research_exposure_summary_open",
        "agent_run_state_open",
        "agent_evidence_ledger_open",
        "kg_relation_graph_search",
        "external_web_search",
    }
)
_MARKET_DRILLDOWN_TOOLS = frozenset(
    {
        "market_dimension_open",
        "market_domain_open",
        "market_evidence_open",
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
    }
)
_MARKET_ENTRY_TOOLS = frozenset(
    {
        "research_data_catalog_open",
        "market_change_brief_open",
        "market_premarket_context_open",
        "market_frame_open",
        "market_topic_open",
    }
)
_GRAPH_DETAIL_TOOLS = frozenset(
    {
        "kg_card_open",
        "kg_card_expand",
        "kg_edge_open",
        "kg_community_open",
        "kg_community_expand",
    }
)
_EXTERNAL_DETAIL_TOOLS = frozenset(
    {"external_web_read", "external_content_read"}
)
_POSITION_DETAIL_TOOLS = frozenset(
    {"research_position_open", "research_position_performance_open"}
)
_CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")
_SERVER_ONLY_TIME_FIELDS = {
    "cutoff_at",
    "created_at",
    "updated_at",
    "collected_at",
    "fetched_at",
    "ingested_at",
}
_FACT_TIME_FIELDS = {
    "as_of",
    "fact_time",
    "current_as_of",
    "baseline_as_of",
    "observed_at",
}
_MODEL_HIDDEN_AMBIGUOUS_FIELDS = {
    # Provider volume_ratio is useful on a dashboard, but its exact baseline and
    # session normalization are not exposed by the current market contract.  It
    # therefore cannot support a research claim about expanding/contracting
    # volume and is withheld until the contract carries those semantics.
    "volume_ratio",
}


class ResearchMCPServerStreamableHttp(MCPServerStreamableHttp):
    """Reject exact duplicate reads within one run without hiding valid drilldowns."""

    def __init__(
        self,
        *args,
        run_context: AgentRunContext | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._completed_read_keys: set[str] = set()
        self._completed_semantic_groups: set[str] = set()
        self._semantic_group_locks: dict[str, asyncio.Lock] = {}
        self._run_context = run_context

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        arguments = _expand_evidence_aliases(arguments, self._run_context)
        cache_key = _read_call_key(tool_name, arguments)
        semantic_group = _semantic_read_group(tool_name)
        if semantic_group is not None:
            lock = self._semantic_group_locks.setdefault(
                semantic_group,
                asyncio.Lock(),
            )
            async with lock:
                if semantic_group in self._completed_semantic_groups:
                    return _skipped_read_result(
                        tool_name,
                        "本次运行已打开语义等价的市场入口；请使用已有结果继续下钻。",
                    )
                result = await self._call_tool_isolated(tool_name, arguments, meta)
                if not bool(result.isError):
                    self._completed_semantic_groups.add(semantic_group)
                    self._completed_read_keys.add(cache_key)
                return _compact_market_evidence(
                    result,
                    self._run_context,
                    tool_name=tool_name,
                )
        if (
            tool_name in RESEARCH_READ_TOOLS
            and tool_name not in _STATEFUL_READ_TOOLS
            and cache_key in self._completed_read_keys
        ):
            return _skipped_read_result(
                tool_name,
                "本次运行已使用完全相同参数完成该读取；"
                "请使用已有结果、调整下钻参数或提交结论。",
            )
        result = await self._call_tool_isolated(tool_name, arguments, meta)
        if tool_name in RESEARCH_READ_TOOLS and not bool(result.isError):
            self._completed_read_keys.add(cache_key)
        return _compact_market_evidence(
            result,
            self._run_context,
            tool_name=tool_name,
        )

    async def _call_tool_isolated(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        meta: dict[str, Any] | None,
    ) -> CallToolResult:
        """Run a parallel read in its own MCP/anyio cancellation scope.

        The primary long-lived session remains responsible for tool discovery.
        A model turn can execute several calls concurrently, but no data call
        shares a ClientSession with a sibling task. The SDK's bounded retry
        policy therefore applies to one failed read without cancelling the run.
        """

        isolated = MCPServerStreamableHttp(
            name=f"{self.name}:{tool_name}",
            params=dict(self.params),
            cache_tools_list=True,
            client_session_timeout_seconds=self.client_session_timeout_seconds,
            max_retry_attempts=self.max_retry_attempts,
            retry_backoff_seconds_base=self.retry_backoff_seconds_base,
            require_approval="never",
        )
        await isolated.connect()
        try:
            return await isolated.call_tool(tool_name, arguments, meta)
        finally:
            await isolated.cleanup()

def _semantic_read_group(tool_name: str) -> str | None:
    if tool_name in {"market_change_brief_open", "market_frame_open"}:
        return "market_starting_frame"
    return None


def _skipped_read_result(tool_name: str, reason: str) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "operation": tool_name,
                        "status": "duplicate_skipped",
                        "reason": reason,
                    },
                    ensure_ascii=False,
                ),
            )
        ],
        isError=False,
    )


def _expand_evidence_aliases(value: Any, context: AgentRunContext | None) -> Any:
    if context is None:
        return value
    if isinstance(value, str):
        return context.evidence_aliases.get(value, value)
    if isinstance(value, list):
        return [_expand_evidence_aliases(item, context) for item in value]
    if isinstance(value, dict):
        return {
            key: _expand_evidence_aliases(item, context)
            for key, item in value.items()
        }
    return value


def _compact_market_evidence(
    result: CallToolResult,
    context: AgentRunContext | None,
    *,
    tool_name: str = "",
) -> CallToolResult:
    """Replace verbose reversible locators with run-local model-facing aliases."""

    if context is None:
        return result
    reverse = {reference: alias for alias, reference in context.evidence_aliases.items()}

    def alias_for(reference: str) -> str:
        alias = reverse.get(reference)
        if alias is None:
            market_alias_count = sum(
                key.startswith("market_ref:M")
                for key in context.evidence_aliases
            )
            alias = f"market_ref:M{market_alias_count + 1}"
            context.evidence_aliases[alias] = reference
            reverse[reference] = alias
        return alias

    def visit(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("market:v1:"):
            return alias_for(value)
        if isinstance(value, str):
            return _compact_model_timestamp(value)
        if isinstance(value, list):
            return [visit(child) for child in value]
        if isinstance(value, dict):
            compacted = {
                key: visit(child)
                for key, child in value.items()
                if key not in _SERVER_ONLY_TIME_FIELDS
                and key not in _MODEL_HIDDEN_AMBIGUOUS_FIELDS
            }
            return _hoist_shared_fact_times(compacted)
        return value

    compacted = []
    for item in result.content:
        if not isinstance(item, TextContent):
            compacted.append(item)
            continue
        text_value = item.text
        try:
            payload = json.loads(text_value)
        except json.JSONDecodeError:
            compacted.append(item)
            continue

        compacted.append(
            TextContent(
                type="text",
                text=json.dumps(
                    _project_model_tool_payload(visit(payload), tool_name),
                    ensure_ascii=False,
                ),
            )
        )
    result.content = compacted
    if isinstance(result.structuredContent, (dict, list)):
        result.structuredContent = _project_model_tool_payload(
            visit(result.structuredContent),
            tool_name,
        )
    return result


def _project_model_tool_payload(value: Any, tool_name: str) -> Any:
    """Remove transport/runtime metadata from the model-facing tool result.

    The full MCP payload remains available in server audit storage.  This view
    contains only facts, comparison semantics, navigation handles, and errors
    that can change the model's next research decision.
    """

    if isinstance(value, list):
        return [
            projected
            for item in value
            if (projected := _project_model_tool_payload(item, tool_name)) is not None
        ]
    if not isinstance(value, dict):
        return value
    if (
        tool_name == "research_current_report_open"
        and isinstance(value.get("report"), dict)
    ):
        return {
            "report": _project_current_research_report(value["report"]),
            "next_operations": value.get("next_operations", []),
        }
    if tool_name == "research_view_open" and isinstance(value.get("view"), dict):
        return {
            "view": _project_research_view(value["view"]),
            "next_operations": value.get("next_operations", []),
        }
    if tool_name == "market_instrument_history" and isinstance(value.get("items"), list):
        return _project_market_history(value)
    if value.get("metric") == "volume_ratio":
        return None

    always_internal = {
        "operation",
        "frame_id",
        "research_state",
        "research_implication",
        "significant_change_count",
        "significant_changes_truncated",
    }
    projected: dict[str, Any] = {}
    for key, item in value.items():
        if key in always_internal:
            continue
        if key == "status" and item == "available":
            continue
        if tool_name == "market_change_brief_open" and key in {
            "focus",
            "comparison_cutoff_at",
            "quality_issues",
            "stop_reason",
        }:
            continue
        if tool_name == "market_change_brief_open" and key == "significant_changes":
            allowed = {
                "dimension",
                "subject_id",
                "metric",
                "unit",
                "current_value",
                "baseline_value",
                "percent_change",
                "current_as_of",
                "baseline_as_of",
                "current_evidence_locator",
                "baseline_evidence_locator",
            }
            item = [
                {field: child[field] for field in allowed if field in child}
                for child in item
                if isinstance(child, dict) and child.get("metric") != "volume_ratio"
            ]
        child = _project_model_tool_payload(item, tool_name)
        if child is not None:
            projected[key] = child
    return projected


def _project_market_history(value: dict[str, Any]) -> dict[str, Any]:
    """Project verbose persisted rows into a lossless-enough research series.

    Storage IDs, duplicate identity fields and collection timestamps add no
    analytical value.  The ordered OHLCV matrix retains every requested bar,
    while deterministic window statistics reduce arithmetic load on the model.
    """

    bars: list[list[Any]] = []
    for item in value.get("items", []):
        if not isinstance(item, dict):
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        trade_date = item.get("trade_date") or data.get("date")
        bars.append([
            trade_date,
            _compact_number(data.get("open")),
            _compact_number(data.get("high")),
            _compact_number(data.get("low")),
            _compact_number(data.get("close")),
            _compact_number(data.get("volume")),
        ])
    valid_bars = [bar for bar in bars if isinstance(bar[4], (int, float))]
    closes = [float(bar[4]) for bar in valid_bars]
    stats: dict[str, Any] = {}
    if closes:
        stats["latest_close"] = _compact_number(closes[0])
        for window in (5, 20, 60, 120):
            if len(closes) >= window:
                sample_bars = valid_bars[:window]
                sample = closes[:window]
                baseline = sample[-1]
                stats[f"return_{window}_bars_pct"] = _compact_number(
                    (sample[0] / baseline - 1) * 100 if baseline else None
                )
                high_bar = max(sample_bars, key=lambda bar: float(bar[4]))
                low_bar = min(sample_bars, key=lambda bar: float(bar[4]))
                stats[f"close_high_{window}_bars"] = [
                    high_bar[0], _compact_number(high_bar[4]),
                ]
                stats[f"close_low_{window}_bars"] = [
                    low_bar[0], _compact_number(low_bar[4]),
                ]
                stats[f"up_days_{window}_bars"] = sum(
                    1
                    for newer, older in zip(sample[:-1], sample[1:], strict=False)
                    if newer > older
                )
                stats[f"down_days_{window}_bars"] = sum(
                    1
                    for newer, older in zip(sample[:-1], sample[1:], strict=False)
                    if newer < older
                )
    result = {
        "code": value.get("code"),
        "data_type": value.get("data_type"),
        "bar_count": len(bars),
        "order": "newest_first",
        "bar_fields": ["trade_date", "open", "high", "low", "close", "volume_raw"],
        "bars": bars[:10],
        "bars_note": (
            "Only the 10 newest bars are shown to the model; window_statistics "
            "are deterministically computed from all requested bars. Full rows "
            "remain in the server evidence audit."
        ),
        "window_statistics": stats,
        "window_evidence": value.get("window_evidence"),
        "series_semantics": value.get("series_semantics"),
    }
    return result


def _compact_number(value: Any) -> Any:
    if not isinstance(value, (int, float)):
        return value
    rounded = round(float(value), 4)
    return int(rounded) if rounded.is_integer() else rounded


def _project_research_view(view: dict[str, Any]) -> dict[str, Any]:
    """Return the durable decision state needed to review an existing view."""

    confidence = view.get("confidence")
    if isinstance(confidence, dict):
        confidence = {
            key: confidence.get(key)
            for key in ("overall", "rationale")
            if confidence.get(key) is not None
        }
    market_structure = view.get("market_structure")
    if isinstance(market_structure, dict):
        market_structure = {
            key: market_structure.get(key)
            for key in (
                "breadth",
                "leadership_concentration",
                "crowding_and_reversal_risk",
                "persistence_assessment",
                "pricing_state",
            )
            if market_structure.get(key) is not None
        }
    mechanism_chain = []
    for link in view.get("mechanism_chain") or []:
        if not isinstance(link, dict):
            continue
        mechanism_chain.append(
            {
                key: link.get(key)
                for key in (
                    "link_id",
                    "cause",
                    "mechanism",
                    "effect",
                    "status",
                    "invalidation_condition",
                )
                if link.get(key) is not None
            }
        )
    return {
        key: item
        for key, item in {
            "view_id": view.get("view_id"),
            "revision_id": view.get("revision_id"),
            "title": view.get("title"),
            "status": view.get("status"),
            "event": view.get("event"),
            "scope": view.get("scope"),
            "thesis": view.get("thesis"),
            "confidence": confidence,
            "valid_until": view.get("valid_until"),
            "hypotheses": view.get("hypotheses"),
            "mechanism_chain": mechanism_chain,
            "market_structure": market_structure,
            "decision_boundary": view.get("decision_boundary"),
            "invalidation_conditions": view.get("invalidation_conditions"),
            "forecasts": view.get("forecasts"),
        }.items()
        if item not in (None, [], {})
    }


def _project_current_research_report(report: dict[str, Any]) -> dict[str, Any]:
    """Expose durable current state without replaying the full old report."""

    active_views = [
        _project_research_view(item)
        for item in report.get("active_views") or []
        if isinstance(item, dict)
    ]
    return {
        key: item
        for key, item in {
            "report_id": report.get("report_id"),
            "report_revision_id": (
                report.get("proposed_report_revision_id")
                or report.get("report_revision_id")
            ),
            "status": report.get("status"),
            "research_question": report.get("research_question"),
            "active_views": active_views,
            "observation_requirements": report.get("observation_requirements"),
            "evidence_gaps": report.get("evidence_gaps"),
            "no_change_reason": report.get("no_change_reason"),
        }.items()
        if item not in (None, [], {})
    }


def _compact_model_timestamp(value: str) -> str:
    """Render ISO timestamps for the model as minute-level China local time."""

    if "T" not in value:
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        parsed = parsed.astimezone(_CHINA_TIMEZONE)
    return parsed.strftime("%Y-%m-%d %H:%M")


def _hoist_shared_fact_times(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep one shared fact time for homogeneous result lists."""

    for field, value in list(payload.items()):
        if not isinstance(value, list) or len(value) < 2:
            continue
        if not all(isinstance(item, dict) for item in value):
            continue
        shared: dict[str, Any] = {}
        for time_field in _FACT_TIME_FIELDS:
            values = [item.get(time_field) for item in value]
            if values[0] is not None and all(item == values[0] for item in values):
                shared[time_field] = values[0]
        if not shared:
            continue
        for item in value:
            for time_field in shared:
                item.pop(time_field, None)
        payload[f"{field}_shared_time"] = shared
    return payload


def _read_call_key(tool_name: str, arguments: dict[str, Any] | None) -> str:
    return f"{tool_name}:" + json.dumps(
        arguments or {},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def financial_tool_filter(context: ToolFilterContext, tool: Any) -> bool:
    """Project only the read tools for the current role task.

    Business writes are never unlocked by a model-run flag.  The model emits a
    Proposal through its local structured-output tool; a deterministic
    application service performs any later commit.
    """

    tool_name = str(getattr(tool, "name", ""))
    run_context = context.run_context.context
    if isinstance(run_context, AgentRunContext):
        context_pack = run_context.research_context
        completed_reads = sum(
            invocation.name in RESEARCH_READ_TOOLS
            for invocation in run_context.tool_invocations
        )
        if (
            context_pack is not None
            and completed_reads >= context_pack.trigger.max_tool_calls
        ):
            return False
    if not (
        isinstance(run_context, AgentRunContext)
        and run_context.task_mode is ResearchTaskMode.RESEARCH_REVIEW
        and tool_name in RESEARCH_READ_TOOLS
    ):
        return False
    context_pack = run_context.research_context
    is_replay = bool(
        context_pack is not None
        and context_pack.trigger.run_mode.value == "replay"
    )
    # Live Research must not accidentally read a stale tracked-instrument row
    # when it intends to inspect an ad-hoc stock or ETF.  Replay is the inverse:
    # it may use persisted facts but can never contact the live upstream.
    if tool_name == "market_instrument_open" and not is_replay:
        return False
    if tool_name in {
        "market_instrument_realtime_open",
        "market_expression_compare_open",
    } and is_replay:
        return False
    called = {item.name for item in run_context.tool_invocations}
    if tool_name in _INITIAL_RESEARCH_TOOLS:
        return True
    if tool_name in _MARKET_DRILLDOWN_TOOLS:
        return bool(called.intersection(_MARKET_ENTRY_TOOLS))
    if tool_name in _GRAPH_DETAIL_TOOLS:
        return "kg_relation_graph_search" in called
    if tool_name in _EXTERNAL_DETAIL_TOOLS:
        return "external_web_search" in called
    if tool_name in _POSITION_DETAIL_TOOLS:
        return "research_exposure_summary_open" in called
    if tool_name in {"research_view_list"}:
        return "research_current_report_open" in called
    if tool_name in {"role_memory_case_open"}:
        return "role_memory_open" in called
    if tool_name in {"research_quality_list", "research_quality_open"}:
        return any(item.name in _SUBMIT_TOOL_NAMES for item in run_context.tool_invocations)
    return False


_SUBMIT_TOOL_NAMES = frozenset(
    {"submit_research_conclusion", "submit_investment_view_revision"}
)


def create_mcp_server(
    settings: AgentSettings,
    *,
    run_authorization: str = "",
    run_context: AgentRunContext | None = None,
) -> MCPServerStreamableHttp:
    headers = {
        "Authorization": f"Bearer {settings.mcp_bearer_token}",
        "X-Smart-Fund-Agent-Role": "research",
    }
    if run_authorization:
        headers["X-Smart-Fund-Run-Authorization"] = run_authorization
    # The Agents SDK forwards ``timeout`` to the HTTP request, not only to TCP
    # connection establishment.  Using the 15-second connect budget here used
    # to cancel legitimate external reads before the 120-second MCP tool
    # timeout/retry policy could run, and anyio then propagated that cancel
    # scope to sibling tools.  The request therefore gets the full tool budget.
    request_timeout = max(
        settings.mcp_connect_timeout,
        settings.mcp_tool_timeout,
    )
    # ``sse_read_timeout`` covers idle reads on the long-lived Streamable HTTP
    # connection. Research spends substantial time in model reasoning between
    # calls, so it must be longer than one tool execution.
    stream_read_timeout = max(settings.mcp_tool_timeout, 1800.0)
    return ResearchMCPServerStreamableHttp(
        name="smart-fund-server",
        params={
            "url": settings.mcp_url,
            "headers": headers,
            "timeout": request_timeout,
            "sse_read_timeout": stream_read_timeout,
            "terminate_on_close": True,
        },
        cache_tools_list=True,
        client_session_timeout_seconds=settings.mcp_tool_timeout,
        tool_filter=financial_tool_filter,
        max_retry_attempts=2,
        retry_backoff_seconds_base=1.0,
        require_approval="never",
        run_context=run_context,
    )
