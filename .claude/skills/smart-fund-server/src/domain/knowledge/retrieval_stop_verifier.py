"""Stop condition verification for agentic KG retrieval."""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field

from src.domain.knowledge.retrieval import RetrievalHit
from src.domain.knowledge.schemas import KnowledgeBaseModel


class StopVerificationResult(KnowledgeBaseModel):
    satisfied: bool
    reason: str
    missing_reasons: list[str] = Field(default_factory=list)
    opened_evidence_ready: bool = False
    keep_count: int = 0
    required_answer_count: int = 0
    answer_candidate_ids: list[str] = Field(default_factory=list)
    new_answer_ids: list[str] = Field(default_factory=list)
    requires_new_answer: bool = False
    coverage_terms: list[str] = Field(default_factory=list)
    missing_coverage_terms: list[str] = Field(default_factory=list)
    coverage_schema: dict[str, Any] = Field(default_factory=dict)
    anchor_coverage: float = 0.0


def verify_stop_condition(
    working_set: Any,
    constraints: Any,
    *,
    search_plan: Any | None = None,
    new_answer_ids: list[str] | None = None,
    hits: list[RetrievalHit] | None = None,
) -> StopVerificationResult:
    hits = hits or []
    missing_reasons: list[str] = []
    evidence_refs = list(getattr(working_set, "evidence_refs", []) or [])
    opened_windows = set(getattr(working_set, "opened_windows", []) or [])
    opened_ready = bool(evidence_refs) and not any(item not in opened_windows for item in evidence_refs)
    if not evidence_refs:
        missing_reasons.append("no_evidence_refs")
    elif not opened_ready:
        missing_reasons.append("evidence_not_opened")

    accepted_candidates = list(getattr(working_set, "accepted_candidates", []) or [])
    keep_count = len(accepted_candidates)
    min_keep = int(getattr(constraints, "min_keep_candidates_to_auto_stop", 2) or 2)
    if keep_count < min_keep:
        missing_reasons.append("insufficient_keep_candidates")

    answer_ids = answer_candidate_ids(working_set, hits)
    required_answers = required_answer_count(search_plan)
    if required_answers and len(answer_ids) < required_answers:
        missing_reasons.append("insufficient_answer_candidates")

    new_answers = list(new_answer_ids or [])
    requires_new = requires_new_answer(search_plan)
    if requires_new and not new_answers:
        missing_reasons.append("no_new_answer_candidate")

    schema = coverage_schema(search_plan)
    terms = list(schema.get("required_categories", []))
    missing_terms = missing_coverage_terms(search_plan, working_set, hits)
    if missing_terms:
        missing_reasons.append("missing_coverage_terms")

    anchor_coverage = max(
        (item.anchor_coverage.get("overall", 0.0) for item in accepted_candidates),
        default=0.0,
    )
    threshold = float(getattr(constraints, "anchor_coverage_stop_threshold", 0.8) or 0.8)
    if anchor_coverage < threshold:
        missing_reasons.append("anchor_coverage_below_threshold")

    return StopVerificationResult(
        satisfied=not missing_reasons,
        reason="stop_condition_satisfied" if not missing_reasons else "stop_condition_unmet",
        missing_reasons=_ordered_unique(missing_reasons),
        opened_evidence_ready=opened_ready,
        keep_count=keep_count,
        required_answer_count=required_answers,
        answer_candidate_ids=answer_ids,
        new_answer_ids=new_answers,
        requires_new_answer=requires_new,
        coverage_terms=terms,
        missing_coverage_terms=missing_terms,
        coverage_schema=schema,
        anchor_coverage=anchor_coverage,
    )


def required_answer_count(search_plan: Any | None) -> int:
    stop_condition = _stop_condition(search_plan)
    if not stop_condition:
        return 0
    if not any(marker in stop_condition for marker in ("至少", "不少于", ">=", "大于等于")):
        return 0
    matches = [int(value) for value in re.findall(r"\d+", stop_condition)]
    return max(matches) if matches else 0


def requires_new_answer(search_plan: Any | None) -> bool:
    stop_condition = _stop_condition(search_plan)
    if not stop_condition:
        return False
    return any(marker in stop_condition for marker in ("再发现", "新增", "新发现", "补充"))


def coverage_terms(search_plan: Any | None) -> list[str]:
    return list(coverage_schema(search_plan).get("required_categories", []))


