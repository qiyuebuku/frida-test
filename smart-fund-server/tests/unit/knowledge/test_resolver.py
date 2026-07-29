"""Unit tests for deterministic node resolution."""

from __future__ import annotations

from src.domain.knowledge.resolver import EntityResolver
from src.domain.knowledge.schemas import NodeDraft
from src.domain.knowledge.toy_adapter import ToyProjectAdapter


def test_entity_resolver_dedupes_same_node() -> None:
    drafts = [
        NodeDraft(node_type="person", stable_key="Alice", canonical_name="Alice"),
        NodeDraft(node_type="person", stable_key="Alice", canonical_name="Alice A."),
    ]

    result = EntityResolver().resolve(
        adapter_spec=ToyProjectAdapter.spec,
        version="v1",
        drafts=drafts,
    )

    assert len(result.nodes) == 1
    assert result.node_id_by_ref["person:Alice"] == result.nodes[0].node_id
    assert result.node_id_by_ref["Alice"] == result.nodes[0].node_id


def test_entity_resolver_keeps_plain_ref_ambiguous_when_key_crosses_types() -> None:
    drafts = [
        NodeDraft(node_type="person", stable_key="Alpha", canonical_name="Alpha"),
        NodeDraft(node_type="project", stable_key="Alpha", canonical_name="Alpha"),
    ]

    result = EntityResolver().resolve(
        adapter_spec=ToyProjectAdapter.spec,
        version="v1",
        drafts=drafts,
    )

    assert len(result.nodes) == 2
    assert "person:Alpha" in result.node_id_by_ref
    assert "project:Alpha" in result.node_id_by_ref
    assert "Alpha" not in result.node_id_by_ref


def test_entity_resolver_reports_invalid_node_type() -> None:
    result = EntityResolver().resolve(
        adapter_spec=ToyProjectAdapter.spec,
        version="v1",
        drafts=[NodeDraft(node_type="missing", stable_key="x", canonical_name="X")],
    )

    assert result.nodes == []
    assert len(result.failed_records) == 1
    assert result.failed_records[0].reason == "node validation failed"
