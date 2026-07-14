"""Relation Discovery 标注集校验与阶段指标计算。"""

from __future__ import annotations

from typing import Any


RELATION_EVALUATION_STAGES = (
    "recall",
    "rerank",
    "merged",
    "selected",
    "screened",
    "verified",
    "final_positive",
)
RELATION_EVALUATION_KINDS = {
    "same_event",
    "temporal_progression",
    "causal_influence",
    "common_driver",
    "confirmation",
    "contradiction",
    "constraint",
}


def validate_relation_evaluation_dataset(data: dict[str, Any]) -> list[dict[str, Any]]:
    """校验人工标注集，禁止用空案例伪装完成评测。"""

    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("关系评测集 cases 必须是非空数组")
    seen_case_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(cases):
        if not isinstance(raw, dict):
            raise ValueError(f"cases[{index}] 必须是对象")
        case_id = str(raw.get("case_id") or "").strip()
        source_card_id = str(raw.get("source_card_id") or "").strip()
        if not case_id or case_id in seen_case_ids:
            raise ValueError(f"cases[{index}] case_id 缺失或重复: {case_id}")
        if not source_card_id:
            raise ValueError(f"{case_id}: source_card_id 不能为空")
        expected = raw.get("expected_relations")
        hard_negatives = _unique_strings(raw.get("hard_negative_card_ids") or [])
        if not isinstance(expected, list) or (not expected and not hard_negatives):
            raise ValueError(f"{case_id}: expected_relations 与 hard_negative_card_ids 不能同时为空")
        normalized_expected: list[dict[str, Any]] = []
        seen_targets: set[str] = set()
        for relation in expected:
            if not isinstance(relation, dict):
                raise ValueError(f"{case_id}: expected_relations 元素必须是对象")
            target_id = str(relation.get("target_card_id") or "").strip()
            decision_class = str(relation.get("decision_class") or "").strip()
            relation_kind = str(relation.get("relation_kind") or "").strip()
            if not target_id or target_id in seen_targets:
                raise ValueError(f"{case_id}: target_card_id 缺失或重复: {target_id}")
            if decision_class not in {"observed", "inferred"}:
                raise ValueError(f"{case_id}: decision_class 只能是 observed/inferred")
            if relation_kind not in RELATION_EVALUATION_KINDS:
                raise ValueError(
                    f"{case_id}: relation_kind 非法: {relation_kind}; "
                    f"allowed={sorted(RELATION_EVALUATION_KINDS)}"
                )
            seen_targets.add(target_id)
            normalized_expected.append(
                {
                    "target_card_id": target_id,
                    "decision_class": decision_class,
                    "relation_kind": relation_kind,
                    "relation_type_contains": _unique_strings(
                        relation.get("relation_type_contains") or []
                    ),
                    "direction_contains": _unique_strings(
                        relation.get("direction_contains") or []
                    ),
                    "source_evidence_refs": _unique_strings(
                        relation.get("source_evidence_refs") or []
                    ),
                    "target_evidence_refs": _unique_strings(
                        relation.get("target_evidence_refs") or []
                    ),
                }
            )
        if set(hard_negatives) & seen_targets:
            raise ValueError(f"{case_id}: 正样本与 hard negative 不能重叠")
        seen_case_ids.add(case_id)
        result.append(
            {
                "case_id": case_id,
                "description": str(raw.get("description") or "").strip(),
                "source_card_id": source_card_id,
                "expected_relations": normalized_expected,
                "hard_negative_card_ids": hard_negatives,
            }
        )
    return result


