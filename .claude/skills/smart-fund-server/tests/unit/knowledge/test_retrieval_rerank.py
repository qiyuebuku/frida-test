"""Tests for KG rerank candidate preparation."""

from __future__ import annotations

import pytest

from src.domain.knowledge.retrieval import RetrievalHit
from src.domain.knowledge.retrieval_rerank import (
    RerankScoredIndex,
    apply_rerank_scores,
    prepare_rerank_candidates,
)


def _hit(
    hit_id: str,
    *,
    title: str | None = None,
    hit_type: str = "node",
    evidence_refs: list[str] | None = None,
    score: float = 1.0,
) -> RetrievalHit:
    return RetrievalHit(
        hit_id=hit_id,
        hit_type=hit_type,  # type: ignore[arg-type]
        title=title if title is not None else hit_id,
        snippet=f"{title or hit_id} 证据摘要",
        score=score,
        source="keyword",
        evidence_refs=evidence_refs or [],
        matched_terms=["并购重组"],
        matched_fields=["title"],
    )


def test_prepare_rerank_candidates_dedupes_filters_and_caps_family() -> None:
    hits = [
        _hit("kg:node:1", title="并购重组", evidence_refs=["kg_ev:1"], score=1.0),
        _hit("kg:node:1", title="并购重组", evidence_refs=["kg_ev:1"], score=2.0),
        _hit("kg:node:2", title="科创板八条", evidence_refs=["kg_ev:1"], score=0.9),
        _hit("kg:node:3", title="券商", evidence_refs=["kg_ev:1"], score=0.8),
        RetrievalHit(
            hit_id="kg:empty",
            hit_type="node",
            title="",
            snippet="",
            score=0.7,
            source="keyword",
        ),
    ]

    prepared = prepare_rerank_candidates(
        "A股并购重组市场涉及哪些主体",
        hits,
        max_documents=10,
        max_per_family=2,
    )

    assert [item.hit.hit_id for item in prepared.candidates] == ["kg:node:1", "kg:node:2"]
    assert prepared.diagnostics["deduped_count"] == 4
    assert prepared.diagnostics["filter_reason_counts"] == {
        "family_diversity_cap": 1,
        "missing_readable_content": 1,
    }
    assert "meaning:" in prepared.candidates[0].document
    assert "evidence_refs: kg_ev:1" in prepared.candidates[0].document


def test_apply_rerank_scores_reorders_and_records_score_metadata() -> None:
    prepared = prepare_rerank_candidates(
        "查询",
        [
            _hit("kg:node:a", title="A", evidence_refs=["kg_ev:a"], score=10.0),
            _hit("kg:node:b", title="B", evidence_refs=["kg_ev:b"], score=9.0),
        ],
    )

    reranked = apply_rerank_scores(
        prepared.candidates,
        [
            RerankScoredIndex(index=1, relevance_score=0.41),
            RerankScoredIndex(index=0, relevance_score=0.22),
        ],
    )

    assert [hit.hit_id for hit in reranked] == ["kg:node:b", "kg:node:a"]
    assert reranked[0].score == 0.41
    assert reranked[0].raw_scores["reranker"] == 0.41
    assert "reranker" in reranked[0].source_channels


def test_apply_rerank_scores_rejects_invalid_indexes() -> None:
    prepared = prepare_rerank_candidates("查询", [_hit("kg:node:a", evidence_refs=["kg_ev:a"])])

    with pytest.raises(ValueError, match="out-of-range"):
        apply_rerank_scores(prepared.candidates, [RerankScoredIndex(index=3, relevance_score=0.1)])
