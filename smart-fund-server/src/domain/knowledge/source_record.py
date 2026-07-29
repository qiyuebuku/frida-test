"""Generic Source Record helpers."""

from __future__ import annotations

from src.domain.knowledge.enums import InputType, RecordKind
from src.domain.knowledge.schemas import KnowledgeInput


INPUT_TYPE_RECORD_KIND_DEFAULTS: dict[InputType, RecordKind] = {
    InputType.SEMI_STRUCTURED_RECORD: RecordKind.TEXT_DOCUMENT,
    InputType.DOCUMENT_CHUNK: RecordKind.TEXT_DOCUMENT,
    InputType.DERIVED_SIGNAL: RecordKind.STRUCTURED_SIGNAL,
    InputType.EVENT_RECORD: RecordKind.EVENT_ASSERTION,
}


def resolve_record_kind(
    *,
    source_type: str,
    input_type: InputType,
    explicit: str | RecordKind | None = None,
    source_type_hints: dict[str, RecordKind] | None = None,
) -> RecordKind:
    """Resolve record kind without hard-coding domain source labels in core."""

    if explicit:
        return _parse_record_kind(explicit)
    if source_type_hints and source_type in source_type_hints:
        return source_type_hints[source_type]
    if input_type in INPUT_TYPE_RECORD_KIND_DEFAULTS:
        return INPUT_TYPE_RECORD_KIND_DEFAULTS[input_type]
    raise ValueError(f"record_kind is required for source_type={source_type}")


def validate_source_record_contract(item: KnowledgeInput) -> None:
    """Validate generic compile-time Source Record requirements."""

    if item.record_kind is None:
        raise ValueError("record_kind is required")
    if item.record_kind == RecordKind.TEXT_DOCUMENT and not (item.raw_text or _payload_text(item.payload)):
        raise ValueError("text_document requires raw_text or readable payload text")
    if item.record_kind in {RecordKind.STRUCTURED_SIGNAL, RecordKind.ENTITY_SNAPSHOT, RecordKind.RELATION_ASSERTION}:
        if not item.payload:
            raise ValueError(f"{item.record_kind.value} requires payload")
    if not _has_traceable_metadata(item.metadata):
        raise ValueError("metadata requires source_table/source_pk, external_source, source_ref, or source_origin")


def _parse_record_kind(value: str | RecordKind) -> RecordKind:
    if isinstance(value, RecordKind):
        return value
    return RecordKind(str(value))


def _payload_text(payload: dict) -> str | None:
    for name in ("title", "summary", "content", "text"):
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _has_traceable_metadata(metadata: dict) -> bool:
    if metadata.get("source_table") and metadata.get("source_pk") is not None:
        return True
    return any(metadata.get(name) for name in ("external_source", "source_ref", "source_origin"))