def evaluate_relation_case(
    case: dict[str, Any],
    relation_result: dict[str, Any],
) -> dict[str, Any]:
    """计算单案例各阶段召回、分类正确性和硬负样本拒绝率。"""

    diagnostics = relation_result.get("card_diagnostics") or []
    if len(diagnostics) != 1 or not isinstance(diagnostics[0], dict):
        raise ValueError(f"{case['case_id']}: 评测模式必须返回一张源 Card 的 diagnostics")
    details = diagnostics[0].get("evaluation_details")
    if not isinstance(details, dict):
        raise ValueError(f"{case['case_id']}: 缺少 evaluation_details")

    route_rows = details.get("routes") or []
    stage_ids = {
        "recall": _union_route_ids(route_rows, "summary_recalled_ids", "focus_recalled_ids"),
        "rerank": _union_route_ids(route_rows, "reranked_ids"),
        "merged": set(details.get("merged_candidate_ids") or []),
        "selected": set(details.get("selected_candidate_ids") or []),
        "screened": set(details.get("screened_related_candidate_ids") or []),
        "verified": set(details.get("verified_candidate_ids") or []),
    }
    decisions = [item for item in relation_result.get("decisions") or [] if isinstance(item, dict)]
    decision_by_target = {
        _decision_other_card_id(item, case["source_card_id"]): item
        for item in decisions
    }
    stage_ids["final_positive"] = {
        target_id
        for target_id, decision in decision_by_target.items()
        if decision.get("decision_class") in {"observed", "inferred"}
    }
    expected_by_target = {
        item["target_card_id"]: item for item in case["expected_relations"]
    }
    expected_targets = set(expected_by_target)
    stage_metrics = {
        stage: _recall_metric(expected_targets, ids)
        for stage, ids in stage_ids.items()
    }
    route_metrics = [
        {
            "route_id": str(row.get("route_id") or ""),
            "route_type": str(row.get("route_type") or ""),
            "role": str(row.get("role") or ""),
            "recall": _recall_metric(
                expected_targets,
                set(row.get("summary_recalled_ids") or [])
                | set(row.get("focus_recalled_ids") or []),
            ),
            "rerank": _recall_metric(
                expected_targets,
                set(row.get("reranked_ids") or []),
            ),
        }
        for row in route_rows
        if isinstance(row, dict)
    ]

    relation_checks: list[dict[str, Any]] = []
    for target_id, expected in expected_by_target.items():
        actual = decision_by_target.get(target_id)
        errors: list[str] = []
        if actual is None:
            errors.append("missing_final_decision")
        else:
            if actual.get("decision_class") != expected["decision_class"]:
                errors.append(
                    f"decision_class:{actual.get('decision_class')}!={expected['decision_class']}"
                )
            if actual.get("relation_kind") != expected["relation_kind"]:
                errors.append(
                    f"relation_kind:{actual.get('relation_kind')}!={expected['relation_kind']}"
                )
            _check_contains(
                errors,
                "relation_type",
                str(actual.get("relation_type") or ""),
                expected["relation_type_contains"],
            )
            _check_contains(
                errors,
                "direction",
                str(actual.get("direction") or ""),
                expected["direction_contains"],
            )
            _check_required_refs(
                errors,
                "source_evidence_refs",
                _refs_for_card(actual, case["source_card_id"]),
                expected["source_evidence_refs"],
            )
            _check_required_refs(
                errors,
                "target_evidence_refs",
                _refs_for_card(actual, target_id),
                expected["target_evidence_refs"],
            )
        relation_checks.append(
            {
                "target_card_id": target_id,
                "expected_class": expected["decision_class"],
                "relation_kind": expected["relation_kind"],
                "actual_class": actual.get("decision_class") if actual else None,
                "passed": not errors,
                "errors": errors,
            }
        )

    false_positive_hard_negatives = sorted(
        set(case["hard_negative_card_ids"]) & stage_ids["final_positive"]
    )
    return {
        "case_id": case["case_id"],
        "source_card_id": case["source_card_id"],
        "stage_metrics": stage_metrics,
        "budgets": dict(details.get("budgets") or {}),
        "route_metrics": route_metrics,
        "stage_candidate_ids": {
            stage: sorted(ids) for stage, ids in stage_ids.items()
        },
        "relation_checks": relation_checks,
        "predicted_relations": [
            {
                "target_card_id": target_id,
                "decision_class": str(decision.get("decision_class") or ""),
                "expected_class": expected_by_target.get(target_id, {}).get("decision_class"),
            }
            for target_id, decision in decision_by_target.items()
            if decision.get("decision_class") in {"observed", "inferred"}
        ],
        "hard_negative_count": len(case["hard_negative_card_ids"]),
        "false_positive_hard_negative_ids": false_positive_hard_negatives,
        "passed": all(item["passed"] for item in relation_checks)
        and not false_positive_hard_negatives,
    }


