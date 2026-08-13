"""Deterministic server-side commit boundary for Research proposals."""

from __future__ import annotations

from typing import Any

from src.application.agents.financial_research.schemas import (
    CurrentResearchReportProposal,
)
from src.application.agents.financial_research.semantic_evaluator import (
    SemanticResearchEvaluation,
)
from src.application.agents.financial_research.quality_evaluator import (
    evaluate_research_quality,
)
from src.application.services.market_evidence_locator import (
    LOCATOR_PREFIX,
    normalize_market_evidence_locator,
)
from src.infrastructure.agent_runtime.run_authorization import (
    RunAuthorizationClaims,
)
from src.infrastructure.persistence.repositories.agent_mcp_audit_repository import (
    AgentMcpAuditRepository,
)
from src.infrastructure.persistence.repositories.agent_research_repository import (
    AgentResearchRepository,
)


class AgentResearchCommitService:
    def __init__(self, *, target: str | None = None) -> None:
        self._research = AgentResearchRepository(target=target)
        self._audit = AgentMcpAuditRepository(target=target)

    def commit(
        self,
        *,
        claims: RunAuthorizationClaims,
        proposal_payload: dict[str, Any],
        publish: bool,
    ) -> dict[str, Any]:
        proposal = CurrentResearchReportProposal.model_validate(
            proposal_payload
        )
        if proposal.run_id != claims.run_id:
            raise ValueError("proposal run_id does not match signed run")
        if proposal.cutoff_at != claims.cutoff_at:
            raise ValueError("proposal cutoff_at does not match signed run")
        if publish and claims.run_mode != "production":
            raise ValueError("only a production run may publish Research state")
        opened = {
            _normalized_evidence_reference(reference)
            for reference in self._audit.opened_evidence_refs(run_id=claims.run_id)
        }
        cited = {
            _normalized_evidence_reference(reference)
            for reference in _proposal_evidence_refs(proposal_payload)
        }
        missing = sorted(cited.difference(opened))
        if missing:
            raise ValueError(
                "proposal cites evidence not opened in this run: "
                + ", ".join(missing[:20])
            )
        ledger = self._audit.open_evidence_ledger(claims=claims, limit=200)
        entries = ledger["entries"]
        quality = evaluate_research_quality(
            proposal,
            tool_names=(item["tool_name"] for item in entries),
            opened_evidence_refs=opened,
        )
        self._research.save_quality_evaluation(quality)
        persistence = None
        if publish:
            persistence = self._research.save_report_proposals_batch(
                [proposal]
            )[0]
        self._audit.complete_run(
            claims=claims,
            status="completed",
            checkpoint={
                "proposal_status": proposal.status.value,
                "publish_requested": publish,
                "published": persistence is not None,
                "cited_evidence_count": len(cited),
                "quality_score": quality.overall_score,
                "quality_grade": quality.grade,
                "quality_passed": quality.passed,
            },
        )
        return {
            "operation": "research_proposal_commit",
            "status": "completed",
            "run_id": claims.run_id,
            "proposal_status": proposal.status.value,
            "published": persistence is not None,
            "quality": quality.model_dump(mode="json"),
            "persistence": (
                {
                    "current_report_updated": (
                        persistence.current_report_updated
                    ),
                    "view_revisions_saved": (
                        persistence.view_revisions_saved
                    ),
                    "idempotent_replay": persistence.idempotent_replay,
                }
                if persistence is not None
                else None
            ),
        }

    def commit_semantic_evaluation(
        self,
        *,
        claims: RunAuthorizationClaims,
        evaluation_payload: dict[str, Any],
    ) -> dict[str, Any]:
        evaluation = SemanticResearchEvaluation.model_validate(evaluation_payload)
        if evaluation.run_id != claims.run_id:
            raise ValueError("semantic evaluation run_id does not match signed run")
        result = self._research.save_semantic_evaluation(evaluation)
        return {
            "operation": "research_semantic_evaluation_commit",
            "status": "completed",
            "run_id": claims.run_id,
            **result,
        }


def _proposal_evidence_refs(value: Any) -> set[str]:
    found: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if "citation_id" in item and isinstance(item.get("reference"), str):
                found.add(item["reference"])
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return found


def _normalized_evidence_reference(reference: str) -> str:
    if not reference.startswith(LOCATOR_PREFIX):
        return reference
    try:
        return normalize_market_evidence_locator(reference)
    except ValueError:
        return reference
