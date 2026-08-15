"""Transactional repository for Research Agent proposals and outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.application.agents.financial_research.schemas import (
    ActiveViewSnapshot,
    CurrentResearchReport,
    CurrentResearchReportProposal,
    Forecast,
    OutcomeEvaluation,
    OutcomeObservation,
    ResearchRunStatus,
)
from src.application.agents.financial_research.quality_evaluator import (
    ResearchQualityEvaluation,
    ResearchQualityScores,
    merge_semantic_quality,
)
from src.application.agents.financial_research.semantic_evaluator import (
    SemanticResearchEvaluation,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.agent_research import (
    AgentCurrentResearchReport,
    AgentInvestmentView,
    AgentInvestmentViewRevision,
    AgentResearchClaim,
    AgentResearchForecast,
    AgentResearchObservationRequirement,
    AgentResearchOutcomeEvaluation,
    AgentResearchOutcomeObservation,
    AgentResearchQualityEvaluation,
    AgentResearchReportRevision,
    AgentResearchRun,
    AgentRoleMemoryCase,
    AgentRoleMemoryItem,
)


@dataclass(frozen=True, slots=True)
class ResearchPersistenceResult:
    run_id: str
    status: str
    current_report_updated: bool
    view_revisions_saved: int
    idempotent_replay: bool = False


class AgentResearchRepository:
    """Persist a proposal atomically after model and evidence validation."""

    def __init__(self, *, target: str | None = None) -> None:
        self._target = target

    def save_report_proposals_batch(
        self,
        proposals: list[CurrentResearchReportProposal],
    ) -> list[ResearchPersistenceResult]:
        if not proposals:
            return []
        run_ids = [item.run_id for item in proposals]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("Proposal batch contains duplicate run_id")

        results: list[ResearchPersistenceResult] = []
        with get_session(self._target) as session:
            for proposal in proposals:
                payload = proposal.model_dump(mode="json")
                existing_run = session.get(AgentResearchRun, proposal.run_id)
                if existing_run is not None:
                    if existing_run.proposal_payload != payload:
                        raise ValueError(
                            f"run_id collision with different payload: {proposal.run_id}"
                        )
                    results.append(
                        ResearchPersistenceResult(
                            run_id=proposal.run_id,
                            status=proposal.status.value,
                            current_report_updated=False,
                            view_revisions_saved=0,
                            idempotent_replay=True,
                        )
                    )
                    continue

                session.add(
                    AgentResearchRun(
                        run_id=proposal.run_id,
                        trigger_id=proposal.trigger_id,
                        trigger_slot=proposal.trigger_slot.value,
                        source_frame_id=proposal.source_frame_id,
                        cutoff_at=proposal.cutoff_at,
                        status=proposal.status.value,
                        publishable=proposal.publishable,
                        proposal_payload=payload,
                    )
                )

                if not proposal.publishable:
                    results.append(
                        ResearchPersistenceResult(
                            run_id=proposal.run_id,
                            status=proposal.status.value,
                            current_report_updated=False,
                            view_revisions_saved=0,
                        )
                    )
                    continue

                report = session.scalar(
                    select(AgentCurrentResearchReport)
                    .where(
                        AgentCurrentResearchReport.report_id == proposal.report_id
                    )
                    .with_for_update()
                )
                if proposal.status == ResearchRunStatus.NO_CHANGE:
                    if report is None:
                        revision_id = self._required_report_revision_id(proposal)
                        session.add(
                            AgentResearchReportRevision(
                                revision_id=revision_id,
                                report_id=proposal.report_id,
                                base_revision_id=None,
                                run_id=proposal.run_id,
                                cutoff_at=proposal.cutoff_at,
                                source_frame_id=proposal.source_frame_id,
                                report_summary=proposal.report_summary,
                                research_question=proposal.research_question,
                                payload=payload,
                            )
                        )
                        session.add(
                            AgentCurrentResearchReport(
                                report_id=proposal.report_id,
                                current_revision_id=revision_id,
                                current_cutoff_at=proposal.cutoff_at,
                                last_checked_at=proposal.cutoff_at,
                                last_check_status=proposal.status.value,
                                last_no_change_reason=proposal.no_change_reason,
                                version=1,
                            )
                        )
                        self._save_observation_requirements(session, proposal)
                        results.append(
                            ResearchPersistenceResult(
                                run_id=proposal.run_id,
                                status=proposal.status.value,
                                current_report_updated=True,
                                view_revisions_saved=0,
                            )
                        )
                        continue
                    if report.current_revision_id is None:
                        raise RuntimeError(
                            "Current Research Report pointer is missing"
                        )
                    if proposal.base_report_revision_id != report.current_revision_id:
                        raise ValueError(
                            "stale no_change proposal: base report revision changed"
                        )
                    if proposal.cutoff_at >= report.last_checked_at:
                        report.last_checked_at = proposal.cutoff_at
                        report.last_check_status = proposal.status.value
                        report.last_no_change_reason = proposal.no_change_reason
                        self._save_observation_requirements(session, proposal)
                    results.append(
                        ResearchPersistenceResult(
                            run_id=proposal.run_id,
                            status=proposal.status.value,
                            current_report_updated=False,
                            view_revisions_saved=0,
                        )
                    )
                    continue

                self._validate_report_pointer(report, proposal)
                for revision in proposal.view_revisions:
                    current_view = session.scalar(
                        select(AgentInvestmentView)
                        .where(AgentInvestmentView.view_id == revision.view_id)
                        .with_for_update()
                    )
                    self._validate_view_pointer(current_view, revision)

                view_rows: list[AgentInvestmentViewRevision] = []
                claim_rows: list[AgentResearchClaim] = []
                forecast_rows: list[AgentResearchForecast] = []
                for revision in proposal.view_revisions:
                    view_rows.append(
                        AgentInvestmentViewRevision(
                            revision_id=revision.proposed_revision_id,
                            view_id=revision.view_id,
                            base_revision_id=revision.base_revision_id,
                            run_id=proposal.run_id,
                            event=revision.event,
                            status=revision.status,
                            cutoff_at=proposal.cutoff_at,
                            title=revision.title,
                            thesis=revision.thesis,
                            scope=revision.scope,
                            hypotheses=[
                                item.model_dump(mode="json")
                                for item in revision.hypotheses
                            ],
                            evidence_plan=[
                                item.model_dump(mode="json")
                                for item in revision.evidence_plan
                            ],
                            mechanism_chain=[
                                item.model_dump(mode="json")
                                for item in revision.mechanism_chain
                            ],
                            market_structure=(
                                revision.market_structure.model_dump(mode="json")
                                if revision.market_structure is not None
                                else None
                            ),
                            decision_boundary=(
                                revision.decision_boundary.model_dump(mode="json")
                                if revision.decision_boundary is not None
                                else None
                            ),
                            invalidation_conditions=revision.invalidation_conditions,
                            confidence=revision.confidence.model_dump(mode="json"),
                            valid_until=revision.valid_until,
                        )
                    )
                    claim_rows.extend(
                        AgentResearchClaim(
                            claim_id=claim.claim_id,
                            revision_id=revision.proposed_revision_id,
                            claim_type=claim.claim_type,
                            epistemic_status=claim.epistemic_status,
                            statement=claim.statement,
                            thesis_effect=claim.thesis_effect,
                            confidence=claim.confidence.value,
                            evidence_refs=[
                                item.model_dump(mode="json")
                                for item in claim.evidence
                            ],
                        )
                        for claim in revision.claims
                    )
                    forecast_rows.extend(
                        AgentResearchForecast(
                            forecast_id=forecast.forecast_id,
                            revision_id=revision.proposed_revision_id,
                            subject_id=forecast.subject_id,
                            metric=forecast.metric,
                            expected_direction=forecast.expected_direction,
                            benchmark_subject_id=forecast.benchmark_subject_id,
                            baseline_value=forecast.baseline_value,
                            expected_min_value=forecast.expected_min_value,
                            expected_max_value=forecast.expected_max_value,
                            evaluation_start_at=forecast.evaluation_start_at,
                            evaluation_end_at=forecast.evaluation_end_at,
                            invalidation_condition=forecast.invalidation_condition,
                            status="pending",
                        )
                        for forecast in revision.forecasts
                    )
                session.add_all(view_rows + claim_rows + forecast_rows)

                for revision in proposal.view_revisions:
                    current_view = session.get(AgentInvestmentView, revision.view_id)
                    if current_view is None:
                        session.add(
                            AgentInvestmentView(
                                view_id=revision.view_id,
                                current_revision_id=revision.proposed_revision_id,
                                current_cutoff_at=proposal.cutoff_at,
                                title=revision.title,
                                status=revision.status,
                                version=1,
                            )
                        )
                    else:
                        current_view.current_revision_id = revision.proposed_revision_id
                        current_view.current_cutoff_at = proposal.cutoff_at
                        current_view.title = revision.title
                        current_view.status = revision.status
                        current_view.version += 1

                session.add(
                    AgentResearchReportRevision(
                        revision_id=self._required_report_revision_id(proposal),
                        report_id=proposal.report_id,
                        base_revision_id=proposal.base_report_revision_id,
                        run_id=proposal.run_id,
                        cutoff_at=proposal.cutoff_at,
                        source_frame_id=proposal.source_frame_id,
                        report_summary=proposal.report_summary,
                        research_question=proposal.research_question,
                        payload=payload,
                    )
                )
                if report is None:
                    session.add(
                        AgentCurrentResearchReport(
                            report_id=proposal.report_id,
                            current_revision_id=self._required_report_revision_id(
                                proposal
                            ),
                            current_cutoff_at=proposal.cutoff_at,
                            last_checked_at=proposal.cutoff_at,
                            last_check_status=proposal.status.value,
                            last_no_change_reason=None,
                            version=1,
                        )
                    )
                else:
                    report.current_revision_id = self._required_report_revision_id(
                        proposal
                    )
                    report.current_cutoff_at = proposal.cutoff_at
                    report.last_checked_at = proposal.cutoff_at
                    report.last_check_status = proposal.status.value
                    report.last_no_change_reason = None
                    report.version += 1
                self._save_observation_requirements(session, proposal)
                results.append(
                    ResearchPersistenceResult(
                        run_id=proposal.run_id,
                        status=proposal.status.value,
                        current_report_updated=True,
                        view_revisions_saved=len(proposal.view_revisions),
                    )
                )
        return results

    def save_quality_evaluation(
        self,
        evaluation: ResearchQualityEvaluation,
    ) -> None:
        payload = evaluation.model_dump(mode="json")
        with get_session(self._target) as session:
            existing = session.get(
                AgentResearchQualityEvaluation,
                evaluation.evaluation_id,
            )
            if existing is not None:
                existing_payload = {
                    "evaluation_id": existing.evaluation_id,
                    "run_id": existing.run_id,
                    "evaluator_version": existing.evaluator_version,
                    "overall_score": existing.overall_score,
                    "grade": existing.grade,
                    "passed": existing.passed,
                    "scores": existing.scores,
                    "hard_failures": existing.hard_failures,
                    "advisory_findings": existing.advisory_findings,
                    "improvement_actions": existing.improvement_actions,
                    "tool_coverage": existing.tool_coverage,
                    "evidence_reference_count": existing.evidence_reference_count,
                    "outcome_adjusted_score": existing.outcome_adjusted_score,
                }
                comparable_payload = dict(payload)
                comparable_payload.pop("evaluated_at", None)
                if existing_payload != comparable_payload:
                    raise ValueError(
                        "quality evaluation id collision with different payload"
                    )
                return
            session.add(
                AgentResearchQualityEvaluation(
                    evaluation_id=evaluation.evaluation_id,
                    run_id=evaluation.run_id,
                    evaluator_version=evaluation.evaluator_version,
                    overall_score=evaluation.overall_score,
                    grade=evaluation.grade,
                    passed=evaluation.passed,
                    scores=evaluation.scores.model_dump(mode="json"),
                    hard_failures=evaluation.hard_failures,
                    advisory_findings=evaluation.advisory_findings,
                    improvement_actions=evaluation.improvement_actions,
                    tool_coverage=evaluation.tool_coverage,
                    evidence_reference_count=evaluation.evidence_reference_count,
                    outcome_adjusted_score=evaluation.outcome_adjusted_score,
                    evaluated_at=evaluation.evaluated_at,
                )
            )

    def save_semantic_evaluation(
        self,
        evaluation: SemanticResearchEvaluation,
    ) -> dict:
        with get_session(self._target) as session:
            row = session.scalar(
                select(AgentResearchQualityEvaluation).where(
                    AgentResearchQualityEvaluation.run_id == evaluation.run_id
                )
            )
            if row is None:
                raise ValueError(
                    "deterministic quality evaluation must exist before semantic evaluation"
                )
            payload = evaluation.model_dump(mode="json")
            if row.semantic_evaluation is not None:
                if row.semantic_evaluation != payload:
                    raise ValueError(
                        "semantic evaluation already exists with different payload"
                    )
                return {
                    "overall_score": row.overall_score,
                    "grade": row.grade,
                    "idempotent_replay": True,
                }
            merged, overall, grade = merge_semantic_quality(
                ResearchQualityScores.model_validate(row.scores),
                evaluation,
            )
            row.scores = merged.model_dump(mode="json")
            row.overall_score = overall
            row.grade = grade if not row.hard_failures else "rejected"
            row.semantic_evaluation = payload
            row.semantic_evaluator_version = evaluation.evaluator_version
            row.semantic_evaluated_at = datetime.now(UTC)
            return {
                "overall_score": overall,
                "grade": row.grade,
                "idempotent_replay": False,
            }

    def get_current_report(self) -> CurrentResearchReport | None:
        with get_session(self._target) as session:
            current = session.get(AgentCurrentResearchReport, "research:current")
            if current is None or current.current_revision_id is None:
                return None
            revision = session.get(
                AgentResearchReportRevision,
                current.current_revision_id,
            )
            if revision is None:
                raise RuntimeError("Current Research Report pointer is broken")
            content = CurrentResearchReportProposal.model_validate(revision.payload)
            if current.current_cutoff_at is None:
                raise RuntimeError("Current Research Report cutoff pointer is missing")
            return CurrentResearchReport(
                report_id=current.report_id,
                current_revision_id=current.current_revision_id,
                version=current.version,
                current_cutoff_at=current.current_cutoff_at,
                last_checked_at=current.last_checked_at,
                last_check_status=current.last_check_status,
                last_no_change_reason=current.last_no_change_reason,
                content=content,
            )

    def list_active_views(self, *, limit: int = 20) -> list[ActiveViewSnapshot]:
        normalized_limit = max(1, min(int(limit), 100))
        with get_session(self._target) as session:
            rows = session.scalars(
                select(AgentInvestmentView)
                .where(AgentInvestmentView.status.in_(["active", "challenged"]))
                .order_by(AgentInvestmentView.current_cutoff_at.desc())
                .limit(normalized_limit)
            ).all()
            revisions = {
                item.revision_id: item
                for item in session.scalars(
                    select(AgentInvestmentViewRevision).where(
                        AgentInvestmentViewRevision.revision_id.in_(
                            [row.current_revision_id for row in rows]
                        )
                    )
                ).all()
            }
            return [
                ActiveViewSnapshot(
                    view_id=row.view_id,
                    revision_id=row.current_revision_id,
                    title=row.title,
                    status=row.status,
                    thesis=revisions[row.current_revision_id].thesis,
                    confidence=revisions[row.current_revision_id].confidence[
                        "overall"
                    ],
                    valid_until=revisions[row.current_revision_id].valid_until,
                )
                for row in rows
            ]

    def save_outcome_evaluations_batch(
        self,
        items: list[tuple[OutcomeObservation, OutcomeEvaluation]],
    ) -> int:
        if not items:
            return 0
        observation_ids = [item[0].observation_id for item in items]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("Outcome batch contains duplicate observation_id")

        with get_session(self._target) as session:
            rows: list[object] = []
            affected_run_ids: set[str] = set()
            for observation, evaluation in items:
                if observation.forecast_id != evaluation.forecast_id:
                    raise ValueError("Outcome evaluation references another forecast")
                if observation.observation_id != evaluation.observation_id:
                    raise ValueError("Outcome evaluation references another observation")
                forecast = session.get(
                    AgentResearchForecast,
                    observation.forecast_id,
                )
                if forecast is None:
                    raise ValueError(
                        f"Unknown forecast_id: {observation.forecast_id}"
                    )
                revision = session.get(
                    AgentInvestmentViewRevision,
                    forecast.revision_id,
                )
                if revision is None:
                    raise ValueError(
                        f"Unknown revision_id: {forecast.revision_id}"
                    )
                affected_run_ids.add(revision.run_id)
                if not (
                    forecast.evaluation_start_at
                    <= observation.observed_at
                    <= forecast.evaluation_end_at
                ):
                    raise ValueError(
                        "Outcome observation is outside its pre-declared window"
                    )
                if evaluation.evaluated_at < observation.observed_at:
                    raise ValueError(
                        "Outcome evaluation cannot precede its observation"
                    )
                rows.extend(
                    [
                        AgentResearchOutcomeObservation(
                            observation_id=observation.observation_id,
                            forecast_id=observation.forecast_id,
                            observed_at=observation.observed_at,
                            actual_value=observation.actual_value,
                            benchmark_value=observation.benchmark_value,
                            invalidation_condition_hit=(
                                observation.invalidation_condition_hit
                            ),
                            evidence_refs=[
                                item.model_dump(mode="json")
                                for item in observation.evidence
                            ],
                        ),
                        AgentResearchOutcomeEvaluation(
                            **evaluation.model_dump()
                        ),
                    ]
                )
                forecast.status = evaluation.status
            session.add_all(rows)
            session.flush()
            self._refresh_outcome_adjusted_quality(
                session,
                run_ids=affected_run_ids,
            )
        return len(items)

    def list_due_forecasts(
        self,
        *,
        due_at: datetime,
        limit: int = 100,
    ) -> list[Forecast]:
        with get_session(self._target) as session:
            rows = list(session.scalars(
                select(AgentResearchForecast)
                .where(
                    AgentResearchForecast.status == "pending",
                    AgentResearchForecast.evaluation_end_at <= due_at,
                )
                .order_by(AgentResearchForecast.evaluation_end_at)
                .limit(max(1, min(int(limit), 500)))
            ).all())
        return [
            Forecast(
                forecast_id=row.forecast_id,
                subject_id=row.subject_id,
                metric=row.metric,
                expected_direction=row.expected_direction,
                benchmark_subject_id=row.benchmark_subject_id,
                baseline_value=row.baseline_value,
                expected_min_value=row.expected_min_value,
                expected_max_value=row.expected_max_value,
                evaluation_start_at=row.evaluation_start_at,
                evaluation_end_at=row.evaluation_end_at,
                invalidation_condition=row.invalidation_condition,
            )
            for row in rows
        ]

    def list_quality_memory_evidence(
        self,
        *,
        since: datetime,
        limit: int = 500,
    ) -> list[dict]:
        with get_session(self._target) as session:
            rows = list(
                session.scalars(
                    select(AgentResearchQualityEvaluation)
                    .where(AgentResearchQualityEvaluation.evaluated_at >= since)
                    .order_by(AgentResearchQualityEvaluation.evaluated_at.desc())
                    .limit(max(1, min(int(limit), 2000)))
                ).all()
            )
        return [
            {
                "evaluation_id": row.evaluation_id,
                "run_id": row.run_id,
                "grade": row.grade,
                "overall_score": row.overall_score,
                "hard_failures": list(row.hard_failures or []),
                "advisory_findings": list(row.advisory_findings or []),
                "improvement_actions": list(row.improvement_actions or []),
                "evaluated_at": row.evaluated_at,
            }
            for row in rows
        ]

    def list_outcome_memory_evidence(
        self,
        *,
        since: datetime,
        limit: int = 1000,
    ) -> list[dict]:
        with get_session(self._target) as session:
            rows = session.execute(
                select(
                    AgentResearchOutcomeEvaluation,
                    AgentResearchForecast,
                    AgentInvestmentViewRevision,
                )
                .join(
                    AgentResearchForecast,
                    AgentResearchForecast.forecast_id
                    == AgentResearchOutcomeEvaluation.forecast_id,
                )
                .join(
                    AgentInvestmentViewRevision,
                    AgentInvestmentViewRevision.revision_id
                    == AgentResearchForecast.revision_id,
                )
                .where(AgentResearchOutcomeEvaluation.evaluated_at >= since)
                .order_by(AgentResearchOutcomeEvaluation.evaluated_at.desc())
                .limit(max(1, min(int(limit), 3000)))
            ).all()
        return [
            {
                "evaluation_id": evaluation.evaluation_id,
                "forecast_id": forecast.forecast_id,
                "run_id": revision.run_id,
                "subject_id": forecast.subject_id,
                "metric": forecast.metric,
                "expected_direction": forecast.expected_direction,
                "status": evaluation.status,
                "summary": evaluation.summary,
                "evaluated_at": evaluation.evaluated_at,
            }
            for evaluation, forecast, revision in rows
        ]

    def upsert_role_memory_with_cases(
        self,
        *,
        memory: dict,
        cases: list[dict],
    ) -> None:
        """Publish one governed memory snapshot and its auditable cases."""

        with get_session(self._target) as session:
            statement = insert(AgentRoleMemoryItem).values(memory)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=["memory_id"],
                    set_={
                        "status": statement.excluded.status,
                        "summary": statement.excluded.summary,
                        "applicability": statement.excluded.applicability,
                        "counterexample": statement.excluded.counterexample,
                        "evidence_references": statement.excluded.evidence_references,
                        "confidence": statement.excluded.confidence,
                        "scope": statement.excluded.scope,
                        "valid_from": statement.excluded.valid_from,
                        "expires_at": statement.excluded.expires_at,
                        "version": statement.excluded.version,
                        "updated_at": datetime.now(UTC),
                    },
                )
            )
            if cases:
                case_statement = insert(AgentRoleMemoryCase).values(cases)
                session.execute(
                    case_statement.on_conflict_do_update(
                        index_elements=["case_id"],
                        set_={
                            "memory_id": case_statement.excluded.memory_id,
                            "role": case_statement.excluded.role,
                            "decision_ref": case_statement.excluded.decision_ref,
                            "outcome_refs": case_statement.excluded.outcome_refs,
                            "context": case_statement.excluded.context,
                            "result": case_statement.excluded.result,
                        },
                    )
                )

    @staticmethod
    def _refresh_outcome_adjusted_quality(session, *, run_ids: set[str]) -> None:
        status_scores = {
            "confirmed": 100.0,
            "partially_confirmed": 75.0,
            "not_confirmed": 25.0,
            "invalidated": 0.0,
            "inconclusive": 50.0,
        }
        for run_id in run_ids:
            quality = session.scalar(
                select(AgentResearchQualityEvaluation).where(
                    AgentResearchQualityEvaluation.run_id == run_id
                )
            )
            if quality is None:
                continue
            statuses = list(
                session.scalars(
                    select(AgentResearchOutcomeEvaluation.status)
                    .join(
                        AgentResearchForecast,
                        AgentResearchForecast.forecast_id
                        == AgentResearchOutcomeEvaluation.forecast_id,
                    )
                    .join(
                        AgentInvestmentViewRevision,
                        AgentInvestmentViewRevision.revision_id
                        == AgentResearchForecast.revision_id,
                    )
                    .where(AgentInvestmentViewRevision.run_id == run_id)
                ).all()
            )
            if not statuses:
                continue
            realized_score = sum(
                status_scores.get(status, 50.0) for status in statuses
            ) / len(statuses)
            quality.outcome_adjusted_score = round(
                quality.overall_score * 0.7 + realized_score * 0.3,
                2,
            )

    @staticmethod
    def _validate_report_pointer(
        current: AgentCurrentResearchReport | None,
        proposal: CurrentResearchReportProposal,
    ) -> None:
        current_revision_id = (
            current.current_revision_id if current is not None else None
        )
        if proposal.base_report_revision_id != current_revision_id:
            raise ValueError("stale report proposal: current revision changed")
        if (
            current is not None
            and proposal.cutoff_at < current.last_checked_at
        ):
            raise ValueError(
                "older cutoff cannot replace or revise Current Research Report"
            )

    @staticmethod
    def _validate_view_pointer(current, revision) -> None:
        if revision.event == "create":
            if current is not None:
                raise ValueError(f"view already exists: {revision.view_id}")
            return
        if current is None:
            raise ValueError(f"view does not exist: {revision.view_id}")
        if revision.base_revision_id != current.current_revision_id:
            raise ValueError(
                f"stale view revision for {revision.view_id}: current revision changed"
            )

    @staticmethod
    def _save_observation_requirements(session, proposal) -> None:
        session.add_all(
            [
                AgentResearchObservationRequirement(
                    requirement_id=item.requirement_id,
                    run_id=proposal.run_id,
                    subject_id=item.subject_id,
                    metric_or_event=item.metric_or_event,
                    reason=item.reason,
                    due_at=item.due_at,
                    source_preference=item.source_preference,
                    related_view_id=item.related_view_id,
                    related_forecast_id=item.related_forecast_id,
                    status="pending",
                )
                for item in proposal.observation_requirements
            ]
        )

    @staticmethod
    def _required_report_revision_id(
        proposal: CurrentResearchReportProposal,
    ) -> str:
        if proposal.proposed_report_revision_id is None:
            raise ValueError("updated report is missing proposed_report_revision_id")
        return proposal.proposed_report_revision_id
