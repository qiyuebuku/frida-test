from src.domain.knowledge.retrieval_eval import (
    RetrievalEvalMetric,
    RetrievalEvalRun,
    RetrievalLabel,
    RetrievalTraceSnapshot,
    aggregate_eval_metrics,
    build_preselect_eval_metrics,
    evaluate_preselect_quality,
    preselect_evaluation_metric,
    retrieval_query_hash,
)
from src.domain.knowledge.quality import replay_bad_case


def test_retrieval_trace_snapshot_fills_stable_query_hash():
    snapshot = RetrievalTraceSnapshot(
        adapter_name="financial",
        query=" 宁德时代 300750 最近受哪些事件影响 ",
        ranking_snapshot={"selected": ["C1"]},
    )

    assert snapshot.query_hash == retrieval_query_hash(snapshot.query)
    assert len(snapshot.query_hash) == 64
    assert snapshot.ranking_snapshot == {"selected": ["C1"]}


def test_retrieval_quality_records_keep_structured_labels_and_metrics():
    label = RetrievalLabel(
        query="宁德时代 300750 最近受哪些事件影响",
        expected_candidates=[{"id": "kg:financial:event:1", "role": "answer"}],
        expected_answers=[{"title": "海外产能扩张"}],
        coverage_requirements={"answer_count": 3},
    )
    run = RetrievalEvalRun(
        strategy_name="rrf_feature_coverage",
        strategy_version="v1",
        config={"top_k": 12},
    )
    metric = RetrievalEvalMetric(
        run_id=run.run_id,
        case_id="case-1",
        query=label.query,
        metrics={"recall_at_30": 1.0, "preselect_recall": 0.75},
        failure_stage="preselect",
    )

    assert label.expected_candidates[0]["role"] == "answer"
    assert run.status == "running"
    assert metric.metrics["preselect_recall"] == 0.75


def test_evaluate_preselect_quality_reports_misses_and_wasted_slots():
    evaluation = evaluate_preselect_quality(
        case_id="case-1",
        query="宁德时代 300750 最近受哪些事件影响",
        expected_candidate_ids=["event-a", "event-b", "event-c"],
        selected_candidate_ids=["event-a", "wiki-x", "event-b", "edge-y"],
        k=4,
    )
    metric = preselect_evaluation_metric(run_id="run-1", evaluation=evaluation)

    assert evaluation.preselect_recall_at_k == 2 / 3
    assert evaluation.preselect_precision_at_k == 0.5
    assert evaluation.missed_candidate_ids == ["event-c"]
    assert evaluation.wasted_slots_at_k == 2
    assert metric.failure_stage == "preselect"
    assert metric.failure_details["missed_candidate_ids"] == ["event-c"]


def test_build_preselect_eval_metrics_from_snapshot_and_label():
    snapshot = RetrievalTraceSnapshot(
        snapshot_id="snapshot-1",
        adapter_name="financial",
        query="宁德时代 300750 最近受哪些事件影响",
        recall_snapshot={
            "hits": [
                {"hit_id": "event-a", "title": "事件A"},
                {"id": "event-b", "title": "事件B"},
                {"id": "wiki-x", "title": "背景"},
            ]
        },
        ranking_snapshot={
            "selected": [
                {"candidate_id": "event-a"},
                {"candidate_id": "wiki-x"},
                {"candidate_id": "event-b"},
            ]
        },
    )
    label = RetrievalLabel(
        snapshot_id="snapshot-1",
        case_id="case-1",
        query=snapshot.query,
        expected_candidates=[
            {"id": "event-a"},
            {"id": "event-b"},
            {"id": "event-c"},
        ],
    )

    metrics = build_preselect_eval_metrics(
        run_id="run-1",
        snapshots=[snapshot],
        labels=[label],
        k_values=(2, 3),
    )
    aggregate = aggregate_eval_metrics(metrics)

    assert [metric.case_id for metric in metrics] == ["case-1@2", "case-1@3"]
    assert metrics[0].metrics["preselect_recall_at_k"] == 2 / 3
    assert metrics[1].metrics["preselect_recall_at_k"] == 2 / 3
    assert metrics[1].failure_stage == "preselect"
    assert aggregate["case_count"] == 1
    assert aggregate["metric_count"] == 2


def test_bad_case_replay_reports_missing_refs() -> None:
    result = replay_bad_case(
        case_id="case-1",
        query="Alpha",
        expected_refs=["a", "b"],
        actual_refs=["a"],
    )

    assert not result.passed
    assert result.details["missing"] == ["b"]


def test_bad_case_replay_passes_when_expected_refs_are_present() -> None:
    result = replay_bad_case(
        case_id="case-2",
        expected_refs=["a"],
        actual_refs=["a", "b"],
    )

    assert result.passed