def aggregate_relation_evaluation(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总阶段 micro recall、分类 precision/recall 和 hard-negative 拒绝率。"""

    if not case_results:
        raise ValueError("关系评测结果不能为空")
    stage_totals = {
        stage: {"expected": 0, "hit": 0}
        for stage in RELATION_EVALUATION_STAGES
    }
    class_totals = {
        item: {"expected": 0, "predicted": 0, "correct": 0}
        for item in ("observed", "inferred")
    }
    hard_negative_total = 0
    hard_negative_false_positive = 0
    relation_kind_coverage = {item: 0 for item in sorted(RELATION_EVALUATION_KINDS)}
    for result in case_results:
        for stage, metric in result["stage_metrics"].items():
            stage_totals[stage]["expected"] += metric["expected"]
            stage_totals[stage]["hit"] += metric["hit"]
        for check in result["relation_checks"]:
            expected_class = check["expected_class"]
            class_totals[expected_class]["expected"] += 1
            relation_kind_coverage[check["relation_kind"]] += 1
        for predicted in result["predicted_relations"]:
            actual_class = predicted["decision_class"]
            expected_class = predicted["expected_class"]
            class_totals[actual_class]["predicted"] += 1
            if actual_class == expected_class:
                class_totals[actual_class]["correct"] += 1
        hard_negative_total += result["hard_negative_count"]
        hard_negative_false_positive += len(result["false_positive_hard_negative_ids"])

    return {
        "cases": len(case_results),
        "passed_cases": sum(bool(item["passed"]) for item in case_results),
        "all_passed": all(bool(item["passed"]) for item in case_results),
        "stage_recall": {
            stage: {
                **counts,
                "recall": _safe_ratio(counts["hit"], counts["expected"]),
            }
            for stage, counts in stage_totals.items()
        },
        "decision_class_metrics": {
            name: {
                **counts,
                "precision": _safe_ratio(counts["correct"], counts["predicted"]),
                "recall": _safe_ratio(counts["correct"], counts["expected"]),
            }
            for name, counts in class_totals.items()
        },
        "hard_negative": {
            "total": hard_negative_total,
            "false_positive": hard_negative_false_positive,
            "reject_rate": _safe_ratio(
                hard_negative_total - hard_negative_false_positive,
                hard_negative_total,
            ),
        },
        "relation_kind_coverage": relation_kind_coverage,
        "missing_relation_kinds": [
            item for item, count in relation_kind_coverage.items() if count == 0
        ],
    }


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("标注字段必须是数组")
    return [item for item in dict.fromkeys(str(value).strip() for value in values) if item]


def _union_route_ids(rows: list[Any], *keys: str) -> set[str]:
    result: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            result.update(str(item) for item in row.get(key) or [] if item)
    return result


def _recall_metric(expected: set[str], actual: set[str]) -> dict[str, Any]:
    hit = len(expected & actual)
    return {
        "expected": len(expected),
        "hit": hit,
        "recall": _safe_ratio(hit, len(expected)),
    }


def _decision_other_card_id(decision: dict[str, Any], source_card_id: str) -> str:
    left = str(decision.get("source_card_id") or "")
    right = str(decision.get("target_card_id") or "")
    return right if left == source_card_id else left


def _refs_for_card(decision: dict[str, Any], card_id: str) -> list[Any]:
    if str(decision.get("source_card_id") or "") == card_id:
        return list(decision.get("source_evidence_refs") or [])
    if str(decision.get("target_card_id") or "") == card_id:
        return list(decision.get("target_evidence_refs") or [])
    return []


def _check_contains(errors: list[str], field: str, actual: str, expected: list[str]) -> None:
    missing = [item for item in expected if item not in actual]
    if missing:
        errors.append(f"{field}_missing:{missing}")


def _check_required_refs(
    errors: list[str],
    field: str,
    actual: list[Any],
    expected: list[str],
) -> None:
    missing = sorted(set(expected) - {str(item) for item in actual})
    if missing:
        errors.append(f"{field}_missing:{missing}")


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None
