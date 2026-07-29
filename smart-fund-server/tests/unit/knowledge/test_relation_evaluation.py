"""Relation Discovery 标注评测指标测试。"""

from __future__ import annotations

import pytest

from src.domain.knowledge.relation_evaluation import (
    aggregate_relation_evaluation,
    evaluate_relation_case,
    validate_relation_evaluation_dataset,
)


def _case() -> dict:
    return validate_relation_evaluation_dataset(
        {
            "cases": [
                {
                    "case_id": "case-1",
                    "source_card_id": "card:1",
                    "expected_relations": [
                        {
                            "target_card_id": "card:2",
                            "decision_class": "inferred",
                            "relation_kind": "common_driver",
                            "relation_type_contains": ["共同"],
                            "direction_contains": ["需求"],
                            "source_evidence_refs": ["s1"],
                            "target_evidence_refs": ["t1"],
                        }
                    ],
                    "hard_negative_card_ids": ["card:3"],
                }
            ]
        }
    )[0]


def _result() -> dict:
    return {
        "decisions": [
            {
                "source_card_id": "card:1",
                "target_card_id": "card:2",
                "decision_class": "inferred",
                "relation_kind": "common_driver",
                "relation_type": "共同驱动",
                "direction": "需求分别驱动双方",
                "source_evidence_refs": ["s1"],
                "target_evidence_refs": ["t1"],
            }
        ],
        "card_diagnostics": [
            {
                "evaluation_details": {
                    "routes": [
                        {
                            "summary_recalled_ids": ["card:2", "card:3"],
                            "focus_recalled_ids": [],
                            "reranked_ids": ["card:2", "card:3"],
                        }
                    ],
                    "merged_candidate_ids": ["card:2", "card:3"],
                    "selected_candidate_ids": ["card:2", "card:3"],
                    "screened_related_candidate_ids": ["card:2"],
                    "verified_candidate_ids": ["card:2"],
                }
            }
        ],
    }


def test_relation_evaluation_calculates_stage_and_class_metrics() -> None:
    case_result = evaluate_relation_case(_case(), _result())
    summary = aggregate_relation_evaluation([case_result])

    assert case_result["passed"] is True
    assert summary["all_passed"] is True
    assert summary["stage_recall"]["recall"]["recall"] == 1.0
    assert summary["stage_recall"]["final_positive"]["recall"] == 1.0
    assert summary["decision_class_metrics"]["inferred"]["precision"] == 1.0
    assert summary["decision_class_metrics"]["inferred"]["recall"] == 1.0
    assert summary["hard_negative"]["reject_rate"] == 1.0
    assert summary["relation_kind_coverage"]["common_driver"] == 1
    assert "causal_influence" in summary["missing_relation_kinds"]


def test_relation_evaluation_counts_hard_negative_false_positive() -> None:
    result = _result()
    result["decisions"].append(
        {
            "source_card_id": "card:1",
            "target_card_id": "card:3",
            "decision_class": "inferred",
            "relation_kind": "causal_influence",
            "relation_type": "错误关系",
            "direction": "错误方向",
            "source_evidence_refs": ["s1"],
            "target_evidence_refs": ["n1"],
        }
    )
    case_result = evaluate_relation_case(_case(), result)
    summary = aggregate_relation_evaluation([case_result])

    assert case_result["passed"] is False
    assert case_result["false_positive_hard_negative_ids"] == ["card:3"]
    assert summary["decision_class_metrics"]["inferred"]["precision"] == 0.5
    assert summary["hard_negative"]["reject_rate"] == 0.0


def test_relation_evaluation_rejects_unlabeled_positive_case() -> None:
    with pytest.raises(ValueError, match="不能同时为空"):
        validate_relation_evaluation_dataset(
            {
                "cases": [
                    {
                        "case_id": "empty",
                        "source_card_id": "card:1",
                        "expected_relations": [],
                        "hard_negative_card_ids": [],
                    }
                ]
            }
        )
