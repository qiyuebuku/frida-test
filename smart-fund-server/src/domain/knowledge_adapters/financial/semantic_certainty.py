"""Rule-driven semantic certainty assessment for the financial adapter."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from src.domain.knowledge.enums import RecordKind
from src.domain.knowledge.schemas import KnowledgeBaseModel, KnowledgeInput


SemanticDecision = Literal["rule_only", "llm_candidate", "skip", "fail"]


class SemanticCertaintyAssessment(KnowledgeBaseModel):
    decision: SemanticDecision
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    ambiguous_items: list[str] = Field(default_factory=list)
    required_llm_tasks: list[str] = Field(default_factory=list)


def assess_semantic_certainty(item: KnowledgeInput) -> SemanticCertaintyAssessment:
    missing = _missing_base_fields(item)
    if missing:
        return SemanticCertaintyAssessment(
            decision="fail",
            confidence=0.0,
            reasons=["missing_base_fields"],
            missing_fields=missing,
        )

    if item.record_kind in {
        RecordKind.ENTITY_SNAPSHOT,
        RecordKind.RELATION_ASSERTION,
        RecordKind.STRUCTURED_SIGNAL,
        RecordKind.EVENT_ASSERTION,
    }:
        return SemanticCertaintyAssessment(
            decision="rule_only",
            confidence=0.9,
            reasons=[f"{item.record_kind.value}_handled_by_rules"],
        )

    if item.record_kind != RecordKind.TEXT_DOCUMENT:
        return SemanticCertaintyAssessment(
            decision="fail",
            confidence=0.0,
            reasons=["unsupported_record_kind"],
        )

    payload = item.payload
    text = _text_content(item)
    deterministic_entities = _has_deterministic_entities(payload)
    ambiguous = _ambiguous_items(payload, text)

    if not text and deterministic_entities:
        return SemanticCertaintyAssessment(
            decision="rule_only",
            confidence=0.86,
            reasons=["deterministic_entity_hints_without_text"],
        )
    if not text:
        return SemanticCertaintyAssessment(
            decision="skip",
            confidence=0.2,
            reasons=["text_document_without_text"],
            missing_fields=["raw_text"],
        )

    tasks = ["extract_entities", "extract_events", "extract_relations"]
    if ambiguous:
        tasks.append("resolve_ambiguity")
    return SemanticCertaintyAssessment(
        decision="llm_candidate",
        confidence=0.55 if ambiguous else 0.65,
        reasons=["text_requires_semantic_extraction"] + (["ambiguity_signals_detected"] if ambiguous else []),
        ambiguous_items=ambiguous,
        required_llm_tasks=tasks,
    )


def assessment_from_metadata(value: Any) -> SemanticCertaintyAssessment | None:
    if isinstance(value, SemanticCertaintyAssessment):
        return value
    if isinstance(value, dict):
        try:
            return SemanticCertaintyAssessment.model_validate(value)
        except Exception:
            return None
    return None


def _missing_base_fields(item: KnowledgeInput) -> list[str]:
    missing: list[str] = []
    if not item.source_id:
        missing.append("source_id")
    if not item.source_type:
        missing.append("source_type")
    if item.record_kind is None:
        missing.append("record_kind")
    if not item.payload and not item.raw_text:
        missing.append("payload_or_raw_text")
    return missing


def _has_deterministic_entities(payload: dict[str, Any]) -> bool:
    return any(
        isinstance(payload.get(name), list) and bool(payload.get(name))
        for name in ("symbols", "entity_hints", "mentioned_entities", "affected_entities")
    )


def _ambiguous_items(payload: dict[str, Any], text: str) -> list[str]:
    result: list[str] = []
    for entity in payload.get("mentioned_entities", []) + payload.get("affected_entities", []):
        if not isinstance(entity, dict):
            continue
        if not entity.get("type"):
            result.append(f"entity_type_missing:{entity.get('name') or entity.get('canonical_name') or 'unknown'}")
    for keyword in ("利好", "承压", "驱动", "约束", "受益", "受损", "风险偏好", "影响"):
        if keyword in text:
            result.append(f"relation_semantics:{keyword}")
    return _unique(result)


def _text_content(item: KnowledgeInput) -> str:
    parts = [
        item.raw_text,
        item.payload.get("title"),
        item.payload.get("summary"),
        item.payload.get("content"),
        item.payload.get("text"),
    ]
    return "\n".join(str(part).strip() for part in parts if isinstance(part, str) and part.strip())


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
