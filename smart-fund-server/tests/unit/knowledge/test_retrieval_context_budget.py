"""Unit tests for retrieval context budget."""

from __future__ import annotations

from src.domain.knowledge.enums import ConfidenceLabel
from src.domain.knowledge.retrieval import (
    RetrievalHit,
    RetrievalOptions,
    apply_context_budget,
)


def test_context_budget_truncates_by_chars_but_keeps_first_hit() -> None:
    options = RetrievalOptions(adapter_name="toy", max_chars=200, max_hits=10)
    hits = [
        RetrievalHit(
            hit_id="a",
            hit_type="evidence",
            title="A",
            snippet="short",
            source="chunk",
            evidence_refs=["ev-a"],
        ),
        RetrievalHit(
            hit_id="b",
            hit_type="evidence",
            title="B",
            snippet="x" * 300,
            source="chunk",
            evidence_refs=["ev-b"],
        ),
    ]

    selected, usage = apply_context_budget(hits, options)

    assert [hit.hit_id for hit in selected] == ["a"]
    assert selected[0].evidence_refs == ["ev-a"]
    assert usage.truncated is True


def test_low_confidence_hit_can_be_identified_by_context_builder_contract() -> None:
    hit = RetrievalHit(
        hit_id="edge-a",
        hit_type="edge",
        title="related",
        snippet="A -> B",
        source="graph",
        confidence=ConfidenceLabel.AMBIGUOUS,
    )

    assert hit.confidence == ConfidenceLabel.AMBIGUOUS
