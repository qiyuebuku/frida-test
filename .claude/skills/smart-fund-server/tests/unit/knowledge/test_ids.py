"""Unit tests for stable knowledge identifiers."""

from __future__ import annotations

import pytest

from src.domain.knowledge.enums import EvidenceType
from src.domain.knowledge.ids import (
    make_edge_id,
    make_evidence_id,
    make_node_id,
    stable_hash,
)


def test_stable_hash_sorts_mapping_keys() -> None:
    assert stable_hash({"b": 2, "a": 1}) == stable_hash({"a": 1, "b": 2})


def test_make_node_id_is_stable_for_same_input() -> None:
    first = make_node_id("toy", "person", "alice")
    second = make_node_id("toy", "person", "alice")
    assert first == second
    assert first.startswith("kg:toy:person:")


def test_make_node_id_differs_by_adapter() -> None:
    assert make_node_id("toy", "person", "alice") != make_node_id("demo", "person", "alice")


def test_make_node_id_differs_by_node_type() -> None:
    assert make_node_id("toy", "person", "alice") != make_node_id("toy", "project", "alice")


def test_make_edge_id_ignores_evidence_order() -> None:
    first = make_edge_id("toy", "owns", "kg:toy:person:a", "kg:toy:project:b", ["ev2", "ev1"])
    second = make_edge_id("toy", "owns", "kg:toy:person:a", "kg:toy:project:b", ["ev1", "ev2"])
    assert first == second
    assert first.startswith("kg_edge:toy:owns:")


def test_make_edge_id_keeps_direction() -> None:
    forward = make_edge_id("toy", "owns", "kg:toy:person:a", "kg:toy:project:b", ["ev1"])
    reverse = make_edge_id("toy", "owns", "kg:toy:project:b", "kg:toy:person:a", ["ev1"])
    assert forward != reverse


def test_make_evidence_id_is_stable_for_same_input() -> None:
    first = make_evidence_id(
        "toy",
        "note",
        "n1",
        EvidenceType.TEXT_SPAN,
        "Alice owns Alpha.",
        {},
    )
    second = make_evidence_id(
        "toy",
        "note",
        "n1",
        EvidenceType.TEXT_SPAN,
        "Alice owns Alpha.",
        {},
    )
    assert first == second
    assert first.startswith("kg_ev:toy:note:n1:")


@pytest.mark.parametrize(
    ("factory", "args"),
    [
        (make_node_id, ("", "person", "alice")),
        (make_node_id, ("toy", "", "alice")),
        (make_node_id, ("toy", "person", "")),
        (make_edge_id, ("", "owns", "kg:toy:person:a", "kg:toy:project:b", [])),
        (make_edge_id, ("toy", "", "kg:toy:person:a", "kg:toy:project:b", [])),
        (make_edge_id, ("toy", "owns", "", "kg:toy:project:b", [])),
        (make_edge_id, ("toy", "owns", "kg:toy:person:a", "", [])),
        (make_evidence_id, ("", "note", "n1", EvidenceType.TEXT_SPAN, "x", {})),
        (make_evidence_id, ("toy", "", "n1", EvidenceType.TEXT_SPAN, "x", {})),
        (make_evidence_id, ("toy", "note", "", EvidenceType.TEXT_SPAN, "x", {})),
    ],
)
def test_id_helpers_reject_empty_required_fields(factory, args) -> None:
    with pytest.raises(ValueError):
        factory(*args)
