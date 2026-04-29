"""Generic domain adapter contract and ontology validation."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field, field_validator, model_validator

from src.domain.knowledge.enums import (
    ConfidenceLabel,
    EdgeStatus,
    InputType,
    ValidationSeverity,
)
from src.domain.knowledge.schemas import (
    EdgeDraft,
    EvidenceDraft,
    KnowledgeBaseModel,
    KnowledgeInput,
    NodeDraft,
    ValidationIssue,
)


class EntityTypeSpec(KnowledgeBaseModel):
    name: str
    description: str = ""
    stable_id_fields: list[str] = Field(default_factory=list)
    required_properties: list[str] = Field(default_factory=list)
    alias_fields: list[str] = Field(default_factory=list)
    allow_auto_create: bool = True
    allow_auto_merge: bool = False
    requires_review_on_merge: bool = True

    @field_validator("name")
    @classmethod
    def _required_name(cls, value: str) -> str:
        return _not_blank(value)


class RelationTypeSpec(KnowledgeBaseModel):
    name: str
    description: str = ""
    source_types: list[str]
    target_types: list[str]
    allow_inferred: bool = True
    requires_evidence: bool = True
    allow_multiple: bool = True
    has_validity_window: bool = False
    allowed_confidence_labels: list[ConfidenceLabel] = Field(
        default_factory=lambda: list(ConfidenceLabel)
    )
    allowed_statuses: list[EdgeStatus] = Field(default_factory=lambda: list(EdgeStatus))

    @field_validator("name")
    @classmethod
    def _required_name(cls, value: str) -> str:
        return _not_blank(value)

    @field_validator("source_types", "target_types")
    @classmethod
    def _non_empty_type_list(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("type list cannot be empty")
        return [_not_blank(item) for item in value]


class SourceTypeSpec(KnowledgeBaseModel):
    name: str
    input_type: InputType
    description: str = ""
    required_fields: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _required_name(cls, value: str) -> str:
        return _not_blank(value)


class ConsumptionRule(KnowledgeBaseModel):
    consumer: str
    allowed_confidence_labels: list[ConfidenceLabel] = Field(default_factory=list)
    allowed_edge_statuses: list[EdgeStatus] = Field(default_factory=list)
    max_depth: int | None = Field(default=None, ge=1)
    requires_human_review: bool = False

    @field_validator("consumer")
    @classmethod
    def _required_consumer(cls, value: str) -> str:
        return _not_blank(value)


class ReviewRule(KnowledgeBaseModel):
    trigger: str
    severity: ValidationSeverity
    description: str = ""
    relation_types: list[str] = Field(default_factory=list)
    node_types: list[str] = Field(default_factory=list)

    @field_validator("trigger")
    @classmethod
    def _required_trigger(cls, value: str) -> str:
        return _not_blank(value)


class WikiPageSpec(KnowledgeBaseModel):
    page_type: str
    subject_types: list[str]
    sections: list[str] = Field(default_factory=list)
    requires_evidence: bool = True

    @field_validator("page_type")
    @classmethod
    def _required_page_type(cls, value: str) -> str:
        return _not_blank(value)

    @field_validator("subject_types")
    @classmethod
    def _non_empty_subject_types(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("subject_types cannot be empty")
        return [_not_blank(item) for item in value]


class AdapterSpec(KnowledgeBaseModel):
    name: str
    version: str
    entities: list[EntityTypeSpec]
    relations: list[RelationTypeSpec]
    sources: list[SourceTypeSpec] = Field(default_factory=list)
    consumption_rules: list[ConsumptionRule] = Field(default_factory=list)
    review_rules: list[ReviewRule] = Field(default_factory=list)
    wiki_pages: list[WikiPageSpec] = Field(default_factory=list)

    @field_validator("name", "version")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _not_blank(value)

    @model_validator(mode="after")
    def _valid_references(self) -> "AdapterSpec":
        entity_names = [item.name for item in self.entities]
        relation_names = [item.name for item in self.relations]
        source_names = [item.name for item in self.sources]
        _ensure_unique(entity_names, "entity")
        _ensure_unique(relation_names, "relation")
        _ensure_unique(source_names, "source")

        entity_set = set(entity_names)
        relation_set = set(relation_names)
        for relation in self.relations:
            for name in relation.source_types + relation.target_types:
                if name not in entity_set:
                    raise ValueError(f"relation {relation.name} references unknown entity {name}")

        for rule in self.review_rules:
            for name in rule.node_types:
                if name not in entity_set:
                    raise ValueError(f"review rule {rule.trigger} references unknown entity {name}")
            for name in rule.relation_types:
                if name not in relation_set:
                    raise ValueError(f"review rule {rule.trigger} references unknown relation {name}")

        for page in self.wiki_pages:
            for name in page.subject_types:
                if name not in entity_set:
                    raise ValueError(f"wiki page {page.page_type} references unknown entity {name}")

        return self


class ValidationResult(KnowledgeBaseModel):
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)

    @classmethod
    def success(cls) -> "ValidationResult":
        return cls(ok=True)

    @classmethod
    def error(
        cls,
        message: str,
        *,
        object_type: str | None = None,
        object_ref: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        return cls(
            ok=False,
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    message=message,
                    object_type=object_type,
                    object_ref=object_ref,
                    details=details or {},
                )
            ],
        )

    @classmethod
    def warning(
        cls,
        message: str,
        *,
        object_type: str | None = None,
        object_ref: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> "ValidationResult":
        return cls(
            ok=True,
            issues=[
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    message=message,
                    object_type=object_type,
                    object_ref=object_ref,
                    details=details or {},
                )
            ],
        )


class DomainAdapter(Protocol):
    spec: AdapterSpec

    def normalize(self, raw: Any) -> list[KnowledgeInput]:
        ...

    async def extract_node_drafts(self, item: KnowledgeInput) -> list[NodeDraft]:
        ...

    async def extract_edge_drafts(self, item: KnowledgeInput, nodes: list[NodeDraft]) -> list[EdgeDraft]:
        ...

    def extract_evidence_drafts(self, item: KnowledgeInput) -> list[EvidenceDraft]:
        ...


def validate_node_against_adapter(
    adapter_spec: AdapterSpec,
    node: NodeDraft,
) -> ValidationResult:
    entity = _entity_spec(adapter_spec, node.node_type)
    if entity is None:
        return ValidationResult.error(
            f"unknown node type: {node.node_type}",
            object_type="node",
            object_ref=node.stable_key,
        )

    missing = [name for name in entity.required_properties if name not in node.properties]
    if missing:
        return ValidationResult.error(
            "required properties missing",
            object_type="node",
            object_ref=node.stable_key,
            details={"missing": missing},
        )

    if not entity.allow_auto_create:
        return ValidationResult.warning(
            "auto create is disabled for this node type",
            object_type="node",
            object_ref=node.stable_key,
        )

    return ValidationResult.success()


def validate_edge_against_adapter(
    adapter_spec: AdapterSpec,
    edge: EdgeDraft,
    source_node: NodeDraft,
    target_node: NodeDraft,
) -> ValidationResult:
    relation = _relation_spec(adapter_spec, edge.relation_type)
    if relation is None:
        return ValidationResult.error(
            f"unknown relation type: {edge.relation_type}",
            object_type="edge",
            object_ref=edge.relation_type,
        )

    if source_node.node_type not in relation.source_types:
        return ValidationResult.error(
            "invalid source node type",
            object_type="edge",
            object_ref=edge.relation_type,
            details={"source_type": source_node.node_type},
        )

    if target_node.node_type not in relation.target_types:
        return ValidationResult.error(
            "invalid target node type",
            object_type="edge",
            object_ref=edge.relation_type,
            details={"target_type": target_node.node_type},
        )

    if edge.confidence_label not in relation.allowed_confidence_labels:
        return ValidationResult.error(
            "confidence label is not allowed",
            object_type="edge",
            object_ref=edge.relation_type,
            details={"confidence_label": edge.confidence_label.value},
        )

    if edge.status not in relation.allowed_statuses:
        return ValidationResult.error(
            "edge status is not allowed",
            object_type="edge",
            object_ref=edge.relation_type,
            details={"status": edge.status.value},
        )

    if not relation.allow_inferred and edge.confidence_label == ConfidenceLabel.INFERRED:
        return ValidationResult.error(
            "inferred confidence is not allowed",
            object_type="edge",
            object_ref=edge.relation_type,
        )

    if relation.requires_evidence and edge.status == EdgeStatus.ACTIVE and not edge.evidence_refs:
        return ValidationResult.error(
            "active edge requires evidence",
            object_type="edge",
            object_ref=edge.relation_type,
        )

    return ValidationResult.success()


def _entity_spec(adapter_spec: AdapterSpec, name: str) -> EntityTypeSpec | None:
    return next((item for item in adapter_spec.entities if item.name == name), None)


def _relation_spec(adapter_spec: AdapterSpec, name: str) -> RelationTypeSpec | None:
    return next((item for item in adapter_spec.relations if item.name == name), None)


def _ensure_unique(values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise ValueError(f"duplicate {label} names: {joined}")


def _not_blank(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-empty string")
    return value
