"""Tests for the agentic retrieval controller."""

from __future__ import annotations

import pytest

from src.domain.knowledge.agentic_retrieval import (
    AgenticRetrievalConstraints,
    AgenticRetrievalController,
    RetrievalControllerDecision,
    RetrievalSearchPlan,
    RetrievalWorkingSet,
    _answer_candidate_ids,
    _coverage_terms,
    _missing_coverage_terms,
    _should_auto_stop,
)
from src.domain.knowledge.retrieval_anchor import build_guarded_query_anchor
from src.domain.knowledge.retrieval import (
    HybridRetrievalRuntime,
    RetrievalHit,
    RetrievalOptions,
    SemanticHybridRetriever,
)
from src.domain.knowledge.retrieval_judge import CandidateJudgement
from src.domain.knowledge.retrieval_tools import RetrievalToolRegistry

from tests.unit.knowledge.test_retrieval_tools import _Repo, _registry


class _ScriptedStrategy:
    def __init__(self, decisions: list[RetrievalControllerDecision]):
        self.decisions = decisions

    async def next_decision(self, *, query, working_set, observations, constraints):
        if observations:
            return RetrievalControllerDecision(next_tool="stop", stop_reason="evidence_sufficient")
        return self.decisions[0]


class _SearchThenOpenStrategy:
    async def next_decision(self, *, query, working_set, observations, constraints):
        if not observations:
            return RetrievalControllerDecision(next_tool="search", query_rewrites=[query])
        if observations[-1].tool == "search" and working_set.evidence_refs:
            return RetrievalControllerDecision(
                next_tool="open",
                target_evidence_refs=working_set.evidence_refs,
                expected_gain="context_window",
            )
        return RetrievalControllerDecision(next_tool="stop", stop_reason="evidence_sufficient")


class _SearchThenStopStrategy:
    async def next_decision(self, *, query, working_set, observations, constraints):
        if not observations:
            return RetrievalControllerDecision(next_tool="search", query_rewrites=[query])
        return RetrievalControllerDecision(next_tool="stop", stop_reason="evidence_sufficient")


class _ManySemanticHits(SemanticHybridRetriever):
    enabled = True
    backend_name = "milvus"

    async def search(self, query: str, options: RetrievalOptions):
        return [
            RetrievalHit(
                hit_id=f"kg_chunk:{index}",
                hit_type="semantic_hybrid",
                title=f"candidate {index}",
                snippet="宁德时代 海外产能" if index % 2 == 0 else "其他候选",
                source="semantic_hybrid",
                score=float(100 - index),
                evidence_refs=[f"kg_ev:financial:news:{index}"],
            )
            for index in range(30)
        ]


class _RecordingSemanticHybrid(_ManySemanticHits):
    def __init__(self):
        self.queries: list[str] = []

    async def search(self, query: str, options: RetrievalOptions):
        self.queries.append(query)
        return await super().search(query, options)


class _ManyEvidenceRepo(_Repo):
    def get_evidence(self, evidence_id: str):
        if evidence_id.startswith("kg_ev:financial:news:"):
            index = int(evidence_id.rsplit(":", 1)[-1])
            return self.evidence.model_copy(
                update={
                    "evidence_id": evidence_id,
                    "source_id": evidence_id.rsplit(":", 1)[-1],
                    "content": (
                        f"宁德时代海外产能候选 {evidence_id}"
                        if index % 2 == 0
                        else f"其他候选 {evidence_id}"
                    ),
                }
            )
        return super().get_evidence(evidence_id)


class _RecordingJudge:
    def __init__(self):
        self.seen_batches: list[list[RetrievalHit]] = []

    async def judge(self, *, query, anchor, hits):
        self.seen_batches.append(list(hits))
        return [
            CandidateJudgement(
                candidate_id=hit.hit_id,
                decision="keep",
                relevance_score=0.9,
                can_expand_graph=True,
                anchor_coverage={"overall": 0.9},
                topic_drift=False,
                reason="test_keep",
                judge_source="llm",
            )
            for hit in hits
        ]


class _DropSemanticJudge:
    async def judge(self, *, query, anchor, hits):
        return [
            CandidateJudgement(
                candidate_id=hit.hit_id,
                decision="drop" if _drop_test_hit(hit) else "keep",
                relevance_score=0.0 if _drop_test_hit(hit) else 0.9,
                can_expand_graph=not _drop_test_hit(hit),
                anchor_coverage={"overall": 0.0 if _drop_test_hit(hit) else 0.9},
                topic_drift=_drop_test_hit(hit),
                reason="test_drop_noise" if _drop_test_hit(hit) else "test_keep",
                judge_source="llm",
            )
            for hit in hits
        ]


