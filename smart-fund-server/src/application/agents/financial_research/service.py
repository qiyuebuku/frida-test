"""Application boundary for server-prepared Research runs."""

from __future__ import annotations

from src.application.agents.financial_research.runtime import FinancialAgentRuntime
from src.application.agents.financial_research.schemas import (
    CurrentResearchReportProposal,
    ResearchTriggerEnvelope,
)


class ResearchReviewApplicationService:
    """Use remote MCP for context preparation and deterministic publication."""

    def __init__(
        self,
        *,
        runtime: FinancialAgentRuntime,
    ) -> None:
        self._runtime = runtime

    async def review(
        self,
        *,
        trigger: ResearchTriggerEnvelope,
        research_question: str | None = None,
        publish: bool = False,
        session_id: str | None = None,
    ) -> CurrentResearchReportProposal:
        return await self._runtime.prepare_and_run(
            trigger,
            research_question=research_question,
            session_id=session_id,
            publish=publish,
        )
