"""Reusable OpenAI Agents SDK runtime for one bounded research review."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from types import TracebackType
from uuid import uuid4

from agents import RunConfig, RunContextWrapper, Runner
from agents.models.openai_provider import OpenAIProvider
from agents.run_config import CallModelData, ModelInputData
from langfuse import propagate_attributes
from openai import AsyncOpenAI

from src.application.agents.financial_research.agent import (
    create_financial_research_agent,
)
from src.application.agents.financial_research.audit import validate_research_result
from src.application.agents.financial_research.context import AgentRunContext
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
from src.infrastructure.agent_runtime.mcp import create_mcp_server
from src.infrastructure.agent_runtime.mcp import RESEARCH_READ_TOOLS
from src.infrastructure.agent_runtime.observability import (
    AgentAuditHooks,
    configure_observability,
)
from src.infrastructure.agent_runtime.run_authorization import (
    issue_run_authorization,
)


logger = logging.getLogger(__name__)


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
            timeout=self.settings.llm_timeout,
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
            call_model_input_filter=_apply_research_budget_guard,
        )
        hooks = AgentAuditHooks(
            langfuse_client=self._langfuse,
            include_sensitive_data=self.settings.trace_sensitive_data,
            model=self.settings.model,
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
            ttl_seconds=context_pack.trigger.max_elapsed_seconds + 120,
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
        await run_mcp_server.connect()
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
            await run_mcp_server.cleanup()

        output = result.final_output
        if isinstance(output, str):
            output = CurrentResearchReportProposal.model_validate_json(output)
        if not isinstance(output, CurrentResearchReportProposal):
            raise TypeError(
                "Research Agent returned an unexpected output type: "
                f"{type(output).__name__}"
            )
        evidence_tool_calls = sum(
            item.name
            not in {
                "submit_research_conclusion",
                "submit_investment_view_revision",
            }
            for item in context.tool_invocations
        )
        if evidence_tool_calls > context_pack.trigger.max_tool_calls:
            raise ValueError(
                "Research Agent exceeded max_tool_calls: "
                f"{evidence_tool_calls} > "
                f"{context_pack.trigger.max_tool_calls}"
            )
        validate_research_result(output, context)
        output = CurrentResearchReportProposal.model_validate(
            _expand_evidence_aliases(
                output.model_dump(mode="python"),
                context.evidence_aliases,
            )
        )

        commit_server = create_mcp_server(
            self.settings,
            run_authorization=run_authorization,
        )
        await commit_server.connect()
        try:
            commit_result = await commit_server.call_tool(
                "research_proposal_commit",
                {
                    "proposal_payload": output.model_dump(mode="json"),
                    "publish": publish,
                },
            )
            _raise_on_tool_error(commit_result, "research_proposal_commit")
        finally:
            await commit_server.cleanup()

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
                evaluation = SemanticResearchEvaluation(
                    **draft.model_dump(mode="python"),
                    run_id=proposal.run_id,
                    model=self.settings.model,
                )
                if current_observation is not None:
                    current_observation.update(output=evaluation.model_dump(mode="json"))
        commit_server = create_mcp_server(
            self.settings,
            run_authorization=issue_run_authorization(
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
            ),
        )
        await commit_server.connect()
        try:
            commit_result = await commit_server.call_tool(
                "research_semantic_evaluation_commit",
                {"evaluation_payload": evaluation.model_dump(mode="json")},
            )
            _raise_on_tool_error(
                commit_result, "research_semantic_evaluation_commit"
            )
        finally:
            await commit_server.cleanup()
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
            ttl_seconds=trigger.max_elapsed_seconds + 180,
        )
        server = create_mcp_server(
            self.settings,
            run_authorization=authorization,
        )
        await server.connect()
        try:
            result = await server.call_tool(
                "research_run_prepare",
                {
                    "trigger_payload": trigger.model_dump(mode="json"),
                    "research_question": research_question or "",
                },
            )
        finally:
            await server.cleanup()
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
            abort_server = create_mcp_server(
                self.settings,
                run_authorization=authorization,
            )
            try:
                await abort_server.connect()
                await abort_server.call_tool(
                    "research_run_abort",
                    {
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            except Exception:
                logger.exception("failed to close Research run audit state")
            finally:
                await abort_server.cleanup()
            raise


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
    cited_references = {
        citation.reference
        for claim in claims
        for citation in claim.evidence
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
        matching = [
            reference
            for reference in cited_references
            if reference in serialized
        ]
        if not matching and invocation.name not in {
            "market_historical_analogue_open",
            "market_technical_state_open",
        }:
            continue
        evidence_records.append({
            "tool": invocation.name,
            "references": matching[:20],
            "result_excerpt": serialized[:6000],
            "truncated": len(serialized) > 6000,
        })
    return {
        "research_run_id": proposal.run_id,
        "report": {
            "status": proposal.status.value,
            "summary": proposal.report_summary,
            "counterevidence_summary": proposal.counterevidence_summary,
            "claims": [claim.model_dump(mode="json") for claim in claims],
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
                        item.model_dump(mode="json")
                        for item in revision.mechanism_chain
                    ],
                    "market_structure": (
                        revision.market_structure.model_dump(mode="json")
                        if revision.market_structure is not None
                        else None
                    ),
                    "forecasts": [
                        item.model_dump(mode="json")
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
        "evidence_records": evidence_records[:40],
    }


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
    max_tool_calls = (
        context.research_context.trigger.max_tool_calls
        if context.research_context is not None
        else 40
    )
    remaining = max(max_tool_calls - evidence_calls, 0)
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
            "运行预算提醒：你已经完成了市场概览、候选比较、精确证据与"
            "历史或来源验证，当前已有形成决定所需的多层证据。"
            f"已完成 {evidence_calls} 次证据工具调用，最多还可调用 {remaining} 次。"
            "不要开启新主题、Community（社区）或无明确问题的搜索。"
            f"{memory_instruction}"
            f"{unresolved_source_instruction}"
            "先判断是否仍存在一个会改变结论的具体阻塞缺口：如果存在，只补"
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
    model_input = data.model_data.input
    if "agent_evidence_ledger_open" in completed_names:
        model_input = _compacted_research_notebook_input(
            original_input=data.model_data.input,
            invocations=completed,
        )
    return ModelInputData(
        input=[*model_input, reminder],
        instructions=data.model_data.instructions,
    )


_NOTEBOOK_RESULT_PRIORITY = {
    "agent_evidence_ledger_open": 100,
    "market_historical_analogue_open": 95,
    "market_technical_state_open": 94,
    "market_expression_compare_open": 93,
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
}
_NOTEBOOK_MAX_RESULT_CHARS = 8_000
_NOTEBOOK_MAX_TOTAL_RESULT_CHARS = 65_000


def _compacted_research_notebook_input(
    *,
    original_input: list,
    invocations: list,
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
        if invocation.name.startswith("submit_") or invocation.result is None:
            continue
        result = _decode_notebook_result(invocation.result)
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
            "tool": entry["tool"],
            "arguments": entry["arguments"],
            "reason": "结果为低优先级导航信息，已从最终综合上下文移除；不得引用其内容",
        }
        for _, order, entry in sorted(candidates, key=lambda item: item[1])
        if order not in selected_orders
    ]
    notebook = {
        "research_notebook": {
            "compaction_policy": (
                "由 Runtime 从本次真实工具结果确定性生成；保留值不得改写，"
                "omitted_results 只能证明调用发生，不能作为事实证据"
            ),
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


def _decode_notebook_result(value):
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
