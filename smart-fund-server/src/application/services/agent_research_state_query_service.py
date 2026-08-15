"""Bounded Research state, outcome, memory, and exposure projections."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.domain.trading.research_quality_reference import (
    run_id_from_quality_ref,
)

from src.infrastructure.persistence.repositories.agent_research_read_repository import (
    AgentResearchReadRepository,
)


class AgentResearchStateQueryService:
    def __init__(
        self,
        *,
        repository: AgentResearchReadRepository | None = None,
        target: str | None = None,
    ) -> None:
        self._repository = repository or AgentResearchReadRepository(
            target=target
        )

    def current_report(self, *, cutoff_at: datetime) -> dict[str, Any]:
        report = self._repository.current_report_at(cutoff_at=cutoff_at)
        return {
            "operation": "research_current_report_open",
            "status": "available" if report else "empty",
            "cutoff_at": cutoff_at.isoformat(),
            "report": report,
            "next_operations": ["research_view_list", "research_view_open"],
        }

    def list_views(
        self,
        *,
        cutoff_at: datetime,
        statuses: list[str],
        limit: int,
    ) -> dict[str, Any]:
        views = self._repository.list_views_at(
            cutoff_at=cutoff_at,
            statuses=statuses,
            limit=limit,
        )
        return {
            "operation": "research_view_list",
            "status": "available" if views else "empty",
            "cutoff_at": cutoff_at.isoformat(),
            "views": views,
            "next_operations": ["research_view_open"],
        }

    def open_view(
        self,
        *,
        cutoff_at: datetime,
        view_id: str,
        revision_id: str = "",
    ) -> dict[str, Any]:
        view = self._repository.open_view_at(
            view_id=_required(view_id, "view_id"),
            revision_id=revision_id.strip() or None,
            cutoff_at=cutoff_at,
        )
        return {
            "operation": "research_view_open",
            "status": "available" if view else "not_found",
            "cutoff_at": cutoff_at.isoformat(),
            "view": view,
            "next_operations": [
                "role_outcome_search",
                "market_evidence_open",
            ],
        }

    def search_memories(self, *, cutoff_at: datetime, **kwargs) -> dict[str, Any]:
        items = self._repository.search_memories(
            role="research",
            cutoff_at=cutoff_at,
            **kwargs,
        )
        return {
            "operation": "role_memory_search",
            "status": "available" if items else "empty",
            "role": "research",
            "cutoff_at": cutoff_at.isoformat(),
            "memories": items,
            "next_operations": (
                ["role_memory_open", "role_memory_case_open"] if items else []
            ),
        }

    def open_memory(
        self,
        *,
        cutoff_at: datetime,
        memory_id: str,
    ) -> dict[str, Any]:
        item = self._repository.open_memory_at(
            role="research",
            memory_id=_required(memory_id, "memory_id"),
            cutoff_at=cutoff_at,
        )
        return {
            "operation": "role_memory_open",
            "status": "available" if item else "not_found",
            "role": "research",
            "cutoff_at": cutoff_at.isoformat(),
            "memory": item,
            "next_operations": ["role_memory_case_open"],
        }

    def open_memory_cases(
        self,
        *,
        cutoff_at: datetime,
        memory_id: str,
        limit: int,
    ) -> dict[str, Any]:
        items = self._repository.open_memory_cases(
            role="research",
            memory_id=_required(memory_id, "memory_id"),
            cutoff_at=cutoff_at,
            limit=limit,
        )
        return {
            "operation": "role_memory_case_open",
            "status": "available" if items else "empty",
            "role": "research",
            "cutoff_at": cutoff_at.isoformat(),
            "memory_id": memory_id,
            "cases": items,
        }

    def search_outcomes(self, *, cutoff_at: datetime, **kwargs) -> dict[str, Any]:
        items = self._repository.search_outcomes(
            cutoff_at=cutoff_at,
            **kwargs,
        )
        return {
            "operation": "role_outcome_search",
            "status": "available" if items else "empty",
            "role": "research",
            "cutoff_at": cutoff_at.isoformat(),
            "outcomes": items,
            "next_operations": ["role_outcome_open"],
        }

    def open_outcome(
        self,
        *,
        cutoff_at: datetime,
        evaluation_id: str,
    ) -> dict[str, Any]:
        item = self._repository.open_outcome_at(
            evaluation_id=_required(evaluation_id, "evaluation_id"),
            cutoff_at=cutoff_at,
        )
        return {
            "operation": "role_outcome_open",
            "status": "available" if item else "not_found",
            "role": "research",
            "cutoff_at": cutoff_at.isoformat(),
            "outcome": item,
        }

    def list_quality(
        self,
        *,
        cutoff_at: datetime,
        passed: bool | None,
        limit: int,
    ) -> dict[str, Any]:
        items = self._repository.list_quality_evaluations(
            cutoff_at=cutoff_at,
            passed=passed,
            limit=limit,
        )
        return {
            "operation": "research_quality_list",
            "status": "available" if items else "empty",
            "cutoff_at": cutoff_at.isoformat(),
            "evaluations": items,
            "next_operations": ["research_quality_open"],
        }

    def open_quality(
        self,
        *,
        cutoff_at: datetime,
        quality_ref: str,
    ) -> dict[str, Any]:
        run_id = run_id_from_quality_ref(_required(quality_ref, "quality_ref"))
        item = self._repository.open_latest_quality_evaluation_for_run_at(
            run_id=run_id,
            cutoff_at=cutoff_at,
        )
        return {
            "operation": "research_quality_open",
            "status": "available" if item else "not_found",
            "cutoff_at": cutoff_at.isoformat(),
            "evaluation": item,
        }

    @staticmethod
    def exposure_unavailable(
        *,
        operation: str,
        cutoff_at: datetime,
        account_ids: tuple[str, ...],
        instrument_id: str = "",
    ) -> dict[str, Any]:
        return {
            "operation": operation,
            "status": "unavailable",
            "cutoff_at": cutoff_at.isoformat(),
            "account_scope": list(account_ids),
            "instrument_id": instrument_id or None,
            "reason_code": "broker_account_projection_not_connected",
            "reason": (
                "券商账户、持仓和盈亏权威投影尚未接入服务端；"
                "Research 不会用市场推测值伪造持仓。"
            ),
        }


def _required(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} 不能为空")
    return normalized
