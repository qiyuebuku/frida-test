"""Agentic retrieval controller for KG search/find/open/expand/summarize tools."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal, Protocol

from pydantic import Field

from src.domain.knowledge.retrieval import RetrievalHit, RetrievalTrace, _clip, _inherit_evidence_scores, dedupe_hits
from src.domain.knowledge.retrieval_anchor import AnchorHint, QueryAnchor, build_guarded_query_anchor
from src.domain.knowledge.retrieval_judge import (
    CandidateJudge,
    CandidateJudgement,
    DeterministicCandidateJudge,
)
from src.domain.knowledge.retrieval_ranker import JudgePreselectResult, judge_preselect
from src.domain.knowledge.retrieval_profile import profile_span
from src.domain.knowledge.retrieval_stop_verifier import (
    answer_candidate_ids as _verify_answer_candidate_ids,
    coverage_terms as _verify_coverage_terms,
    missing_coverage_terms as _verify_missing_coverage_terms,
    normalized_answer_candidate_id as _verify_normalized_answer_candidate_id,
    parent_candidate_id_for_evidence as _verify_parent_candidate_id_for_evidence,
    required_answer_count as _verify_required_answer_count,
    requires_new_answer as _verify_requires_new_answer,
    verify_stop_condition,
)
from src.domain.knowledge.retrieval_trace_log import trace_agentic_event
from src.domain.knowledge.retrieval_tools import RetrievalToolCall, RetrievalToolRegistry, RetrievalToolResult
from src.domain.knowledge.schemas import KnowledgeBaseModel

ControllerTool = Literal["search", "scoped_search", "find", "open", "expand", "summarize", "stop"]
ExpectedGain = Literal["evidence_coverage", "disambiguation", "context_window", "compression", "none"]


class AgenticRetrievalConstraints(KnowledgeBaseModel):
    max_turns: int = Field(default=6, ge=1)
    max_tool_calls: int = Field(default=6, ge=1)
    max_hits: int = Field(default=30, ge=1)
    recall_pool_max_hits: int = Field(default=60, ge=1)
    no_gain_round_limit: int = Field(default=2, ge=1)
    judge_top_k: int = Field(default=8, ge=1)
    judge_top_k_complex: int = Field(default=12, ge=1)
    judge_top_k_max: int = Field(default=15, ge=1)
    evidence_backfill_limit: int = Field(default=2, ge=0, le=5)
    min_keep_evidence_to_auto_open: int = Field(default=1, ge=1)
    min_keep_candidates_to_auto_stop: int = Field(default=2, ge=1)
    anchor_coverage_stop_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    max_llm_calls_normal_query: int = Field(default=2, ge=1)
    max_llm_calls_complex_query: int = Field(default=3, ge=1)
    max_auto_expand_candidates: int = Field(default=4, ge=0, le=10)
    semantic_query_rewrite_limit: int = Field(default=3, ge=0, le=5)


class RetrievalSearchPlan(KnowledgeBaseModel):
    answer_targets: list[str] = Field(default_factory=list)
    negative_boundaries: list[str] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)
    relation_intents: list[str] = Field(default_factory=list)
    stop_condition: str = ""


class RetrievalControllerDecision(KnowledgeBaseModel):
    next_tool: ControllerTool
    reason: str = ""
    target_candidate_ids: list[str] = Field(default_factory=list)
    target_evidence_refs: list[str] = Field(default_factory=list)
    query_rewrites: list[str] = Field(default_factory=list)
    search_plan: RetrievalSearchPlan = Field(default_factory=RetrievalSearchPlan)
    expected_gain: ExpectedGain = "none"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    stop_reason: str | None = None


class AgenticToolTrace(KnowledgeBaseModel):
    tool_name: str
    tool_input: dict = Field(default_factory=dict)
    raw_candidate_count: int = 0
    package_count: int = 0
    keep_count: int = 0
    weak_keep_count: int = 0
    drop_count: int = 0
    added_evidence_refs: list[str] = Field(default_factory=list)
    decision_reason: str = ""
    controller_duration_ms: float = 0.0
    tool_duration_ms: float = 0.0
    judge_duration_ms: float = 0.0
    auto_action: str | None = None
    auto_action_reason: str | None = None
    llm_calls_used: int = 0
    llm_call_budget: int = 0
    judge_top_k: int = 0
    stop_reason: str | None = None


class SupportingEdgeContext(KnowledgeBaseModel):
    edge_id: str
    relation_type: str
    source_node_id: str
    target_node_id: str
    source_name: str = ""
    target_name: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    score: float = 0.0


class SupportingEvidenceExcerpt(KnowledgeBaseModel):
    evidence_id: str
    source_type: str = ""
    source_id: str = ""
    excerpt: str = ""
    score: float = 0.0


class CandidateContextPackage(KnowledgeBaseModel):
    candidate: RetrievalHit
    supporting_edges: list[SupportingEdgeContext] = Field(default_factory=list)
    supporting_evidence_excerpt: list[SupportingEvidenceExcerpt] = Field(default_factory=list)
    why_recalled: list[str] = Field(default_factory=list)
    search_plan: dict[str, Any] = Field(default_factory=dict)

    @property
    def hit_id(self) -> str:
        return self.candidate.hit_id

    @property
    def hit_type(self) -> str:
        return self.candidate.hit_type

    @property
    def source(self) -> str:
        return self.candidate.source

    @property
    def title(self) -> str:
        return self.candidate.title

    @property
    def snippet(self) -> str:
        return self.candidate.snippet

    @property
    def node_refs(self) -> list[str]:
        return self.candidate.node_refs

    @property
    def edge_refs(self) -> list[str]:
        return self.candidate.edge_refs

    @property
    def evidence_refs(self) -> list[str]:
        return self.candidate.evidence_refs

    @property
    def score(self) -> float:
        return self.candidate.score


class RetrievalWorkingSet(KnowledgeBaseModel):
    query_anchor: QueryAnchor
    accepted_candidates: list[CandidateJudgement] = Field(default_factory=list)
    background_candidates: list[CandidateJudgement] = Field(default_factory=list)
    dropped_candidates: list[CandidateJudgement] = Field(default_factory=list)
    opened_windows: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    missing_anchor_items: list[str] = Field(default_factory=list)
    tool_call_count: int = 0
    no_gain_rounds: int = 0
    stop_reason: str | None = None

    def update_from_judgements(
        self,
        *,
        hits: list[RetrievalHit],
        judgements: list[CandidateJudgement],
        tool_name: str,
    ) -> list[str]:
        hit_by_id = {hit.hit_id: hit for hit in hits}
        before = set(self.evidence_refs)
        for judgement in judgements:
            hit = hit_by_id.get(judgement.candidate_id)
            if judgement.decision == "keep":
                self.accepted_candidates.append(judgement)
                if hit is not None:
                    self.evidence_refs = _ordered_unique([*self.evidence_refs, *hit.evidence_refs])
            elif judgement.decision == "weak_keep":
                self.background_candidates.append(judgement)
            else:
                self.dropped_candidates.append(judgement)
        if tool_name == "open":
            self.opened_windows = _ordered_unique(
                [
                    *self.opened_windows,
                    *(hit.hit_id for hit in hits),
                    *(ref for hit in hits for ref in hit.evidence_refs),
                ]
            )
        added = [item for item in self.evidence_refs if item not in before]
        self.no_gain_rounds = 0 if added else self.no_gain_rounds + 1
        return added


class AgenticRetrievalResult(KnowledgeBaseModel):
    query: str
    hits: list[RetrievalHit] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    trace: RetrievalTrace
    stop_reason: str
    working_set: RetrievalWorkingSet


class AgenticRetrievalStrategy(Protocol):
    async def next_decision(
        self,
        *,
        query: str,
        working_set: RetrievalWorkingSet,
        observations: list[RetrievalToolResult],
        constraints: AgenticRetrievalConstraints,
    ) -> RetrievalControllerDecision:
        """Return the next tool call decision or stop decision."""


class AgenticRetrievalController:
    """Runs a constrained Agentic RAG loop over KG retrieval tools."""

    def __init__(
        self,
        registry: RetrievalToolRegistry,
        strategy: AgenticRetrievalStrategy,
        candidate_judge: CandidateJudge | None = None,
        constraints: AgenticRetrievalConstraints | None = None,
        *,
        bootstrap_first: bool = False,
        trace_mode: str = "agentic_arag",
    ):
        self.registry = registry
        self.strategy = strategy
        self.candidate_judge = candidate_judge or DeterministicCandidateJudge()
        self.constraints = constraints or AgenticRetrievalConstraints()
        self.bootstrap_first = bootstrap_first
        self.trace_mode = trace_mode

    async def run(self, query: str) -> AgenticRetrievalResult:
        anchor = build_guarded_query_anchor(
            query,
            known_nodes=self.registry.runtime.repository.list_nodes(self.registry.options.adapter_name),
        )
        working_set = RetrievalWorkingSet(query_anchor=anchor)
        observations: list[RetrievalToolResult] = []
        hits: list[RetrievalHit] = []
        tool_traces: list[AgenticToolTrace] = []
        stop_reason = "max_turns"
        controller_call_count = 0
        judge_call_count = 0
        active_search_plan = RetrievalSearchPlan()
        preselect_summaries: list[dict[str, Any]] = []

        if self.bootstrap_first and working_set.tool_call_count < self.constraints.max_tool_calls:
            started = time.perf_counter()
            bootstrap_call = RetrievalToolCall(
                tool="search",
                query=query,
                limit=self.constraints.recall_pool_max_hits,
            )
            bootstrap_result = await self.registry.execute(bootstrap_call)
            bootstrap_duration_ms = (time.perf_counter() - started) * 1000
            observations.append(bootstrap_result)
            working_set.tool_call_count += 1
            tool_traces.append(
                AgenticToolTrace(
                    tool_name="search",
                    tool_input={
                        **bootstrap_call.model_dump(mode="json"),
                        "mode": "bootstrap",
                        "rewrite": False,
                        "search_diagnostics": bootstrap_result.step.input.get("search_diagnostics", {}),
                    },
                    raw_candidate_count=len(bootstrap_result.hits),
                    package_count=0,
                    keep_count=0,
                    weak_keep_count=0,
                    drop_count=0,
                    added_evidence_refs=[],
                    decision_reason="bootstrap_raw_query_before_agent",
                    tool_duration_ms=round(bootstrap_duration_ms, 1),
                    auto_action="bootstrap_search",
                    auto_action_reason="raw_query_first_recall",
                    llm_calls_used=0,
                    llm_call_budget=_controller_budget_for_query(
                        query,
                        active_search_plan,
                        self.constraints,
                    ),
                    judge_top_k=0,
                )
            )
            trace_agentic_event(
                "agentic_bootstrap_search",
                {
                    "event_meaning": "raw query bootstrap recall ran before the LLM controller; no LLM request.",
                    "_human_lines": [
                        f"查询: {query}",
                        "首轮: 使用原始 query 做 bootstrap 召回，未调用 LLM rewrite。",
                        f"候选数: {len(bootstrap_result.hits)}",
                    ],
                    "query": query,
                    "hits": _hit_summaries(bootstrap_result.hits[:10]),
                    "search_diagnostics": bootstrap_result.step.input.get("search_diagnostics", {}),
                    "tool_ms": round(bootstrap_duration_ms, 1),
                },
            )

        for _turn in range(self.constraints.max_turns):
            if working_set.tool_call_count >= self.constraints.max_tool_calls:
                stop_reason = "max_tool_calls"
                break
            if working_set.no_gain_rounds >= self.constraints.no_gain_round_limit:
                stop_reason = "no_new_evidence"
                break
            started = time.perf_counter()
            with profile_span(
                "agentic.controller_decision",
                turn=working_set.tool_call_count + 1,
                observations=len(observations),
            ):
                decision = await self.strategy.next_decision(
                    query=query,
                    working_set=working_set,
                    observations=observations,
                    constraints=self.constraints,
                )
            controller_call_count += 1
            controller_duration_ms = (time.perf_counter() - started) * 1000
            controller_was_called = True
            if _search_plan_has_content(decision.search_plan):
                active_search_plan = decision.search_plan
            if decision.next_tool == "stop":
                stop_reason = decision.stop_reason or "controller_stop"
                break
            call = _tool_call_from_decision(decision, query=query, working_set=working_set)
            if call is None:
                stop_reason = "invalid_controller_decision"
                break
            trace_agentic_event(
                "agentic_controller_apply",
                {
                    "event_meaning": "retrieval decision is being applied as the next tool call.",
                    "controller_called": controller_was_called,
                    "controller_next_tool": decision.next_tool,
                    "controller_reason": decision.reason,
                    "expected_gain": decision.expected_gain,
                    "confidence": decision.confidence,
                    "applied_tool": call.tool,
                    "applied_query": call.query,
                    "applied_queries": _search_queries_from_decision(decision, fallback=call.query or query)
                    if call.tool in {"search", "scoped_search"}
                    else [],
                    "search_plan": decision.search_plan.model_dump(mode="json"),
                    "target_evidence_refs": call.evidence_ids,
                    "target_candidate_ids": call.candidate_ids,
                },
            )
            started = time.perf_counter()
            result = await _execute_tool_call(
                self.registry,
                call,
                decision=decision,
                fallback_query=query,
                constraints=self.constraints,
            )
            tool_duration_ms = (time.perf_counter() - started) * 1000
            if call.tool == "open":
                result = result.model_copy(update={"hits": _inherit_evidence_scores(result.hits, hits)})
            package_started = time.perf_counter()
            with profile_span(
                "agentic.candidate_package_build",
                tool=call.tool,
                raw_candidates=len(result.hits),
            ):
                judge_anchor = _anchor_with_search_plan(anchor, active_search_plan)
                preselect_result = _preselect_judge_hits(
                    result.hits,
                    self.constraints,
                    judge_anchor,
                    query=query,
                    search_plan=active_search_plan,
                )
                preselect_summaries.append(_preselect_summary(preselect_result, raw_candidates=len(result.hits)))
                judge_input_hits = [item.hit for item in preselect_result.selected]
                candidate_packages = _build_candidate_context_packages(
                    judge_input_hits,
                    registry=self.registry,
                    query=query,
                    anchor=judge_anchor,
                    search_plan=active_search_plan,
                    evidence_backfill_limit=self.constraints.evidence_backfill_limit,
                )
            package_duration_ms = (time.perf_counter() - package_started) * 1000
            judge_hits = [item.candidate for item in candidate_packages]
            trace_agentic_event(
                "agentic_ranker_preselect",
                {
                    "event_meaning": "recall candidates were ranked by RRF + features + coverage before judge; no LLM request.",
                    "_human_lines": _human_ranker_preselect_lines(preselect_result),
                    "strategy": preselect_result.strategy_name,
                    "top_k_requested": preselect_result.top_k_requested,
                    "top_k_reason": preselect_result.top_k_reason,
                    "raw_candidates": len(result.hits),
                    "selected_candidates": len(preselect_result.selected),
                    "missed_coverage_terms": preselect_result.missed_coverage_terms,
                    "channel_contribution": preselect_result.channel_contribution,
                    "selected": _ranked_candidate_summaries(preselect_result.selected),
                    "remaining_high_potential": _ranked_candidate_summaries(
                        preselect_result.remaining_high_potential[:5]
                    ),
                },
            )
            trace_agentic_event(
                "agentic_candidate_package",
                {
                    "event_meaning": "retrieved hits have been converted into human-readable candidate packages for judge; no LLM request.",
                    "tool": call.tool,
                    "raw_candidates": len(result.hits),
                    "judge_candidates": len(candidate_packages),
                    "duration_ms": round(package_duration_ms, 1),
                    "candidates": _hit_summaries(judge_hits[:10]),
                },
            )
            started = time.perf_counter()
            with profile_span(
                "agentic.candidate_judge",
                turn=working_set.tool_call_count + 1,
                tool=call.tool,
                candidates=len(candidate_packages),
                raw_candidates=len(result.hits),
            ):
                judgements = await self.candidate_judge.judge(
                    query=query,
                    anchor=judge_anchor,
                    hits=candidate_packages,
                )
            judge_call_count += 1
            judge_duration_ms = (time.perf_counter() - started) * 1000
            answer_ids_before = set(_answer_candidate_ids(working_set, hits))
            added_evidence = working_set.update_from_judgements(
                hits=judge_hits,
                judgements=judgements,
                tool_name=call.tool,
            )
            new_answer_ids = [
                item for item in _answer_candidate_ids(working_set, [*hits, *judge_hits]) if item not in answer_ids_before
            ]
            observations.append(result)
            hits.extend(_hits_allowed_for_context(judge_hits, judgements))
            working_set.tool_call_count += 1
            tool_traces.append(
                AgenticToolTrace(
                    tool_name=call.tool,
                    tool_input=call.model_dump(mode="json"),
                    raw_candidate_count=len(result.hits),
                    package_count=len(candidate_packages),
                    keep_count=sum(1 for item in judgements if item.decision == "keep"),
                    weak_keep_count=sum(1 for item in judgements if item.decision == "weak_keep"),
                    drop_count=sum(1 for item in judgements if item.decision == "drop"),
                    added_evidence_refs=added_evidence,
                    decision_reason=decision.reason,
                    controller_duration_ms=round(controller_duration_ms, 1),
                    tool_duration_ms=round(tool_duration_ms, 1),
                    judge_duration_ms=round(judge_duration_ms, 1),
                    llm_calls_used=controller_call_count + judge_call_count,
                    llm_call_budget=_controller_budget_for_query(
                        query,
                        active_search_plan,
                        self.constraints,
                    ),
                    judge_top_k=len(judge_hits),
                )
            )
            trace_agentic_event(
                "agentic_tool",
                {
                    "event_meaning": "tool execution has finished; this is post-judge bookkeeping, not an LLM request.",
                    "tool": call.tool,
                    "raw_candidates": len(result.hits),
                    "judged_packages": len(candidate_packages),
                    "keep": tool_traces[-1].keep_count,
                    "weak_keep": tool_traces[-1].weak_keep_count,
                    "drop": tool_traces[-1].drop_count,
                    "added_evidence_refs": added_evidence,
                    "added_evidence": _evidence_ref_summaries(
                        added_evidence,
                        repository=self.registry.runtime.repository,
                    ),
                    "kept_candidates": _judgement_summaries(
                        judgements,
                        judge_hits,
                        decisions={"keep"},
                    ),
                    "background_candidates": _judgement_summaries(
                        judgements,
                        judge_hits,
                        decisions={"weak_keep"},
                    ),
                    "dropped_candidates": _judgement_summaries(
                        judgements,
                        judge_hits,
                        decisions={"drop"},
                    ),
                    "controller_ms": tool_traces[-1].controller_duration_ms,
                    "tool_ms": tool_traces[-1].tool_duration_ms,
                    "judge_ms": tool_traces[-1].judge_duration_ms,
                },
            )
            if call.tool != "open" and _should_auto_open(working_set, self.constraints):
                auto_result = await self._auto_open(
                    working_set=working_set,
                    observations=observations,
                    hits=hits,
                    tool_traces=tool_traces,
                    controller_call_count=controller_call_count,
                    judge_call_count=judge_call_count,
                )
                if auto_result is not None:
                    hits.extend(auto_result)
            pending_expand = _pending_expand_candidate_summaries(working_set, hits)
            if pending_expand:
                auto_open_expand_evidence_result = await self._auto_open_expand_evidence(
                    working_set=working_set,
                    observations=observations,
                    hits=hits,
                    tool_traces=tool_traces,
                    pending_expand=pending_expand,
                    controller_call_count=controller_call_count,
                    judge_call_count=judge_call_count,
                )
                if auto_open_expand_evidence_result is not None:
                    hits.extend(auto_open_expand_evidence_result)
                pending_expand = _pending_expand_candidate_summaries(working_set, hits)
            controller_budget = _controller_budget_for_query(query, active_search_plan, self.constraints)
            can_ask_controller = controller_call_count < controller_budget
            if _should_auto_stop(
                working_set,
                self.constraints,
                search_plan=active_search_plan,
                new_answer_ids=new_answer_ids,
                hits=hits,
            ) and (not pending_expand or not can_ask_controller):
                stop_reason = "auto_stop_context_sufficient"
                if tool_traces:
                    evidence_summaries = _evidence_ref_summaries(
                        working_set.evidence_refs,
                        repository=self.registry.runtime.repository,
                    )
                    accepted_summaries = _judgement_summaries(
                        working_set.accepted_candidates,
                        hits,
                        decisions={"keep"},
                        dedupe_by_id=True,
                    )
                    background_summaries = _judgement_summaries(
                        working_set.background_candidates,
                        hits,
                        decisions={"weak_keep"},
                        dedupe_by_id=True,
                    )
                    tool_traces[-1] = tool_traces[-1].model_copy(
                        update={
                            "stop_reason": stop_reason,
                            "auto_action": "stop"
                            if tool_traces[-1].auto_action is None
                            else tool_traces[-1].auto_action,
                            "auto_action_reason": "context_sufficient_after_open",
                        }
                    )
                    trace_agentic_event(
                        "agentic_auto_stop",
                        {
                            "event_meaning": "controller stopped automatically after opened evidence covered the query; no LLM request.",
                            "_human_lines": _human_auto_stop_lines(
                                query=query,
                                stop_reason=stop_reason,
                                evidence=evidence_summaries,
                                accepted=accepted_summaries,
                                background=background_summaries,
                            ),
                            "stop_reason": stop_reason,
                            "pending_expand_candidates": pending_expand,
                            "controller_budget_remaining": can_ask_controller,
                            "distinct_answer_candidate_ids": _answer_candidate_ids(working_set, hits),
                            "new_answer_candidate_ids": new_answer_ids,
                            "coverage_terms": _coverage_terms(active_search_plan),
                            "missing_coverage_terms": _missing_coverage_terms(
                                active_search_plan,
                                working_set,
                                hits,
                            ),
                            "evidence_refs": working_set.evidence_refs,
                            "evidence": evidence_summaries,
                            "accepted_candidates": accepted_summaries,
                            "background_candidates": background_summaries,
                        },
                    )
                break
            if pending_expand and _should_auto_stop(
                working_set,
                self.constraints,
                search_plan=active_search_plan,
                new_answer_ids=new_answer_ids,
                hits=hits,
            ):
                trace_agentic_event(
                    "agentic_auto_stop_skipped",
                    {
                        "event_meaning": "automatic stop was skipped because answer/support candidates still requested expansion; controller will decide next.",
                        "_human_lines": _human_auto_stop_skipped_lines(
                            query=query,
                            pending_expand=pending_expand,
                        ),
                        "pending_expand_candidates": pending_expand,
                        "controller_budget_remaining": can_ask_controller,
                        "coverage_terms": _coverage_terms(active_search_plan),
                        "missing_coverage_terms": _missing_coverage_terms(
                            active_search_plan,
                            working_set,
                            hits,
                        ),
                    },
                )
            if not can_ask_controller:
                stop_reason = "llm_budget_exhausted_stop_condition_unmet"
                trace_agentic_event(
                    "agentic_budget_stop",
                    {
                        "event_meaning": "controller LLM budget is exhausted and current search_plan stop condition is not met; stop is explicit, not evidence sufficient.",
                        "_human_lines": [
                            f"查询: {query}",
                            "停止: controller LLM 预算已用完，但当前 search_plan 的停止条件未满足。",
                            "这不是证据充分停止；需要增加预算或改进召回才能继续补证。",
                        ],
                        "stop_reason": stop_reason,
                        "controller_budget_remaining": can_ask_controller,
                        "distinct_answer_candidate_ids": _answer_candidate_ids(working_set, hits),
                        "new_answer_candidate_ids": new_answer_ids,
                        "coverage_terms": _coverage_terms(active_search_plan),
                        "missing_coverage_terms": _missing_coverage_terms(
                            active_search_plan,
                            working_set,
                            hits,
                        ),
                        "search_plan": active_search_plan.model_dump(mode="json"),
                    },
                )
                break
        selected_hits = dedupe_hits(hits)[: self.constraints.max_hits]
        evidence_refs = _ordered_unique(
            evidence_id for hit in selected_hits for evidence_id in hit.evidence_refs
        )
        working_set.stop_reason = stop_reason
        stop_verification = verify_stop_condition(
            working_set,
            self.constraints,
            search_plan=active_search_plan,
            hits=hits,
        )
        case_summary = _case_summary(
            query=query,
            observations=observations,
            tool_traces=tool_traces,
            working_set=working_set,
            hits=hits,
            preselect_summaries=preselect_summaries,
            stop_reason=stop_reason,
            stop_verification=stop_verification,
            repository=self.registry.runtime.repository,
        )
        trace_agentic_event(
            "agentic_case_summary",
            {
                "event_meaning": "final per-case retrieval summary for human review; no LLM request.",
                "_human_lines": _human_case_summary_lines(case_summary),
                **case_summary,
            },
        )
        semantic_enabled = bool(getattr(self.registry.runtime.semantic_retriever, "enabled", False))
        backend_name = str(getattr(self.registry.runtime.semantic_retriever, "backend_name", "none"))
        return AgenticRetrievalResult(
            query=query,
            hits=selected_hits,
            evidence_refs=evidence_refs,
            working_set=working_set,
            trace=RetrievalTrace(
                mode=self.trace_mode,
                channels_enabled=list(self.registry.available_tools),
                channels_used=_ordered_unique(item.tool for item in observations),
                semantic_enabled=semantic_enabled,
                milvus_enabled=semantic_enabled and backend_name == "milvus",
                agentic_enabled=True,
                planner_enabled=False,
                steps=[item.step for item in observations],
                warnings=[],
                query_anchor=anchor.model_dump(mode="json"),
                candidate_judgements=[
                    item.model_dump(mode="json")
                    for item in [
                        *working_set.accepted_candidates,
                        *working_set.background_candidates,
                        *working_set.dropped_candidates,
                    ]
                ],
                working_set=working_set.model_dump(mode="json"),
                controller_decisions=[item.model_dump(mode="json") for item in tool_traces],
            ),
            stop_reason=stop_reason,
        )

    async def _auto_open(
        self,
        *,
        working_set: RetrievalWorkingSet,
        observations: list[RetrievalToolResult],
        hits: list[RetrievalHit],
        tool_traces: list[AgenticToolTrace],
        controller_call_count: int,
        judge_call_count: int,
    ) -> list[RetrievalHit] | None:
        evidence_ids = _auto_open_evidence_ids(working_set)
        if not evidence_ids:
            return None
        if working_set.tool_call_count >= self.constraints.max_tool_calls:
            return None
        call = RetrievalToolCall(tool="open", evidence_ids=evidence_ids)
        started = time.perf_counter()
        result = await self.registry.execute(call)
        tool_duration_ms = (time.perf_counter() - started) * 1000
        result = result.model_copy(update={"hits": _inherit_evidence_scores(result.hits, hits)})
        observations.append(result)
        working_set.opened_windows = _ordered_unique(
            [
                *working_set.opened_windows,
                *(hit.hit_id for hit in result.hits),
                *(ref for hit in result.hits for ref in hit.evidence_refs),
            ]
        )
        working_set.tool_call_count += 1
        tool_traces.append(
            AgenticToolTrace(
                tool_name="open",
                tool_input=call.model_dump(mode="json"),
                raw_candidate_count=len(result.hits),
                package_count=len(result.hits),
                keep_count=len(result.hits),
                added_evidence_refs=evidence_ids,
                decision_reason="auto_open_sufficient_keep_evidence",
                tool_duration_ms=round(tool_duration_ms, 1),
                auto_action="open",
                auto_action_reason="sufficient_keep_evidence",
                llm_calls_used=controller_call_count + judge_call_count,
                llm_call_budget=self.constraints.max_llm_calls_normal_query,
                judge_top_k=0,
            )
        )
        trace_agentic_event(
            "agentic_auto_open",
            {
                "event_meaning": "program opened selected evidence windows after judge accepted candidates; no LLM request.",
                "evidence_refs": evidence_ids,
                "opened_hits": _hit_summaries(result.hits[:10]),
                "tool_ms": round(tool_duration_ms, 1),
                "reason": "sufficient_keep_evidence",
            },
        )
        return result.hits

    async def _auto_open_expand_evidence(
        self,
        *,
        working_set: RetrievalWorkingSet,
        observations: list[RetrievalToolResult],
        hits: list[RetrievalHit],
        tool_traces: list[AgenticToolTrace],
        pending_expand: list[dict[str, object]],
        controller_call_count: int,
        judge_call_count: int,
    ) -> list[RetrievalHit] | None:
        if self.constraints.max_auto_expand_candidates <= 0:
            return None
        if working_set.tool_call_count >= self.constraints.max_tool_calls:
            return None
        candidate_ids = [
            str(item.get("id") or "")
            for item in pending_expand[: self.constraints.max_auto_expand_candidates]
            if str(item.get("id") or "")
        ]
        if not candidate_ids:
            return None
        expand_limit = max(
            self.registry.options.evidence_limit,
            len(candidate_ids) * max(1, self.constraints.evidence_backfill_limit),
        )
        call = RetrievalToolCall(tool="open", candidate_ids=candidate_ids, limit=expand_limit)
        started = time.perf_counter()
        result = await self.registry.execute(call)
        tool_duration_ms = (time.perf_counter() - started) * 1000
        result = result.model_copy(update={"hits": _inherit_evidence_scores(result.hits, hits)})
        observations.append(result)
        working_set.opened_windows = _ordered_unique(
            [
                *working_set.opened_windows,
                *(_expand_marker(candidate_id) for candidate_id in candidate_ids),
                *(hit.hit_id for hit in result.hits),
                *(ref for hit in result.hits for ref in hit.evidence_refs),
            ]
        )
        working_set.tool_call_count += 1
        tool_traces.append(
            AgenticToolTrace(
                tool_name="open",
                tool_input=call.model_dump(mode="json"),
                raw_candidate_count=len(result.hits),
                package_count=len(result.hits),
                keep_count=len(result.hits),
                added_evidence_refs=_ordered_unique(ref for hit in result.hits for ref in hit.evidence_refs),
                decision_reason="auto_open_expand_evidence_for_judged_candidates",
                tool_duration_ms=round(tool_duration_ms, 1),
                auto_action="open_expand_evidence",
                auto_action_reason="candidate_judge_requested_evidence_open",
                llm_calls_used=controller_call_count + judge_call_count,
                llm_call_budget=self.constraints.max_llm_calls_normal_query,
                judge_top_k=0,
            )
        )
        trace_agentic_event(
            "agentic_auto_open_expand_evidence",
            {
                "event_meaning": "program opened evidence windows for answer/support candidates marked expand=true; no graph expansion was executed.",
                "_human_lines": _human_auto_open_expand_evidence_lines(
                    pending_expand=pending_expand,
                    opened_hits=_hit_summaries(result.hits[:10]),
                ),
                "target_candidate_ids": candidate_ids,
                "pending_expand_candidates": pending_expand,
                "opened_hits": _hit_summaries(result.hits[:10]),
                "tool_ms": round(tool_duration_ms, 1),
                "reason": "candidate_judge_requested_evidence_open",
            },
        )
        return result.hits


def _tool_call_from_decision(
    decision: RetrievalControllerDecision,
    *,
    query: str,
    working_set: RetrievalWorkingSet,
) -> RetrievalToolCall | None:
    if decision.next_tool == "search":
        query_text = (decision.query_rewrites[0] if decision.query_rewrites else query).strip()
        return RetrievalToolCall(tool="search", query=query_text)
    if decision.next_tool == "scoped_search":
        query_text = (decision.query_rewrites[0] if decision.query_rewrites else query).strip()
        candidate_ids = decision.target_candidate_ids
        evidence_ids = decision.target_evidence_refs or working_set.evidence_refs
        if not candidate_ids and not evidence_ids:
            return None
        return RetrievalToolCall(
            tool="scoped_search",
            query=query_text,
            evidence_ids=evidence_ids,
            candidate_ids=candidate_ids,
        )
    if decision.next_tool == "find":
        evidence_ids = decision.target_evidence_refs or working_set.evidence_refs
        if not evidence_ids:
            return None
        query_text = (decision.query_rewrites[0] if decision.query_rewrites else query).strip()
        return RetrievalToolCall(tool="find", query=query_text, evidence_ids=evidence_ids)
    if decision.next_tool == "open":
        candidate_ids = decision.target_candidate_ids
        evidence_ids = decision.target_evidence_refs
        if not evidence_ids and not candidate_ids:
            evidence_ids = working_set.evidence_refs
        if not evidence_ids and not candidate_ids:
            return None
        return RetrievalToolCall(tool="open", evidence_ids=evidence_ids, candidate_ids=candidate_ids)
    if decision.next_tool == "expand":
        candidate_ids = decision.target_candidate_ids
        if not candidate_ids:
            return None
        return RetrievalToolCall(tool="expand", candidate_ids=candidate_ids)
    if decision.next_tool == "summarize":
        return RetrievalToolCall(tool="summarize")
    return None


async def _execute_tool_call(
    registry: RetrievalToolRegistry,
    call: RetrievalToolCall,
    *,
    decision: RetrievalControllerDecision,
    fallback_query: str,
    constraints: AgenticRetrievalConstraints,
) -> RetrievalToolResult:
    if call.tool != "search":
        return await registry.execute(call)
    queries = _search_queries_from_decision(decision, fallback=call.query or fallback_query)
    search_limit = max(constraints.max_hits, constraints.recall_pool_max_hits)
    if len(queries) <= 1:
        result = await registry.execute(call.model_copy(update={"query": queries[0], "limit": search_limit}))
        diagnostics = (result.step.input or {}).get("search_diagnostics") or {}
        semantic_enabled = bool(
            registry.options.semantic_hybrid_limit > 0
            and getattr(registry.runtime.semantic_retriever, "enabled", False)
        )
        query_mode = {
            "query": queries[0],
            "mode": "full" if semantic_enabled else "cheap_no_semantic",
            "semantic_enabled": semantic_enabled,
            "hit_count": len(result.hits),
            "source_counts": _hit_source_counts(result.hits),
            "pre_dedupe_counts": diagnostics.get("pre_dedupe_counts") or {},
            "post_dedupe_primary_source_counts": diagnostics.get("post_dedupe_primary_source_counts") or {},
            "merged_channel_counts": diagnostics.get("merged_channel_counts") or {},
            "selected_merged_channel_counts": diagnostics.get("selected_merged_channel_counts") or {},
            "source_samples": diagnostics.get("source_samples") or {},
            "semantic_hybrid": diagnostics.get("semantic_hybrid") or diagnostics.get("vector_semantic") or {},
            "query_parser": diagnostics.get("query_parser") or {},
        }
        trace_agentic_event(
            "agentic_search_result",
            {
                "event_meaning": "single search query was executed; source diagnostics show raw channel recall and merged channel coverage.",
                "_human_lines": _human_search_plan_result_lines(
                    queries=queries,
                    query_modes=[query_mode],
                    search_plan=decision.search_plan,
                    hit_count=len(result.hits),
                ),
                "query": queries[0],
                "query_mode": query_mode,
                "source_diagnostics": _aggregate_search_diagnostics([query_mode]),
                "selected_hit_count": len(result.hits),
                "selected_hits": _trace_selected_hit_summaries(result.hits),
                "recall_pool_sample": _hit_summaries(result.hits[:10]),
            },
        )
        return result

    results: list[RetrievalToolResult] = []
    query_modes: list[dict[str, object]] = []
    cheap_registry = _cheap_search_registry(registry)
    for index, query in enumerate(queries):
        use_semantic = index < constraints.semantic_query_rewrite_limit
        query_registry = registry if use_semantic else cheap_registry
        semantic_enabled = query_registry.options.semantic_hybrid_limit > 0
        query_modes.append(
            {
                "query": query,
                "mode": "full" if semantic_enabled else "cheap_no_semantic",
                "semantic_enabled": semantic_enabled,
            }
        )
        with profile_span(
            "agentic.search_plan.query",
            query=query,
            mode=query_modes[-1]["mode"],
            semantic_enabled=semantic_enabled,
        ):
            result = await query_registry.execute(call.model_copy(update={"query": query, "limit": search_limit}))
            results.append(result)
            diagnostics = (result.step.input or {}).get("search_diagnostics") or {}
            query_modes[-1]["hit_count"] = len(result.hits)
            query_modes[-1]["source_counts"] = _hit_source_counts(result.hits)
            query_modes[-1]["pre_dedupe_counts"] = diagnostics.get("pre_dedupe_counts") or {}
            query_modes[-1]["post_dedupe_primary_source_counts"] = (
                diagnostics.get("post_dedupe_primary_source_counts") or {}
            )
            query_modes[-1]["merged_channel_counts"] = diagnostics.get("merged_channel_counts") or {}
            query_modes[-1]["selected_merged_channel_counts"] = (
                diagnostics.get("selected_merged_channel_counts") or {}
            )
            query_modes[-1]["source_samples"] = diagnostics.get("source_samples") or {}
            query_modes[-1]["semantic_hybrid"] = (
                diagnostics.get("semantic_hybrid") or diagnostics.get("vector_semantic") or {}
            )
            query_modes[-1]["query_parser"] = diagnostics.get("query_parser") or {}
    combined_hits = dedupe_hits([hit for result in results for hit in result.hits])
    hits = _rerank_hits_for_search_plan(combined_hits, decision.search_plan, fallback_query=fallback_query)[
        : search_limit
    ]
    trace_agentic_event(
        "agentic_search_plan_result",
        {
            "event_meaning": "search query rewrites were executed and search_plan was applied to rerank recalled candidates; no LLM request.",
            "_human_lines": _human_search_plan_result_lines(
                queries=queries,
                query_modes=query_modes,
                search_plan=decision.search_plan,
                hit_count=len(hits),
            ),
            "query_rewrites": queries,
            "query_modes": query_modes,
            "search_plan_usage": {
                "query_rewrites_executed": True,
                "semantic_search": f"first_{constraints.semantic_query_rewrite_limit}_queries",
                "keyword_entity_wiki_graph": "all_queries",
                "search_plan_rerank": True,
                "hard_filter_by_negative_boundaries": False,
            },
            "channel_contribution": _channel_contribution_for_hits(
                pooled_hits=[hit for result in results for hit in result.hits],
                selected_hits=hits,
            ),
            "source_diagnostics": _aggregate_search_diagnostics(query_modes),
            "selected_hit_count": len(hits),
            "selected_hits": _trace_selected_hit_summaries(hits),
            "recall_pool_sample": _hit_summaries(hits[:10]),
        },
    )
    first_step = results[0].step
    combined_step = first_step.model_copy(
        update={
            "input": {
                "tool": "search",
                "query_rewrites": queries,
                "query_modes": query_modes,
                "search_plan": decision.search_plan.model_dump(mode="json"),
            },
            "output_refs": [hit.hit_id for hit in hits],
            "hit_count": len(hits),
            "warning": f"multi_query_search:{len(queries)};semantic_queries:{min(len(queries), constraints.semantic_query_rewrite_limit)}",
        }
    )
    return RetrievalToolResult(
        tool="search",
        hits=hits,
        step=combined_step,
        summary=f"multi_query_search:{len(queries)};semantic_queries:{min(len(queries), constraints.semantic_query_rewrite_limit)}",
    )


def _cheap_search_registry(registry: RetrievalToolRegistry) -> RetrievalToolRegistry:
    options = registry.options.model_copy(update={"semantic_hybrid_limit": 0})
    return RetrievalToolRegistry(
        registry.runtime,
        options,
        reranker_client=registry.reranker_client,
        reranker_max_documents=registry.reranker_max_documents,
        reranker_default_top_n=registry.reranker_default_top_n,
    )


def _search_queries_from_decision(decision: RetrievalControllerDecision, *, fallback: str) -> list[str]:
    queries = _ordered_unique([*(decision.query_rewrites or []), fallback])
    cleaned = [query.strip() for query in queries if isinstance(query, str) and query.strip()]
    return cleaned[:3] or [fallback]


def _hit_source_counts(hits: list[RetrievalHit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        for key in _hit_channels(hit):
            counts[key] = counts.get(key, 0) + 1
    return counts


def _primary_source_counts(hits: list[RetrievalHit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        key = hit.source or hit.hit_type or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _hit_channels(hit: RetrievalHit) -> list[str]:
    return _ordered_unique([*(hit.source_channels or []), hit.source or hit.hit_type or "unknown"])


def _channel_contribution_for_hits(
    *,
    pooled_hits: list[RetrievalHit],
    selected_hits: list[RetrievalHit],
) -> dict[str, object]:
    pool_counts = _hit_source_counts(pooled_hits)
    selected_counts = _hit_source_counts(selected_hits)
    channels_by_id: dict[str, set[str]] = {}
    for hit in pooled_hits:
        channels_by_id.setdefault(hit.hit_id, set()).update(_hit_channels(hit))
    selected_ids = [hit.hit_id for hit in selected_hits]
    keyword_ids = {hit.hit_id for hit in pooled_hits if "keyword" in _hit_channels(hit)}
    semantic_ids = {hit.hit_id for hit in pooled_hits if "semantic_hybrid" in _hit_channels(hit)}
    graph_ids = {hit.hit_id for hit in pooled_hits if "graph" in _hit_channels(hit)}
    return {
        "pool_counts": pool_counts,
        "selected_counts": selected_counts,
        "selected_primary_source_counts": _primary_source_counts(selected_hits),
        "keyword_only_selected": sum(
            1 for hit_id in selected_ids if hit_id in keyword_ids and hit_id not in semantic_ids and hit_id not in graph_ids
        ),
        "semantic_only_added_selected": sum(
            1 for hit_id in selected_ids if hit_id in semantic_ids and hit_id not in keyword_ids
        ),
        "graph_confirmed_selected": sum(
            1 for hit_id in selected_ids if hit_id in graph_ids and (hit_id in keyword_ids or hit_id in semantic_ids)
        ),
        "multi_channel_selected": sum(
            1 for hit_id in selected_ids if len(channels_by_id.get(hit_id, set())) >= 2
        ),
    }


def _aggregate_search_diagnostics(query_modes: list[dict[str, object]]) -> dict[str, object]:
    return {
        "pre_dedupe_counts": _sum_count_maps(item.get("pre_dedupe_counts") for item in query_modes),
        "post_dedupe_primary_source_counts": _sum_count_maps(
            item.get("post_dedupe_primary_source_counts") for item in query_modes
        ),
        "merged_channel_counts": _sum_count_maps(item.get("merged_channel_counts") for item in query_modes),
        "selected_merged_channel_counts": _sum_count_maps(
            item.get("selected_merged_channel_counts") for item in query_modes
        ),
        "semantic_hybrid": [
            {
                "query": item.get("query"),
                **(item.get("semantic_hybrid") if isinstance(item.get("semantic_hybrid"), dict) else {}),
            }
            for item in query_modes
        ],
        "query_parser": [
            {
                "query": item.get("query"),
                **(item.get("query_parser") if isinstance(item.get("query_parser"), dict) else {}),
            }
            for item in query_modes
        ],
        "source_samples": {
            str(item.get("query") or index): item.get("source_samples") or {}
            for index, item in enumerate(query_modes, start=1)
        },
    }


def _sum_count_maps(items) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            try:
                counts[str(key)] = counts.get(str(key), 0) + int(value)
            except (TypeError, ValueError):
                continue
    return counts


def _rerank_hits_for_search_plan(
    hits: list[RetrievalHit],
    search_plan: RetrievalSearchPlan,
    *,
    fallback_query: str,
) -> list[RetrievalHit]:
    if not hits:
        return []
    positive_terms = _plan_terms(
        [
            fallback_query,
            *search_plan.answer_targets,
            *search_plan.expected_evidence,
            *search_plan.relation_intents,
            search_plan.stop_condition,
        ]
    )
    negative_terms = _plan_terms(search_plan.negative_boundaries)
    if not positive_terms and not negative_terms:
        return hits

    scored: list[tuple[float, int, RetrievalHit]] = []
    for index, hit in enumerate(hits):
        text = f"{hit.title}\n{hit.snippet}\n{hit.source}\n{hit.hit_type}".lower()
        positive = sum(1 for term in positive_terms if term.lower() in text)
        negative = sum(1 for term in negative_terms if term.lower() in text)
        relation_bonus = 0.25 if search_plan.relation_intents and any(
            relation.lower() in text for relation in search_plan.relation_intents
        ) else 0.0
        evidence_bonus = 0.2 if hit.evidence_refs else 0.0
        plan_score = positive + relation_bonus + evidence_bonus - (negative * 2.0)
        scored.append((plan_score, index, hit))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [hit for _, _, hit in scored]


def _plan_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        terms.append(text)
        for token in (
            text.replace("（", " ")
            .replace("）", " ")
            .replace("(", " ")
            .replace(")", " ")
            .replace("，", " ")
            .replace(",", " ")
            .replace("、", " ")
            .split()
        ):
            if len(token.strip()) >= 2:
                terms.append(token.strip())
    return _ordered_unique(terms)


def _hits_allowed_for_context(
    hits: list[RetrievalHit],
    judgements: list[CandidateJudgement],
) -> list[RetrievalHit]:
    judgement_by_id = {item.candidate_id: item for item in judgements}
    return [
        hit
        for hit in hits
        if (judgement := judgement_by_id.get(hit.hit_id)) is None
        or judgement.decision == "keep"
    ]


def _hit_summaries(hits: list[RetrievalHit]) -> list[dict[str, object]]:
    return [
        {
            "title": hit.title,
            "type": hit.hit_type,
            "source": hit.source,
            "source_channels": _hit_channels(hit),
            "evidence_refs": hit.evidence_refs[:2],
            "id": hit.hit_id,
        }
        for hit in hits
    ]


def _trace_selected_hit_summaries(hits: list[RetrievalHit]) -> list[dict[str, object]]:
    """Show the human-facing selected sample in judge-like priority order."""
    ranked = sorted(dedupe_hits(hits), key=_trace_hit_rank)
    return _hit_summaries(ranked[:10])


def _trace_hit_rank(hit: RetrievalHit) -> tuple[int, int, float, str]:
    text = f"{hit.title}\n{hit.snippet}\n{hit.source}\n{hit.hit_type}".lower()
    if hit.hit_type == "node" and _looks_like_event_hit(text):
        type_rank = 0
    elif hit.hit_type == "evidence":
        type_rank = 1
    elif hit.hit_type == "node":
        type_rank = 2
    elif hit.hit_type == "edge":
        type_rank = 4
    elif hit.hit_type == "path":
        type_rank = 5
    elif hit.hit_type == "wiki":
        type_rank = 7
    else:
        type_rank = 8
    evidence_rank = 0 if hit.evidence_refs else 1
    return (type_rank, evidence_rank, -float(hit.score or 0.0), hit.hit_id)


def _judgement_summaries(
    judgements: list[CandidateJudgement],
    hits: list[RetrievalHit],
    *,
    decisions: set[str],
    dedupe_by_id: bool = False,
) -> list[dict[str, object]]:
    hit_by_id = {hit.hit_id: hit for hit in hits}
    source_judgements = judgements
    if dedupe_by_id:
        latest_by_id: dict[str, CandidateJudgement] = {}
        for judgement in judgements:
            if judgement.decision in decisions:
                latest_by_id[judgement.candidate_id] = judgement
        source_judgements = list(latest_by_id.values())
    summaries: list[dict[str, object]] = []
    for judgement in source_judgements:
        if judgement.decision not in decisions:
            continue
        hit = hit_by_id.get(judgement.candidate_id)
        summaries.append(
            {
                "title": hit.title if hit is not None else judgement.candidate_id,
                "type": hit.hit_type if hit is not None else "",
                "role": judgement.role,
                "decision": judgement.decision,
                "score": judgement.relevance_score,
                "expand": judgement.can_expand_graph,
                "reason": judgement.reason_code or judgement.reason,
                "evidence_refs": hit.evidence_refs[:2] if hit is not None else [],
                "id": judgement.candidate_id,
            }
        )
    return summaries


def _evidence_ref_summaries(evidence_ids: list[str], *, repository) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    for evidence_id in evidence_ids:
        evidence = repository.get_evidence(evidence_id)
        if evidence is None:
            summaries.append({"id": evidence_id, "source": "", "excerpt": ""})
            continue
        summaries.append(
            {
                "id": evidence_id,
                "source": str(getattr(evidence, "source_id", "") or ""),
                "excerpt": _clip(_evidence_text(evidence), 120),
            }
        )
    return summaries


def _human_auto_stop_lines(
    *,
    query: str,
    stop_reason: str,
    evidence: list[dict[str, str]],
    accepted: list[dict[str, object]],
    background: list[dict[str, object]],
) -> list[str]:
    lines = [
        f"查询: {query}",
        f"自动停止: 已打开的证据覆盖当前问题 ({stop_reason})",
    ]
    if evidence:
        lines.append("关键证据:")
        for index, item in enumerate(evidence[:5], start=1):
            source = item.get("source") or item.get("id") or ""
            excerpt = item.get("excerpt") or ""
            lines.append(f"  {index}. {source}: {excerpt}")
    answer_items = [item for item in accepted if item.get("role") == "answer"]
    support_items = [item for item in accepted if item.get("role") != "answer"]
    if answer_items:
        lines.append("答案候选:")
        for index, item in enumerate(answer_items[:5], start=1):
            lines.append(f"  {index}. {_human_candidate_line(item)}")
    if support_items:
        lines.append("支撑候选:")
        for index, item in enumerate(support_items[:5], start=1):
            lines.append(f"  {index}. {_human_candidate_line(item)}")
    readable_background = [
        item for item in background if not _looks_like_internal_id(str(item.get("title") or ""))
    ]
    if readable_background:
        lines.append("背景候选:")
        for index, item in enumerate(readable_background[:3], start=1):
            lines.append(f"  {index}. {_human_candidate_line(item)}")
    return lines


def _human_auto_stop_skipped_lines(
    *,
    query: str,
    pending_expand: list[dict[str, object]],
) -> list[str]:
    lines = [
        f"查询: {query}",
        "未自动停止: 仍有 answer/support 候选要求 expand，交回 controller 判断是否继续检索。",
        "待判断候选:",
    ]
    for index, item in enumerate(pending_expand[:5], start=1):
        lines.append(f"  {index}. {_human_candidate_line(item)}")
    return lines


def _human_auto_open_expand_evidence_lines(
    *,
    pending_expand: list[dict[str, object]],
    opened_hits: list[dict[str, object]],
) -> list[str]:
    lines = [
        "自动打开待展开候选证据: 对 answer/support 且 expand=True 的候选执行 open(candidate_ids)，不是 graph expand。"
    ]
    if pending_expand:
        lines.append("待打开证据候选:")
        for index, item in enumerate(pending_expand[:5], start=1):
            lines.append(f"  {index}. {_human_candidate_line(item)}")
    if opened_hits:
        lines.append("打开证据:")
        for index, item in enumerate(opened_hits[:5], start=1):
            title = str(item.get("title") or item.get("id") or "")
            evidence_refs = item.get("evidence_refs") or []
            lines.append(f"  {index}. {title} evidence={evidence_refs}")
    else:
        lines.append("打开证据: 无新增 evidence；候选已标记为已展开。")
    return lines


def _human_search_plan_result_lines(
    *,
    queries: list[str],
    query_modes: list[dict[str, object]],
    search_plan: RetrievalSearchPlan,
    hit_count: int,
) -> list[str]:
    lines = ["搜索计划执行:"]
    for index, query in enumerate(queries, start=1):
        mode = query_modes[index - 1] if index - 1 < len(query_modes) else {}
        source_counts = mode.get("source_counts") or {}
        pre_dedupe = mode.get("pre_dedupe_counts") or {}
        merged_channels = mode.get("merged_channel_counts") or {}
        semantic = mode.get("semantic_hybrid") or {}
        lines.append(
            f"  {index}. {query} mode={mode.get('mode')} semantic={mode.get('semantic_enabled')} "
            f"hits={mode.get('hit_count')} sources={source_counts} raw={pre_dedupe} merged_channels={merged_channels} "
            f"semantic_detail={semantic}"
        )
    if search_plan.answer_targets:
        lines.append(f"answer_targets: {', '.join(search_plan.answer_targets)}")
    if search_plan.expected_evidence:
        lines.append(f"expected_evidence: {', '.join(search_plan.expected_evidence)}")
    if search_plan.negative_boundaries:
        lines.append(f"negative_boundaries: {', '.join(search_plan.negative_boundaries)}")
    if search_plan.stop_condition:
        lines.append(f"stop_condition: {search_plan.stop_condition}")
    lines.append(f"召回后已按 search_plan rerank，进入候选池: {hit_count}")
    return lines


def _human_ranker_preselect_lines(result: JudgePreselectResult) -> list[str]:
    lines = [
        f"Judge 预选: strategy={result.strategy_name} topK={result.top_k_requested} reason={result.top_k_reason}",
        f"通道贡献: {result.channel_contribution}",
        "入选候选:",
    ]
    for index, item in enumerate(result.selected[:10], start=1):
        lines.append(
            f"  {index}. {item.hit.title} ({item.hit.hit_type}, source={item.hit.source}, channels={_hit_channels(item.hit)}, "
            f"final={item.final_score:.3f}, fusion={item.fusion_score:.3f}, feature={item.feature_score:.3f}, "
            f"coverage={item.coverage_bonus:.3f}) new_coverage={item.new_coverage_terms} reasons={item.rank_reasons}"
        )
    if result.missed_coverage_terms:
        lines.append(f"未覆盖项: {', '.join(result.missed_coverage_terms)}")
    if result.remaining_high_potential:
        lines.append("剩余高潜候选:")
        for index, item in enumerate(result.remaining_high_potential[:5], start=1):
            lines.append(f"  {index}. {item.hit.title} ({item.hit.hit_type}, final={item.final_score:.3f})")
    return lines


def _ranked_candidate_summaries(items) -> list[dict[str, object]]:
    return [
        {
            "title": item.hit.title,
            "type": item.hit.hit_type,
            "source": item.hit.source,
            "source_channels": _hit_channels(item.hit),
            "id": item.hit.hit_id,
            "final_score": round(item.final_score, 4),
            "fusion_score": round(item.fusion_score, 4),
            "feature_score": round(item.feature_score, 4),
            "coverage_bonus": round(item.coverage_bonus, 4),
            "redundancy_penalty": round(item.redundancy_penalty, 4),
            "rank_reasons": item.rank_reasons,
            "coverage_terms": item.coverage_terms,
            "new_coverage_terms": item.new_coverage_terms,
            "matched_terms": item.hit.matched_terms,
            "matched_fields": item.hit.matched_fields,
            "evidence_refs": item.hit.evidence_refs[:3],
        }
        for item in items
    ]


def _preselect_summary(result: JudgePreselectResult, *, raw_candidates: int) -> dict[str, Any]:
    return {
        "raw_candidates": raw_candidates,
        "top_k_requested": result.top_k_requested,
        "top_k_reason": result.top_k_reason,
        "strategy": result.strategy_name,
        "channel_contribution": result.channel_contribution,
        "missed_coverage_terms": result.missed_coverage_terms,
        "selected": _ranked_candidate_summaries(result.selected),
    }


def _case_summary(
    *,
    query: str,
    observations: list[RetrievalToolResult],
    tool_traces: list[AgenticToolTrace],
    working_set: RetrievalWorkingSet,
    hits: list[RetrievalHit],
    preselect_summaries: list[dict[str, Any]],
    stop_reason: str,
    stop_verification: Any,
    repository,
) -> dict[str, Any]:
    search_steps = [item.step for item in observations if item.tool == "search"]
    query_rewrites: list[str] = []
    query_modes: list[dict[str, object]] = []
    for step in search_steps:
        payload = step.input or {}
        query_rewrites.extend(str(item) for item in payload.get("query_rewrites") or [] if str(item).strip())
        query_modes.extend(item for item in payload.get("query_modes") or [] if isinstance(item, dict))
    accepted = _judgement_summaries(
        working_set.accepted_candidates,
        hits,
        decisions={"keep"},
        dedupe_by_id=True,
    )
    dropped = _judgement_summaries(
        working_set.dropped_candidates,
        hits,
        decisions={"drop"},
        dedupe_by_id=True,
    )
    background = _judgement_summaries(
        working_set.background_candidates,
        hits,
        decisions={"weak_keep"},
        dedupe_by_id=True,
    )
    opened_evidence = [
        item for item in working_set.evidence_refs if item in set(working_set.opened_windows)
    ]
    return {
        "query": query,
        "query_rewrites_executed": bool(query_rewrites),
        "query_rewrites": _ordered_unique(query_rewrites),
        "query_modes": query_modes,
        "channel_contribution": _aggregate_channel_contribution(preselect_summaries),
        "preselect": preselect_summaries[-1] if preselect_summaries else {},
        "judge": {
            "keep": len(accepted),
            "weak_keep": len(background),
            "drop": len(dropped),
            "accepted": accepted[:8],
            "dropped": dropped[:8],
        },
        "opened_evidence_refs": opened_evidence,
        "opened_evidence": _evidence_ref_summaries(opened_evidence, repository=repository),
        "stop": {
            "reason": stop_reason,
            "satisfied": bool(getattr(stop_verification, "satisfied", False)),
            "missing_reasons": list(getattr(stop_verification, "missing_reasons", []) or []),
            "coverage_terms": list(getattr(stop_verification, "coverage_terms", []) or []),
            "missing_coverage_terms": list(getattr(stop_verification, "missing_coverage_terms", []) or []),
            "required_answer_count": int(getattr(stop_verification, "required_answer_count", 0) or 0),
            "answer_candidate_ids": list(getattr(stop_verification, "answer_candidate_ids", []) or []),
            "budget_stop": stop_reason == "llm_budget_exhausted_stop_condition_unmet",
        },
        "tool_traces": [item.model_dump(mode="json") for item in tool_traces],
    }


def _aggregate_channel_contribution(preselect_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    if not preselect_summaries:
        return {}
    aggregate = {
        "pool_counts": {},
        "selected_counts": {},
        "keyword_only_selected": 0,
        "semantic_only_added_selected": 0,
        "graph_confirmed_selected": 0,
        "multi_channel_selected": 0,
    }
    for summary in preselect_summaries:
        contribution = summary.get("channel_contribution") or {}
        for key in ("pool_counts", "selected_counts"):
            counts = contribution.get(key) or {}
            for channel, count in counts.items():
                aggregate[key][channel] = aggregate[key].get(channel, 0) + int(count or 0)
        for key in (
            "keyword_only_selected",
            "semantic_only_added_selected",
            "graph_confirmed_selected",
            "multi_channel_selected",
        ):
            aggregate[key] += int(contribution.get(key) or 0)
    return aggregate


def _human_case_summary_lines(summary: dict[str, Any]) -> list[str]:
    lines = [
        f"查询: {summary.get('query')}",
        f"query_rewrites: {'已执行' if summary.get('query_rewrites_executed') else '未执行'} {summary.get('query_rewrites') or []}",
        f"通道贡献: {summary.get('channel_contribution') or {}}",
    ]
    preselect = summary.get("preselect") or {}
    if preselect:
        lines.append(
            f"30->topK: raw={preselect.get('raw_candidates')} topK={preselect.get('top_k_requested')} "
            f"reason={preselect.get('top_k_reason')}"
        )
        for index, item in enumerate((preselect.get("selected") or [])[:8], start=1):
            lines.append(
                f"  {index}. {item.get('title')} ({item.get('type')}, source={item.get('source')}, "
                f"final={item.get('final_score')}, new_coverage={item.get('new_coverage_terms')})"
            )
    judge = summary.get("judge") or {}
    lines.append(
        f"judge: keep={judge.get('keep', 0)} weak_keep={judge.get('weak_keep', 0)} drop={judge.get('drop', 0)}"
    )
    opened = summary.get("opened_evidence") or []
    if opened:
        lines.append("opened evidence:")
        for index, item in enumerate(opened[:5], start=1):
            lines.append(f"  {index}. {item.get('source') or item.get('id')}: {item.get('excerpt')}")
    stop = summary.get("stop") or {}
    lines.append(
        f"stop: reason={stop.get('reason')} satisfied={stop.get('satisfied')} "
        f"missing={stop.get('missing_reasons') or []} missing_coverage={stop.get('missing_coverage_terms') or []}"
    )
    return lines


def _human_candidate_line(item: dict[str, object]) -> str:
    title = str(item.get("title") or item.get("id") or "")
    role = str(item.get("role") or "")
    score = item.get("score")
    reason = str(item.get("reason") or "")
    candidate_type = str(item.get("type") or "")
    score_text = f"{float(score):.2f}" if isinstance(score, (int, float)) else str(score or "")
    details = ", ".join(part for part in [candidate_type, role, score_text, reason] if part)
    return f"{title} ({details})" if details else title


def _looks_like_internal_id(value: str) -> bool:
    return value.startswith(("kg:", "kg_", "kg_edge:", "kg_ev:", "kg_wiki:"))


def _pending_expand_candidate_summaries(
    working_set: RetrievalWorkingSet,
    hits: list[RetrievalHit],
) -> list[dict[str, object]]:
    hit_by_id = {hit.hit_id: hit for hit in hits}
    opened = set(working_set.opened_windows)
    pending: list[dict[str, object]] = []
    for judgement in working_set.accepted_candidates:
        if judgement.decision != "keep" or not judgement.can_expand_graph:
            continue
        hit = hit_by_id.get(judgement.candidate_id)
        if hit is not None and hit.hit_type == "evidence":
            continue
        if _expand_marker(judgement.candidate_id) in opened:
            continue
        pending.append(
            {
                "title": hit.title if hit is not None else judgement.candidate_id,
                "type": hit.hit_type if hit is not None else "",
                "role": judgement.role,
                "decision": judgement.decision,
                "score": judgement.relevance_score,
                "expand": judgement.can_expand_graph,
                "reason": judgement.reason_code or judgement.reason,
                "evidence_refs": hit.evidence_refs[:2] if hit is not None else [],
                "id": judgement.candidate_id,
            }
        )
    return pending


def _expand_marker(candidate_id: str) -> str:
    return f"candidate_expand:{candidate_id}"


def _build_candidate_context_packages(
    hits: list[RetrievalHit],
    *,
    registry: RetrievalToolRegistry,
    query: str,
    anchor: QueryAnchor,
    search_plan: RetrievalSearchPlan | None = None,
    evidence_backfill_limit: int,
) -> list[CandidateContextPackage]:
    if not hits:
        return []
    adapter_name = registry.options.adapter_name
    repository = registry.runtime.repository
    with profile_span("agentic.candidate_package.preload_nodes", adapter=adapter_name):
        nodes_by_id = {node.node_id: node for node in repository.list_nodes(adapter_name)}
    with profile_span("agentic.candidate_package.preload_edges", adapter=adapter_name):
        all_edges = repository.list_edges(adapter_name)
    edges_by_id = {edge.edge_id: edge for edge in all_edges}
    edges_by_node: dict[str, list] = {}
    for edge in all_edges:
        if not edge.evidence_ids:
            continue
        edges_by_node.setdefault(edge.source_node_id, []).append(edge)
        edges_by_node.setdefault(edge.target_node_id, []).append(edge)
    evidence_cache = _preload_evidence_cache(repository, adapter_name)

    packages: list[CandidateContextPackage] = []
    for hit in hits:
        support_edges = _supporting_edges_for_hit(
            hit,
            edges_by_node=edges_by_node,
            edges_by_id=edges_by_id,
            nodes_by_id=nodes_by_id,
            query=query,
            anchor=anchor,
        )
        evidence_refs = _ranked_evidence_refs(
            hit,
            support_edges=support_edges,
            repository=repository,
            evidence_cache=evidence_cache,
            query=query,
            anchor=anchor,
            limit=evidence_backfill_limit,
        )
        candidate = hit if evidence_refs == hit.evidence_refs else hit.model_copy(update={"evidence_refs": evidence_refs})
        packages.append(
            CandidateContextPackage(
                candidate=candidate,
                supporting_edges=support_edges[:3],
                supporting_evidence_excerpt=_supporting_evidence_excerpts(
                    evidence_refs,
                    repository=repository,
                    evidence_cache=evidence_cache,
                    query=query,
                    anchor=anchor,
                ),
                why_recalled=_why_recalled(candidate, support_edges),
                search_plan=search_plan.model_dump(mode="json") if search_plan is not None else {},
            )
        )
    return packages


def _anchor_with_search_plan(anchor: QueryAnchor, search_plan: RetrievalSearchPlan) -> QueryAnchor:
    if not _search_plan_has_content(search_plan):
        return anchor
    inferred_hints = [
        *anchor.inferred_hints,
        *[
            AnchorHint(
                text=value,
                hint_type="topic",
                strength="inferred",
                confidence=0.75,
                source="llm",
            )
            for value in [*search_plan.answer_targets, *search_plan.expected_evidence]
            if value
        ],
        *[
            AnchorHint(
                text=value,
                hint_type="relation",
                strength="inferred",
                confidence=0.75,
                source="llm",
            )
            for value in search_plan.relation_intents
            if value
        ],
    ]
    return anchor.model_copy(
        update={
            "negative_boundaries": _ordered_unique(
                [*anchor.negative_boundaries, *search_plan.negative_boundaries]
            ),
            "relation_intents": _ordered_unique(
                [*anchor.relation_intents, *search_plan.relation_intents]
            ),
            "inferred_hints": _dedupe_anchor_hints(inferred_hints),
        }
    )


def _search_plan_has_content(search_plan: RetrievalSearchPlan) -> bool:
    return any(
        [
            search_plan.answer_targets,
            search_plan.negative_boundaries,
            search_plan.expected_evidence,
            search_plan.relation_intents,
            search_plan.stop_condition,
        ]
    )


def _supporting_edges_for_hit(
    hit: RetrievalHit,
    *,
    edges_by_node: dict[str, list],
    edges_by_id: dict[str, Any],
    nodes_by_id: dict[str, Any],
    query: str,
    anchor: QueryAnchor,
) -> list[SupportingEdgeContext]:
    raw_edges = []
    for node_id in hit.node_refs:
        raw_edges.extend(edges_by_node.get(node_id, []))
    for edge_id in hit.edge_refs:
        edge = edges_by_id.get(edge_id)
        if edge is not None:
            raw_edges.append(edge)
    seen: set[str] = set()
    contexts: list[SupportingEdgeContext] = []
    for edge in raw_edges:
        if edge.edge_id in seen:
            continue
        seen.add(edge.edge_id)
        source = nodes_by_id.get(edge.source_node_id)
        target = nodes_by_id.get(edge.target_node_id)
        context = SupportingEdgeContext(
            edge_id=edge.edge_id,
            relation_type=edge.relation_type,
            source_node_id=edge.source_node_id,
            target_node_id=edge.target_node_id,
            source_name=getattr(source, "canonical_name", "") if source is not None else "",
            target_name=getattr(target, "canonical_name", "") if target is not None else "",
            evidence_refs=list(edge.evidence_ids or []),
            score=_support_score(
                " ".join(
                    [
                        edge.relation_type,
                        edge.source_node_id,
                        edge.target_node_id,
                        getattr(source, "canonical_name", "") if source is not None else "",
                        getattr(target, "canonical_name", "") if target is not None else "",
                        " ".join(edge.evidence_ids or []),
                    ]
                ),
                query=query,
                anchor=anchor,
            ),
        )
        contexts.append(context)
    return sorted(contexts, key=lambda item: (-item.score, item.edge_id))


def _ranked_evidence_refs(
    hit: RetrievalHit,
    *,
    support_edges: list[SupportingEdgeContext],
    repository,
    evidence_cache: dict[str, Any | None],
    query: str,
    anchor: QueryAnchor,
    limit: int,
) -> list[str]:
    if limit <= 0:
        return list(hit.evidence_refs)
    evidence_ids = _ordered_unique(
        [
            *hit.evidence_refs,
            *(evidence_id for edge in support_edges for evidence_id in edge.evidence_refs),
        ]
    )
    scored: list[tuple[float, str]] = []
    for evidence_id in evidence_ids:
        evidence = _cached_evidence(repository, evidence_cache, evidence_id)
        text = _evidence_text(evidence) if evidence is not None else evidence_id
        base = 1.0 if evidence_id in hit.evidence_refs else 0.0
        scored.append((base + _support_score(text, query=query, anchor=anchor), evidence_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [evidence_id for _, evidence_id in scored[:limit]]


def _supporting_evidence_excerpts(
    evidence_ids: list[str],
    *,
    repository,
    evidence_cache: dict[str, Any | None],
    query: str,
    anchor: QueryAnchor,
) -> list[SupportingEvidenceExcerpt]:
    excerpts: list[SupportingEvidenceExcerpt] = []
    for evidence_id in evidence_ids:
        evidence = _cached_evidence(repository, evidence_cache, evidence_id)
        if evidence is None:
            excerpts.append(SupportingEvidenceExcerpt(evidence_id=evidence_id))
            continue
        text = _evidence_text(evidence)
        excerpts.append(
            SupportingEvidenceExcerpt(
                evidence_id=evidence_id,
                source_type=str(getattr(evidence, "source_type", "") or ""),
                source_id=str(getattr(evidence, "source_id", "") or ""),
                excerpt=_clip(text, 260),
                score=_support_score(text, query=query, anchor=anchor),
            )
        )
    return excerpts


def _preload_evidence_cache(repository, adapter_name: str) -> dict[str, Any | None]:
    try:
        with profile_span("agentic.candidate_package.preload_evidence", adapter=adapter_name):
            return {
                evidence.evidence_id: evidence
                for evidence in repository.list_evidence(adapter_name)
            }
    except AttributeError:
        return {}


def _cached_evidence(repository, cache: dict[str, Any | None], evidence_id: str):
    if evidence_id not in cache:
        cache[evidence_id] = repository.get_evidence(evidence_id)
    return cache[evidence_id]


def _support_score(text: str, *, query: str, anchor: QueryAnchor) -> float:
    haystack = text.lower()
    terms = _anchor_scoring_terms(query, anchor)
    if not terms:
        return 0.0
    matched = 0
    for term in terms:
        if term and term.lower() in haystack:
            matched += 1
    return matched / len(terms)


def _anchor_scoring_terms(query: str, anchor: QueryAnchor) -> list[str]:
    values = [
        *(item.text for item in anchor.core_entities),
        *(item.text for item in anchor.core_topics),
        *(item.value for item in anchor.guard_constraints if item.must_preserve),
    ]
    for token in query.replace("，", " ").replace(",", " ").split():
        if len(token.strip()) >= 2:
            values.append(token.strip())
    return _ordered_unique(value for value in values if value)


def _evidence_text(evidence) -> str:
    if evidence is None:
        return ""
    if getattr(evidence, "content", None):
        return str(evidence.content)
    payload = getattr(evidence, "payload", {}) or {}
    if isinstance(payload, dict):
        parts = [
            str(payload.get(name) or "")
            for name in ("title", "summary", "text", "content", "source_name")
            if payload.get(name)
        ]
        if parts:
            return "\n".join(parts)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _why_recalled(hit: RetrievalHit, support_edges: list[SupportingEdgeContext]) -> list[str]:
    reasons = [f"channel:{hit.source}", f"type:{hit.hit_type}"]
    if hit.evidence_refs:
        reasons.append("has_evidence")
    if support_edges:
        reasons.append("has_supporting_edges")
    if hit.node_refs:
        reasons.append("node_anchor")
    if hit.edge_refs:
        reasons.append("edge_anchor")
    return reasons


def _preselect_judge_hits(
    hits: list[RetrievalHit],
    constraints: AgenticRetrievalConstraints,
    anchor: QueryAnchor,
    *,
    query: str,
    search_plan: RetrievalSearchPlan,
) -> JudgePreselectResult:
    return judge_preselect(
        hits,
        query=query,
        anchor=anchor,
        search_plan=search_plan,
        top_k_simple=constraints.judge_top_k,
        top_k_complex=constraints.judge_top_k_complex,
        top_k_max=constraints.judge_top_k_max,
    )


def _is_simple_anchor(anchor: QueryAnchor) -> bool:
    strong_entities = [item for item in anchor.core_entities if item.strength == "strong"]
    strong_constraints = [item for item in anchor.guard_constraints if item.must_preserve]
    return bool(strong_entities or strong_constraints or anchor.source_hints or anchor.time_hints)


def _judge_priority(hit: RetrievalHit, anchor: QueryAnchor) -> tuple[int, int, float, str]:
    text = f"{hit.title}\n{hit.snippet}\n{hit.source}\n{hit.hit_type}".lower()
    relation_intents = set(anchor.relation_intents)
    if hit.hit_type == "node" and _looks_like_event_hit(text):
        type_rank = 0
    elif hit.hit_type == "evidence":
        type_rank = 1
    elif hit.hit_type == "node":
        type_rank = 2
    elif hit.hit_type == "edge":
        type_rank = _edge_judge_rank(text, relation_intents)
    elif hit.hit_type == "path":
        type_rank = 6
    elif hit.hit_type == "wiki":
        type_rank = 7
    elif hit.hit_type == "semantic_hybrid":
        type_rank = 8
    else:
        type_rank = 9
    evidence_rank = 0 if hit.evidence_refs else 1
    return (type_rank, evidence_rank, -float(hit.score or 0.0), hit.hit_id)


def _looks_like_event_hit(text: str) -> bool:
    return any(marker in text for marker in ('"type": "event"', '"type":"event"', "类型：event", "type：event"))


def _edge_judge_rank(text: str, relation_intents: set[str]) -> int:
    if "belongs_to" in text:
        return 9
    if "impact" in relation_intents and "affects" in text:
        return 3
    if "benefits_from" in text:
        return 4
    if "mentions" in text:
        return 5
    return 6


def _should_auto_open(
    working_set: RetrievalWorkingSet,
    constraints: AgenticRetrievalConstraints,
) -> bool:
    return len(_auto_open_evidence_ids(working_set)) >= constraints.min_keep_evidence_to_auto_open


def _auto_open_evidence_ids(working_set: RetrievalWorkingSet) -> list[str]:
    opened = set(working_set.opened_windows)
    return [item for item in working_set.evidence_refs if item not in opened]


def _should_auto_stop(
    working_set: RetrievalWorkingSet,
    constraints: AgenticRetrievalConstraints,
    *,
    search_plan: RetrievalSearchPlan | None = None,
    new_answer_ids: list[str] | None = None,
    hits: list[RetrievalHit] | None = None,
) -> bool:
    return verify_stop_condition(
        working_set,
        constraints,
        search_plan=search_plan,
        new_answer_ids=new_answer_ids,
        hits=hits or [],
    ).satisfied


def _required_answer_count(search_plan: RetrievalSearchPlan | None) -> int:
    return _verify_required_answer_count(search_plan)


def _requires_new_answer(search_plan: RetrievalSearchPlan | None) -> bool:
    return _verify_requires_new_answer(search_plan)


def _coverage_terms(search_plan: RetrievalSearchPlan | None) -> list[str]:
    return _verify_coverage_terms(search_plan)


def _clean_coverage_term(value: str) -> str:
    terms = _verify_coverage_terms(RetrievalSearchPlan(stop_condition=f"覆盖{value}")) if value else []
    return terms[0] if terms else ""


def _missing_coverage_terms(
    search_plan: RetrievalSearchPlan | None,
    working_set: RetrievalWorkingSet,
    hits: list[RetrievalHit],
) -> list[str]:
    return _verify_missing_coverage_terms(search_plan, working_set, hits)


def _stop_context_text(working_set: RetrievalWorkingSet, hits: list[RetrievalHit]) -> str:
    parts: list[str] = []
    accepted_ids = {item.candidate_id for item in working_set.accepted_candidates if item.decision == "keep"}
    evidence_refs = set(working_set.evidence_refs)
    for hit in hits:
        if hit.hit_id in accepted_ids or hit.hit_id in evidence_refs or evidence_refs.intersection(hit.evidence_refs):
            parts.extend([hit.hit_id, hit.title, hit.snippet, " ".join(hit.evidence_refs)])
    parts.extend(accepted_ids)
    parts.extend(evidence_refs)
    return "\n".join(str(item or "") for item in parts).lower()


def _answer_candidate_ids(
    working_set: RetrievalWorkingSet,
    hits: list[RetrievalHit] | None = None,
) -> list[str]:
    return _verify_answer_candidate_ids(working_set, hits or [])


def _normalized_answer_candidate_id(candidate_id: str, hits: list[RetrievalHit]) -> str:
    return _verify_normalized_answer_candidate_id(candidate_id, hits)


def _parent_candidate_id_for_evidence(evidence_id: str, hits: list[RetrievalHit]) -> str:
    return _verify_parent_candidate_id_for_evidence(evidence_id, hits)


def _controller_budget_for_query(
    query: str,
    search_plan: RetrievalSearchPlan | None,
    constraints: AgenticRetrievalConstraints,
) -> int:
    text = "\n".join(
        [
            query,
            " ".join((search_plan.answer_targets if search_plan else []) or []),
            search_plan.stop_condition if search_plan else "",
        ]
    )
    if any(marker in text for marker in ("哪些", "什么", "列表", "清单", "覆盖", "分别", "资产和行业")):
        return max(constraints.max_llm_calls_normal_query, constraints.max_llm_calls_complex_query)
    return constraints.max_llm_calls_normal_query


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dedupe_anchor_hints(values: list[AnchorHint]) -> list[AnchorHint]:
    result: list[AnchorHint] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        key = (value.text, value.hint_type)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