def _answer(candidate_id: str) -> CandidateJudgement:
    return CandidateJudgement(
        candidate_id=candidate_id,
        decision="keep",
        role="answer",
        relevance_score=0.9,
        can_expand_graph=True,
        anchor_coverage={"overall": 0.9},
        topic_drift=False,
        reason="test_answer",
        reason_code="direct_answer",
        judge_source="llm",
    )


def _drop_test_hit(hit: RetrievalHit) -> bool:
    return "其他候选" in hit.snippet or hit.hit_id.endswith(":news:0")


@pytest.mark.asyncio
async def test_agentic_controller_records_tool_trace_and_evidence_refs() -> None:
    controller = AgenticRetrievalController(
        _registry(),
        _ScriptedStrategy(
            [
                RetrievalControllerDecision(
                    next_tool="search",
                    query_rewrites=["宁德时代 海外产能"],
                )
            ]
        ),
    )

    result = await controller.run("宁德时代最近受哪些事件影响")

    assert result.trace.mode == "agentic_arag"
    assert result.trace.agentic_enabled is True
    assert result.trace.milvus_enabled is True
    assert result.trace.channels_used == ["search", "open"]
    assert result.evidence_refs == ["kg_ev:financial:news:1"]
    assert result.stop_reason == "evidence_sufficient"
    assert result.trace.controller_decisions[-1]["auto_action"] == "open"


@pytest.mark.asyncio
async def test_agentic_controller_can_apply_scoped_search_decision() -> None:
    controller = AgenticRetrievalController(
        _registry(),
        _ScriptedStrategy(
            [
                RetrievalControllerDecision(
                    next_tool="scoped_search",
                    query_rewrites=["海外产能"],
                    target_candidate_ids=["kg:financial:stock:300750"],
                )
            ]
        ),
    )

    result = await controller.run("宁德时代最近受哪些事件影响")

    assert result.trace.steps[0].tool == "scoped_search"
    assert "scoped_search" in result.trace.channels_used
    assert result.trace.steps[0].input["search_diagnostics"]["scope"]["node_ids"] == [
        "kg:financial:stock:300750",
        "kg:financial:event:1",
    ]


@pytest.mark.asyncio
async def test_agentic_controller_does_not_invent_forced_low_level_calls() -> None:
    result = await AgenticRetrievalController(
        _registry(),
        _SearchThenStopStrategy(),
    ).run("宁德时代 300750 最近受哪些事件影响")

    assert result.trace.channels_used == ["search", "open"]
    assert result.evidence_refs == ["kg_ev:financial:news:1"]
    assert result.stop_reason == "evidence_sufficient"


@pytest.mark.asyncio
async def test_agentic_controller_stops_on_tool_call_budget() -> None:
    class _NeverStopStrategy:
        async def next_decision(self, *, query, working_set, observations, constraints):
            return RetrievalControllerDecision(next_tool="search", query_rewrites=[query])

    controller = AgenticRetrievalController(
        _registry(),
        _NeverStopStrategy(),
        constraints=AgenticRetrievalConstraints(max_turns=5, max_tool_calls=1),
    )

    result = await controller.run("宁德时代")

    assert len(result.trace.steps) == 1
    assert result.stop_reason == "max_tool_calls"
    assert result.trace.channels_used == ["search"]


def test_agentic_controller_uses_registry_without_keyword_tool() -> None:
    registry = _registry()

    assert "keyword_search" not in RetrievalToolRegistry.available_tools
    assert "keyword_search" not in registry.available_tools


def test_auto_stop_counts_distinct_answer_candidates() -> None:
    working_set = RetrievalWorkingSet(
        query_anchor=build_guarded_query_anchor("宁德时代 300750 最近受哪些事件影响", known_nodes=[])
    )
    working_set.evidence_refs = ["kg_ev:financial:news:1"]
    working_set.opened_windows = ["kg_ev:financial:news:1"]
    working_set.accepted_candidates = [
        _answer("kg:financial:event:1"),
        _answer("kg:financial:event:2"),
        _answer("kg:financial:event:1"),
    ]

    assert _answer_candidate_ids(working_set) == [
        "kg:financial:event:1",
        "kg:financial:event:2",
    ]
    assert not _should_auto_stop(
        working_set,
        AgenticRetrievalConstraints(),
        search_plan=RetrievalSearchPlan(stop_condition="找到至少3个明确影响宁德时代的近期事件"),
    )


