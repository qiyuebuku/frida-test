"""Unit tests for generic knowledge schemas."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.domain.knowledge.enums import (
    ConfidenceLabel,
    EdgeStatus,
    EvidenceType,
    InputType,
    NodeStatus,
    ValidationSeverity,
)
from src.domain.knowledge.schemas import (
    CompiledEdge,
    CompiledEvidence,
    CompiledNode,
    EdgeDraft,
    EvidenceDraft,
    KnowledgeInput,
    NodeDraft,
    ValidationIssue,
)


NOW = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)


def _valid_input(**overrides) -> KnowledgeInput:
    data = {
        "input_type": InputType.STRUCTURED_RECORD,
        "source_type": "note",
        "source_id": "n1",
        "observed_at": NOW,
        "adapter_name": "toy",
        "adapter_version": "v1",
        "payload": {"title": "Alpha note"},
    }
    data.update(overrides)
    return KnowledgeInput(**data)


def test_knowledge_input_requires_source_type() -> None:
    with pytest.raises(ValidationError):
        _valid_input(source_type="")


def test_knowledge_input_requires_source_id() -> None:
    with pytest.raises(ValidationError):
        _valid_input(source_id="")


def test_knowledge_input_requires_adapter_name() -> None:
    with pytest.raises(ValidationError):
        _valid_input(adapter_name="")


def test_knowledge_input_requires_payload_or_raw_text() -> None:
    with pytest.raises(ValidationError):
        _valid_input(payload={}, raw_text=None)


def test_node_draft_allows_arbitrary_node_type() -> None:
    node = NodeDraft(
        node_type="artifact",
        stable_key="a1",
        canonical_name="Artifact One",
    )
    assert node.node_type == "artifact"
    assert node.status == NodeStatus.CANDIDATE


def test_node_draft_requires_canonical_name() -> None:
    with pytest.raises(ValidationError):
        NodeDraft(node_type="artifact", stable_key="a1", canonical_name="")


def test_edge_draft_requires_source_ref() -> None:
    with pytest.raises(ValidationError):
        EdgeDraft(source_ref="", target_ref="b", relation_type="links_to")


def test_edge_draft_requires_target_ref() -> None:
    with pytest.raises(ValidationError):
        EdgeDraft(source_ref="a", target_ref="", relation_type="links_to")


def test_edge_draft_requires_relation_type() -> None:
    with pytest.raises(ValidationError):
        EdgeDraft(source_ref="a", target_ref="b", relation_type="")


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_edge_draft_rejects_invalid_confidence_score(score: float) -> None:
    with pytest.raises(ValidationError):
        EdgeDraft(source_ref="a", target_ref="b", relation_type="links_to", confidence_score=score)


def test_rejected_edge_draft_cannot_be_active() -> None:
    with pytest.raises(ValidationError):
        EdgeDraft(
            source_ref="a",
            target_ref="b",
            relation_type="links_to",
            confidence_label=ConfidenceLabel.REJECTED,
            status=EdgeStatus.ACTIVE,
            evidence_refs=["ev1"],
        )


def test_active_edge_draft_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        EdgeDraft(
            source_ref="a",
            target_ref="b",
            relation_type="links_to",
            status=EdgeStatus.ACTIVE,
        )


def test_evidence_draft_requires_content_or_payload() -> None:
    with pytest.raises(ValidationError):
        EvidenceDraft(evidence_type=EvidenceType.TEXT_SPAN, source_type="note", source_id="n1")


def test_evidence_draft_rejects_invalid_span_range() -> None:
    with pytest.raises(ValidationError):
        EvidenceDraft(
            evidence_type=EvidenceType.TEXT_SPAN,
            source_type="note",
            source_id="n1",
            content="abc",
            span_start=3,
            span_end=1,
        )


def test_compiled_node_id_prefix() -> None:
    with pytest.raises(ValidationError):
        CompiledNode(
            node_id="bad",
            adapter_name="toy",
            node_type="artifact",
            canonical_name="Artifact One",
            status=NodeStatus.ACTIVE,
            version="v1",
        )


def test_compiled_edge_id_prefix() -> None:
    with pytest.raises(ValidationError):
        CompiledEdge(
            edge_id="bad",
            adapter_name="toy",
            source_node_id="kg:toy:artifact:a",
            target_node_id="kg:toy:artifact:b",
            relation_type="links_to",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=1.0,
            status=EdgeStatus.ACTIVE,
            evidence_ids=["kg_ev:toy:note:n1:x"],
            version="v1",
        )


def test_compiled_edge_requires_node_ref_prefix() -> None:
    with pytest.raises(ValidationError):
        CompiledEdge(
            edge_id="kg_edge:toy:links_to:x",
            adapter_name="toy",
            source_node_id="bad",
            target_node_id="kg:toy:artifact:b",
            relation_type="links_to",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=1.0,
            status=EdgeStatus.ACTIVE,
            evidence_ids=["kg_ev:toy:note:n1:x"],
            version="v1",
        )


def test_compiled_edge_active_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        CompiledEdge(
            edge_id="kg_edge:toy:links_to:x",
            adapter_name="toy",
            source_node_id="kg:toy:artifact:a",
            target_node_id="kg:toy:artifact:b",
            relation_type="links_to",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=1.0,
            status=EdgeStatus.ACTIVE,
            version="v1",
        )


def test_compiled_evidence_id_prefix() -> None:
    with pytest.raises(ValidationError):
        CompiledEvidence(
            evidence_id="bad",
            adapter_name="toy",
            evidence_type=EvidenceType.TEXT_SPAN,
            source_type="note",
            source_id="n1",
            content="Alice owns Alpha.",
            version="v1",
        )


def test_validation_issue_requires_message() -> None:
    with pytest.raises(ValidationError):
        ValidationIssue(severity=ValidationSeverity.ERROR, message="")
