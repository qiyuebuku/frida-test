"""Independent semantic evaluator for completed Research runs."""

from __future__ import annotations

import json
import logging
from typing import Literal

from agents import Agent, FunctionTool, ToolsToFinalOutputResult
from pydantic import Field

from src.application.agents.financial_research.schemas import ResearchContract
from src.application.agents.financial_research.model_settings import (
    research_model_settings,
)


SEMANTIC_EVALUATOR_VERSION = "research-semantic-v2"
SEMANTIC_PROMPT_VERSION = "research-semantic-prompt-v2"
logger = logging.getLogger(__name__)


class ClaimCitationAssessment(ResearchContract):
    claim_id: str = Field(min_length=1, max_length=180)
    reference: str = Field(
        min_length=1,
        max_length=512,
        description=(
            "只填写报告 Citation 的短 citation_id；Runtime 会映射为真实证据定位符"
        ),
    )
    verdict: Literal[
        "fully_supports",
        "partially_supports",
        "context_only",
        "contradicts",
        "unrelated",
    ]
    unsupported_part: str | None = Field(default=None, max_length=300)
    rationale: str = Field(min_length=1, max_length=300)


class SemanticResearchScores(ResearchContract):
    exploration_depth: float = Field(ge=0, le=10)
    evidence_entailment: float = Field(ge=0, le=10)
    clarity_and_structure: float = Field(ge=0, le=10)
    counterevidence_directness: float = Field(ge=0, le=10)
    forecast_calibration: float = Field(ge=0, le=10)
    source_independence: float = Field(ge=0, le=10)
    market_structure_and_pricing: float = Field(ge=0, le=10)
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
    non_scoring_limitations: list[str] = Field(default_factory=list, max_length=20)
    recommended_research_actions: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)


