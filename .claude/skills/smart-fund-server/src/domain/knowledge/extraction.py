"""Generic text extraction contracts and pipeline."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field, field_validator, model_validator

from src.domain.knowledge.adapter import ValidationResult
from src.domain.knowledge.schemas import KnowledgeBaseModel


class EvidenceSpan(KnowledgeBaseModel):
    field_name: str
    text: str
    start: int | None = None
    end: int | None = None

    @field_validator("field_name", "text")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value

    @model_validator(mode="after")
    def _valid_span(self) -> "EvidenceSpan":
        if self.start is None and self.end is None:
            return self
        if self.start is None or self.end is None:
            raise ValueError("start and end must be set together")
        if self.start < 0 or self.start > self.end:
            raise ValueError("invalid span range")
        return self


class ExtractedEntity(KnowledgeBaseModel):
    entity_type: str
    canonical_name: str
    identifiers: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("entity_type", "canonical_name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value


class ExtractedRelation(KnowledgeBaseModel):
    relation_type: str
    source: str
    target: str
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    status_hint: str | None = None
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("relation_type", "source", "target")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value


class TextExtractionInput(KnowledgeBaseModel):
    source_id: str
    source_type: str
    title: str | None = None
    text: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id", "source_type")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value

    @model_validator(mode="after")
    def _has_content(self) -> "TextExtractionInput":
        if not self.title and not self.text and not self.fields:
            raise ValueError("title, text and fields cannot all be empty")
        return self

    def lookup_field(self, name: str) -> str | None:
        if name == "title":
            return self.title
        if name == "text":
            return self.text
        return self.fields.get(name)


class TextExtractionResult(KnowledgeBaseModel):
    mentioned_entities: list[ExtractedEntity] = Field(default_factory=list)
    affected_entities: list[ExtractedEntity] = Field(default_factory=list)
    relations: list[ExtractedRelation] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TextExtractionStrategy(Protocol):
    name: str
    version: str

    async def extract(self, item: TextExtractionInput) -> TextExtractionResult:
        ...

    def validate_result(self, result: TextExtractionResult, item: TextExtractionInput) -> ValidationResult:
        ...


class TextExtractionPipeline:
    async def extract(
        self,
        item: TextExtractionInput,
        strategy: TextExtractionStrategy,
    ) -> TextExtractionResult:
        try:
            result = await strategy.extract(item)
        except Exception as exc:
            return TextExtractionResult(warnings=[f"extraction failed: {exc}"])

        warnings = list(result.warnings)
        warnings.extend(_span_warnings(item, result))

        validation = strategy.validate_result(result, item)
        warnings.extend(issue.message for issue in validation.issues)
        if not validation.ok:
            return TextExtractionResult(warnings=warnings)

        return result.model_copy(update={"warnings": warnings})


def _span_warnings(item: TextExtractionInput, result: TextExtractionResult) -> list[str]:
    warnings: list[str] = []
    spans: list[EvidenceSpan] = []
    for entity in result.mentioned_entities + result.affected_entities:
        spans.extend(entity.evidence_spans)
    for relation in result.relations:
        spans.extend(relation.evidence_spans)

    for span in spans:
        field_value = item.lookup_field(span.field_name)
        if not field_value:
            warnings.append(f"span field is missing: {span.field_name}")
            continue
        if span.start is not None and span.end is not None:
            if field_value[span.start:span.end] != span.text:
                warnings.append(f"span text does not match field: {span.field_name}")
        elif span.text not in field_value:
            warnings.append(f"span text not found in field: {span.field_name}")
    return warnings
