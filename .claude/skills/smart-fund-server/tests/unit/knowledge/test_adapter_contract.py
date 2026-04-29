"""Unit tests for the generic adapter contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domain.knowledge.adapter import (
    AdapterSpec,
    EntityTypeSpec,
    RelationTypeSpec,
    validate_edge_against_adapter,
    validate_node_against_adapter,
)
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus
from src.domain.knowledge.schemas import EdgeDraft, NodeDraft


def _adapter_spec() -> AdapterSpec:
    return AdapterSpec(
        name="toy",
        version="v1",
        entities=[
            EntityTypeSpec(name="person"),
            EntityTypeSpec(name="project", required_properties=["kind"]),
            EntityTypeSpec(name="document"),
        ],
        relations=[
            RelationTypeSpec(
                name="owns",
                source_types=["person"],
                target_types=["project"],
                allow_inferred=False,
                requires_evidence=True,
                allowed_confidence_labels=[ConfidenceLabel.EXTRACTED],
                allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE],
            )
        ],
    )


def test_adapter_spec_requires_name() -> None:
    with pytest.raises(ValidationError):
        AdapterSpec(name="", version="v1", entities=[], relations=[])


def test_adapter_spec_rejects_duplicate_entity_names() -> None:
    with pytest.raises(ValidationError):
        AdapterSpec(
            name="toy",
            version="v1",
            entities=[EntityTypeSpec(name="person"), EntityTypeSpec(name="person")],
            relations=[],
        )


def test_adapter_spec_rejects_duplicate_relation_names() -> None:
    with pytest.raises(ValidationError):
        AdapterSpec(
            name="toy",
            version="v1",
            entities=[EntityTypeSpec(name="person"), EntityTypeSpec(name="project")],
            relations=[
                RelationTypeSpec(name="owns", source_types=["person"], target_types=["project"]),
                RelationTypeSpec(name="owns", source_types=["person"], target_types=["project"]),
            ],
        )


def test_adapter_spec_rejects_unknown_source_entity_reference() -> None:
    with pytest.raises(ValidationError):
        AdapterSpec(
            name="toy",
            version="v1",
            entities=[EntityTypeSpec(name="project")],
            relations=[RelationTypeSpec(name="owns", source_types=["person"], target_types=["project"])],
        )


def test_adapter_spec_rejects_unknown_target_entity_reference() -> None:
    with pytest.raises(ValidationError):
        AdapterSpec(
            name="toy",
            version="v1",
            entities=[EntityTypeSpec(name="person")],
            relations=[RelationTypeSpec(name="owns", source_types=["person"], target_types=["project"])],
        )


def test_validate_node_accepts_declared_type_with_required_properties() -> None:
    result = validate_node_against_adapter(
        _adapter_spec(),
        NodeDraft(
            node_type="project",
            stable_key="alpha",
            canonical_name="Alpha",
            properties={"kind": "initiative"},
        ),
    )
    assert result.ok


def test_validate_node_rejects_unknown_type() -> None:
    result = validate_node_against_adapter(
        _adapter_spec(),
        NodeDraft(node_type="unknown", stable_key="x", canonical_name="Unknown"),
    )
    assert not result.ok


def test_validate_node_rejects_missing_required_properties() -> None:
    result = validate_node_against_adapter(
        _adapter_spec(),
        NodeDraft(node_type="project", stable_key="alpha", canonical_name="Alpha"),
    )
    assert not result.ok


def test_validate_edge_accepts_declared_relation_direction() -> None:
    result = validate_edge_against_adapter(
        _adapter_spec(),
        EdgeDraft(
            source_ref="alice",
            target_ref="alpha",
            relation_type="owns",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=1.0,
            status=EdgeStatus.ACTIVE,
            evidence_refs=["ev1"],
        ),
        NodeDraft(node_type="person", stable_key="alice", canonical_name="Alice"),
        NodeDraft(node_type="project", stable_key="alpha", canonical_name="Alpha"),
    )
    assert result.ok


def test_validate_edge_rejects_reversed_relation_direction() -> None:
    result = validate_edge_against_adapter(
        _adapter_spec(),
        EdgeDraft(
            source_ref="alpha",
            target_ref="alice",
            relation_type="owns",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=1.0,
            status=EdgeStatus.ACTIVE,
            evidence_refs=["ev1"],
        ),
        NodeDraft(node_type="project", stable_key="alpha", canonical_name="Alpha"),
        NodeDraft(node_type="person", stable_key="alice", canonical_name="Alice"),
    )
    assert not result.ok


def test_validate_edge_rejects_unknown_relation() -> None:
    result = validate_edge_against_adapter(
        _adapter_spec(),
        EdgeDraft(source_ref="alice", target_ref="alpha", relation_type="likes"),
        NodeDraft(node_type="person", stable_key="alice", canonical_name="Alice"),
        NodeDraft(node_type="project", stable_key="alpha", canonical_name="Alpha"),
    )
    assert not result.ok


def test_validate_edge_rejects_inferred_when_not_allowed() -> None:
    result = validate_edge_against_adapter(
        _adapter_spec(),
        EdgeDraft(
            source_ref="alice",
            target_ref="alpha",
            relation_type="owns",
            confidence_label=ConfidenceLabel.INFERRED,
            confidence_score=0.7,
            status=EdgeStatus.CANDIDATE,
        ),
        NodeDraft(node_type="person", stable_key="alice", canonical_name="Alice"),
        NodeDraft(node_type="project", stable_key="alpha", canonical_name="Alpha"),
    )
    assert not result.ok


def test_validate_edge_rejects_active_relation_without_evidence() -> None:
    edge = EdgeDraft.model_construct(
        source_ref="alice",
        target_ref="alpha",
        relation_type="owns",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_refs=[],
        properties={},
    )

    result = validate_edge_against_adapter(
        _adapter_spec(),
        edge,
        NodeDraft(node_type="person", stable_key="alice", canonical_name="Alice"),
        NodeDraft(node_type="project", stable_key="alpha", canonical_name="Alpha"),
    )
    assert not result.ok
