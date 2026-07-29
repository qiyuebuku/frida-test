"""Unit tests for hybrid retrieval fusion."""

from __future__ import annotations

from src.domain.knowledge.retrieval import RetrievalHit, reciprocal_rank_fusion


def test_reciprocal_rank_fusion_sums_scores_and_sorts_stably() -> None:
    alpha = RetrievalHit(
        hit_id="a",
        hit_type="node",
        title="Alpha",
        snippet="Alpha",
        source="keyword",
    )
    beta = RetrievalHit(
        hit_id="b",
        hit_type="node",
        title="Beta",
        snippet="Beta",
        source="wiki",
    )

    result = reciprocal_rank_fusion([[alpha, beta], [beta]], k=60)

    assert [hit.hit_id for hit in result] == ["b", "a"]
    assert result[0].score > result[1].score
