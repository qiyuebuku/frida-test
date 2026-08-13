"""Server-owned run state and evidence ledger for remote Agent MCP calls."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.infrastructure.agent_runtime.run_authorization import (
    RunAuthorizationClaims,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.agent_research import (
    AgentRuntimeRun,
    AgentToolInvocation,
)


class AgentMcpAuditRepository:
    """Persist only server-observed calls; the model has no write contract."""

    def __init__(self, *, target: str | None = None) -> None:
        self._target = target

    def record_result(
        self,
        *,
        claims: RunAuthorizationClaims,
        tool_name: str,
        called_at: datetime,
        result: Any | None = None,
        error_type: str | None = None,
    ) -> None:
        completed_at = datetime.now(UTC)
        normalized = _json_value(result) if result is not None else None
        digest = (
            hashlib.sha256(
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            if normalized is not None
            else None
        )
        evidence_refs = _extract_opened_evidence(tool_name, normalized)
        with get_session(self._target) as session:
            session.execute(
                insert(AgentRuntimeRun)
                .values(
                    run_id=claims.run_id,
                    role=claims.role,
                    task=claims.task,
                    run_mode=claims.run_mode,
                    cutoff_at=claims.cutoff_at,
                    authorized_tools=sorted(claims.tools),
                    account_ids=list(claims.account_ids),
                    status="running",
                    budget={},
                    checkpoint={},
                    tool_call_count=0,
                    started_at=called_at,
                    last_activity_at=called_at,
                )
                .on_conflict_do_nothing(index_elements=["run_id"])
            )
            run = session.get(AgentRuntimeRun, claims.run_id)
            if run is None:
                raise RuntimeError("failed to establish Agent run audit state")
            _validate_run_identity(run, claims)
            run.last_activity_at = completed_at
            run.tool_call_count += 1
            session.add(
                AgentToolInvocation(
                    invocation_id=f"tool-{uuid4().hex}",
                    run_id=claims.run_id,
                    tool_name=tool_name,
                    status="failed" if error_type else "completed",
                    response_digest=digest,
                    evidence_refs=evidence_refs,
                    error_type=error_type,
                    called_at=called_at,
                    completed_at=completed_at,
                )
            )

    def open_run_state(
        self,
        *,
        claims: RunAuthorizationClaims,
    ) -> dict[str, Any]:
        with get_session(self._target) as session:
            run = session.get(AgentRuntimeRun, claims.run_id)
            if run is None:
                return _empty_run_state(claims)
            _validate_run_identity(run, claims)
            return {
                "run_id": run.run_id,
                "role": run.role,
                "task": run.task,
                "run_mode": run.run_mode,
                "cutoff_at": run.cutoff_at.isoformat(),
                "status": run.status,
                "budget": run.budget or {},
                "checkpoint": run.checkpoint or {},
                "tool_call_count": run.tool_call_count,
                "started_at": _iso(run.started_at),
                "last_activity_at": _iso(run.last_activity_at),
                "completed_at": _iso(run.completed_at),
            }

    def open_evidence_ledger(
        self,
        *,
        claims: RunAuthorizationClaims,
        limit: int = 100,
    ) -> dict[str, Any]:
        normalized_limit = max(1, min(int(limit), 200))
        with get_session(self._target) as session:
            rows = list(
                session.scalars(
                    select(AgentToolInvocation)
                    .where(AgentToolInvocation.run_id == claims.run_id)
                    .order_by(AgentToolInvocation.called_at.asc())
                    .limit(normalized_limit)
                ).all()
            )
        return {
            "run_id": claims.run_id,
            "cutoff_at": claims.cutoff_at.isoformat(),
            "entries": [
                {
                    "invocation_id": row.invocation_id,
                    "tool_name": row.tool_name,
                    "status": row.status,
                    "evidence_refs": row.evidence_refs or [],
                    "response_digest": row.response_digest,
                    "called_at": row.called_at.isoformat(),
                    "completed_at": row.completed_at.isoformat(),
                }
                for row in rows
            ],
            "truncated": len(rows) >= normalized_limit,
        }

    def opened_evidence_refs(self, *, run_id: str) -> set[str]:
        with get_session(self._target) as session:
            rows = session.scalars(
                select(AgentToolInvocation.evidence_refs).where(
                    AgentToolInvocation.run_id == run_id,
                    AgentToolInvocation.status == "completed",
                )
            ).all()
        return {
            str(reference)
            for references in rows
            for reference in (references or [])
            if str(reference)
        }

    def complete_run(
        self,
        *,
        claims: RunAuthorizationClaims,
        status: str,
        checkpoint: dict[str, Any],
    ) -> None:
        completed_at = datetime.now(UTC)
        with get_session(self._target) as session:
            run = session.get(AgentRuntimeRun, claims.run_id)
            if run is None:
                raise RuntimeError("Agent run audit state does not exist")
            _validate_run_identity(run, claims)
            run.status = status
            run.checkpoint = _json_value(checkpoint)
            run.last_activity_at = completed_at
            run.completed_at = completed_at


def _validate_run_identity(
    run: AgentRuntimeRun,
    claims: RunAuthorizationClaims,
) -> None:
    immutable = (
        run.role,
        run.task,
        run.run_mode,
        run.cutoff_at,
    )
    expected = (
        claims.role,
        claims.task,
        claims.run_mode,
        claims.cutoff_at,
    )
    if immutable != expected:
        raise ValueError("run_id was already bound to different authorization")


def _empty_run_state(claims: RunAuthorizationClaims) -> dict[str, Any]:
    return {
        "run_id": claims.run_id,
        "role": claims.role,
        "task": claims.task,
        "run_mode": claims.run_mode,
        "cutoff_at": claims.cutoff_at.isoformat(),
        "status": "authorized",
        "budget": {},
        "checkpoint": {},
        "tool_call_count": 0,
        "started_at": None,
        "last_activity_at": None,
        "completed_at": None,
    }


def _extract_opened_evidence(tool_name: str, value: Any) -> list[str]:
    key_by_tool = {
        "market_dimension_open": {"evidence_locator"},
        "market_change_brief_open": {
            "evidence_locator",
            "current_evidence_locator",
            "baseline_evidence_locator",
        },
        "market_premarket_context_open": {"evidence_locator"},
        "market_domain_open": {"evidence_locator"},
        "market_topic_open": {"evidence_locator"},
        "market_evidence_open": {"locator", "evidence_locator"},
        "kg_card_open": {"card_id", "evidence_id"},
        "kg_edge_open": {"edge_id"},
        "external_web_read": {"url", "content_handle"},
        "external_repo_read": {"content_handle"},
        "external_content_read": {"content_handle"},
        "market_instrument_open": {"evidence_locator"},
        "market_instrument_realtime_open": {"evidence_locator"},
        "market_expression_compare_open": {"evidence_locator", "evidence_locators"},
        "market_instrument_history": {"evidence_locator"},
        "market_technical_state_open": {
            "evidence_locator",
            "evidence_locators",
            "high_evidence_locator",
            "low_evidence_locator",
        },
        "market_historical_analogue_open": {"evidence_locator", "evidence_locators"},
        "market_sector_overview": {"evidence_locator"},
        "market_sector_rankings": {"evidence_locator"},
        "market_sector_open": {"evidence_locator"},
        "market_sector_compare_open": {"evidence_locator"},
    }
    keys = key_by_tool.get(tool_name, set())
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in keys and isinstance(child, (str, int)) and str(child):
                    found.append(str(child))
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return list(dict.fromkeys(found))[:500]


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