def test_auto_stop_requires_new_answer_for_incremental_stop_condition() -> None:
    working_set = RetrievalWorkingSet(
        query_anchor=build_guarded_query_anchor("宁德时代 300750 最近受哪些事件影响", known_nodes=[])
    )
    working_set.evidence_refs = ["kg_ev:financial:news:1"]
    working_set.opened_windows = ["kg_ev:financial:news:1"]
    working_set.accepted_candidates = [
        _answer("kg:financial:event:1"),
        _answer("kg:financial:event:2"),
    ]
    plan = RetrievalSearchPlan(stop_condition="至少再发现1-2个与宁德时代直接相关的近期事件")

    assert not _should_auto_stop(
        working_set,
        AgenticRetrievalConstraints(),
        search_plan=plan,
        new_answer_ids=[],
    )
    assert _should_auto_stop(
        working_set,
        AgenticRetrievalConstraints(),
        search_plan=plan,
        new_answer_ids=["kg:financial:event:2"],
    )


def test_auto_stop_requires_enumerated_coverage_terms() -> None:
    working_set = RetrievalWorkingSet(
        query_anchor=build_guarded_query_anchor("中东冲突影响哪些资产和行业", known_nodes=[])
    )
    working_set.evidence_refs = ["kg_ev:financial:news:middle_east"]
    working_set.opened_windows = ["kg_ev:financial:news:middle_east"]
    working_set.accepted_candidates = [
        _answer("kg:financial:event:oil_gold"),
        _answer("kg:financial:industry:airline"),
    ]
    hits = [
        RetrievalHit(
            hit_id="kg:financial:event:oil_gold",
            hit_type="node",
            title="中东冲突推升原油和黄金",
            snippet="中东冲突影响原油、黄金和航空运输。",
            source="entity_resolve",
            evidence_refs=["kg_ev:financial:news:middle_east"],
        )
    ]
    plan = RetrievalSearchPlan(
        stop_condition="至少覆盖股票、债券、商品、货币四类资产，以及能源、运输、旅游、保险等行业的关键影响"
    )

    assert _coverage_terms(plan) == ["股票", "债券", "商品", "货币", "能源", "运输", "旅游", "保险"]
    assert _missing_coverage_terms(plan, working_set, hits) == [
        "股票",
        "债券",
        "商品",
        "货币",
        "能源",
        "旅游",
        "保险",
    ]
    assert not _should_auto_stop(
        working_set,
        AgenticRetrievalConstraints(),
        search_plan=plan,
        hits=hits,
    )


def test_answer_candidate_ids_map_evidence_to_parent_candidate() -> None:
    working_set = RetrievalWorkingSet(
        query_anchor=build_guarded_query_anchor("低利率环境利好什么资产和行业", known_nodes=[])
    )
    working_set.accepted_candidates = [_answer("kg_ev:financial:policy:low_rate")]
    hits = [
        RetrievalHit(
            hit_id="kg:financial:policy:low_rate",
            hit_type="node",
            title="低利率政策",
            snippet="低利率环境提升成长资产估值。",
            source="entity_resolve",
            evidence_refs=["kg_ev:financial:policy:low_rate"],
        )
    ]

    assert _answer_candidate_ids(working_set, hits) == ["kg:financial:policy:low_rate"]


def test_answer_candidate_ids_exclude_edges_even_if_marked_answer() -> None:
    working_set = RetrievalWorkingSet(
        query_anchor=build_guarded_query_anchor("海外工厂投产会带动哪些产业链机会", known_nodes=[])
    )
    working_set.accepted_candidates = [_answer("kg_edge:financial:mentions:1")]

    assert _answer_candidate_ids(working_set) == []


@pytest.mark.asyncio
async def test_agentic_controller_inherits_scores_for_opened_evidence() -> None:
    result = await AgenticRetrievalController(
        _registry(),
        _SearchThenOpenStrategy(),
    ).run("宁德时代")

    evidence_hits = [hit for hit in result.hits if hit.hit_type == "evidence"]
    assert evidence_hits
    assert evidence_hits[0].score != 1.0
    assert result.working_set.opened_windows


