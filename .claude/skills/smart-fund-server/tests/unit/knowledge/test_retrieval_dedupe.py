"""Unit tests for retrieval hit dedupe."""

from __future__ import annotations

from src.domain.knowledge.retrieval import RetrievalHit, dedupe_hits


def test_dedupe_hits_uses_canonical_node_ref_and_keeps_best_score() -> None:
    lower = RetrievalHit(
        hit_id="node-alpha-2",
        hit_type="node",
        title="Alpha duplicate",
        snippet="Alpha",
        source="keyword",
        score=0.1,
        node_refs=["kg:toy:project:alpha"],
    )
    higher = RetrievalHit(
        hit_id="node-alpha",
        hit_type="node",
        title="Alpha",
        snippet="Alpha",
        source="keyword",
        score=0.2,
        node_refs=["kg:toy:project:alpha"],
    )

    result = dedupe_hits([lower, higher])

    assert len(result) == 1
    assert result[0].hit_id == "node-alpha"
