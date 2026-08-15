"""Plain-text semantic checkpoints for Research context surface replacement."""

from __future__ import annotations

import re

from agents import Agent

from src.application.agents.financial_research.model_settings import (
    research_model_settings,
)


_REQUIRED_SECTIONS = (
    "研究目标与用户意图",
    "当前判断",
    "候选假设",
    "已削弱或排除的解释",
    "已完成验证",
    "关键证据索引",
    "最强反证索引",
    "热证据原文保留",
    "未解决问题",
    "当前工作",
    "下一步",
    "关键限制",
)


def create_context_compactor_agent(*, model: str) -> Agent:
    """Create a one-shot, tool-free semantic checkpoint writer."""

    return Agent(
        name="Research Context Compactor｜研究上下文压缩智能体",
        instructions=_INSTRUCTIONS,
        model=model,
        model_settings=research_model_settings(
            model=model,
            reasoning_effort="low",
            parallel_tool_calls=False,
            max_tokens=8_000,
        ),
        tools=[],
    )


def validate_context_checkpoint(value: object) -> str:
    """Validate a terse Markdown checkpoint returned by the compactor."""

    if not isinstance(value, str):
        raise TypeError("context compactor must return Markdown text")
    text = value.strip()
    if len(text) > 4_500:
        raise ValueError(f"context checkpoint exceeds 4500 characters: {len(text)}")
    if re.search(r"run_evidence:E\d+\s*[-—~至]\s*(?:E)?\d+", text):
        raise ValueError("context checkpoint must list recovery references individually")
    hot_references = checkpoint_hot_evidence_refs(text)
    if len(hot_references) > 6:
        raise ValueError("context checkpoint may retain at most 6 hot evidence references")
    missing = [
        section
        for section in _REQUIRED_SECTIONS
        if re.search(rf"(?m)^##\s+{re.escape(section)}\s*$", text) is None
    ]
    if missing:
        raise ValueError("context checkpoint missing sections: " + ", ".join(missing))
    return text


def checkpoint_hot_evidence_refs(checkpoint: str) -> list[str]:
    """Return immediate-next-step evidence selected for exact retention."""

    match = re.search(
        r"(?ms)^## 热证据原文保留\s*$\n(.*?)(?=^## |\Z)",
        checkpoint,
    )
    if match is None:
        return []
    return list(dict.fromkeys(re.findall(r"run_evidence:E\d+", match.group(1))))


def frame_context_checkpoint(checkpoint: str, *, generation: int) -> str:
    """Frame a model-visible replacement checkpoint as established context."""

    return (
        "以下是 Runtime 生成的研究上下文检查点，替代更早的一段历史。"
        "把它视为已经完成的背景并直接继续，不要复述检查点，也不要重新执行其中"
        "已经完成的工作。检查点可用于继续分析和起草 Proposal；被折叠的完整"
        "原始工具结果仍由 Runtime 保存，并会在提交时对 Citation 和 Claim 做"
        "确定性校验。不要仅为复制检查点中已经明确记录的数值或定位符而恢复"
        "原文；只有检查点标明不确定、字段被截断，或提交校验指出具体缺口时，"
        "才调用 run_evidence_reopen 定向恢复。"
        "“热证据原文保留”所列结果已紧随检查点保留，不要再次打开。\n\n"
        f"<research-context-checkpoint generation=\"{generation}\">\n"
        f"{checkpoint}\n"
        "</research-context-checkpoint>"
    )


_INSTRUCTIONS = """
你是金融 Research Agent（研究智能体）的上下文压缩器。输入包含一段即将退出模型
活动上下文的真实历史，以及其中工具结果对应的可恢复 run_evidence:E* 索引。

你的任务是生成一个极简 Markdown 检查点，让主智能体在保留的近期原文之后无缝继续。
你不是研究员，不得形成新观点、补充常识、修改事实或替主智能体完成下一步。

必须严格输出以下章节，章节名和顺序不得改变；每节使用短项目符号，没有内容写“无”：

## 研究目标与用户意图
## 当前判断
## 候选假设
## 已削弱或排除的解释
## 已完成验证
## 关键证据索引
## 最强反证索引
## 热证据原文保留
## 未解决问题
## 当前工作
## 下一步
## 关键限制

规则：
1. 总长度不得超过 4500 字符，优先控制在 2500 字符以内。
2. 只保存继续研究不可缺少的状态，不复述工具结果、协议字段、长数字列表或过程寒暄。
3. 事实导航必须带输入中真实存在的 run_evidence:E*；不得创造编号，也不得用
   E1-E4 之类范围缩写，逐个列出实际需要恢复的编号。
4. 明确保留主假设、直接反证、已经放弃的路径、唯一紧接着要做的动作。
5. “热证据原文保留”最多列6个 run_evidence:E*，只选主智能体下一步马上需要
   逐字引用、审计或提交的工具结果；导航资料、已经用完的证据和以后可能用到的资料不得列入。
6. 如果输入包含上一代 research-context-checkpoint，合并仍有效内容、删除过时内容，
   只输出一份新的检查点，不复制旧检查点全文。
7. “下一步”只保存动作和尚未解决的判断，不规划 Claim 数量、字段清单或大段提交结构；
   已经打开 Evidence Ledger 时，下一步默认是直接形成最小充分 Proposal 并提交。
8. 输出纯 Markdown，不输出 JSON，不调用工具，不解释压缩过程。
""".strip()