class SemanticResearchEvaluation(SemanticResearchEvaluationDraft):
    run_id: str = Field(min_length=1, max_length=180)
    evaluator_version: Literal["research-semantic-v2"] = SEMANTIC_EVALUATOR_VERSION
    prompt_version: Literal["research-semantic-prompt-v2"] = SEMANTIC_PROMPT_VERSION
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
        model_settings=research_model_settings(
            model=model,
            # The evaluator receives a completed, bounded audit package and
            # applies a fixed rubric. GLM-5.3 max spent several minutes and
            # tens of thousands of reasoning tokens re-deriving the report.
            # Low is sufficient for citation-by-citation classification; the
            # deterministic evaluator still enforces structural invariants.
            reasoning_effort="low",
            parallel_tool_calls=False,
            tool_choice="required",
            max_tokens=18_000,
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

这是确定性的结构化评测任务。不要展开分析过程，不要生成冗长 reasoning；直接根据输入完成逐项判断并
输出符合 Schema 的 JSON。只有无法直接确定某条主张与证据关系时才使用最少量内部推理。

逐条检查 Claim（主张）的语义蕴含。引用真实不代表它能支持整句话；无法支持的部分必须写入 unsupported_part。每个 Claim 只输出一条 assessment：reference 选择对该 Claim 最关键的一条短 citation_id；若多个引用共同支持 inference，在 rationale 中简要说明其余前提是否齐全，不要为同一 Claim 的每个 Citation 重复输出 assessment。禁止复制 market:v1、Card ID 或其他长定位符；Runtime 会自动恢复正式 reference。rationale 最多120个汉字，只写支持或不支持的关键差异，不复述整条证据。识别同一底层来源的转载、同一研报的不同 Card、同一行情表的多个字段，不得把它们机械算成独立来源。来源独立性主要评价新闻、产业机制和因果主张；对交易所行情、板块成分和资金等客观测量，不要求为了凑数再找第二家行情供应商，但不得把同一张行情表拆成多个独立印证。
如果报告不依赖外部产业或事件叙事形成中心结论，或把单一来源严格限定为可删除的背景且未据此推导
因果，不得仅因“没有第二篇新闻”降低来源独立性；此时应评价实际使用的证据血缘是否被如实披露。
若中心结论只依赖客观行情测量和透明的确定性统计，报告如实说明这些数据来自同一行情血缘、没有把
派生统计伪装成独立来源，且没有未验证的因果叙事，来源独立性可以达到8分；不要因为客观行情没有
人为寻找第二家供应商而机械限制在7分。
对一篇同时包含正反观点的来源，要检查报告是否对称呈现，而不是只摘取有利一侧。

九个固定分项均为0至10分，后续版本不得改名、合并或删除：探索深度、证据蕴含、清晰度与结构、
预测校准、组合决策价值、直接反证、市场结构与定价、机制完整性、来源独立性。探索深度不仅计算工具
数量，还要评价是否从全市场导航到候选比较、对象深读、历史校准和精确证据，并避免无目的重复调用。
清晰度评价结论、依据、反证、边界能否被不同职责直接消费，不因报告诚实披露必要限制而扣分。
预测仅有当前行情外推最高3分；普通多窗口历史最高5分；历史相似场景最高8分；同时具备条件样本、
基准、分布和已经到期的样本外验证才可高于8分。若预测验证窗口尚未结束，样本外结果属于未来待评估
事项，不得把“当前不可能取得结果”列为本次报告缺陷或额外扣分；此时按条件样本、基准、分布和稳健性
本身评分，最高8分。只有背景风险不能获得直接反证高分。只挑有利窗口、把局部高点写成长周期高点、
用长期上涨推出短期上涨，都应降低市场结构与定价分。
已失效旧观点中保留的 Forecast 是历史审计记录，不是本轮新预测；除非报告继续依赖它作前瞻判断，
不得用它拉低本轮预测校准分。一个高质量的“当前无可用方向”观点，如果明确说明候选淘汰链、共同市场
约束、何种证据会解除限制、组合当前不应做什么和何时重新研究，机制完整性与决策价值都可以达到8分
以上；不得仅因它没有给出买入方向而扣分。相反，没有证据的“市场差所以压制所有板块”仍应扣分。
若报告把结论严格限定为“当前单日横截面焦点”，明确拒绝声称跨日持续或因果传导，并把多日验证列为
观察条件，则缺少跨日资金序列不是该当前结论的机制断裂，不得重复扣分。此时若已区分已观察共现、
未验证传导、定价位置和后续确认信号，机制完整性可评9分。若同时给出不做清单、解除限制条件、验证
期限和表达层边界，决策价值可评9分。主对象存在至少两条对象对齐直接反证，覆盖当前弱项与历史不稳定，
且最不利尾部进入决策边界时，直接反证可评9分以上。
当历史稳健性或当前确认不足时，拒绝生成方向 Forecast 本身就是正确的校准结果。若报告完成多候选双窗口
检验、使用正确基准口径、披露分布和尾部，并给出可验证的重新研究条件，预测校准可以达到8分；不得因
它诚实地没有方向预测而自动限制在7分以下。

输入中的 evidence_records 是实际工具结果的有界摘要。没有提供内容的引用不能假定得到支持。market_historical_analogue_open 等确定性分析工具，是对报告所引用行情样本的程序计算；当审计包包含该次工具结果，且 Claim 引用的样本确实属于同一结果时，聚合统计应视为由确定性计算直接支持，不要求虚构一条聚合数据库记录。但匹配宽松、样本过少、挑选有利结果、忽略严格子集或稳健性检查仍必须扣分。
Citation（引用）的 `observed_at` 是供应商记录被采集或观察的时刻，`as_of` 才是该市场事实所属的交易日
或有效时点。周末采集周五收盘记录时 observed_at 晚于 as_of 是正常现象；只要正文日期与 as_of / 原始
trade_date 一致，不得把采集时间不同当作时间纪律缺陷。
输入中的 deterministic_checks 是程序按正式 Forecast 与同对象、同窗口历史结果计算的审计结论。对于
预测区间是否绑定错窗口等机械事实，以该字段为准；不得因多个窗口数字相邻出现而自行推断串绑。

评测结果不会反馈给本次 Research Agent，因此应客观记录缺陷与后续研究动作，不要为了让报告通过而宽松打分。
`defects` 只能填写实际降低某个分项分数的问题；未进入中心结论、已被正确降级且不影响当前决策边界的
数据限制必须放入 `non_scoring_limitations`，不得重复扣分。具体而言：未输出具体 ETF/基金代码时，
代表 ETF 导航候选不稳定不是本轮报告缺陷；非核心替代对象某一期限样本不足，若已披露并未据此推导
方向，不是中心校准缺陷；成交量原始单位未确认，但报告只使用同对象同字段的无量纲比值或完全未使用时，
不是证据或机制缺陷。只有模型越过这些边界作出表达、方向或因果结论时才记入 defects 并扣对应分数。
不要在隐藏推理中重写报告或逐字复述全部证据。完成必要判断后立即调用 submit_semantic_evaluation；
工具参数的第一个顶层字段必须是完整的 scores，先填写九项分数，再填写每个 Claim 一条的精简 assessment。
每条 rationale 只写一个关键差异，strengths、defects 和 recommended_research_actions 只保留会影响评分的项目。
""".strip()
