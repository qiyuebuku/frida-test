"""Remote Smart Fund MCP connection and least-privilege tool filtering."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from agents.mcp import MCPServerStreamableHttp, ToolFilterContext
from mcp.types import CallToolResult, TextContent

from src.infrastructure.agent_runtime.config import AgentSettings
from src.application.agents.financial_research.context import AgentRunContext
from src.application.agents.financial_research.schemas import ResearchTaskMode
from src.application.services.market_evidence_locator import (
    MarketEvidenceIdentity,
    encode_market_evidence_locator,
    historical_analogue_evidence_locator,
)


RESEARCH_READ_TOOLS = frozenset(
    {
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
        "market_global_overview_open",
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
_MARKET_EVIDENCE_ALIAS_TOOLS = frozenset(
    {
        "market_dimension_open",
        "market_change_brief_open",
        "market_premarket_context_open",
        "market_global_overview_open",
        "market_domain_open",
        "market_topic_open",
        "market_evidence_open",
        "market_instrument_open",
        "market_instrument_realtime_open",
        "market_expression_compare_open",
        "market_instrument_history",
        "market_technical_state_open",
        "market_historical_analogue_open",
        "market_sector_overview",
        "market_sector_rankings",
        "market_sector_open",
        "market_sector_compare_open",
    }
)
_MARKET_ENTRY_TOOLS = frozenset(
    {
        "research_data_catalog_open",
        "market_change_brief_open",
        "market_premarket_context_open",
        "market_global_overview_open",
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
        # Preserve parallel research while protecting the remote API from a
        # retry storm when one model turn emits many tools during a network
        # flap. Four concurrent reads still cover a broad batch efficiently.
        self._read_semaphore = asyncio.Semaphore(4)
        self._run_context = run_context
        self._stateless_tools_cache: list | None = None

    async def list_tools(self, run_context=None, agent=None):
        """Discover tools through a disposable session and cache plain schemas.

        This client already executes every model read in an isolated session.
        Keeping a separate long-lived Streamable HTTP session only for tool
        discovery ties an anyio cancel scope to the Runner task and has caused
        ClosedResourceError after transient disconnects.  Tool schemas are
        immutable during one run, so retain the returned value, not the
        transport session.
        """

        if self._stateless_tools_cache is None:
            async def one_attempt():
                isolated = MCPServerStreamableHttp(
                    name=f"{self.name}:tool-discovery",
                    params=dict(self.params),
                    cache_tools_list=True,
                    client_session_timeout_seconds=self.client_session_timeout_seconds,
                    max_retry_attempts=self.max_retry_attempts,
                    retry_backoff_seconds_base=self.retry_backoff_seconds_base,
                    require_approval="never",
                )
                try:
                    await isolated.connect()
                    return await isolated.list_tools()
                finally:
                    try:
                        await isolated.cleanup()
                    except BaseException:
                        pass

            last_error: BaseException | None = None
            for attempt in range(1, 5):
                try:
                    self._stateless_tools_cache = await asyncio.create_task(
                        one_attempt()
                    )
                    break
                except BaseException as exc:
                    last_error = exc
                    if attempt < 4:
                        await asyncio.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))
            if self._stateless_tools_cache is None:
                assert last_error is not None
                raise last_error

        tools = self._stateless_tools_cache
        if self.tool_filter is not None:
            tools = await self._apply_tool_filter(tools, run_context, agent)
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        arguments = _expand_evidence_aliases(arguments, self._run_context)
        # Internal prepare/commit/abort contracts are sequential runtime calls.
        # They already have a connected, dedicated MCP session and must not
        # create a second short-lived session (which can leak an anyio
        # cancellation while the first session remains healthy).  Isolation is
        # needed only for model-owned reads that may run in parallel.
        if tool_name not in RESEARCH_READ_TOOLS:
            result = await super().call_tool(tool_name, arguments, meta)
            return _compact_market_evidence(
                result,
                self._run_context,
                tool_name=tool_name,
            )
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
        async with self._read_semaphore:
            return await self._call_tool_isolated_retried(
                tool_name, arguments, meta
            )

    async def _call_tool_isolated_retried(
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

        async def one_attempt() -> CallToolResult:
            isolated = MCPServerStreamableHttp(
                name=f"{self.name}:{tool_name}",
                params=dict(self.params),
                cache_tools_list=True,
                client_session_timeout_seconds=self.client_session_timeout_seconds,
                max_retry_attempts=self.max_retry_attempts,
                retry_backoff_seconds_base=self.retry_backoff_seconds_base,
                require_approval="never",
            )
            try:
                await isolated.connect()
                return await isolated.call_tool(tool_name, arguments, meta)
            finally:
                try:
                    await isolated.cleanup()
                except BaseException:
                    # The child task is disposable.  Cleanup errors must not
                    # replace a successful tool result or the transport error
                    # that controls the retry decision.
                    pass

        last_error: BaseException | None = None
        for attempt in range(1, 4):
            try:
                # Contain the MCP client's anyio cancellation scope.  Without
                # a child task one broken Streamable HTTP session can cancel
                # sibling reads and the whole Agent turn.
                result = await asyncio.create_task(one_attempt())
                if not _is_transient_tool_error(result):
                    return result
            except BaseException as exc:
                last_error = exc
            if attempt < 3:
                await asyncio.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))
        if last_error is not None:
            raise last_error
        return result


def _is_transient_tool_error(result: CallToolResult) -> bool:
    if not bool(result.isError):
        return False
    text = " ".join(
        item.text for item in result.content if isinstance(item, TextContent)
    ).lower()
    return any(
        marker in text
        for marker in (
            "cancelled",
            "canceled",
            "taskgroup",
            "connection",
            "disconnected",
            "remoteprotocolerror",
            "timed out",
            "timeout",
            "transport",
        )
    )

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
        if tool_name in _MARKET_EVIDENCE_ALIAS_TOOLS:
            context.opened_market_aliases.add(alias)
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
        payload = _attach_calculation_evidence(payload, tool_name)

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
        result.structuredContent = _attach_calculation_evidence(
            result.structuredContent,
            tool_name,
        )
        result.structuredContent = _project_model_tool_payload(
            visit(result.structuredContent),
            tool_name,
        )
    return result


def _attach_calculation_evidence(value: Any, tool_name: str) -> Any:
    """Give deterministic aggregate analyses their own auditable identity."""

    if tool_name != "market_historical_analogue_open" or not isinstance(value, dict):
        return value
    enriched = dict(value)
    enriched["analysis_evidence_locator"] = (
        historical_analogue_evidence_locator(value)
    )
    return enriched


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
    if tool_name in {"market_sector_compare_open", "market_sector_open"}:
        value = _annotate_sector_comparison_evidence(value)
    if tool_name == "market_historical_analogue_open":
        signal = value.get("signal_definition")
        robustness = value.get("robustness")
        temporal = robustness.get("temporal_holdout") if isinstance(robustness, dict) else None
        relative_temporal = (
            robustness.get("relative_temporal_holdout")
            if isinstance(robustness, dict) else None
        )
        return {
            key: item
            for key, item in {
                "subject_id": value.get("subject_id"),
                "benchmark_subject_id": value.get("benchmark_subject_id"),
                "signal_definition": (
                    {
                        child_key: signal.get(child_key)
                        for child_key in (
                            "features",
                            "match_distance_threshold",
                            "current_signal",
                        )
                        if signal.get(child_key) is not None
                    }
                    if isinstance(signal, dict)
                    else signal
                ),
                "forward_window_bars": value.get("forward_window_bars"),
                "sample_count": value.get("sample_count"),
                "minimum_sample_count": value.get("minimum_sample_count"),
                "calibration_status": value.get("calibration_status"),
                # Keep one explicitly named full-sample distribution.  The raw
                # service payload also contains temporal/relative distributions;
                # exposing all of them under generic ``statistics`` keys caused
                # models to copy a development value as a holdout value.
                "full_sample_distribution": value.get("statistics"),
                "calibration_readout": {
                    "absolute_return": _holdout_readout(temporal, relative=False),
                    "relative_to_benchmark": _holdout_readout(
                        relative_temporal, relative=True
                    ),
                    "usage_rule": (
                        "预测超额/相对收益时只使用 relative_to_benchmark；"
                        "预测对象自身涨跌时只使用 absolute_return。"
                    ),
                },
                "distribution_stability_readout": _distribution_stability_readout(
                    value.get("statistics"), robustness
                ),
                "robustness": (
                    {
                        child_key: robustness.get(child_key)
                        for child_key in (
                            "leakage_controls",
                            "threshold_sensitivity",
                            "strict_distance_threshold",
                            "strict_sample_count",
                            "strict_statistics",
                            "trimmed_one_each_tail_statistics",
                            "wide_match_share",
                            "interpretation",
                        )
                        if robustness.get(child_key) is not None
                    }
                    if isinstance(robustness, dict)
                    else robustness
                ),
                "analysis_evidence_locator": value.get("analysis_evidence_locator"),
            }.items()
            if item is not None
        }
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


def _holdout_readout(value: Any, *, relative: bool) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    development = value.get("development_statistics")
    holdout = value.get("holdout_statistics")
    development = development if isinstance(development, dict) else {}
    holdout = holdout if isinstance(holdout, dict) else {}
    label = "relative_return_pct" if relative else "return_pct"
    development_median = development.get("median_return_pct")
    holdout_median = holdout.get("median_return_pct")
    consistent = value.get("median_direction_consistent")
    if consistent is True and development_median is not None and holdout_median is not None:
        if development_median > 0 and holdout_median > 0:
            direction_readout = "开发集与留出集均为正，正方向一致"
        elif development_median < 0 and holdout_median < 0:
            direction_readout = "开发集与留出集均为负，负方向一致"
        else:
            direction_readout = "方向一致，但中位数包含零值，不能形成单向结论"
    elif consistent is False:
        direction_readout = "开发集与留出集方向不一致，不得声称规律稳定"
    else:
        direction_readout = "样本不足或尚未完成时间留出验证"
    return {
        f"development_median_{label}": development.get("median_return_pct"),
        f"holdout_median_{label}": holdout.get("median_return_pct"),
        f"development_positive_share_{label}": development.get("positive_share"),
        f"holdout_positive_share_{label}": holdout.get("positive_share"),
        "median_direction_consistent": value.get("median_direction_consistent"),
        "validation_status": value.get("validation_status"),
        "program_interpretation": direction_readout,
    }


def _distribution_stability_readout(
    full_statistics: Any,
    robustness: Any,
) -> dict[str, Any] | None:
    """Expose sign conflicts between full, strict and trimmed samples.

    These values already exist in the deterministic analysis, but leaving the
    model to compare three generic distribution trees caused it to omit the
    strongest counterevidence.  This readout performs no market judgement; it
    only labels whether the medians have the same arithmetic sign.
    """

    if not isinstance(full_statistics, dict) or not isinstance(robustness, dict):
        return None
    strict = robustness.get("strict_statistics")
    trimmed = robustness.get("trimmed_one_each_tail_statistics")
    strict = strict if isinstance(strict, dict) else {}
    trimmed = trimmed if isinstance(trimmed, dict) else {}
    full_median = full_statistics.get("median_return_pct")
    strict_median = strict.get("median_return_pct")
    trimmed_median = trimmed.get("median_return_pct")

    def sign(value: Any) -> int | None:
        if not isinstance(value, (int, float)):
            return None
        return 1 if value > 0 else -1 if value < 0 else 0

    full_sign = sign(full_median)
    strict_sign = sign(strict_median)
    sign_conflict = (
        full_sign is not None
        and strict_sign is not None
        and full_sign != strict_sign
    )
    return {
        "full_sample_median_return_pct": full_median,
        "strict_subset_median_return_pct": strict_median,
        "trimmed_sample_median_return_pct": trimmed_median,
        "strict_vs_full_direction_conflict": sign_conflict,
        "program_interpretation": (
            "严格相似子集与完整样本的中位方向相反，必须作为直接反证披露，"
            "不得声称分布方向稳健"
            if sign_conflict
            else "严格相似子集与完整样本未出现中位方向翻转"
        ),
    }


def _annotate_sector_comparison_evidence(value: dict[str, Any]) -> dict[str, Any]:
    """Make comparison endpoints explicit instead of asking the model to infer them.

    A historical anchor intentionally carries only identity until it is opened.
    Without this annotation models can confuse it with an unrelated baseline
    locator from the market brief and then persist that mistake in working
    memory.
    """

    projected = deepcopy(value)
    requirements: list[dict[str, Any]] = []
    candidates = projected.get("candidates") or []
    if projected.get("provider_sector_code") and projected.get("latest"):
        candidates = [projected]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        signals = candidate.get("latest_signals") or candidate.get("latest") or []
        dated = [item for item in signals if isinstance(item, dict) and item.get("trade_date")]
        if len(dated) < 2:
            continue
        current_date = max(str(item["trade_date"]) for item in dated)
        for signal in dated:
            locator = signal.get("evidence_locator")
            if not locator or str(signal.get("trade_date")) >= current_date:
                continue
            if any(
                key in signal
                for key in ("metric", "metric_value", "main_net_inflow", "change_pct")
            ):
                continue
            signal["comparison_role"] = "baseline_identity_only"
            signal["citation_ready"] = False
            requirements.append(
                {
                    "subject_id": candidate.get("provider_sector_code"),
                    "trade_date": signal.get("trade_date"),
                    "evidence_locator": locator,
                    "required_action": "market_evidence_open",
                    "reason": "基线仅有身份；打开后才能读取数值并证明变化。",
                }
            )
    if requirements:
        projected["comparison_evidence_requirements"] = requirements
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
                stats[f"drawdown_from_close_high_{window}_bars_pct"] = _compact_number(
                    (sample_bars[0][4] / high_bar[4] - 1) * 100
                    if high_bar[4]
                    else None
                )
                stats[f"close_low_{window}_bars"] = [
                    low_bar[0], _compact_number(low_bar[4]),
                ]
                high_price_bars = [
                    bar for bar in sample_bars if isinstance(bar[2], (int, float))
                ]
                if high_price_bars:
                    intraday_high_bar = max(
                        high_price_bars, key=lambda bar: float(bar[2])
                    )
                    stats[f"intraday_high_{window}_bars"] = [
                        intraday_high_bar[0], _compact_number(intraday_high_bar[2]),
                    ]
                    stats[
                        f"drawdown_from_intraday_high_{window}_bars_pct"
                    ] = _compact_number(
                        (sample_bars[0][4] / intraday_high_bar[2] - 1) * 100
                        if intraday_high_bar[2]
                        else None
                    )
                stats[f"up_transitions_within_{window}_bars"] = sum(
                    1
                    for newer, older in zip(sample[:-1], sample[1:], strict=False)
                    if newer > older
                )
                stats[f"down_transitions_within_{window}_bars"] = sum(
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
        "window_statistics_note": (
            "return_N_bars compares the newest close with the oldest close "
            "inside those N bars. up/down_transitions count the N-1 adjacent "
            "close-to-close moves inside that same window; they are not N "
            "complete daily returns because the prior close before the oldest "
            "bar is outside the window."
            " close_high uses closing prices; intraday_high uses daily high "
            "prices. Their drawdown fields are computed separately and must "
            "not be mixed."
        ),
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


def _completed_calls(run_context: AgentRunContext, tool_name: str) -> list[Any]:
    return [
        invocation
        for invocation in run_context.tool_invocations
        if invocation.name == tool_name
        and invocation.finished_at is not None
        and invocation.result is not None
        and "unavailable" not in str(invocation.result)
        and "not_found" not in str(invocation.result)
        and "Error executing tool" not in str(invocation.result)
    ]


def _decoded_call_result(invocation: Any) -> dict[str, Any]:
    return _decode_tool_result_object(invocation.result)


def _decode_tool_result_object(value: Any) -> dict[str, Any]:
    """Unwrap Agents SDK/MCP result envelopes into one JSON object."""

    if isinstance(value, dict):
        if isinstance(value.get("structuredContent"), dict):
            return value["structuredContent"]
        if value.get("type") == "text" and "text" in value:
            return _decode_tool_result_object(value["text"])
        return value
    if isinstance(value, str):
        candidate: Any = value.strip()
        decoded_any = False
        for _ in range(3):
            if not isinstance(candidate, str) or not candidate.startswith(("{", "[", '"')):
                break
            try:
                candidate = json.loads(candidate)
                decoded_any = True
            except (TypeError, json.JSONDecodeError):
                break
        return _decode_tool_result_object(candidate) if decoded_any else {}
    structured = getattr(value, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(value, "content", None)
    if content is not None:
        result = _decode_tool_result_object(content)
        if result:
            return result
    if isinstance(value, (list, tuple)):
        for item in value:
            result = _decode_tool_result_object(item)
            if result:
                return result
    text = getattr(value, "text", None)
    if text is not None:
        return _decode_tool_result_object(text)
    return {}


def research_ledger_missing_requirements(
    run_context: AgentRunContext,
) -> list[str]:
    """Explain the remaining convergence work in terms the Agent can act on."""

    called = {item.name for item in run_context.tool_invocations}
    latest_checkpoint_index = max(
        (
            index
            for index, invocation in enumerate(run_context.tool_invocations)
            if invocation.name == "checkpoint_research_working_memory"
            and invocation.finished_at is not None
            and invocation.result is not None
        ),
        default=-1,
    )
    reads_since_checkpoint = sum(
        invocation.name in RESEARCH_READ_TOOLS
        for invocation in run_context.tool_invocations[latest_checkpoint_index + 1 :]
    )
    completed_reads = sum(
        invocation.name in RESEARCH_READ_TOOLS
        for invocation in run_context.tool_invocations
    )
    analogue_calls = _completed_calls(
        run_context, "market_historical_analogue_open"
    )
    analogue_subjects = {
        str((invocation.arguments or {}).get("code") or "")
        for invocation in analogue_calls
        if isinstance(invocation.arguments, dict)
    }
    analogue_subjects.discard("")
    calls_by_subject: dict[str, list[Any]] = {}
    for invocation in analogue_calls:
        if not isinstance(invocation.arguments, dict):
            continue
        subject = str(invocation.arguments.get("code") or "")
        if subject:
            calls_by_subject.setdefault(subject, []).append(invocation)
    analogue_windows_by_subject = {
        subject: {
            int(invocation.arguments.get("forward_window") or 0)
            for invocation in subject_calls
            if isinstance(invocation.arguments, dict)
        }
        for subject, subject_calls in calls_by_subject.items()
    }
    historical_ready = bool(calls_by_subject) and all(
        any(
            _decoded_call_result(invocation).get("calibration_status")
            != "insufficient_samples"
            for invocation in subject_calls
        )
        or len(subject_calls) >= 2
        for subject_calls in calls_by_subject.values()
    )
    source_calls = [
        invocation
        for candidate in {
            "kg_card_open",
            "kg_edge_open",
            "external_content_read",
            "external_web_read",
        }
        for invocation in _completed_calls(run_context, candidate)
    ]
    quality_list_calls = _completed_calls(run_context, "research_quality_list")
    quality_detail_required = any(
        bool(_decoded_call_result(invocation).get("evaluations"))
        for invocation in quality_list_calls
    )
    quality_detail_opened = bool(
        _completed_calls(run_context, "research_quality_open")
    )
    opened_market_refs = {
        str(result.get("evidence_locator"))
        for invocation in _completed_calls(run_context, "market_evidence_open")
        if (result := _decoded_call_result(invocation)).get("evidence_locator")
    }
    required_comparison_refs: set[str] = set()
    for tool_name in ("market_sector_compare_open", "market_sector_open"):
        for invocation in _completed_calls(run_context, tool_name):
            result = _decoded_call_result(invocation)
            for requirement in result.get("comparison_evidence_requirements") or []:
                if isinstance(requirement, dict) and requirement.get("evidence_locator"):
                    required_comparison_refs.add(str(requirement["evidence_locator"]))
    unopened_comparison_refs = sorted(required_comparison_refs - opened_market_refs)
    context_pack = run_context.research_context
    is_broad_market_review = bool(
        context_pack is not None
        and "全市场" in str(context_pack.research_question or "")
    )
    checks = [
        (
            run_context.working_memory is not None and reads_since_checkpoint <= 12,
            "更新结构化研究工作记忆，使候选、已回答问题、剩余缺口和下一步反映最近证据",
        ),
        (completed_reads >= 12, "至少完成12次有效读取"),
        (
            bool(
                called.intersection(
                    {"market_instrument_history", "market_technical_state_open"}
                )
            ),
            "读取主候选的技术状态或对象历史",
        ),
        (
            historical_ready,
            "每个候选的历史类比样本不足时，保持对象和窗口不变并以更宽阈值重查一次",
        ),
        (
            any(_completed_calls(run_context, "market_evidence_open")),
            "打开至少一条记录级精确市场证据",
        ),
        (
            not unopened_comparison_refs,
            "打开比较变化所需的基线记录级证据："
            + "、".join(unopened_comparison_refs),
        ),
        (
            len(source_calls) >= 1,
            "仅在与候选判断直接相关时打开至少一份Card、Edge或外部原文；市场数据已足够时不要为凑数引入转载",
        ),
        (
            not quality_detail_required or quality_detail_opened,
            "打开最近一次研究质量评测详情，把已知缺陷和改进动作纳入本轮工作记忆",
        ),
        ("role_memory_search" in called, "查询适用研究记忆"),
        (
            not is_broad_market_review or len(analogue_subjects) >= 3,
            "全市场研究至少对三个板块候选完成同口径历史类比，且须覆盖至少两个异质候选族",
        ),
        (
            not is_broad_market_review
            or (
                len(analogue_subjects) >= 3
                and all(
                    len(analogue_windows_by_subject.get(subject, set()) - {0}) >= 2
                    for subject in analogue_subjects
                )
            ),
            "全市场研究的每个正式候选都要完成两个不同前瞻窗口的历史类比；不要留下会影响结论的窗口缺口",
        ),
        (
            not is_broad_market_review
            or "market_expression_compare_open" in called,
            "比较候选方向的可交易ETF表达",
        ),
    ]
    return [message for passed, message in checks if not passed]


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
        already_called = {item.name for item in run_context.tool_invocations}
        latest_checkpoint_index = max(
            (
                index
                for index, invocation in enumerate(run_context.tool_invocations)
                if invocation.name == "checkpoint_research_working_memory"
                and invocation.finished_at is not None
                and invocation.result is not None
            ),
            default=-1,
        )
        latest_quality_detail_index = max(
            (
                index
                for index, invocation in enumerate(run_context.tool_invocations)
                if invocation.name == "research_quality_open"
                and invocation.finished_at is not None
                and invocation.result is not None
            ),
            default=-1,
        )
        reads_since_checkpoint = sum(
            invocation.name in RESEARCH_READ_TOOLS
            and invocation.finished_at is not None
            for invocation in run_context.tool_invocations[latest_checkpoint_index + 1 :]
        )
        if (
            latest_quality_detail_index > latest_checkpoint_index
            or reads_since_checkpoint >= 12
        ):
            # All remote reads resume after the local checkpoint replaces the
            # run's structured state. This is a recurring state boundary, not
            # permanent one-shot tool hiding.
            return False
        last_failed_submit_index = max(
            (
                index
                for index, invocation in enumerate(run_context.tool_invocations)
                if invocation.name in _SUBMIT_TOOL_NAMES
                and invocation.result is not None
                and "校验失败" in str(invocation.result)
            ),
            default=-1,
        )
        if last_failed_submit_index >= 0:
            failed_text = str(
                run_context.tool_invocations[last_failed_submit_index].result
            )
            if tool_name in failed_text:
                recovered = any(
                    index > last_failed_submit_index
                    and invocation.name == tool_name
                    and invocation.finished_at is not None
                    and invocation.result is not None
                    and "unavailable" not in str(invocation.result)
                    and "not_found" not in str(invocation.result)
                    for index, invocation in enumerate(run_context.tool_invocations)
                )
                if not recovered:
                    return True
        completed_reads = sum(
            invocation.name in RESEARCH_READ_TOOLS
            for invocation in run_context.tool_invocations
        )
        missing_requirements = research_ledger_missing_requirements(run_context)
        reserve_threshold = max(
            0,
            (context_pack.trigger.max_tool_calls - 5)
            if context_pack is not None
            else 0,
        )
        hard_convergence_threshold = (
            context_pack.trigger.max_tool_calls
            if context_pack is not None
            else 0
        )
        if (
            context_pack is not None
            and completed_reads >= hard_convergence_threshold
        ):
            # Recovery reads are useful only while they are converging. A
            # missing locator that repeatedly returns unavailable must not
            # create an unbounded read loop. The ledger records unresolved
            # requirements explicitly; the agent can then submit an honest
            # evidence gap or insufficient-evidence conclusion.
            return (
                tool_name == "agent_evidence_ledger_open"
                and tool_name not in already_called
            )
        if context_pack is not None and completed_reads >= reserve_threshold:
            recovery_tools: set[str] = set()
            if any(
                "来源材料" in item or "外部原文" in item
                for item in missing_requirements
            ):
                if _completed_calls(run_context, "external_web_search"):
                    recovery_tools.add("external_web_read")
                else:
                    recovery_tools.add("external_web_search")
            if any("基线记录级证据" in item for item in missing_requirements):
                recovery_tools.add("market_evidence_open")
            if any("历史类比" in item for item in missing_requirements):
                recovery_tools.add("market_historical_analogue_open")
            if any("质量评测详情" in item for item in missing_requirements):
                recovery_tools.add("research_quality_open")
            if any("可交易ETF表达" in item for item in missing_requirements):
                recovery_tools.add("market_expression_compare_open")
            if recovery_tools:
                return tool_name in recovery_tools
        if (
            context_pack is not None
            and completed_reads >= context_pack.trigger.max_tool_calls
        ):
            # The read budget must stop further exploration without making the
            # run impossible to close. The ledger is a convergence/readout
            # operation, not additional market exploration.
            return not missing_requirements and (
                tool_name == "agent_evidence_ledger_open"
                and tool_name not in already_called
            )
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
    if "agent_evidence_ledger_open" in called:
        last_failed_submit_index = max(
            (
                index
                for index, invocation in enumerate(run_context.tool_invocations)
                if invocation.name in _SUBMIT_TOOL_NAMES
                and invocation.result is not None
                and "校验失败" in str(invocation.result)
            ),
            default=-1,
        )
        failed_text = (
            str(run_context.tool_invocations[last_failed_submit_index].result)
            if last_failed_submit_index >= 0
            else ""
        )
        recovery_tools = set()
        for candidate in RESEARCH_READ_TOOLS:
            if candidate not in failed_text:
                continue
            recovered = any(
                index > last_failed_submit_index
                and invocation.name == candidate
                and invocation.finished_at is not None
                and invocation.result is not None
                and "unavailable" not in str(invocation.result)
                and "not_found" not in str(invocation.result)
                for index, invocation in enumerate(run_context.tool_invocations)
            )
            if not recovered:
                recovery_tools.add(candidate)
        if recovery_tools:
            return tool_name in recovery_tools
        # Opening the ledger is the Agent's explicit convergence decision. The
        # Runtime then compacts all evidence into a trusted notebook and leaves
        # only local Proposal submission tools visible.
        return False
    if tool_name == "agent_evidence_ledger_open":
        return not research_ledger_missing_requirements(run_context)
    if tool_name == "research_current_report_open":
        return tool_name not in called
    if tool_name == "research_quality_list":
        return "research_current_report_open" in called and tool_name not in called
    if tool_name == "research_quality_open":
        return "research_quality_list" in called and tool_name not in called
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
