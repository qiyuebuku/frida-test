"""LLM-assisted, reversible context compaction for long Research runs."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Literal

from agents import Agent, FunctionTool, ModelSettings, ToolsToFinalOutputResult
from pydantic import Field

from src.application.agents.financial_research.schemas import ResearchContract


logger = logging.getLogger(__name__)

CompactText = Annotated[str, Field(min_length=1, max_length=500)]


class CompressedEvidenceItem(ResearchContract):
    finding: str = Field(min_length=1, max_length=600)
    evidence_refs: list[str] = Field(min_length=1, max_length=12)
    role: Literal["supports", "contradicts", "context", "data_quality"]
    caveat: str | None = Field(default=None, max_length=400)


class ResearchContextSummary(ResearchContract):
    phase: Literal["exploration", "verification", "final_synthesis"]
    research_goal: str = Field(min_length=1, max_length=600)
    completed_work: list[CompactText] = Field(min_length=1, max_length=20)
    immediate_next_action: str = Field(min_length=1, max_length=600)
    hot_evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    current_assessment: str = Field(min_length=1, max_length=1200)
    candidate_hypotheses: list[CompactText] = Field(default_factory=list, max_length=10)
    rejected_or_weakened_hypotheses: list[CompactText] = Field(
        default_factory=list,
        max_length=12,
    )
    key_evidence: list[CompressedEvidenceItem] = Field(
        default_factory=list,
        max_length=40,
    )
    strongest_counterevidence: list[CompressedEvidenceItem] = Field(
        default_factory=list,
        max_length=16,
    )
    unresolved_questions: list[CompactText] = Field(default_factory=list, max_length=12)
    discarded_paths: list[CompactText] = Field(default_factory=list, max_length=12)
    next_steps: list[CompactText] = Field(default_factory=list, max_length=10)


async def _submit_context_summary(_wrapper, raw_arguments: str) -> str:
    try:
        summary = ResearchContextSummary.model_validate_json(raw_arguments)
        serialized = summary.model_dump_json()
        if len(serialized) > 5_000:
            raise ValueError(
                f"摘要共 {len(serialized)} 字符，超过 5000 字符上限；"
                "删除过程复述，只保留继续工作必需的信息"
            )
        return serialized
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        logger.warning("research_context_summary_validation_error: %s", error)
        return f"研究上下文摘要校验失败，请修正全部字段后重试：{error}"


submit_context_summary = FunctionTool(
    name="submit_context_summary",
    description="提交可恢复的 Research 研究主线摘要；事实必须关联已有 run_evidence 引用。",
    params_json_schema=ResearchContextSummary.model_json_schema(),
    on_invoke_tool=_submit_context_summary,
    strict_json_schema=True,
)


def create_context_compactor_agent(*, model: str) -> Agent:
    return Agent(
        name="Research Context Compactor｜研究上下文压缩智能体",
        instructions=_INSTRUCTIONS,
        model=model,
        model_settings=ModelSettings(
            parallel_tool_calls=False,
            include_usage=True,
            tool_choice="required",
        ),
        tools=[submit_context_summary],
        tool_use_behavior=_summary_is_final,
        reset_tool_choice=False,
    )


def _summary_is_final(_context, tool_results: list) -> ToolsToFinalOutputResult:
    for result in tool_results:
        if result.tool.name != "submit_context_summary":
            continue
        text = str(result.output)
        if text.startswith("研究上下文摘要校验失败"):
            continue
        try:
            ResearchContextSummary.model_validate_json(text)
        except (ValueError, TypeError):
            continue
        return ToolsToFinalOutputResult(is_final_output=True, final_output=text)
    return ToolsToFinalOutputResult(is_final_output=False, final_output=None)


_INSTRUCTIONS = """
你是 Research Agent（研究智能体）的上下文压缩器。你的任务不是重新研究、形成新观点，
而是把输入中的 Research Notebook（研究笔记）压缩成一份能让主智能体无缝继续工作的
研究主线摘要。

必须遵守：
1. 只使用输入中已经存在的内容，不补充常识、背景或新结论。
2. 每条事实性 key_evidence 或 strongest_counterevidence 都必须关联真实存在的
   run_evidence:E*；禁止编造引用编号。
3. 数字、日期、对象和因果关系只在确有必要时写入。摘要不是正式证据；除继续原样保留
   的 hot_evidence_refs 外，主智能体在最终引用前必须通过 run_evidence_reopen 恢复原始结果。
   但如果下一步紧接着就需要逐字核验某条 retained_results（已保留结果），必须把对应
   run_evidence:E* 放入 hot_evidence_refs；Runtime 会把该原文继续留在活动上下文，
   避免“刚压缩就重新打开”。只选立即下一步真正需要的最小集合。
4. 保留研究目标、当前主判断、竞争假设、最强反证、已排除路径、未解决问题和下一步。
   如果输入中已经打开 Evidence Ledger（证据账本），phase 必须是 final_synthesis，
   completed_work 要明确列出已经完成的入口、比较、历史验证和来源验证，
   immediate_next_action 只能是恢复最终采用证据、补一个明确缺口或提交，不能重新开始研究。
5. 不要把 recent_working_notes 中的模型判断冒充事实。工具结果与工作记忆冲突时，明确
   标注冲突，不替主智能体裁决。
6. 删除寒暄、重复过程、工具协议字段和对最终判断没有帮助的导航信息。
7. 必须调用 submit_context_summary；不要输出自然语言答案。
8. 整份摘要必须少于 5000 字符。不要把工具结果逐条复述进摘要；数字和原文应交给
   hot_evidence_refs 或可恢复引用保存，摘要只保存判断结构和继续工作的最小信息。
""".strip()
