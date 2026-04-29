"""Generic knowledge domain schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.knowledge.enums import (
    ConfidenceLabel,
    EdgeStatus,
    EvidenceType,
    InputType,
    NodeStatus,
    ValidationSeverity,
)


class KnowledgeBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeInput(KnowledgeBaseModel):
    input_type: InputType
    source_type: str
    source_id: str
    observed_at: datetime
    adapter_name: str
    adapter_version: str
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type", "source_id", "adapter_name", "adapter_version")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)

    @model_validator(mode="after")
    def _has_content(self) -> "KnowledgeInput":
        if not self.payload and not _has_text(self.raw_text):
            raise ValueError("raw_text and payload cannot both be empty")
        return self


class NodeDraft(KnowledgeBaseModel):
    node_type: str
    stable_key: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)
    status: NodeStatus = NodeStatus.CANDIDATE
    source_refs: list[str] = Field(default_factory=list)

    @field_validator("node_type", "stable_key", "canonical_name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)


class EdgeDraft(KnowledgeBaseModel):
    source_ref: str
    target_ref: str
    relation_type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence_label: ConfidenceLabel = ConfidenceLabel.INFERRED
    confidence_score: float = Field(0.0, ge=0.0, le=1.0)
    status: EdgeStatus = EdgeStatus.CANDIDATE
    evidence_refs: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("source_ref", "target_ref", "relation_type")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)

    @model_validator(mode="after")
    def _valid_status_combo(self) -> "EdgeDraft":
        _check_edge_state(self.confidence_label, self.status, self.evidence_refs)
        return self


class EvidenceDraft(KnowledgeBaseModel):
    evidence_type: EvidenceType
    source_type: str
    source_id: str
    content: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    span_start: int | None = None
    span_end: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type", "source_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)

    @model_validator(mode="after")
    def _valid_content_and_span(self) -> "EvidenceDraft":
        _check_evidence_content(self.content, self.payload)
        _check_span(self.span_start, self.span_end)
        return self


class CompiledNode(KnowledgeBaseModel):
    node_id: str
    adapter_name: str
    node_type: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    properties: dict[str, Any] = Field(default_factory=dict)
    status: NodeStatus
    version: str

    @field_validator("adapter_name", "node_type", "canonical_name", "version")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)

    @field_validator("node_id")
    @classmethod
    def _valid_node_id(cls, value: str) -> str:
        value = _not_blank(value)
        if not value.startswith("kg:"):
            raise ValueError("node_id must start with kg:")
        return value


class CompiledEdge(KnowledgeBaseModel):
    edge_id: str
    adapter_name: str
    source_node_id: str
    target_node_id: str
    relation_type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    confidence_label: ConfidenceLabel
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    status: EdgeStatus
    evidence_ids: list[str] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    version: str

    @field_validator("adapter_name", "relation_type", "version")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)

    @field_validator("edge_id")
    @classmethod
    def _valid_edge_id(cls, value: str) -> str:
        value = _not_blank(value)
        if not value.startswith("kg_edge:"):
            raise ValueError("edge_id must start with kg_edge:")
        return value

    @field_validator("source_node_id", "target_node_id")
    @classmethod
    def _valid_node_ref(cls, value: str) -> str:
        value = _not_blank(value)
        if not value.startswith("kg:"):
            raise ValueError("node refs must start with kg:")
        return value

    @model_validator(mode="after")
    def _valid_status_combo(self) -> "CompiledEdge":
        _check_edge_state(self.confidence_label, self.status, self.evidence_ids)
        return self


class CompiledEvidence(KnowledgeBaseModel):
    evidence_id: str
    adapter_name: str
    evidence_type: EvidenceType
    source_type: str
    source_id: str
    content: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    span_start: int | None = None
    span_end: int | None = None
    version: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("adapter_name", "source_type", "source_id", "version")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)

    @field_validator("evidence_id")
    @classmethod
    def _valid_evidence_id(cls, value: str) -> str:
        value = _not_blank(value)
        if not value.startswith("kg_ev:"):
            raise ValueError("evidence_id must start with kg_ev:")
        return value

    @model_validator(mode="after")
    def _valid_content_and_span(self) -> "CompiledEvidence":
        _check_evidence_content(self.content, self.payload)
        _check_span(self.span_start, self.span_end)
        return self


class EvidenceChunk(KnowledgeBaseModel):
    chunk_id: str
    adapter_name: str
    evidence_id: str
    content: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("chunk_id", "adapter_name", "evidence_id", "content")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)


class FailedRecord(KnowledgeBaseModel):
    source_type: str
    source_id: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type", "source_id", "reason")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)


class ValidationIssue(KnowledgeBaseModel):
    severity: ValidationSeverity
    message: str
    object_type: str | None = None
    object_ref: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)


class CompileResult(KnowledgeBaseModel):
    run_id: str
    adapter_name: str
    adapter_version: str
    version: str
    nodes: list[CompiledNode] = Field(default_factory=list)
    edges: list[CompiledEdge] = Field(default_factory=list)
    evidence: list[CompiledEvidence] = Field(default_factory=list)
    failed_records: list[FailedRecord] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)

    @field_validator("run_id", "adapter_name", "adapter_version", "version")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)


def _not_blank(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-empty string")
    return value


def _has_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_edge_state(
    confidence_label: ConfidenceLabel,
    status: EdgeStatus,
    evidence_refs: list[str],
) -> None:
    if confidence_label == ConfidenceLabel.REJECTED and status == EdgeStatus.ACTIVE:
        raise ValueError("rejected edges cannot be active")
    if status == EdgeStatus.ACTIVE and not evidence_refs:
        raise ValueError("active edges require evidence")


def _check_evidence_content(content: str | None, payload: dict[str, Any]) -> None:
    if not payload and not _has_text(content):
        raise ValueError("content and payload cannot both be empty")


def _check_span(span_start: int | None, span_end: int | None) -> None:
    if span_start is None and span_end is None:
        return
    if span_start is None or span_end is None:
        raise ValueError("span_start and span_end must be set together")
    if span_start < 0 or span_start > span_end:
        raise ValueError("invalid span range")