def coverage_schema(search_plan: Any | None) -> dict[str, Any]:
    stop_condition = _stop_condition(search_plan)
    if not stop_condition:
        return {"required_categories": [], "raw_stop_condition": ""}
    if not any(marker in stop_condition for marker in ("覆盖", "包括", "包含", "涵盖")):
        return {"required_categories": [], "raw_stop_condition": stop_condition}
    normalized = (
        stop_condition.replace("以及", "、")
        .replace("及", "、")
        .replace("和", "、")
        .replace("，", "、")
        .replace(",", "、")
        .replace("；", "、")
        .replace(";", "、")
    )
    terms: list[str] = []
    for raw in re.split(r"[、\s]+", normalized):
        term = _clean_coverage_term(raw)
        if term:
            terms.append(term)
    return {
        "required_categories": _ordered_unique(terms),
        "raw_stop_condition": stop_condition,
    }


def missing_coverage_terms(
    search_plan: Any | None,
    working_set: Any,
    hits: list[RetrievalHit],
) -> list[str]:
    terms = coverage_terms(search_plan)
    if not terms:
        return []
    text = _stop_context_text(working_set, hits)
    return [term for term in terms if term.lower() not in text]


def answer_candidate_ids(
    working_set: Any,
    hits: list[RetrievalHit] | None = None,
) -> list[str]:
    hits = hits or []
    return _ordered_unique(
        normalized_answer_candidate_id(item.candidate_id, hits)
        for item in list(getattr(working_set, "accepted_candidates", []) or [])
        if item.decision == "keep"
        and item.role == "answer"
        and not item.candidate_id.startswith("kg_edge:")
        and normalized_answer_candidate_id(item.candidate_id, hits)
    )


def normalized_answer_candidate_id(candidate_id: str, hits: list[RetrievalHit]) -> str:
    if candidate_id.startswith("kg_edge:"):
        return ""
    if not candidate_id.startswith("kg_ev:"):
        return candidate_id
    return parent_candidate_id_for_evidence(candidate_id, hits) or candidate_id


def parent_candidate_id_for_evidence(evidence_id: str, hits: list[RetrievalHit]) -> str:
    preferred_types = {"node", "path"}
    for hit in hits:
        if hit.hit_id == evidence_id:
            continue
        if hit.hit_type not in preferred_types:
            continue
        if evidence_id in hit.evidence_refs:
            return hit.hit_id
    return ""


def _stop_condition(search_plan: Any | None) -> str:
    if search_plan is None:
        return ""
    return str(getattr(search_plan, "stop_condition", "") or "")


def _clean_coverage_term(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^.*?(?:覆盖|包括|包含|涵盖)", "", text)
    text = re.sub(r"^至少", "", text)
    text = re.sub(r"^[且并需要]+", "", text)
    text = re.sub(r"^.*?(?:如|例如|比如)", "", text)
    text = re.sub(r"^找到?\d+个受影响", "", text)
    text = re.sub(r"[一二三四五六七八九十\d]+类.*$", "", text)
    text = re.sub(r"等.*$", "", text)
    text = re.sub(r"的.*$", "", text)
    text = re.sub(r"(资产类别|资产|行业|板块|维度|关键影响|影响|明确证据|证据|不同|具体)$", "", text)
    text = text.strip("：:，,。、；; ")
    if len(text) < 2:
        return ""
    if text in {"至少", "覆盖", "包括", "包含", "涵盖", "不同", "具体", "明确", "关键"}:
        return ""
    return text


def _stop_context_text(working_set: Any, hits: list[RetrievalHit]) -> str:
    accepted_ids = {
        item.candidate_id
        for item in list(getattr(working_set, "accepted_candidates", []) or [])
        if item.decision == "keep"
    }
    evidence_refs = set(getattr(working_set, "evidence_refs", []) or [])
    parts: list[str] = []
    for hit in hits:
        if hit.hit_id in accepted_ids or hit.hit_id in evidence_refs or evidence_refs.intersection(hit.evidence_refs):
            parts.extend([hit.hit_id, hit.title, hit.snippet, " ".join(hit.evidence_refs)])
    parts.extend(accepted_ids)
    parts.extend(evidence_refs)
    return "\n".join(str(item or "") for item in parts).lower()


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
