"""Reusable OpenAI Agents SDK runtime for one bounded research review."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from types import TracebackType
from uuid import uuid4

from agents import RunConfig, RunContextWrapper, Runner
from agents.models.openai_provider import OpenAIProvider
from agents.run_config import CallModelData, ModelInputData
from langfuse import propagate_attributes
from openai import AsyncOpenAI

from src.application.agents.financial_research.agent import (
    _historical_forward_window,
    _market_subject_equivalence_key,
    _select_forecast_calibration,
    create_financial_research_agent,
)
from src.application.agents.financial_research.audit import validate_research_result
from src.application.agents.financial_research.context import AgentRunContext
from src.application.agents.financial_research.context_compactor import (
    checkpoint_hot_evidence_refs,
    create_context_compactor_agent,
    frame_context_checkpoint,
    validate_context_checkpoint,
)
from src.application.agents.financial_research.instructions import build_run_input
from src.application.agents.financial_research.schemas import (
    CurrentResearchReportProposal,
    ResearchContextPack,
    ResearchTaskMode,
    ResearchTriggerEnvelope,
)
from src.application.agents.financial_research.semantic_evaluator import (
    SemanticResearchEvaluation,
    SemanticResearchEvaluationDraft,
    create_semantic_evaluator_agent,
)
from src.infrastructure.agent_runtime.config import AgentSettings
from src.infrastructure.agent_runtime.mcp import (
    RESEARCH_READ_TOOLS,
    _decode_tool_result_object,
    create_mcp_server,
    research_ledger_missing_requirements,
)
from src.infrastructure.agent_runtime.observability import (
    AgentAuditHooks,
    _langfuse_usage_details,
    configure_observability,
)
from src.infrastructure.agent_runtime.run_authorization import (
    issue_run_authorization,
)


logger = logging.getLogger(__name__)
_RUN_AUTHORIZATION_TTL_FLOOR_SECONDS = 6 * 60 * 60

# GLM-5.3 accepts roughly 1M input tokens. Its separate 128K maximum applies
# to one response (reasoning + visible output), not to the input context.
_MODEL_CONTEXT_WINDOW_TOKENS = 1_000_000
_EXPLORATION_COMPACTION_RATIO = float(
    os.getenv("SMART_FUND_RESEARCH_COMPACTION_RATIO", "0.80")
)
_COMPACTION_RETAIN_RATIO = float(
    os.getenv("SMART_FUND_RESEARCH_COMPACTION_RETAIN_RATIO", "0.10")
)
_COMPACTION_MIN_GROWTH_RATIO = float(
    os.getenv("SMART_FUND_RESEARCH_COMPACTION_MIN_GROWTH_RATIO", "0.06")
)
_REASONING_PRESSURE_TOKENS = int(
    os.getenv("SMART_FUND_RESEARCH_REASONING_PRESSURE_TOKENS", "18000")
)
_REASONING_PRESSURE_SHARE = float(
    os.getenv("SMART_FUND_RESEARCH_REASONING_PRESSURE_SHARE", "0.25")
)
_REASONING_PRESSURE_RETAIN_TOKENS = int(
    os.getenv("SMART_FUND_RESEARCH_REASONING_RETAIN_TOKENS", "20000")
)
_REASONING_PRESSURE_MIN_GROWTH_TOKENS = int(
    os.getenv("SMART_FUND_RESEARCH_REASONING_MIN_GROWTH_TOKENS", "8000")
)
_REPEATED_COMPACTION_RATIO = 0.42
_POST_LEDGER_COMPACTION_RATIO = 0.95


class FinancialAgentRuntime:
    """Execution container shared by scheduled, debug, and replay entrypoints.

    Scheduling and business-state persistence are intentionally outside this
    class.  The runtime owns only the model/tool loop and evidence audit.
    """

    def __init__(self, settings: AgentSettings | None = None) -> None:
        self.settings = settings or AgentSettings.from_env()
        self.settings.validate()
        self._openai_client = AsyncOpenAI(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            # GLM-5.3 always reasons and may need several minutes for a large
            # structured proposal. Keep a generous configurable safety bound;
            # the run-level deadline remains the final runaway guard.
            timeout=self.settings.llm_timeout,
            # One Research run commonly contains dozens of model turns.  A
            # short proxy/network flap must not discard all evidence already
            # gathered in the run.  The OpenAI client retries only idempotent
            # response creation failures and applies bounded backoff.
            # Final structured proposals are comparatively large. Keep two
            # transport retries so a timeout followed by a proxy disconnect
            # does not discard an otherwise complete research run.
            max_retries=2,
        )
        self._model_provider = OpenAIProvider(
            openai_client=self._openai_client,
            use_responses=True,
        )
        self._mcp_server = create_mcp_server(self.settings)
        self._agent = create_financial_research_agent(
            model=self.settings.model,
            mcp_server=self._mcp_server,
        )
        self._semantic_evaluator = create_semantic_evaluator_agent(
            model=self.settings.model,
        )
        self._context_compactor = create_context_compactor_agent(
            model=self.settings.model,
        )
        self._langfuse = configure_observability(self.settings)
        self._connected = False

    async def __aenter__(self) -> "FinancialAgentRuntime":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._connected:
            return
        await self._mcp_server.connect()
        self._connected = True

    async def close(self) -> None:
        if self._connected:
            await self._mcp_server.cleanup()
            self._connected = False
        await self._openai_client.close()
        if self._langfuse is not None:
            self._langfuse.flush()

    async def list_tools(self) -> list[str]:
        await self.connect()
        context = AgentRunContext(
            run_id=f"check-{uuid4().hex}",
            session_id="mcp-check",
            task_mode=ResearchTaskMode.RESEARCH_REVIEW,
        )
        tools = await self._mcp_server.list_tools(
            RunContextWrapper(context),
            self._agent,
        )
        return sorted(tool.name for tool in tools)

    async def _apply_research_input_filter(
        self,
        data: CallModelData[AgentRunContext],
        *,
        hooks: AgentAuditHooks,
    ) -> ModelInputData:
        """Project full SDK history onto a capacity-bounded model surface."""

        guarded = _apply_research_budget_guard(data)
        context = data.context
        raw_input = list(data.model_data.input)
        transient = guarded.input[len(raw_input):]
        active_input = _research_surface_input(
            raw_input=raw_input,
            checkpoint=context.surface_checkpoint,
            hot_items=context.surface_hot_items,
            shadowed_item_count=context.surface_shadowed_item_count,
            generation=context.surface_generation,
            transient=transient,
        )
        active = ModelInputData(input=active_input, instructions=guarded.instructions)
        before_tokens = _estimate_model_input_tokens(active)
        threshold_ratio = _compaction_threshold_ratio(context)
        threshold = int(_MODEL_CONTEXT_WINDOW_TOKENS * threshold_ratio)
        reasoning_tokens = _estimate_reasoning_surface_tokens(active_input)
        reasoning_share = reasoning_tokens / max(before_tokens, 1)
        capacity_pressure = before_tokens >= threshold
        # GLM can technically accept a very large input while still wasting
        # attention and latency by replaying many completed reasoning blocks.
        # Treat that as a separate pressure signal. We replace only a balanced,
        # closed prefix; the recent protocol/tool chain remains verbatim.
        reasoning_pressure = _reasoning_surface_is_pressured(
            context=context,
            total_tokens=before_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        if not capacity_pressure and not reasoning_pressure:
            return active
        pressure_kind = "capacity" if capacity_pressure else "reasoning_surface"
        min_growth_tokens = (
            int(_MODEL_CONTEXT_WINDOW_TOKENS * _COMPACTION_MIN_GROWTH_RATIO)
            if capacity_pressure
            else _REASONING_PRESSURE_MIN_GROWTH_TOKENS
        )
        if (
            context.surface_generation > 0
            and before_tokens - context.surface_last_after_tokens
            < min_growth_tokens
        ):
            logger.info(
                "research_context_pressure_in_hysteresis run_id=%s generation=%s "
                "kind=%s before=%s previous_after=%s",
                context.run_id,
                context.surface_generation,
                pressure_kind,
                before_tokens,
                context.surface_last_after_tokens,
            )
            return active

        retain_tokens = (
            int(_MODEL_CONTEXT_WINDOW_TOKENS * _COMPACTION_RETAIN_RATIO)
            if capacity_pressure
            else min(_REASONING_PRESSURE_RETAIN_TOKENS, before_tokens // 3)
        )
        selection = _select_surface_replacement(
            raw_input=raw_input,
            checkpoint=context.surface_checkpoint,
            shadowed_item_count=context.surface_shadowed_item_count,
            generation=context.surface_generation,
            retain_tokens=retain_tokens,
        )
        if selection is None:
            logger.warning(
                "research_context_pressure_without_safe_range run_id=%s tokens=%s",
                context.run_id,
                before_tokens,
            )
            return active

        source_items, retained_items, new_shadowed_count = selection
        fingerprint = hashlib.sha256(
            json.dumps(source_items, ensure_ascii=False, default=str).encode()
        ).hexdigest()[:16]
        evidence_index = _surface_evidence_index(
            context.tool_invocations,
            source_items,
        )
        try:
            checkpoint = await self._generate_context_checkpoint(
                source_items=source_items,
                evidence_index=evidence_index,
                fingerprint=fingerprint,
                run_id=context.run_id,
                generation=context.surface_generation + 1,
            )
        except Exception as error:  # noqa: BLE001
            logger.warning(
                "research_context_checkpoint_failed run_id=%s error=%s",
                context.run_id,
                error,
            )
            return active
        generation = context.surface_generation + 1
        hot_items = _surface_hot_evidence_items(
            raw_input=raw_input,
            invocations=context.tool_invocations,
            evidence_refs=checkpoint_hot_evidence_refs(checkpoint),
            retained_items=retained_items,
        )
        compacted = ModelInputData(
            input=[
                _surface_checkpoint_item(checkpoint, generation=generation),
                *hot_items,
                *retained_items,
                *transient,
            ],
            instructions=guarded.instructions,
        )
        after_tokens = _estimate_model_input_tokens(compacted)
        source_tokens = _estimate_input_tokens(source_items)
        checkpoint_tokens = _estimate_input_tokens(checkpoint)
        if checkpoint_tokens >= source_tokens:
            logger.warning(
                "research_context_checkpoint_exceeds_source run_id=%s "
                "checkpoint=%s source=%s",
                context.run_id,
                checkpoint_tokens,
                source_tokens,
            )
            return active
        if after_tokens >= before_tokens:
            logger.warning(
                "research_context_checkpoint_not_smaller run_id=%s before=%s after=%s",
                context.run_id,
                before_tokens,
                after_tokens,
            )
            return active
        if after_tokens >= threshold:
            logger.warning(
                "research_context_still_pressured run_id=%s after=%s threshold=%s; "
                "preserving balanced recent tail",
                context.run_id,
                after_tokens,
                threshold,
            )
        context.surface_checkpoint = checkpoint
        context.surface_hot_items = hot_items
        context.surface_shadowed_item_count = new_shadowed_count
        context.surface_generation = generation
        context.surface_last_before_tokens = before_tokens
        context.surface_last_after_tokens = after_tokens
        hooks.record_context_surface(
            run_id=context.run_id,
            generation=generation,
            checkpoint=checkpoint,
            evidence_index=evidence_index,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            shadowed_item_count=len(source_items),
            retained_item_count=len(hot_items) + len(retained_items),
            source_fingerprint=fingerprint,
        )
        logger.info(
            "research_surface_replaced: generation=%s kind=%s before=%s after=%s "
            "reasoning_tokens=%s reasoning_share=%.3f reduction=%.1f%% "
            "shadowed_items=%s retained_items=%s",
            generation,
            pressure_kind,
            before_tokens,
            after_tokens,
            reasoning_tokens,
            reasoning_share,
            100 * (before_tokens - after_tokens) / max(before_tokens, 1),
            len(source_items),
            len(hot_items) + len(retained_items),
        )
        return compacted

    async def _generate_context_checkpoint(
        self,
        *,
        source_items: list,
        evidence_index: list[dict],
        fingerprint: str,
        run_id: str,
        generation: int,
    ) -> str:
        projected_source_items = _checkpoint_source_projection(source_items)
        compactor_input = {
            "task": "把 shadowed_history 压缩为可继续研究的 Markdown 检查点。",
            "prior_and_shadowed_history": projected_source_items,
            "recoverable_evidence_index": evidence_index,
        }
        run_config = RunConfig(
            model_provider=self._model_provider,
            workflow_name="Research Context Compaction｜研究上下文压缩",
            tracing_disabled=True,
            trace_include_sensitive_data=self.settings.trace_sensitive_data,
        )
        observation = (
            self._langfuse.start_as_current_observation(
                name="06 上下文压缩｜LLM 研究摘要",
                as_type="generation",
                input={
                    "run_id": run_id,
                    "surface_generation": generation,
                    "source_fingerprint": fingerprint,
                    "source_item_count": len(source_items),
                    "source_tokens_estimated": _estimate_input_tokens(source_items),
                    "projected_item_count": len(projected_source_items),
                    "projected_tokens_estimated": _estimate_input_tokens(
                        projected_source_items
                    ),
                    "reasoning_items_omitted": len(source_items)
                    - len(projected_source_items),
                    "evidence_index": evidence_index,
                },
                metadata={
                    "run_id": run_id,
                    "source_fingerprint": fingerprint,
                    "surface_generation": generation,
                    "compression_mode": "surface_replacement",
                },
                model=self.settings.model,
            )
            if self._langfuse is not None
            else _null_context()
        )
        with observation as current_observation:
            validation_error = ""
            result = None
            checkpoint = ""
            for _attempt in range(3):
                payload = dict(compactor_input)
                if validation_error:
                    payload["previous_output_error"] = validation_error
                try:
                    result = await Runner.run(
                        self._context_compactor,
                        json.dumps(payload, ensure_ascii=False, default=str),
                        max_turns=1,
                        run_config=run_config,
                    )
                    checkpoint = validate_context_checkpoint(result.final_output)
                    referenced = set(re.findall(r"run_evidence:E\d+", checkpoint))
                    valid_references = {
                        str(item["evidence_ref"])
                        for item in evidence_index
                        if item.get("evidence_ref")
                    }
                    invented = referenced - valid_references
                    if invented:
                        raise ValueError(
                            "context checkpoint invented recovery references: "
                            + ", ".join(sorted(invented))
                        )
                    break
                except Exception as error:  # noqa: BLE001
                    validation_error = str(error)
            if not checkpoint:
                raise RuntimeError(
                    "context compactor failed to produce a valid checkpoint: "
                    + validation_error
                )
            if current_observation is not None:
                current_observation.update(
                    output={
                        "surface_generation": generation,
                        "checkpoint": checkpoint,
                        "checkpoint_chars": len(checkpoint),
                        "checkpoint_tokens_estimated": _estimate_input_tokens(checkpoint),
                    },
                    usage_details=_langfuse_usage_details(
                        result.context_wrapper.usage  # type: ignore[union-attr]
                    ),
                    status_message="completed",
                )
        if self._langfuse is not None:
            self._langfuse.flush()
        return checkpoint

    async def run(
        self,
        context_pack: ResearchContextPack,
        *,
        session_id: str | None = None,
        publish: bool = False,
        _run_id: str | None = None,
        _run_authorization: str | None = None,
    ) -> CurrentResearchReportProposal:
        run_id = _run_id or f"research-run-{uuid4().hex}"
        effective_session_id = session_id or run_id
        context = AgentRunContext(
            run_id=run_id,
            session_id=effective_session_id,
            task_mode=ResearchTaskMode.RESEARCH_REVIEW,
            research_context=context_pack,
            evidence_aliases={"frame_ref:F1": context_pack.market_state.frame_id},
        )
        run_input = build_run_input(
            context_pack=context_pack,
        )
        if len(run_input) > self.settings.max_input_chars:
            raise ValueError(
                "Research run input exceeds configured limit: "
                f"{len(run_input)} > {self.settings.max_input_chars}"
            )

        trace_metadata = {
            "run_id": run_id,
            "task_mode": ResearchTaskMode.RESEARCH_REVIEW.value,
            "run_mode": context_pack.trigger.run_mode.value,
            "trigger_slot": context_pack.trigger.trigger_slot.value,
            "data_time_policy": (
                "historical_replay_boundary"
                if context_pack.trigger.run_mode.value == "replay"
                else "latest_available_at_each_tool_call"
            ),
            "frame_id": context_pack.market_state.frame_id,
            "model": self.settings.model,
            "mcp_access": "read_only",
        }
        hooks = AgentAuditHooks(
            langfuse_client=self._langfuse,
            include_sensitive_data=self.settings.trace_sensitive_data,
            model=self.settings.model,
        )
        run_config = RunConfig(
            model_provider=self._model_provider,
            workflow_name="Research Review｜市场研究复核",
            group_id=effective_session_id,
            trace_metadata=trace_metadata,
            # Research uses business-semantic Langfuse observations from
            # AgentAuditHooks. Disable the SDK's noisy turn/response/mcp_tools tree.
            tracing_disabled=True,
            trace_include_sensitive_data=self.settings.trace_sensitive_data,
            # The model owns the research path.  The input filter adds only a
            # late self-review reminder after broad evidence coverage; it does
            # not choose a conclusion or block submission.
            call_model_input_filter=lambda data: self._apply_research_input_filter(
                data,
                hooks=hooks,
            ),
        )
        run_authorization = _run_authorization or issue_run_authorization(
            secret=self.settings.mcp_bearer_token,
            run_id=run_id,
            role="research",
            task=ResearchTaskMode.RESEARCH_REVIEW.value,
            cutoff_at=context_pack.trigger.cutoff_at,
            tools=[
                *RESEARCH_READ_TOOLS,
                "research_proposal_commit",
                "research_semantic_evaluation_commit",
                "research_run_abort",
            ],
            run_mode=context_pack.trigger.run_mode.value,
            # Runtime uses monotonic elapsed-time enforcement. Keep the signed
            # remote capability longer-lived so an NTP/WSL wall-clock jump does
            # not invalidate every subsequent MCP read mid-run.
            ttl_seconds=max(
                _RUN_AUTHORIZATION_TTL_FLOOR_SECONDS,
                context_pack.trigger.max_elapsed_seconds + 120,
            ),
        )
        run_mcp_server = create_mcp_server(
            self.settings,
            run_authorization=run_authorization,
            run_context=context,
        )
        run_agent = create_financial_research_agent(
            model=self.settings.model,
            mcp_server=run_mcp_server,
        )

        logger.info(
            "research_run_start run_id=%s session_id=%s trigger=%s cutoff=%s model=%s",
            run_id,
            effective_session_id,
            context_pack.trigger.trigger_slot.value,
            context_pack.trigger.cutoff_at.isoformat(),
            self.settings.model,
        )

        attributes = {
            "session_id": effective_session_id,
            "tags": [
                "smart-fund-agent",
                "research",
                context_pack.trigger.run_mode.value,
            ],
            "metadata": trace_metadata,
            "version": "3.0.0",
        }
        propagation = (
            propagate_attributes(**attributes)
            if self._langfuse is not None
            else _null_context()
        )
        trace_input: dict[str, object] = {
            "run_id": run_id,
            "session_id": effective_session_id,
            "trigger_reason": context_pack.trigger.reason,
            "research_question": context_pack.research_question,
            "run_mode": context_pack.trigger.run_mode.value,
        }
        if context_pack.trigger.run_mode.value == "replay":
            trace_input["replay_boundary"] = context_pack.trigger.cutoff_at.isoformat()
        run_observation = (
            self._langfuse.start_as_current_observation(
                name="Research Agent 研究运行",
                as_type="agent",
                input=trace_input,
                metadata=trace_metadata,
            )
            if self._langfuse is not None
            else _null_context()
        )
        try:
            with propagation:
                with run_observation as observation:
                    if observation is not None:
                        hooks.set_parent(
                            trace_id=observation.trace_id,
                            parent_span_id=observation.id,
                        )
                    async with asyncio.timeout(
                        context_pack.trigger.max_elapsed_seconds
                    ):
                        result = await Runner.run(
                            run_agent,
                            run_input,
                            context=context,
                            max_turns=self.settings.max_turns,
                            hooks=hooks,
                            run_config=run_config,
                        )
                        for repair_attempt in range(1, 4):
                            if _is_research_proposal_output(result.final_output):
                                break
                            logger.warning(
                                "research_non_proposal_final_output_retry "
                                "run_id=%s attempt=%s output_type=%s",
                                run_id,
                                repair_attempt,
                                type(result.final_output).__name__,
                            )
                            missing = research_ledger_missing_requirements(context)
                            continuation = result.to_input_list()
                            continuation.append({
                                "role": "user",
                                "content": (
                                    "你刚才只说明了下一步，没有完成它。自然语言不能结束本次运行。"
                                    "当前尚未满足的确定性收敛条件是："
                                    + ("；".join(missing) if missing else "无")
                                    + "。若仍有条件，立即只调用对应工具补齐；"
                                    "如果证据账本尚未打开且已无条件，立即实际调用 "
                                    "agent_evidence_ledger_open；"
                                    "随后必须调用 submit_research_conclusion 或 "
                                    "submit_investment_view_revision，直到工具返回通过校验的正式 Proposal。"
                                ),
                            })
                            result = await Runner.run(
                                run_agent,
                                continuation,
                                context=context,
                                max_turns=self.settings.max_turns,
                                hooks=hooks,
                                run_config=run_config,
                            )
                    if observation is not None:
                        raw_output = result.final_output
                        if isinstance(raw_output, CurrentResearchReportProposal):
                            trace_output: object = (
                                raw_output.model_dump(mode="json")
                                if self.settings.trace_sensitive_data
                                else {
                                    "run_id": raw_output.run_id,
                                    "status": raw_output.status.value,
                                    "report_summary": raw_output.report_summary,
                                    "view_revision_count": len(
                                        raw_output.view_revisions
                                    ),
                                    "evidence_gap_count": len(
                                        raw_output.evidence_gaps
                                    ),
                                }
                            )
                        else:
                            trace_output = raw_output
                        observation.update(output=trace_output)
        finally:
            hooks.close_open_observations()

        output = result.final_output
        if isinstance(output, str):
            output = CurrentResearchReportProposal.model_validate_json(output)
        if not isinstance(output, CurrentResearchReportProposal):
            raise TypeError(
                "Research Agent returned an unexpected output type: "
                f"{type(output).__name__}"
            )
        validate_research_result(output, context)
        output = CurrentResearchReportProposal.model_validate(
            _expand_evidence_aliases(
                output.model_dump(mode="python"),
                context.evidence_aliases,
            )
        )

        commit_result = await _call_internal_mcp_tool(
            self.settings,
            run_authorization=run_authorization,
            tool_name="research_proposal_commit",
            arguments={
                "proposal_payload": output.model_dump(mode="json"),
                "publish": publish,
            },
        )
        _raise_on_tool_error(commit_result, "research_proposal_commit")

        try:
            await self._run_semantic_evaluation(
                proposal=output,
                context=context,
                session_id=effective_session_id,
            )
        except Exception:
            # The evaluator is deliberately downstream and independent. Its
            # transient failure must not roll back or mislabel a valid Research
            # run; the missing semantic_evaluated_at remains directly observable.
            logger.exception(
                "research_semantic_evaluation_failed run_id=%s session_id=%s",
                output.run_id,
                effective_session_id,
            )

        logger.info(
            "research_run_end run_id=%s status=%s llm_calls=%s tool_calls=%s "
            "view_revisions=%s",
            run_id,
            output.status.value,
            context.llm_calls,
            len(context.tool_invocations),
            len(output.view_revisions),
        )
        if self._langfuse is not None:
            self._langfuse.flush()
        return output
    async def _run_semantic_evaluation(
        self,
        *,
        proposal: CurrentResearchReportProposal,
        context: AgentRunContext,
        session_id: str,
    ) -> SemanticResearchEvaluation:
        """Evaluate a completed report in an isolated model context."""

        evaluator_input = _semantic_evaluator_input(proposal, context)
        metadata = {
            "research_run_id": proposal.run_id,
            "evaluator_version": "research-semantic-v1",
            "model": self.settings.model,
            "isolation_policy": "never_feed_back_to_same_research_run",
        }
        run_config = RunConfig(
            model_provider=self._model_provider,
            workflow_name="Research Semantic Evaluation｜研究语义评测",
            group_id=session_id,
            trace_metadata=metadata,
            tracing_disabled=True,
            trace_include_sensitive_data=self.settings.trace_sensitive_data,
        )
        propagation = (
            propagate_attributes(
                session_id=session_id,
                tags=["smart-fund-agent", "research-evaluator"],
                metadata=metadata,
                version="1.0.0",
            )
            if self._langfuse is not None
            else _null_context()
        )
        observation = (
            self._langfuse.start_as_current_observation(
                name="Research Quality Evaluator 研究质量评测",
                as_type="agent",
                input=evaluator_input,
                metadata=metadata,
            )
            if self._langfuse is not None
            else _null_context()
        )
        evaluator_context = AgentRunContext(
            run_id=f"{proposal.run_id}:semantic",
            session_id=session_id,
            task_mode=ResearchTaskMode.RESEARCH_REVIEW,
        )
        hooks = AgentAuditHooks(
            langfuse_client=self._langfuse,
            include_sensitive_data=self.settings.trace_sensitive_data,
            model=self.settings.model,
        )
        with propagation:
            with observation as current_observation:
                if current_observation is not None:
                    hooks.set_parent(
                        trace_id=current_observation.trace_id,
                        parent_span_id=current_observation.id,
                    )
                try:
                    result = await Runner.run(
                        self._semantic_evaluator,
                        json.dumps(evaluator_input, ensure_ascii=False, default=str),
                        context=evaluator_context,
                        max_turns=3,
                        hooks=hooks,
                        run_config=run_config,
                    )
                finally:
                    hooks.close_open_observations()
                draft = result.final_output
                if isinstance(draft, str):
                    draft = SemanticResearchEvaluationDraft.model_validate_json(draft)
                if not isinstance(draft, SemanticResearchEvaluationDraft):
                    raise TypeError("semantic evaluator returned an unexpected output type")
                draft = _bind_semantic_assessment_references(draft, proposal)
                evaluation = SemanticResearchEvaluation(
                    **draft.model_dump(mode="python"),
                    run_id=proposal.run_id,
                    model=self.settings.model,
                )
                if current_observation is not None:
                    current_observation.update(output=evaluation.model_dump(mode="json"))
        evaluation_authorization = issue_run_authorization(
                secret=self.settings.mcp_bearer_token,
                run_id=proposal.run_id,
                role="research",
                task=ResearchTaskMode.RESEARCH_REVIEW.value,
                cutoff_at=proposal.cutoff_at,
                tools=["research_semantic_evaluation_commit"],
                run_mode=(
                    context.research_context.trigger.run_mode.value
                    if context.research_context is not None
                    else "shadow"
                ),
                ttl_seconds=180,
            )
        commit_result = await _call_internal_mcp_tool(
            self.settings,
            run_authorization=evaluation_authorization,
            tool_name="research_semantic_evaluation_commit",
            arguments={"evaluation_payload": evaluation.model_dump(mode="json")},
        )
        _raise_on_tool_error(commit_result, "research_semantic_evaluation_commit")
        if self._langfuse is not None:
            self._langfuse.flush()
        return evaluation

    async def prepare_and_run(
        self,
        trigger: ResearchTriggerEnvelope,
        *,
        research_question: str | None = None,
        session_id: str | None = None,
        publish: bool = False,
    ) -> CurrentResearchReportProposal:
        """Prepare context remotely, then run and commit through the same run ID."""
        if publish and trigger.run_mode.value != "production":
            raise ValueError("only a production run may publish Research state")
        run_id = f"research-run-{uuid4().hex}"
        authorization = issue_run_authorization(
            secret=self.settings.mcp_bearer_token,
            run_id=run_id,
            role="research",
            task=ResearchTaskMode.RESEARCH_REVIEW.value,
            cutoff_at=trigger.cutoff_at,
            tools=[
                *RESEARCH_READ_TOOLS,
                "research_run_prepare",
                "research_proposal_commit",
                "research_semantic_evaluation_commit",
                "research_run_abort",
            ],
            run_mode=trigger.run_mode.value,
            ttl_seconds=max(
                _RUN_AUTHORIZATION_TTL_FLOOR_SECONDS,
                trigger.max_elapsed_seconds + 180,
            ),
        )
        result = await _call_internal_mcp_tool(
            self.settings,
            run_authorization=authorization,
            tool_name="research_run_prepare",
            arguments={
                "trigger_payload": trigger.model_dump(mode="json"),
                "research_question": research_question or "",
            },
        )
        _raise_on_tool_error(result, "research_run_prepare")
        payload = _tool_result_payload(result)
        context_pack = ResearchContextPack.model_validate(
            payload["context_pack"]
        )
        try:
            return await self.run(
                context_pack,
                session_id=session_id,
                publish=publish,
                _run_id=run_id,
                _run_authorization=authorization,
            )
        except BaseException as exc:
            try:
                await _call_internal_mcp_tool(
                    self.settings,
                    run_authorization=authorization,
                    tool_name="research_run_abort",
                    arguments={
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            except BaseException:
                logger.exception("failed to close Research run audit state")
            raise


def _is_research_proposal_output(value: object) -> bool:
    if isinstance(value, CurrentResearchReportProposal):
        return True
    if not isinstance(value, str):
        return False
    try:
        CurrentResearchReportProposal.model_validate_json(value)
    except (ValueError, TypeError):
        return False
    return True


async def _call_internal_mcp_tool(
    settings: AgentSettings,
    *,
    run_authorization: str,
    tool_name: str,
    arguments: dict[str, object],
    attempts: int = 4,
):
    """Call an idempotent runtime contract through a fresh retriable session."""

    async def one_attempt():
        # The SDK's Streamable HTTP implementation uses an anyio cancel scope.
        # A transport failure can leave the *calling task* cancelled.  Keep
        # that scope inside a disposable child task so the orchestration task
        # remains able to perform the next bounded retry.
        server = create_mcp_server(
            settings,
            run_authorization=run_authorization,
        )
        try:
            await server.connect()
            return await server.call_tool(tool_name, arguments)
        finally:
            try:
                await server.cleanup()
            except BaseException:
                logger.warning(
                    "internal_mcp_cleanup_failed tool=%s",
                    tool_name,
                    exc_info=True,
                )

    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.create_task(one_attempt())
        except BaseException as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            logger.warning(
                "internal_mcp_transient_retry tool=%s attempt=%s/%s error=%s",
                tool_name,
                attempt,
                attempts,
                type(exc).__name__,
            )
            await asyncio.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))
    assert last_error is not None
    raise last_error


class _null_context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def _semantic_evaluator_input(
    proposal: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> dict[str, object]:
    """Build a bounded post-run audit pack without the Research conversation."""

    claims = [
        *proposal.claims,
        *[
            claim
            for revision in proposal.view_revisions
            for claim in revision.claims
        ],
    ]
    cited_references = _collect_semantic_audit_references(
        proposal.model_dump(mode="python")
    )
    reference_aliases = _semantic_citation_reference_aliases(proposal)
    canonical_to_alias = {
        canonical: alias for alias, canonical in context.evidence_aliases.items()
    }
    evidence_records: list[dict[str, object]] = []
    for invocation in context.tool_invocations:
        if invocation.result is None:
            continue
        serialized = json.dumps(
            invocation.result,
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )
        matching = []
        for reference in cited_references:
            alias = canonical_to_alias.get(reference)
            if _semantic_reference_matches(reference, alias, serialized):
                matching.append(reference)
        if not matching and invocation.name not in {
            "market_historical_analogue_open",
            "market_technical_state_open",
        }:
            continue
        excerpt = _semantic_evidence_excerpt(
            serialized,
            references=matching,
            canonical_to_alias=canonical_to_alias,
        )
        for reference, alias in reference_aliases.items():
            excerpt = excerpt.replace(reference, alias)
        evidence_records.append({
            "tool": invocation.name,
            "references": [
                reference_aliases.get(reference, reference)
                for reference in matching[:20]
            ],
            "result_excerpt": excerpt,
            "truncated": len(excerpt) < len(serialized),
        })
    evidence_records.sort(
        key=lambda item: (bool(item.get("references")), item.get("tool") == "market_historical_analogue_open"),
        reverse=True,
    )
    return {
        "research_run_id": proposal.run_id,
        "report": {
            "status": proposal.status.value,
            "summary": proposal.report_summary,
            "counterevidence_summary": proposal.counterevidence_summary,
            "claims": [
                _project_semantic_references(
                    claim.model_dump(mode="json"),
                    reference_aliases,
                )
                for claim in claims
            ],
            "views": [
                {
                    "view_id": revision.view_id,
                    "title": revision.title,
                    "thesis": revision.thesis,
                    "hypotheses": [
                        item.model_dump(mode="json")
                        for item in revision.hypotheses
                    ],
                    "mechanism_chain": [
                        _project_semantic_references(
                            item.model_dump(mode="json"),
                            reference_aliases,
                        )
                        for item in revision.mechanism_chain
                    ],
                    "market_structure": (
                        _project_semantic_references(
                            revision.market_structure.model_dump(mode="json"),
                            reference_aliases,
                        )
                        if revision.market_structure is not None
                        else None
                    ),
                    "forecasts": [
                        _project_semantic_references(
                            item.model_dump(mode="json"),
                            reference_aliases,
                        )
                        for item in revision.forecasts
                    ],
                    "decision_boundary": (
                        revision.decision_boundary.model_dump(mode="json")
                        if revision.decision_boundary is not None
                        else None
                    ),
                }
                for revision in proposal.view_revisions
            ],
        },
        "tool_trajectory": [
            invocation.name for invocation in context.tool_invocations
        ],
        "evidence_records": evidence_records[:60],
        "deterministic_checks": {
            "forecast_calibration_bindings": _forecast_calibration_bindings(
                proposal, context
            ),
        },
    }


def _semantic_citation_reference_aliases(
    proposal: CurrentResearchReportProposal,
) -> dict[str, str]:
    """Map opaque formal locators to model-friendly citation IDs."""

    aliases: dict[str, str] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            citation_id = value.get("citation_id")
            reference = value.get("reference")
            if isinstance(citation_id, str) and isinstance(reference, str):
                aliases.setdefault(reference, citation_id)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(proposal.model_dump(mode="python"))
    return aliases


def _project_semantic_references(
    value: object,
    aliases: dict[str, str],
) -> object:
    """Replace formal locators in the model-facing evaluator package."""

    if isinstance(value, dict):
        return {
            key: (
                aliases.get(child, child)
                if key == "reference" and isinstance(child, str)
                else _project_semantic_references(child, aliases)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_project_semantic_references(child, aliases) for child in value]
    return value


def _bind_semantic_assessment_references(
    draft: SemanticResearchEvaluationDraft,
    proposal: CurrentResearchReportProposal,
) -> SemanticResearchEvaluationDraft:
    """Replace model-friendly citation IDs with authoritative locators.

    Long opaque evidence locators are deterministic report data. Requiring the
    evaluator to reproduce them wastes output tokens and creates truncation or
    transcription risk, so the model selects a citation_id and Runtime binds
    its immutable reference before persistence.
    """

    aliases: dict[str, str] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            citation_id = value.get("citation_id")
            reference = value.get("reference")
            if isinstance(citation_id, str) and isinstance(reference, str):
                aliases[citation_id] = reference
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(proposal.model_dump(mode="python"))
    payload = draft.model_dump(mode="python")
    for assessment in payload["claim_citation_assessments"]:
        short_reference = assessment["reference"]
        assessment["reference"] = aliases.get(short_reference, short_reference)
    return SemanticResearchEvaluationDraft.model_validate(payload)


def _semantic_evidence_excerpt(
    serialized: str,
    *,
    references: list[str],
    canonical_to_alias: dict[str, str],
    max_chars: int = 15000,
) -> str:
    """Keep the cited rows, not merely the first rows of a large tool result."""

    # A projected tool result that already fits the audit budget is the most
    # faithful evidence. Slicing around a locator placed at the end of an
    # analogue result previously removed its development/holdout calibration
    # fields even though the complete model-facing result was only a few KB.
    if len(serialized) <= max_chars:
        return serialized
    if not references:
        return serialized[:max_chars]
    chunks: list[str] = []
    remaining = max_chars
    for reference in references:
        token = canonical_to_alias.get(reference) or reference
        index = serialized.find(token)
        if index < 0:
            continue
        # Most record locators precede their values, but compact overview tools
        # intentionally place one locator *after* a bounded table (for example
        # ``indices_evidence_locator`` follows the full US-index quote array).
        # A tiny backward window made those values visible to Research but
        # invisible to the semantic evaluator, producing false "unverifiable"
        # defects. Keep a symmetric neighbourhood around the cited locator.
        # The canonical reversible locator is already present in the sibling
        # ``references`` field and can be hundreds of characters long. Repeat
        # only its short run alias here so audit budget is spent on facts.
        label = f"reference_alias={token}\n"
        available = max(remaining - len(label), 0)
        before = min(1800, int(available * 0.7))
        after = max(available - before, 0)
        start = max(0, index - before)
        chunk = serialized[start : min(len(serialized), index + after)]
        labelled = f"{label}{chunk}"
        chunks.append(labelled)
        remaining -= len(labelled)
        if remaining <= 0:
            break
    return "\n---\n".join(chunks) if chunks else serialized[:max_chars]


def _forecast_calibration_bindings(
    proposal: CurrentResearchReportProposal,
    context: AgentRunContext,
) -> list[dict[str, object]]:
    """Expose same-subject/same-window range checks to the semantic judge."""

    results: dict[tuple[str, int | None], dict[str, object]] = {}
    for invocation in context.tool_invocations:
        if invocation.name != "market_historical_analogue_open":
            continue
        result = _decode_tool_result_object(invocation.result)
        subject = _market_subject_equivalence_key(
            str(result.get("subject_id") or "")
        )
        window = result.get("forward_window_bars")
        if subject and isinstance(window, (int, float)):
            results[(subject, int(window))] = result
    checks: list[dict[str, object]] = []
    for revision in proposal.view_revisions:
        for forecast in revision.forecasts:
            match = re.search(
                r"(?<!\d)(\d{1,3})\s*(?:个?交易日|日|bars?|根)",
                forecast.metric,
            )
            window = int(match.group(1)) if match else None
            relative = bool(forecast.benchmark_subject_id) or any(
                term in forecast.metric for term in ("相对", "超额")
            )
            result = _select_forecast_calibration(
                results,
                subject_key=_market_subject_equivalence_key(forecast.subject_id),
                declared_window=window,
                relative=relative,
            )
            statistics = result.get("statistics") if isinstance(result, dict) else None
            selected_window = (
                _historical_forward_window(result)
                if isinstance(result, dict)
                else None
            )
            lower_key = (
                "lower_quartile_relative_return_pct"
                if relative else "lower_quartile_return_pct"
            )
            upper_key = (
                "upper_quartile_relative_return_pct"
                if relative else "upper_quartile_return_pct"
            )
            calibrated_min = statistics.get(lower_key) if isinstance(statistics, dict) else None
            calibrated_max = statistics.get(upper_key) if isinstance(statistics, dict) else None
            matches = (
                isinstance(calibrated_min, (int, float))
                and isinstance(calibrated_max, (int, float))
                and forecast.expected_min_value is not None
                and forecast.expected_max_value is not None
                and abs(float(forecast.expected_min_value) - float(calibrated_min)) < 1e-6
                and abs(float(forecast.expected_max_value) - float(calibrated_max)) < 1e-6
            )
            checks.append({
                "forecast_id": forecast.forecast_id,
                "subject_id": forecast.subject_id,
                "declared_window_bars": window,
                "selected_window_bars": selected_window,
                "reported_range": [
                    forecast.expected_min_value,
                    forecast.expected_max_value,
                ],
                "calibrated_range": [calibrated_min, calibrated_max],
                "range_matches_same_window": matches,
            })
    return checks


def _collect_semantic_audit_references(value: object) -> set[str]:
    """Collect citations from claims, mechanisms, hypotheses and forecasts."""

    references: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
            return
        if not isinstance(item, dict):
            return
        reference = item.get("reference")
        support = item.get("support")
        if isinstance(reference, str) and support in {
            "supports",
            "contradicts",
            "context_only",
        }:
            references.add(reference)
        for child in item.values():
            visit(child)

    visit(value)
    return references


def _semantic_reference_matches(
    reference: str,
    alias: str | None,
    serialized_tool_result: str,
) -> bool:
    """Match canonical persisted citations against model-facing tool aliases."""

    return reference in serialized_tool_result or (
        alias is not None and alias in serialized_tool_result
    )


_DECISION_COVERAGE_TOOL_GROUPS = (
    frozenset({"market_change_brief_open", "market_frame_open"}),
    frozenset(
        {
            "market_sector_compare_open",
            "market_sector_rankings",
            "market_sector_overview",
        }
    ),
    frozenset(
        {
            "market_evidence_open",
            "market_sector_open",
            "market_instrument_open",
            "kg_card_open",
            "external_content_read",
        }
    ),
    frozenset(
        {
            "market_instrument_history",
            "market_technical_state_open",
            "market_historical_analogue_open",
            "market_sector_compare_open",
            "external_content_read",
            "role_outcome_open",
        }
    ),
)


def _apply_research_budget_guard(
    data: CallModelData[AgentRunContext],
) -> ModelInputData:
    """Tell an autonomous research run when it has enough evidence to converge.

    This does not prescribe which tools to call or provide a conclusion.  It only
    prevents an otherwise healthy tool loop from repeatedly widening its scope
    after the run already covers market context, comparison, precise evidence,
    and temporal or source validation.
    """

    context = data.context
    if not isinstance(context, AgentRunContext):
        return data.model_data
    completed = [
        invocation
        for invocation in context.tool_invocations
        if invocation.finished_at is not None
    ]
    completed_names = {invocation.name for invocation in completed}
    evidence_calls = sum(
        name
        not in {
            "submit_research_conclusion",
            "submit_investment_view_revision",
        }
        for name in (invocation.name for invocation in completed)
    )
    has_decision_coverage = all(
        completed_names.intersection(group)
        for group in _DECISION_COVERAGE_TOOL_GROUPS
    )
    completed_capabilities = "、".join(sorted(completed_names))
    last_search_index = max(
        (index for index, item in enumerate(completed) if item.name == "external_web_search"),
        default=-1,
    )
    last_read_index = max(
        (index for index, item in enumerate(completed) if item.name == "external_web_read"),
        default=-1,
    )
    has_unresolved_external_search = last_search_index > last_read_index
    if "agent_evidence_ledger_open" in completed_names:
        reminder_text = (
            "提交前结构审计：Evidence Ledger（证据账本）已经打开，现在不要再"
            "扩展研究范围。逐条检查 Proposal（提案）：observed_fact（观察事实）"
            "只能复述证据直接给出的对象、日期、字段和值；凡含有“说明、表明、"
            "意味着、背离、支持/不支持趋势、形成主线、持续、连续”等解释判断，"
            "必须拆成 inference（推断）。所有 UTC 时间必须先换算为北京时间再"
            "判断盘前/盘中/盘后；不同交易日或不同口径不得写成连续趋势。完成"
            "Hypothesis（假设）角色检查：data_quality（数据质量）只能讨论覆盖、"
            "新鲜度、截止时间、口径或缺失；任何市场方向、主线、涨跌原因解释"
            "必须归入 primary（主假设）或 alternative（替代假设）。"
            "直接反证必须削弱主观点的同一对象、传导机制或表达工具；仅反驳相邻"
            "行业不能冒充主观点反证。机制链中的政策/事件与资金行情共现只能标为"
            "inferred（推断），不能标为 observed（已观察）。只凭 volume_ratio"
            "（量比）不得写放量/缩量确认。删除所有没有历史校准的成交额、排名、"
            "涨跌幅和情绪阈值；Forecast 优先使用方向或相对基准方向。候选 ETF"
            "必须已经打开 identity（身份），未核验代码一律删除。"
            "Forecast 的 metric（指标）、baseline_value（基线值）和 benchmark"
            "（基准）必须使用同一语义：预测对象自身指标时，baseline_value 只能"
            "填写该对象同指标的当前值；预测相对基准收益时，metric 必须明确为"
            "相对收益，baseline_value 只能填写对象与基准当前值之差，无法计算就"
            "留空，不能拿对象自身涨跌幅冒充相对收益基线。"
            "单篇二手报道中的超常规模、收入、利润或资本开支数字，在没有正式"
            "原文或另一独立来源确认前，只能标为 source_claim（来源声称）和"
            "证据缺口，不能升级为主机制的 observed_fact（已观察事实）。"
            "09:30—11:30 和 13:00—15:00 的运行统一称盘中；只有午间休市且"
            "数据完整截至 11:30 时才称半日。情绪温度没有提供方定义或历史分布"
            "时只陈述数值，不得自行命名偏热、偏冷或极端。"
            "盘中指数和 ETF quote（行情）的 latest/close 字段只能称最新价或"
            "最新点位，不能称收盘价或净值。稀疏历史锚点只能写曾经出现，不能"
            "写连续、多日持续或趋势。"
            "重新审计 Evidence Gap（证据缺口）的 critical（关键）等级：只有"
            "缺失证据会让主要竞争假设无法区分、连低置信度条件预测都不能形成"
            "时才是关键。辅助资金渠道、单一历史序列或额外交叉确认缺失通常只"
            "降低置信度，不能用来逃避形成可证伪观点。"
            "状态一致性检查：只要 evidence_gaps（证据缺口）仍含 critical（关键）"
            "项，就不能提交 no_change（不修订），必须先补证据或提交 "
            "insufficient_evidence（证据不足）；此时还必须明确组合层可继续评估"
            "什么、不能据此做什么以及后续监控信号。完成这次自检后直接提交，"
            "不要先输出草稿，也不要改写事实来迎合结论。"
        )
    elif evidence_calls >= 18 and has_decision_coverage:
        missing_requirements = research_ledger_missing_requirements(context)
        readiness_instruction = (
            "Evidence Ledger（证据账本）当前尚未开放。还必须完成："
            + "；".join(missing_requirements)
            + "。只补齐这些项目，不要盲目试交报告。"
            if missing_requirements
            else "Evidence Ledger（证据账本）已经满足开放条件，立即调用它。"
        )
        memory_instruction = (
            "在打开 Evidence Ledger（证据账本）前，先调用 role_memory_search"
            "（角色记忆搜索）查找与当前主假设、直接反证或相似市场结构相关的"
            "既有研究经验；如果没有命中，报告中明确本次没有可适用经验，依赖"
            "当前事实与对象历史判断。"
            if "role_memory_search" not in completed_names
            else "适用研究记忆已经查询，不要重复搜索。"
        )
        unresolved_source_instruction = (
            "你在最近一次打开外部正文之后又进行了搜索。搜索结果仍只是导航，"
            "现在不要打开证据账本：从结果中只选择一篇最能确认或推翻主机制的"
            "原文打开；如果任何结果都不值得打开，就明确丢弃这些摘要中的全部"
            "事实和机制，不得把它们写进假设、机制链、主张或报告摘要。"
            if has_unresolved_external_search
            else ""
        )
        reminder_text = (
            "研究收敛提醒：你已经完成了市场概览、候选比较、精确证据与"
            "历史或来源验证，当前已有形成决定所需的多层证据。"
            f"本轮已经完成的工具能力：{completed_capabilities}。"
            "不要用相同参数重复调用这些能力；只有刷新实时数据、修正失败参数或"
            "下钻不同对象/字段时才再次调用。"
            "不要开启新主题、Community（社区）或无明确问题的搜索。"
            "Card、Edge 和外部原文不是打开证据账本的固定前置条件。只有当你"
            "准备把产业、政策、公司事件或其他叙事写入最终假设、机制链、主张"
            "或摘要时，才打开能够直接支持或反驳该叙事的原文；若判断只依赖"
            "已经打开的市场事实与确定性历史统计，不得为了满足流程补查来源。"
            f"{memory_instruction}"
            f"{unresolved_source_instruction}"
            f"{readiness_instruction}"
            "不要先在内部起草整份 Proposal 或手工复算工具已经返回的统计。"
            "只判断是否仍存在一个会改变结论的具体阻塞缺口：如果存在，只补"
            "这个缺口；如果不存在，立即打开 Evidence Ledger（证据账本）并"
            "提交你独立得出的结论。可以得出 no_change（不修订）；如果当前无"
            "观点且已有同对象行情或资金、对象历史或业绩、机制和直接反证，就"
            "应形成带条件、较低置信度且可证伪的观点，不能仅因缺少一个辅助"
            "维度改报 insufficient_evidence（证据不足）。不得为了"
            "满足格式而虚构因果链、趋势或确定性。"
        )
    else:
        return data.model_data
    reminder = {
        "role": "user",
        "content": reminder_text,
    }
    if "agent_evidence_ledger_open" in completed_names:
        reminder["content"] = (
            "证据账本已经打开。优先做最终事实审计和提交；如果发现会改变结论的"
            "具体证据缺口，仍可使用读取工具定向补齐，不要无目的扩展范围。逐条检查"
            "Claim 中的每个公司、产品、技术、事件和数字是否逐字出现在所引"
            "Citation 的保留结果中；没有出现就删除。observed_fact 只能复述"
            "记录原文；由多个数据推出的判断必须拆成 inference 并同时引用全部前提。预测只能使用该预测对象"
            "自己的历史校准，不能借用替代候选样本。最强反证必须直接攻击"
            "最终主假设。方向预测必须填写该对象历史分布支持的数值区间；若"
            "主候选样本不足，应改选已校准的候选或不做方向预测。若打开的来源"
            "正文包含明确反方观点或分歧，必须如实纳入反证，不能只摘有利段落。"
            "报告不得猜测媒体归属，只按工具返回的标题和来源署名。若保留结果"
            "仍出现 UTC 时间必须先换算为北京时间。"
            "结论范围必须与实际检验范围一致：只检验若干候选时只能写‘已检验"
            "候选中’，不得写‘全市场无方向’。累计上涨不等于连续上涨；百分比"
            "必须按证据值重新计算并统一四舍五入。"
            "不同域名不等于独立来源；正文含UGC、用户上传、转载或平台仅提供"
            "存储空间声明时，不得计作独立确认。多个媒体都复述同一公司公告时"
            "必须披露共同原始血缘。"
            "run_evidence:E* 只是压缩后重新打开工具结果的运行时指针，永远不能"
            "写入 Citation；调用 run_evidence_reopen 后，正式引用只能使用其"
            "返回内容内部的 market_ref、external_ref、Card 或 Edge 定位符。"
            "压缩前的完整工具结果仍由 Runtime 保存在服务端证据审计中，提交时"
            "会据此校验 Claim 与 Citation。检查点已经明确记录的对象、数值和"
            "正式定位符可以直接用于起草；不要仅为逐字复制而批量恢复。只有某个"
            "字段在检查点中不确定、被截断，或提交校验明确指出该项不受支持时，"
            "才使用 run_evidence_reopen 定向核对该项。"
            "同一对象同一指标若存在多个盘中快照，正文默认只采用最新快照；"
            "只有明确比较变化时才能同时引用旧值和新值，并写清各自时点。"
            "盘中形成的方向观点必须明确为条件观点，并给出下午或收盘反转时"
            "如何降级/推翻结论，不能把未完成交易日当成完整日样本。"
            "如果最终判断依赖历史稳定性，至少比较最终候选两个前瞻窗口并披露"
            "留出半样本数量，同时说明历史工具返回的非重叠窗口、防前视和阈值"
            "敏感性结果；未完成多窗口检验时缩小结论范围。"
            "已有可证伪方向性结论时不能提交 no_change。报告保持精炼，不复述研究过程。"
            + reminder_text
        )
    return ModelInputData(
        input=[*data.model_data.input, reminder],
        instructions=data.model_data.instructions,
    )


def _estimate_input_tokens(value) -> int:
    """Conservatively estimate mixed Chinese/JSON model-input tokens.

    The provider does not expose a preflight tokenizer. ASCII JSON averages
    roughly four characters per token while Chinese text is commonly close to
    one character per token, so count both classes separately and include a
    small structural allowance. This estimate is intentionally conservative
    without treating 30k JSON characters as an exhausted context window.
    """

    serialized = json.dumps(value, ensure_ascii=False, default=str)
    ascii_chars = sum(ord(char) < 128 for char in serialized)
    non_ascii_chars = len(serialized) - ascii_chars
    return max(1, (ascii_chars + 3) // 4 + non_ascii_chars + len(serialized) // 100)


def _surface_checkpoint_item(checkpoint: str, *, generation: int) -> dict[str, str]:
    return {
        "role": "user",
        "content": frame_context_checkpoint(checkpoint, generation=generation),
    }


def _research_surface_input(
    *,
    raw_input: list,
    checkpoint: str | None,
    hot_items: list,
    shadowed_item_count: int,
    generation: int,
    transient: list,
) -> list:
    """Build the replaceable model surface from the append-only SDK history."""

    shadowed = min(max(shadowed_item_count, 0), len(raw_input))
    projected = list(raw_input[shadowed:])
    visible_pair_ids = {
        pair_id
        for item in projected
        for pair_id in _item_call_pair(item)
        if pair_id
    }
    retained_hot = [
        item
        for item in hot_items
        if not any(
            pair_id in visible_pair_ids
            for pair_id in _item_call_pair(item)
            if pair_id
        )
    ]
    if checkpoint:
        projected = [
            _surface_checkpoint_item(checkpoint, generation=generation),
            *retained_hot,
            *projected,
        ]
    projected.extend(transient)
    return projected


def _surface_hot_evidence_items(
    *,
    raw_input: list,
    invocations: list,
    evidence_refs: list[str],
    retained_items: list,
) -> list:
    """Keep exact checkpoint-selected evidence without duplicating the tail."""

    orders = {
        int(match.group(1))
        for reference in evidence_refs[:6]
        if (match := re.fullmatch(r"run_evidence:E(\d+)", reference))
    }
    call_ids = {
        invocation.call_id
        for order, invocation in enumerate(invocations, start=1)
        if order in orders and invocation.call_id
    }
    retained_call_ids = {
        pair_id
        for item in retained_items
        for pair_id in _item_call_pair(item)
        if pair_id
    }
    selected_call_ids = call_ids - retained_call_ids
    return [
        item
        for item in raw_input
        if any(
            pair_id in selected_call_ids
            for pair_id in _item_call_pair(item)
            if pair_id
        )
    ]


def _item_call_pair(item: object) -> tuple[str | None, str | None]:
    if not isinstance(item, dict):
        return None, None
    item_type = item.get("type")
    call_id = item.get("call_id")
    if not isinstance(call_id, str):
        return None, None
    if item_type == "function_call":
        return call_id, None
    if item_type == "function_call_output":
        return None, call_id
    return None, None


def _balanced_surface_boundary(items: list, boundary: int) -> int:
    """Move a boundary left until no Responses reasoning/tool turn is split."""

    while boundary > 0:
        head_calls: set[str] = set()
        head_outputs: set[str] = set()
        tail_calls: set[str] = set()
        tail_outputs: set[str] = set()
        for index, item in enumerate(items):
            call_id, output_id = _item_call_pair(item)
            calls = head_calls if index < boundary else tail_calls
            outputs = head_outputs if index < boundary else tail_outputs
            if call_id:
                calls.add(call_id)
            if output_id:
                outputs.add(output_id)
        split_ids = (head_calls & tail_outputs) | (head_outputs & tail_calls)
        if split_ids:
            boundary = min(
                index
                for index, item in enumerate(items)
                if any(identifier in split_ids for identifier in _item_call_pair(item))
            )
            continue

        # A Responses assistant turn is commonly serialized as
        # reasoning -> one or more function_call items -> outputs. If the tail
        # starts at a function call, retain the reasoning item that immediately
        # introduced that call as required provider state. Do not attempt to
        # edit or summarize an item inside the retained protocol turn.
        if (
            boundary < len(items)
            and isinstance(items[boundary], dict)
            and items[boundary].get("type") == "function_call"
        ):
            reasoning_index = None
            for index in range(boundary - 1, -1, -1):
                prior = items[index]
                if not isinstance(prior, dict):
                    break
                prior_type = prior.get("type")
                if prior_type == "reasoning":
                    reasoning_index = index
                    break
                if prior_type not in {"function_call"}:
                    break
            if reasoning_index is not None:
                boundary = reasoning_index
                continue
        return boundary
    return boundary


def _select_surface_replacement(
    *,
    raw_input: list,
    checkpoint: str | None,
    shadowed_item_count: int,
    generation: int,
    retain_tokens: int,
) -> tuple[list, list, int] | None:
    """Choose an old head to summarize and an exact recent tail to retain."""

    shadowed = min(max(shadowed_item_count, 0), len(raw_input))
    active_raw = list(raw_input[shadowed:])
    if not active_raw:
        return None
    entries = list(active_raw)
    synthetic_checkpoint = None
    if checkpoint:
        synthetic_checkpoint = _surface_checkpoint_item(
            checkpoint,
            generation=generation,
        )
        entries.insert(0, synthetic_checkpoint)

    retained_tokens = 0
    boundary = len(entries)
    while boundary > 0:
        candidate_tokens = _estimate_input_tokens(entries[boundary - 1])
        if retained_tokens and retained_tokens + candidate_tokens > retain_tokens:
            break
        retained_tokens += candidate_tokens
        boundary -= 1
    boundary = _balanced_surface_boundary(entries, boundary)
    minimum_source = 2 if synthetic_checkpoint is not None else 1
    if boundary < minimum_source:
        return None

    source_items = entries[:boundary]
    retained_items = entries[boundary:]
    raw_source_count = boundary - (1 if synthetic_checkpoint is not None else 0)
    if raw_source_count <= 0:
        return None
    return source_items, retained_items, shadowed + raw_source_count


def _surface_evidence_index(invocations: list, source_items: list) -> list[dict]:
    """Expose stable recovery pointers for tool results shadowed by a checkpoint."""

    source_call_ids = {
        pair_id
        for item in source_items
        for pair_id in _item_call_pair(item)
        if pair_id
    }
    refs_in_prior_checkpoint = set(
        re.findall(
            r"run_evidence:E\d+",
            json.dumps(source_items, ensure_ascii=False, default=str),
        )
    )
    index: list[dict] = []
    for order, invocation in enumerate(invocations, start=1):
        evidence_ref = f"run_evidence:E{order}"
        if (
            invocation.finished_at is None
            or invocation.name.startswith("submit_")
            or (
                invocation.call_id not in source_call_ids
                and evidence_ref not in refs_in_prior_checkpoint
            )
        ):
            continue
        index.append(
            {
                "evidence_ref": evidence_ref,
                "tool": invocation.name,
                "call_id": invocation.call_id,
            }
        )
    return index


def _estimate_model_input_tokens(model_input: ModelInputData) -> int:
    return _estimate_input_tokens({
        "instructions": model_input.instructions,
        "input": model_input.input,
    })


def _estimate_reasoning_surface_tokens(items: list) -> int:
    """Estimate only model reasoning blocks currently replayed to the model.

    Reasoning remains fully observable on the generation that produced it.
    This metric only decides when completed old turns should leave the active
    model surface and be represented by a reversible research checkpoint.
    """

    reasoning_items = [
        item
        for item in items
        if isinstance(item, dict) and item.get("type") == "reasoning"
    ]
    if not reasoning_items:
        return 0
    return _estimate_input_tokens(reasoning_items)


def _checkpoint_source_projection(source_items: list) -> list:
    """Exclude old hidden reasoning from the checkpoint summarizer input.

    Tool facts, visible assistant decisions, and the original task are enough
    to reconstruct research state. Re-summarizing provider reasoning is slow,
    duplicates those decisions, and can make the checkpoint preserve obsolete
    exploratory paths. The append-only SDK/Langfuse history remains untouched.
    """

    return [
        item
        for item in source_items
        if not (isinstance(item, dict) and item.get("type") == "reasoning")
    ]


def _research_ledger_is_open(context: AgentRunContext) -> bool:
    """Return whether the run has entered its exact final evidence audit."""

    return any(
        invocation.name == "agent_evidence_ledger_open"
        and invocation.finished_at is not None
        for invocation in context.tool_invocations
    )


def _reasoning_surface_is_pressured(
    *,
    context: AgentRunContext,
    total_tokens: int,
    reasoning_tokens: int,
) -> bool:
    """Detect attention pressure without pretending the 1M window is full."""

    return (
        not _research_ledger_is_open(context)
        and reasoning_tokens >= _REASONING_PRESSURE_TOKENS
        and reasoning_tokens / max(total_tokens, 1) >= _REASONING_PRESSURE_SHARE
    )


def _compaction_threshold_ratio(context: AgentRunContext) -> float:
    """Compact renewed evidence pressure without entering a recovery loop.

    Hysteresis requires at least six percent of genuinely new input after a
    replacement.  Once a run has crossed the first pressure threshold, keep its
    active surface below the GLM gateway's observed unstable range instead of
    letting it grow back to the initial threshold.  A later checkpoint still
    represents real research progress, not a fixed workflow phase.
    """

    ratio = _EXPLORATION_COMPACTION_RATIO
    if context.surface_generation > 0:
        ratio = min(ratio, _REPEATED_COMPACTION_RATIO)
    if _research_ledger_is_open(context):
        ratio = max(ratio, _POST_LEDGER_COMPACTION_RATIO)
    return ratio


_NOTEBOOK_RESULT_PRIORITY = {
    "agent_evidence_ledger_open": 100,
    # This bounded semantic view contains citable US index, breadth and
    # leadership facts. It must survive compaction ahead of repeated analogue
    # calls; otherwise synthesis remembers a number but loses its source.
    "market_global_overview_open": 97,
    "market_historical_analogue_open": 95,
    "market_technical_state_open": 94,
    "market_expression_compare_open": 93,
    "research_quality_open": 92,
    "market_evidence_open": 90,
    "kg_edge_open": 89,
    "kg_card_open": 88,
    "external_content_read": 88,
    "external_web_read": 87,
    "market_sector_compare_open": 84,
    "market_sector_open": 83,
    "market_instrument_history": 82,
    "market_instrument_realtime_open": 81,
    "research_view_open": 80,
    "role_memory_open": 79,
    "role_memory_search": 78,
    "market_change_brief_open": 75,
    "market_dimension_open": 76,
}
_NOTEBOOK_MAX_RESULT_CHARS = 12_000
# The notebook replaces the entire raw conversation, so a 40k-character cap
# was unnecessarily aggressive: eight dual-window historical results could
# crowd out every current market record and leave final synthesis unable to
# compare calibration with today's price/flow counterevidence. 100k characters
# remains comfortably below the model context budget while preserving a
# balanced evidence set for long, cross-market research runs.
_NOTEBOOK_MAX_TOTAL_RESULT_CHARS = 100_000


def _compacted_research_notebook_input(
    *,
    original_input: list,
    invocations: list,
    working_memory: dict | None = None,
    working_memory_revision: int = 0,
) -> list:
    """Replace the long tool transcript with one exact, bounded research notebook.

    The final synthesis does not need every assistant preamble or the MCP call/result
    envelope. It does need the actual facts and references it opened. We therefore
    project completed tool results from trusted runtime state, retain the most
    decision-relevant results in full, and leave an explicit index for omitted
    navigation-only payloads. No model summary is used, so compaction cannot invent
    facts or silently alter values.
    """

    candidates: list[tuple[int, int, dict]] = []
    for order, invocation in enumerate(invocations):
        if (
            invocation.name.startswith("submit_")
            or invocation.name == "agent_evidence_ledger_open"
            or invocation.result is None
        ):
            continue
        result = _project_notebook_result(
            invocation.name,
            _decode_notebook_result(invocation.result),
        )
        serialized = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        if len(serialized) > _NOTEBOOK_MAX_RESULT_CHARS:
            result = {
                "truncated": True,
                "original_chars": len(serialized),
                "reason": "单次结果过大，最终综合上下文不保留其内容；不得引用",
            }
            serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        entry = {
            "order": order + 1,
            "evidence_ref": f"run_evidence:E{order + 1}",
            "tool": invocation.name,
            "arguments": invocation.arguments,
            "result": result,
        }
        candidates.append(
            (_NOTEBOOK_RESULT_PRIORITY.get(invocation.name, 50), order, entry)
        )

    selected_orders: set[int] = set()
    selected_size = 0
    for _, order, entry in sorted(candidates, key=lambda item: (-item[0], -item[1])):
        size = len(json.dumps(entry, ensure_ascii=False, default=str))
        if selected_size + size > _NOTEBOOK_MAX_TOTAL_RESULT_CHARS:
            continue
        selected_orders.add(order)
        selected_size += size

    evidence = [
        entry
        for _, order, entry in sorted(candidates, key=lambda item: item[1])
        if order in selected_orders
    ]
    omitted = [
        {
            "order": entry["order"],
            "evidence_ref": entry["evidence_ref"],
            "tool": entry["tool"],
            "arguments": entry["arguments"],
            "reason": "结果为低优先级导航信息，已从最终综合上下文移除；不得引用其内容",
        }
        for _, order, entry in sorted(candidates, key=lambda item: item[1])
        if order not in selected_orders
    ]
    working_notes = _recent_research_working_notes(original_input)
    completed_operations = [
        {
            "order": entry["order"],
            "evidence_ref": entry["evidence_ref"],
            "tool": entry["tool"],
            "arguments": entry["arguments"],
            "result_retained": order in selected_orders,
        }
        for _, order, entry in sorted(candidates, key=lambda item: item[1])
    ]
    notebook = {
        "research_notebook": {
            "compaction_policy": (
                "由 Runtime 从本次真实工具结果确定性生成；保留值不得改写，"
                "omitted_results 只能证明调用发生，不能作为事实证据。"
                "recent_working_notes 是模型压缩前的阶段计划和判断，不是事实证据；"
                "它用于延续研究意图，必须用 retained_results 中的证据复核。"
            ),
            "working_memory": {
                "revision": working_memory_revision,
                "state": working_memory,
                "authority": (
                    "本次运行的计划与判断，不是事实证据；继续研究时以它为当前状态，"
                    "不得把已回答问题当作待办，也不得恢复 discarded_paths。"
                ),
            },
            "recent_working_notes": working_notes,
            "completed_operations": completed_operations,
            "retained_results": evidence,
            "omitted_results": omitted,
        }
    }
    initial = original_input[:1]
    return [
        *initial,
        {
            "role": "user",
            "content": json.dumps(
                notebook,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
        },
    ]


def _recent_research_working_notes(original_input: list) -> list[dict[str, str]]:
    """Preserve the Agent's recent plan across deterministic compaction.

    Tool evidence alone cannot reconstruct why a tool was called or which
    competing hypothesis the Agent had already rejected.  Keeping a small tail
    of assistant-authored planning messages supplies run-local continuity while
    explicitly preventing those notes from becoming evidence.
    """

    notes: list[dict[str, str]] = []
    remaining = 8_000
    for item in reversed(original_input):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        text = item.get("text") or item.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        if re.search(r"(.)\1{199,}", text, flags=re.DOTALL):
            # Provider glitches occasionally emit huge repeated-token messages;
            # they carry no recoverable plan and must not poison compaction.
            continue
        excerpt = text.strip()[-min(len(text.strip()), remaining, 4_000) :]
        notes.append({"type": "non_evidentiary_working_note", "text": excerpt})
        remaining -= len(excerpt)
        if len(notes) >= 3 or remaining <= 0:
            break
    notes.reverse()
    return notes


def _decode_notebook_result(value):
    if isinstance(value, dict):
        structured = value.get("structuredContent")
        if isinstance(structured, dict):
            return _decode_notebook_result(structured)
        if value.get("type") == "text" and "text" in value:
            return _decode_notebook_result(value["text"])
        content = value.get("content")
        if isinstance(content, list):
            for item in content:
                decoded = _decode_notebook_result(item)
                if isinstance(decoded, dict) and decoded:
                    return decoded
        return value
    if isinstance(value, (list, tuple)):
        for item in value:
            decoded = _decode_notebook_result(item)
            if isinstance(decoded, dict) and decoded:
                return decoded
        return value
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped.startswith(("{", "[", '"')):
        return value
    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError:
        return value
    return _decode_notebook_result(decoded)


def _project_notebook_result(tool_name: str, value):
    """Keep source substance while dropping page chrome from final synthesis."""

    # Compaction must preserve the same canonical semantic view that the model
    # saw when the tool returned.  Re-projecting the raw audited invocation with
    # a second, older schema reintroduced duplicate generic ``statistics`` and
    # raw temporal-holdout trees, which made development/holdout and
    # absolute/relative figures easy to mix up after compaction.
    if tool_name == "market_historical_analogue_open":
        if not isinstance(value, dict):
            return value
        # Aggregate statistics and their calculation locator are the
        # decision-bearing part. Raw sample rows and per-row locators would
        # crowd competing candidates out of the synthesis notebook.
        projected = {
            key: value.get(key)
            for key in (
                "data_type",
                "benchmark_subject_id",
                "subject_id",
                "signal_definition",
                "forward_window_bars",
                "sample_count",
                "minimum_sample_count",
                "calibration_status",
                "full_sample_distribution",
                "calibration_readout",
                "distribution_stability_readout",
                "robustness",
                "analysis_evidence_locator",
            )
            if value.get(key) is not None
        }
        return projected
    if tool_name not in {"external_web_read", "external_content_read"}:
        return value
    if not isinstance(value, dict):
        return value
    projected = {
        key: value.get(key)
        for key in ("provider", "title", "url", "content_handle")
        if value.get(key) is not None
    }
    body = value.get("preview") or value.get("content") or value.get("text")
    if isinstance(body, str):
        projected["source_excerpt"] = body[:4_500]
        projected["source_excerpt_truncated"] = len(body) > 4_500
    return projected


def _tool_result_payload(result) -> dict:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    raise RuntimeError("MCP tool returned no structured JSON object")


def _expand_evidence_aliases(value, aliases: dict[str, str]):
    """Restore canonical evidence identities before server-side commit."""

    if isinstance(value, str):
        return aliases.get(value, value)
    if isinstance(value, list):
        return [_expand_evidence_aliases(item, aliases) for item in value]
    if isinstance(value, dict):
        return {
            key: _expand_evidence_aliases(item, aliases)
            for key, item in value.items()
        }
    return value


def _raise_on_tool_error(result, tool_name: str) -> None:
    """Turn an MCP error result into a failed orchestration boundary.

    The MCP protocol represents business validation failures as successful
    HTTP responses whose ``isError`` flag is true.  Ignoring that flag would
    let the local runtime report success even when the server rejected the
    prepared run or proposal commit.
    """

    if not bool(getattr(result, "isError", False)):
        return
    messages = [
        str(getattr(item, "text", "")).strip()
        for item in (getattr(result, "content", None) or [])
        if str(getattr(item, "text", "")).strip()
    ]
    detail = "; ".join(messages) or "unknown MCP error"
    raise RuntimeError(f"{tool_name} failed: {detail}")
