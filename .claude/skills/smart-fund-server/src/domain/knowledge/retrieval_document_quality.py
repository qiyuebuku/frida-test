"""Quality metrics for retrieval documents.

These metrics validate ingestion-time search enrichment. They are not recall
metrics; they tell us whether stored retrieval documents have enough clean
fields for keyword, semantic, ranking, and candidate package stages.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from src.domain.knowledge.retrieval_document import RetrievalDocument

_JSON_NOISE_TERMS = {
    "aliases",
    "code",
    "company_name",
    "exchange",
    "name",
    "source_id",
    "source_type",
    "status",
    "title",
}

_QUALITY_FIELDS = (
    "search_text",
    "key_phrases",
    "aliases",
    "readable_relations",
    "evidence_summary",
    "answer_candidate_type",
    "relation_intents",
    "source_type_tags",
    "time_tags",
)


def build_retrieval_document_quality_report(
    documents: list[RetrievalDocument],
    *,
    expected_generation_version: str | None = None,
) -> dict[str, Any]:
    total = len(documents)
    field_counts = {field: 0 for field in _QUALITY_FIELDS}
    empty_summary_by_fact_type: Counter[str] = Counter()
    json_noise_samples: list[dict[str, Any]] = []
    version_counts: Counter[str] = Counter()
    by_fact_type: Counter[str] = Counter()
    by_answer_type: Counter[str] = Counter()

    for document in documents:
        version_counts[document.generation_version] += 1
        by_fact_type[document.source_fact_type] += 1
        by_answer_type[document.answer_candidate_type] += 1

        for field in _QUALITY_FIELDS:
            if _has_field_value(field, getattr(document, field)):
                field_counts[field] += 1

        if not document.evidence_summary.strip():
            empty_summary_by_fact_type[document.source_fact_type] += 1

        noise_terms = _json_noise_terms(document.key_phrases)
        if noise_terms and len(json_noise_samples) < 10:
            json_noise_samples.append(
                {
                    "document_id": document.document_id,
                    "title": document.title,
                    "source_fact_type": document.source_fact_type,
                    "noise_terms": noise_terms,
                }
            )

    field_ratios = {
        field: round(count / total, 4) if total else 0.0
        for field, count in field_counts.items()
    }
    expected_version_count = (
        version_counts.get(expected_generation_version, 0)
        if expected_generation_version
        else 0
    )

    return {
        "total": total,
        "by_fact_type": dict(by_fact_type),
        "by_answer_type": dict(by_answer_type),
        "version_counts": dict(version_counts),
        "expected_generation_version": expected_generation_version,
        "expected_generation_version_count": expected_version_count,
        "expected_generation_version_ratio": round(expected_version_count / total, 4)
        if total and expected_generation_version
        else None,
        "field_counts": field_counts,
        "field_ratios": field_ratios,
        "empty_summary_by_fact_type": dict(empty_summary_by_fact_type),
        "json_noise_count": sum(1 for document in documents if _json_noise_terms(document.key_phrases)),
        "json_noise_samples": json_noise_samples,
        "warnings": _quality_warnings(
            total=total,
            expected_generation_version=expected_generation_version,
            expected_generation_version_count=expected_version_count,
            field_ratios=field_ratios,
            empty_summary_by_fact_type=empty_summary_by_fact_type,
            json_noise_samples=json_noise_samples,
        ),
    }


def _has_field_value(field: str, value: Any) -> bool:
    if field == "answer_candidate_type":
        return bool(value and value != "unknown")
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return value is not None


def _json_noise_terms(key_phrases: list[str]) -> list[str]:
    noise: list[str] = []
    for phrase in key_phrases:
        item = str(phrase).strip()
        if not item:
            continue
        lowered = item.lower().strip("'\"")
        if lowered in _JSON_NOISE_TERMS or item.startswith(("{", '"')):
            noise.append(item)
    return noise


def _quality_warnings(
    *,
    total: int,
    expected_generation_version: str | None,
    expected_generation_version_count: int,
    field_ratios: dict[str, float],
    empty_summary_by_fact_type: Counter[str],
    json_noise_samples: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    if expected_generation_version and total and expected_generation_version_count != total:
        warnings.append(
            f"generation_version_mismatch expected={expected_generation_version} "
            f"actual={expected_generation_version_count}/{total}"
        )
    if field_ratios.get("search_text", 0.0) < 1.0:
        warnings.append("search_text_not_fully_populated")
    if field_ratios.get("key_phrases", 0.0) < 1.0:
        warnings.append("key_phrases_not_fully_populated")
    if field_ratios.get("evidence_summary", 0.0) < 0.95:
        warnings.append("evidence_summary_coverage_below_95_percent")
    if empty_summary_by_fact_type.get("node", 0) > 0:
        warnings.append(f"node_evidence_summary_empty={empty_summary_by_fact_type['node']}")
    if json_noise_samples:
        warnings.append("key_phrases_json_noise_detected")
    return warnings
