from src.domain.knowledge.agentic_retrieval import (
    AgenticRetrievalConstraints,
    RetrievalSearchPlan,
    RetrievalWorkingSet,
)
from src.domain.knowledge.retrieval import RetrievalHit
from src.domain.knowledge.retrieval_anchor import build_guarded_query_anchor
from src.domain.knowledge.retrieval_judge import CandidateJudgement
from src.domain.knowledge.retrieval_stop_verifier import (
    answer_candidate_ids,
    coverage_terms,
    missing_coverage_terms,
    verify_stop_condition,
)


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


def test_stop_verifier_rejects_unopened_evidence():
    working_set = RetrievalWorkingSet(
        query_anchor=build_guarded_query_anchor("宁德时代 300750 最近受哪些事件影响", known_nodes=[])
    )
    working_set.evidence_refs = ["kg_ev:financial:news:1"]
    working_set.opened_windows = []
    working_set.accepted_candidates = [_answer("kg:financial:event:1"), _answer("kg:financial:event:2")]

    result = verify_stop_condition(working_set, AgenticRetrievalConstraints())

    assert not result.satisfied
    assert "evidence_not_opened" in result.missing_reasons


def test_stop_verifier_requires_enumerated_coverage_terms():
    working_set = RetrievalWorkingSet(
        query_anchor=build_guarded_query_anchor("中东冲突影响哪些资产和行业", known_nodes=[])
    )
    working_set.evidence_refs = ["kg_ev:financial:news:middle_east"]
    working_set.opened_windows = ["kg_ev:financial:news:middle_east"]
    working_set.accepted_candidates = [_answer("kg:financial:event:oil_gold"), _answer("kg:financial:industry:airline")]
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

    result = verify_stop_condition(
        working_set,
        AgenticRetrievalConstraints(),
        search_plan=plan,
        hits=hits,
    )

    assert coverage_terms(plan) == ["股票", "债券", "商品", "货币", "能源", "运输", "旅游", "保险"]
    assert "missing_coverage_terms" in result.missing_reasons
    assert missing_coverage_terms(plan, working_set, hits) == [
        "股票",
        "债券",
        "商品",
        "货币",
        "能源",
        "旅游",
        "保险",
    ]


def test_stop_verifier_maps_evidence_answer_to_parent_and_excludes_edges():
    working_set = RetrievalWorkingSet(
        query_anchor=build_guarded_query_anchor("低利率环境利好什么资产和行业", known_nodes=[])
    )
    working_set.accepted_candidates = [
        _answer("kg_ev:financial:policy:low_rate"),
        _answer("kg_edge:financial:mentions:1"),
    ]
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

    assert answer_candidate_ids(working_set, hits) == ["kg:financial:policy:low_rate"]


def test_stop_verifier_cleans_noisy_examples_from_coverage_terms():
    plan = RetrievalSearchPlan(
        stop_condition="至少覆盖资产如原油、黄金、货币等，以及具体产业链环节如上游材料"
    )

    assert coverage_terms(plan) == ["原油", "黄金", "货币", "上游材料"]