@pytest.mark.asyncio
async def test_agentic_controller_limits_judge_candidates_before_llm_judge() -> None:
    judge = _RecordingJudge()
    registry = RetrievalToolRegistry(
        HybridRetrievalRuntime(_ManyEvidenceRepo(), semantic_retriever=_ManySemanticHits()),
        RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=30, max_hits=40),
    )

    result = await AgenticRetrievalController(
        registry,
        _SearchThenStopStrategy(),
        candidate_judge=judge,
        constraints=AgenticRetrievalConstraints(max_tool_calls=1, judge_top_k=12),
    ).run("宁德时代 300750 最近受哪些事件影响")

    assert judge.seen_batches
    assert len(judge.seen_batches[0]) == 12
    first_trace = result.trace.controller_decisions[0]
    assert first_trace["raw_candidate_count"] > first_trace["package_count"]
    assert first_trace["package_count"] == 12


@pytest.mark.asyncio
async def test_agentic_controller_executes_multiple_search_rewrites() -> None:
    semantic = _RecordingSemanticHybrid()
    registry = RetrievalToolRegistry(
        HybridRetrievalRuntime(_ManyEvidenceRepo(), semantic_retriever=semantic),
        RetrievalOptions(
            adapter_name="financial",
            semantic_hybrid_limit=30,
            keyword_limit=5,
            graph_limit=5,
            wiki_limit=5,
            max_hits=20,
        ),
    )
    controller = AgenticRetrievalController(
        registry,
        _ScriptedStrategy(
            [
                RetrievalControllerDecision(
                    next_tool="search",
                    query_rewrites=["宁德时代 海外产能", "300750 事件影响"],
                    search_plan=RetrievalSearchPlan(
                        answer_targets=["宁德时代事件"],
                        negative_boundaries=["无关军事新闻"],
                        expected_evidence=["事件影响证据"],
                        relation_intents=["impact"],
                    ),
                )
            ]
        ),
        constraints=AgenticRetrievalConstraints(max_tool_calls=1),
    )

    result = await controller.run("宁德时代最近受哪些事件影响")

    assert result.trace.steps[0].input["query_rewrites"] == ["宁德时代 海外产能", "300750 事件影响"]
    assert result.trace.steps[0].warning == "multi_query_search:2;semantic_queries:2"
    assert result.trace.steps[0].input["search_plan"]["answer_targets"] == ["宁德时代事件"]
    assert [
        {key: item[key] for key in ("query", "mode", "semantic_enabled")}
        for item in result.trace.steps[0].input["query_modes"]
    ] == [
        {"query": "宁德时代 海外产能", "mode": "full", "semantic_enabled": True},
        {"query": "300750 事件影响", "mode": "full", "semantic_enabled": True},
    ]
    assert result.trace.steps[0].input["query_modes"][0]["hit_count"] > 0
    assert semantic.queries == ["宁德时代 海外产能", "事件影响"]


@pytest.mark.asyncio
async def test_agentic_controller_keeps_dropped_candidates_out_of_context() -> None:
    registry = RetrievalToolRegistry(
        HybridRetrievalRuntime(_ManyEvidenceRepo(), semantic_retriever=_ManySemanticHits()),
        RetrievalOptions(adapter_name="financial", semantic_hybrid_limit=30, max_hits=40),
    )
    result = await AgenticRetrievalController(
        registry,
        _SearchThenStopStrategy(),
        candidate_judge=_DropSemanticJudge(),
        constraints=AgenticRetrievalConstraints(max_tool_calls=1),
    ).run("宁德时代 300750 最近受哪些事件影响")

    assert all(hit.hit_type != "semantic_hybrid" for hit in result.hits)
    assert any(item["decision"] == "drop" for item in result.trace.candidate_judgements)
    assert result.trace.controller_decisions[0]["drop_count"] >= 1


@pytest.mark.asyncio
async def test_agentic_controller_backfills_evidence_from_kept_node_edges() -> None:
    result = await AgenticRetrievalController(
        _registry(),
        _SearchThenStopStrategy(),
        candidate_judge=_DropSemanticJudge(),
    ).run("宁德时代 300750 最近受哪些事件影响")

    assert result.evidence_refs == ["kg_ev:financial:news:1"]
    assert result.trace.channels_used == ["search", "open"]
    assert result.stop_reason == "evidence_sufficient"
