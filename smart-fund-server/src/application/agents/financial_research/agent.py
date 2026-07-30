"""Definition of the single Financial Research Agent."""

from __future__ import annotations

from agents import Agent, ModelSettings, RunContextWrapper, function_tool

from src.application.agents.financial_research.context import AgentRunContext
from src.application.agents.financial_research.instructions import load_financial_research_instructions
from src.application.agents.financial_research.schemas import (
    ConfidenceLevel,
    EvidenceCitation,
    FinancialResearchResult,
    ResearchTaskMode,
)


@function_tool(
    name_override="submit_financial_research",
    description_override=(
        "Submit the final typed financial research result after all necessary "
        "evidence tools have been opened. This is the only valid way to finish."
    ),
    strict_mode=True,
    failure_error_function=None,
)
def submit_financial_research(
    wrapper: RunContextWrapper[AgentRunContext],
    task_mode: ResearchTaskMode,
    conclusion: str,
    confidence: ConfidenceLevel,
    evidence: list[EvidenceCitation],
    uncertainties: list[str],
    next_actions: list[str],
) -> str:
    """Validate and serialize the final financial research result."""

    result = FinancialResearchResult.model_validate(
        {
            "task_mode": task_mode,
            "conclusion": conclusion,
            "confidence": confidence,
            "evidence": evidence,
            "uncertainties": uncertainties,
            "next_actions": next_actions,
        }
    )
    if result.task_mode != wrapper.context.task_mode:
        raise ValueError(
            f"task_mode must be {wrapper.context.task_mode.value}, "
            f"got {result.task_mode.value}"
        )
    return result.model_dump_json()


def create_financial_research_agent(
    *,
    model: str,
    mcp_server,
) -> Agent[AgentRunContext]:
    return Agent[AgentRunContext](
        name="Smart Fund Financial Research Agent",
        instructions=load_financial_research_instructions(),
        model=model,
        model_settings=ModelSettings(
            parallel_tool_calls=True,
            include_usage=True,
            tool_choice="required",
        ),
        tools=[submit_financial_research],
        mcp_servers=[mcp_server],
        tool_use_behavior={
            "stop_at_tool_names": ["submit_financial_research"],
        },
        reset_tool_choice=False,
    )
