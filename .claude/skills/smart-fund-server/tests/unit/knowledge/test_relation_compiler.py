"""Unit tests for relation compilation."""

from __future__ import annotations

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus
from src.domain.knowledge.relation_compiler import RelationCompiler
from src.domain.knowledge.resolver import EntityResolver
from src.domain.knowledge.schemas import EdgeDraft, NodeDraft
from src.domain.knowledge.toy_adapter import ToyProjectAdapter


def test_relation_compiler_compiles_active_edge_with_evidence() -> None:
    nodes = [
        NodeDraft(node_type="person", stable_key="Alice", canonical_name="Alice"),
        NodeDraft(node_type="project", stable_key="Alpha", canonical_name="Alpha"),
    ]
    node_result = EntityResolver().resolve(
        adapter_spec=ToyProjectAdapter.spec,
        version="v1",
        drafts=nodes,
    )
    edge = EdgeDraft(
        source_ref="Alice",
        target_ref="Alpha",
        relation_type="owns",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_refs=["note:n1"],
    )

    result = RelationCompiler().compile(
        adapter_spec=ToyProjectAdapter.spec,
        version="v1",
        drafts=[edge],
        draft_by_ref=node_result.draft_by_ref,
        node_id_by_ref=node_result.node_id_by_ref,
        evidence_ref_map={"note:n1": ["kg_ev:toy:note:n1:abc"]},
    )

    assert len(result.edges) == 1
    assert result.edges[0].relation_type == "owns"
    assert result.edges[0].evidence_ids == ["kg_ev:toy:note:n1:abc"]


def test_relation_compiler_rejects_unresolved_endpoint() -> None:
    edge = EdgeDraft(
        source_ref="Alice",
        target_ref="Alpha",
        relation_type="owns",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_refs=["note:n1"],
    )

    result = RelationCompiler().compile(
        adapter_spec=ToyProjectAdapter.spec,
        version="v1",
        drafts=[edge],
        draft_by_ref={},
        node_id_by_ref={},
        evidence_ref_map={"note:n1": ["kg_ev:toy:note:n1:abc"]},
    )

    assert result.edges == []
    assert result.failed_records[0].reason == "edge endpoint cannot be resolved"
    assert result.failed_records[0].details["source_resolved"] is False
    assert result.failed_records[0].details["target_resolved"] is False
    assert result.failed_records[0].details["source_ref"] == "Alice"
    assert result.failed_records[0].details["target_ref"] == "Alpha"


def test_relation_compiler_rejects_active_edge_with_unresolved_evidence() -> None:
    nodes = [
        NodeDraft(node_type="person", stable_key="Alice", canonical_name="Alice"),
        NodeDraft(node_type="project", stable_key="Alpha", canonical_name="Alpha"),
    ]
    node_result = EntityResolver().resolve(
        adapter_spec=ToyProjectAdapter.spec,
        version="v1",
        drafts=nodes,
    )
    edge = EdgeDraft(
        source_ref="Alice",
        target_ref="Alpha",
        relation_type="owns",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_refs=["missing"],
    )

    result = RelationCompiler().compile(
        adapter_spec=ToyProjectAdapter.spec,
        version="v1",
        drafts=[edge],
        draft_by_ref=node_result.draft_by_ref,
        node_id_by_ref=node_result.node_id_by_ref,
        evidence_ref_map={},
    )

    assert result.edges == []
    assert result.failed_records[0].reason == "edge evidence cannot be resolved"
