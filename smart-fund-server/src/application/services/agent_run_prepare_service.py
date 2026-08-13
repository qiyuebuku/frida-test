"""Deterministically prepare a bounded Research Context Pack on the server."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.application.agents.financial_research.research_context import (
    ResearchContextBuilder,
)
from src.application.agents.financial_research.schemas import (
    ActiveViewSnapshot,
    ConfidenceLevel,
    DataQualityIssue,
    MarketStateFrame,
    ResearchMemoryItem,
    ResearchTriggerEnvelope,
)
from src.application.services.agent_market_query_service import (
    AgentMarketQueryService,
)
from src.infrastructure.persistence.repositories.agent_research_read_repository import (
    AgentResearchReadRepository,
)


class AgentRunPrepareService:
    def __init__(
        self,
        *,
        market_service: AgentMarketQueryService | None = None,
        research_repository: AgentResearchReadRepository | None = None,
        target: str | None = None,
    ) -> None:
        self._market = market_service or AgentMarketQueryService()
        self._research = research_repository or AgentResearchReadRepository(
            target=target
        )
        self._builder = ResearchContextBuilder()

    def prepare_research(
        self,
        *,
        trigger_payload: dict[str, Any],
        signed_cutoff_at: datetime,
        research_question: str = "",
    ) -> dict[str, Any]:
        trigger = ResearchTriggerEnvelope.model_validate(trigger_payload)
        if trigger.cutoff_at.astimezone(UTC) != signed_cutoff_at.astimezone(UTC):
            raise ValueError("trigger cutoff does not match signed run cutoff")
        raw_frame = self._market.market_frame(cutoff_at=signed_cutoff_at)
        report = self._research.current_report_at(cutoff_at=signed_cutoff_at)
        raw_views = self._research.list_views_at(
            cutoff_at=signed_cutoff_at,
            statuses=["active", "challenged"],
            limit=20,
        )
        raw_memories = self._research.search_memories(
            role="research",
            cutoff_at=signed_cutoff_at,
            limit=12,
        )
        pack = self._builder.build(
            trigger=trigger,
            market_state=_market_state_frame(raw_frame, signed_cutoff_at),
            current_report_revision_id=(
                str(report["revision_id"]) if report else None
            ),
            active_views=[_active_view(item) for item in raw_views],
            memory_items=[_memory_item(item) for item in raw_memories],
            research_question=research_question.strip() or None,
        )
        return {
            "operation": "research_run_prepare",
            "status": "available",
            "cutoff_at": signed_cutoff_at.isoformat(),
            "context_pack": pack.model_dump(mode="json"),
        }


def _market_state_frame(
    value: dict[str, Any],
    cutoff_at: datetime,
) -> MarketStateFrame:
    # The initial pack is a task envelope, not a dump of the market catalogue.
    # Counts, dimension-level timestamps and truncation warnings are useful for
    # platform observability but give the model no investable fact.  Research
    # opens the current brief and relevant dimensions through MCP on demand.
    quality_issues: list[DataQualityIssue] = []
    for item in value.get("quality_issues") or []:
        if item.get("severity") != "critical":
            continue
        quality_issues.append(
            DataQualityIssue(
                issue_code=str(item.get("issue_code") or "unknown"),
                severity=str(item.get("severity") or "warning"),
                dimension="market_frame",
                description=str(item.get("description") or "未知数据质量问题"),
                affected_handles=[
                    str(handle)
                    for handle in (
                        item.get("affected_dimensions")
                        or item.get("affected_handles")
                        or []
                    )[:20]
                ],
            )
        )
    return MarketStateFrame(
        frame_id=str(value["frame_id"]),
        cutoff_at=cutoff_at,
        trade_date=value.get("trade_date"),
        market_session=value.get("market_session") or "unknown",
        overview=(
            "初始上下文仅提供交易会话和既有研究状态；"
            "当前市场事实、变化和数据质量应通过 MCP 按研究问题打开。"
        ),
        dimensions=[],
        significant_changes=[],
        quality_issues=quality_issues,
        drilldown_handles=[],
    )


def _active_view(value: dict[str, Any]) -> ActiveViewSnapshot:
    confidence = value.get("confidence") or {}
    if isinstance(confidence, dict):
        confidence = confidence.get("overall") or "low"
    return ActiveViewSnapshot(
        view_id=value["view_id"],
        revision_id=value["revision_id"],
        title=value["title"],
        status=value["status"],
        thesis=value["thesis"],
        confidence=ConfidenceLevel(confidence),
        valid_until=value.get("valid_until"),
    )


def _memory_item(value: dict[str, Any]) -> ResearchMemoryItem:
    return ResearchMemoryItem(
        memory_id=value["memory_id"],
        summary=value["summary"],
        applicability=value["applicability"],
        counterexample=value["counterexample"],
        evidence_references=value.get("evidence_references") or [],
        confidence=ConfidenceLevel(value.get("confidence") or "low"),
        expires_at=value.get("expires_at"),
    )
