"""Independent semantic evaluator for completed Research runs."""

from __future__ import annotations

import json
import logging
from typing import Literal

from agents import Agent, FunctionTool, ModelSettings, ToolsToFinalOutputResult
from pydantic import Field

from src.application.agents.financial_research.schemas import ResearchContract


SEMANTIC_EVALUATOR_VERSION = "research-semantic-v1"
SEMANTIC_PROMPT_VERSION = "research-semantic-prompt-v1"
logger = logging.getLogger(__name__)


class ClaimCitationAssessment(ResearchContract):
    claim_id: str = Field(min_length=1, max_length=180)
    reference: str = Field(min_length=1, max_length=512)
    verdict: Literal[
        "fully_supports",
        "partially_supports",
        "context_only",
        "contradicts",
        "unrelated",
    ]
    unsupported_part: str | None = Field(default=None, max_length=800)
    rationale: str = Field(min_length=1, max_length=1000)


class SemanticResearchScores(ResearchContract):
    evidence_entailment: float = Field(ge=0, le=10)
    counterevidence_directness: float = Field(ge=0, le=10)
    forecast_calibration: float = Field(ge=0, le=10)
    source_independence: float = Field(ge=0, le=10)
    narrative_selection_bias: float = Field(ge=0, le=10)
    mechanism_completeness: float = Field(ge=0, le=10)
    decision_value: float = Field(ge=0, le=10)


class SemanticResearchEvaluationDraft(ResearchContract):
    scores: SemanticResearchScores
    claim_citation_assessments: list[ClaimCitationAssessment] = Field(
        default_factory=list, max_length=80
    )
    evidence_lineage_groups: list[str] = Field(default_factory=list, max_length=30)
    strengths: list[str] = Field(default_factory=list, max_length=12)
    defects: list[str] = Field(default_factory=list, max_length=20)
    recommended_research_actions: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)


class SemanticResearchEvaluation(SemanticResearchEvaluationDraft):
    run_id: str = Field(min_length=1, max_length=180)
    evaluator_version: Literal["research-semantic-v1"] = SEMANTIC_EVALUATOR_VERSION
    prompt_version: Literal["research-semantic-prompt-v1"] = SEMANTIC_PROMPT_VERSION
    model: str = Field(min_length=1, max_length=180)


async def _submit_semantic_evaluation(_wrapper, raw_arguments: str) -> str:
    try:
        return SemanticResearchEvaluationDraft.model_validate(
            _decode_nested_json_strings(json.loads(raw_arguments))
        ).model_dump_json()
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        logger.warning("semantic_evaluation_validation_error: %s", error)
        return f"语义评测结构校验失败，请修正全部字段后重试：{error}"


def _decode_nested_json_strings(value):
    """Normalize providers that stringify nested structured-output fields."""

    if isinstance(value, list):
        return [_decode_nested_json_strings(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _decode_nested_json_strings(item)
            for key, item in value.items()
        }
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return _decode_nested_json_strings(json.loads(stripped))
            except json.JSONDecodeError:
                return value
    return value


submit_semantic_evaluation = FunctionTool(
    name="submit_semantic_evaluation",
    description="提交对一个已经结束的 Research 报告的独立语义质量评价。",
    params_json_schema=SemanticResearchEvaluationDraft.model_json_schema(),
    on_invoke_tool=_submit_semantic_evaluation,
    strict_json_schema=True,
)


def create_semantic_evaluator_agent(*, model: str) -> Agent:
    return Agent(
        name="Research Quality Evaluator｜研究质量评测智能体",
        instructions=_INSTRUCTIONS,
        model=model,
        model_settings=ModelSettings(
            parallel_tool_calls=False,
            include_usage=True,
            tool_choice="required",
        ),
        tools=[submit_semantic_evaluation],
        tool_use_behavior=_evaluation_is_final,
        reset_tool_choice=False,
    )


def _evaluation_is_final(_context, tool_results: list) -> ToolsToFinalOutputResult:
    for result in tool_results:
        if result.tool.name != "submit_semantic_evaluation":
            continue
        try:
            SemanticResearchEvaluationDraft.model_validate_json(result.output)
        except (ValueError, TypeError):
            continue
        return ToolsToFinalOutputResult(is_final_output=True, final_output=result.output)
    return ToolsToFinalOutputResult(is_final_output=False)


_INSTRUCTIONS = """
你是独立的金融研究质量评测智能体。你评价已经完成的报告，不生成投资观点，也不替研究智能体改稿。

逐条检查 Claim（主张）和 Citation（引用）的语义蕴含。引用真实不代表它能支持整句话；无法支持的部分必须写入 unsupported_part。识别同一底层来源的转载、同一研报的不同 Card、同一行情表的多个字段，不得把它们机械算成独立来源。

七个分项均为0至10分：证据蕴含、直接反证、预测校准、来源独立性、叙事选择偏差控制、机制完整性、决策价值。预测仅有当前行情外推最高3分；普通多窗口历史最高5分；历史相似场景最高8分；同时具备条件样本、基准、分布和样本外验证才可高于8分。只有背景风险不能获得直接反证高分。只挑有利窗口、把局部高点写成长周期高点、用长期上涨推出短期上涨，都应降低叙事选择偏差控制分。

输入中的 evidence_records 是实际工具结果的有界摘要。没有提供内容的引用不能假定得到支持。评测结果不会反馈给本次 Research Agent，因此应客观记录缺陷与后续研究动作，不要为了让报告通过而宽松打分。最后必须调用 submit_semantic_evaluation。
""".strip()
