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
    hit_type: str = "evidence",
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


def test_prepare_rerank_candidates_dedupes_and_filters_without_family_cap() -> None:
    hits = [
        _hit("kg_chunk:1", title="并购重组", evidence_refs=["kg_ev:1"], score=1.0),
        _hit("kg_chunk:1", title="并购重组", evidence_refs=["kg_ev:1"], score=2.0),
        _hit("kg_chunk:2", title="科创板八条", evidence_refs=["kg_ev:1"], score=0.9),
        _hit("kg_chunk:3", title="券商", evidence_refs=["kg_ev:1"], score=0.8),
        RetrievalHit(
            hit_id="kg:empty",
            hit_type="evidence",
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
    )

    assert [item.hit.hit_id for item in prepared.candidates] == ["kg_chunk:1", "kg_chunk:2", "kg_chunk:3"]
    assert prepared.diagnostics["deduped_count"] == 4
    assert prepared.diagnostics["filter_reason_counts"] == {
        "missing_readable_content": 1,
    }
    assert "候选含义:" not in prepared.candidates[0].document
    assert "证据状态: 有直接证据支撑" not in prepared.candidates[0].document
    assert prepared.diagnostics["document_samples"][0]["candidate_key"] == "C1"


def test_prepare_rerank_filters_non_evidence_navigation_hits() -> None:
    prepared = prepare_rerank_candidates(
        "A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
        [
            RetrievalHit(
                hit_id="kg_edge:financial:mentions:1",
                hit_type="edge",
                title="A股并购重组市场呈现三方面新变化 mentions 半导体",
                snippet=(
                    "关系事实: A股并购重组市场呈现三方面新变化（event） "
                    "--mentions--> 半导体（industry）\n"
                    "关系焦点: 半导体；焦点类型: industry\n"
                    "证据短句: 新闻提到半导体、生物医药、高端装备制造等新质生产力领域。"
                ),
                score=42.0,
                source="pg_deterministic",
                source_channels=["pg_deterministic", "semantic_hybrid"],
                evidence_refs=["kg_ev:1"],
            )
        ],
    )

    assert prepared.candidates == []
    assert prepared.diagnostics["filter_reason_counts"] == {"non_evidence_chunk": 1}


def test_prepare_rerank_evidence_document_uses_readable_chunk_text_and_channels() -> None:
    prepared = prepare_rerank_candidates(
        "A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
        [
            RetrievalHit(
                hit_id="kg_chunk:financial:1",
                hit_type="evidence",
                title="A股并购重组市场呈现三方面新变化",
                snippet="新闻提到半导体、生物医药、高端装备制造等新质生产力领域。",
                score=42.0,
                source="pg_deterministic",
                source_channels=["pg_deterministic", "semantic_hybrid"],
                evidence_refs=["kg_ev:1"],
                edge_refs=["kg_edge:financial:mentions:1"],
                node_refs=["kg:financial:industry:semi"],
            )
        ],
    )

    document = prepared.candidates[0].document
    assert "候选类型: 证据分片" in document
    assert "标题: A股并购重组市场呈现三方面新变化" in document
    assert "片段: 新闻提到半导体、生物医药、高端装备制造等新质生产力领域。" in document
    assert "命中线索: 召回通道=pg_deterministic,semantic_hybrid；关联节点数=1；关联关系数=1" in document
    assert "candidate_id:" not in document


def test_apply_rerank_scores_reorders_and_records_score_metadata() -> None:
    prepared = prepare_rerank_candidates(
        "查询",
        [
            _hit("kg_chunk:a", title="A", evidence_refs=["kg_ev:a"], score=10.0),
            _hit("kg_chunk:b", title="B", evidence_refs=["kg_ev:b"], score=9.0),
        ],
    )

    reranked = apply_rerank_scores(
        prepared.candidates,
        [
            RerankScoredIndex(index=1, relevance_score=0.41),
            RerankScoredIndex(index=0, relevance_score=0.22),
        ],
    )

    assert [hit.hit_id for hit in reranked] == ["kg_chunk:b", "kg_chunk:a"]
    assert reranked[0].score == 0.41
    assert reranked[0].raw_scores["reranker"] == 0.41
    assert "reranker" in reranked[0].source_channels


def test_apply_rerank_scores_rejects_invalid_indexes() -> None:
    prepared = prepare_rerank_candidates("查询", [_hit("kg_chunk:a", evidence_refs=["kg_ev:a"])])

    with pytest.raises(ValueError, match="out-of-range"):
        apply_rerank_scores(prepared.candidates, [RerankScoredIndex(index=3, relevance_score=0.1)])
