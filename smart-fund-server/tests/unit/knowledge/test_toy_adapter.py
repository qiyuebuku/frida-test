"""Unit tests for the toy adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain.knowledge.adapter import validate_edge_against_adapter
from src.domain.knowledge.enums import EvidenceType
from src.domain.knowledge.schemas import KnowledgeInput
from src.domain.knowledge.toy_adapter import ToyProjectAdapter


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "knowledge"


def _load_json(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_toy_adapter_normalizes_fixture() -> None:
    adapter = ToyProjectAdapter()
    records = _load_json("toy_records.json")
    inputs = adapter.normalize(records)

    assert len(inputs) == 1
    assert isinstance(inputs[0], KnowledgeInput)
    assert inputs[0].adapter_name == adapter.spec.name
    assert inputs[0].adapter_version == adapter.spec.version


@pytest.mark.asyncio
async def test_toy_adapter_extracts_expected_node_types() -> None:
    adapter = ToyProjectAdapter()
    item = adapter.normalize(_load_json("toy_records.json"))[0]
    nodes = await adapter.extract_node_drafts(item)

    expected = _load_json("toy_adapter_expected.json")
    assert sorted({node.node_type for node in nodes}) == expected["node_types"]
    assert {node.stable_key for node in nodes} == {"Alice", "Alpha", "Beta", "Design Note"}


@pytest.mark.asyncio
async def test_toy_adapter_extracts_expected_relation_types() -> None:
    adapter = ToyProjectAdapter()
    item = adapter.normalize(_load_json("toy_records.json"))[0]
    nodes = await adapter.extract_node_drafts(item)
    edges = await adapter.extract_edge_drafts(item, nodes)

    expected = _load_json("toy_adapter_expected.json")
    assert sorted({edge.relation_type for edge in edges}) == expected["relation_types"]
    assert len(edges) == 3


def test_toy_adapter_extracts_evidence() -> None:
    adapter = ToyProjectAdapter()
    item = adapter.normalize(_load_json("toy_records.json"))[0]
    evidence = adapter.extract_evidence_drafts(item)

    assert len(evidence) == 1
    assert evidence[0].evidence_type == EvidenceType.TEXT_SPAN
    assert evidence[0].content


@pytest.mark.asyncio
async def test_toy_adapter_edges_validate_against_ontology() -> None:
    adapter = ToyProjectAdapter()
    item = adapter.normalize(_load_json("toy_records.json"))[0]
    nodes = await adapter.extract_node_drafts(item)
    node_by_key = {node.stable_key: node for node in nodes}
    edges = await adapter.extract_edge_drafts(item, nodes)

    for edge in edges:
        result = validate_edge_against_adapter(
            adapter.spec,
            edge,
            node_by_key[edge.source_ref],
            node_by_key[edge.target_ref],
        )
        assert result.ok


@pytest.mark.asyncio
async def test_toy_adapter_expected_fixture_matches_output() -> None:
    adapter = ToyProjectAdapter()
    expected = _load_json("toy_adapter_expected.json")
    records = _load_json("toy_records.json")
    inputs = adapter.normalize(records)
    nodes = await adapter.extract_node_drafts(inputs[0])
    edges = await adapter.extract_edge_drafts(inputs[0], nodes)

    assert expected["input_count"] == len(inputs)
    assert expected["node_types"] == sorted({node.node_type for node in nodes})
    assert expected["relation_types"] == sorted({edge.relation_type for edge in edges})
