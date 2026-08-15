"""Independent scheduled entrypoints for the Research Agent."""

from __future__ import annotations

from datetime import UTC, datetime

from jettask import TaskRouter

from src.application.agents.financial_research.runtime import (
    FinancialAgentRuntime,
)
from src.application.agents.financial_research.schemas import (
    ResearchTriggerEnvelope,
    ResearchTriggerSlot,
)
from src.application.services.china_exchange_calendar_service import (
    ChinaExchangeCalendarService,
)
from src.application.services.research_outcome_evaluation_service import (
    ResearchOutcomeEvaluationService,
)
from src.application.services.research_memory_consolidation_service import (
    ResearchMemoryConsolidationService,
)


router = TaskRouter()


@router.task(
    queue="run_research_agent",
    max_retries=1,
    retry_backoff=60,
    retry_backoff_max=300,
)
async def run_research_agent(
    trigger_slot: str,
    reason: str,
) -> dict:
    cutoff_at = datetime.now(UTC)
    session = ChinaExchangeCalendarService().resolve(cutoff_at)
    if not session.is_trading_day:
        return {
            "status": "skipped",
            "reason": "non_trading_day",
            "cutoff_at": cutoff_at.isoformat(),
        }
    slot = ResearchTriggerSlot(trigger_slot)
    trigger = ResearchTriggerEnvelope(
        trigger_id=(
            f"scheduled-research:{slot.value}:"
            f"{session.trade_date.isoformat()}"
        ),
        trigger_slot=slot,
        source="schedule",
        reason=reason,
        cutoff_at=cutoff_at,
        run_mode="production",
    )
    async with FinancialAgentRuntime() as runtime:
        proposal = await runtime.prepare_and_run(
            trigger,
            publish=True,
            session_id=trigger.trigger_id,
        )
    return {
        "status": "completed",
        "trigger_id": trigger.trigger_id,
        "proposal_status": proposal.status.value,
        "report_summary": proposal.report_summary,
        "cutoff_at": cutoff_at.isoformat(),
    }


@router.task(
    queue="evaluate_research_outcomes",
    max_retries=2,
    retry_backoff=60,
    retry_backoff_max=300,
)
async def evaluate_research_outcomes() -> dict:
    return ResearchOutcomeEvaluationService().evaluate_due()


@router.task(
    queue="consolidate_research_memory",
    max_retries=2,
    retry_backoff=60,
    retry_backoff_max=300,
)
async def consolidate_research_memory() -> dict:
    return ResearchMemoryConsolidationService().consolidate()
