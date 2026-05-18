"""Domain helpers for KG candidate reranking.

This module stays infrastructure-free: it prepares reranker input documents,
applies deterministic hygiene before model rerank, and maps reranker indexes
back to RetrievalHit objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.domain.knowledge.retrieval import RetrievalHit, dedupe_hits


@dataclass(frozen=True)
class RerankPreparedCandidate:
    candidate_key: str
    hit: RetrievalHit
    document: str
    family_key: str
    hygiene_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RerankPreparation:
    candidates: list[RerankPreparedCandidate]
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RerankScoredIndex:
    index: int
    relevance_score: float


def prepare_rerank_candidates(
    query: str,
    hits: list[RetrievalHit],
    *,
    max_documents: int = 100,
    max_per_family: int = 8,
) -> RerankPreparation:
    """Clean and convert retrieval hits into short reranker documents."""

    deduped = dedupe_hits(hits)
    filtered: list[RetrievalHit] = []
    reason_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for hit in deduped:
        reason = _hard_filter_reason(hit)
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            continue
        family_key = _family_key(hit)
        count = family_counts.get(family_key, 0)
        if count >= max_per_family:
            reason_counts["family_diversity_cap"] = reason_counts.get("family_diversity_cap", 0) + 1
            continue
        family_counts[family_key] = count + 1
        filtered.append(hit)
        if len(filtered) >= max_documents:
            break

    candidates = [
        RerankPreparedCandidate(
            candidate_key=f"C{index}",
            hit=hit,
            document=build_rerank_document(query, hit),
            family_key=_family_key(hit),
            hygiene_flags=tuple(_hygiene_flags(hit)),
        )
        for index, hit in enumerate(filtered, start=1)
    ]
    diagnostics = {
        "input_count": len(hits),
        "deduped_count": len(deduped),
        "prepared_count": len(candidates),
        "filtered_count": len(deduped) - len(filtered),
        "filter_reason_counts": reason_counts,
        "family_cap": max_per_family,
        "family_count": len(family_counts),
        "max_documents": max_documents,
        "type_counts": _type_counts(filtered),
        "source_channel_counts": _source_channel_counts(filtered),
    }
    return RerankPreparation(candidates=candidates, diagnostics=diagnostics)


def build_rerank_document(query: str, hit: RetrievalHit) -> str:
    """Build a compact, query-aware reranker document for one candidate."""

    channels = hit.source_channels or [hit.source]
    evidence_refs = hit.evidence_refs[:4]
    matched_terms = hit.matched_terms[:8]
    relation_or_title = hit.title.strip()
    lines = [
        f"query: {query.strip()}",
        f"candidate_id: {hit.hit_id}",
        f"type: {hit.hit_type}",
        f"title: {relation_or_title}",
        f"meaning: {_candidate_meaning(hit)}",
        f"channels: {', '.join(item for item in channels if item)}",
    ]
    if hit.node_refs:
        lines.append(f"node_refs: {', '.join(hit.node_refs[:5])}")
    if hit.edge_refs:
        lines.append(f"edge_refs: {', '.join(hit.edge_refs[:5])}")
    if evidence_refs:
        lines.append(f"evidence_refs: {', '.join(evidence_refs)}")
    if matched_terms:
        lines.append(f"matched_terms: {', '.join(matched_terms)}")
    if hit.matched_fields:
        lines.append(f"matched_fields: {', '.join(hit.matched_fields[:8])}")
    if hit.snippet:
        lines.append(f"evidence_summary: {_clip_whitespace(hit.snippet, 900)}")
    if hit.hit_type == "wiki":
        lines.append("background_policy: wiki/background candidate; useful only when it supports the query.")
    if not evidence_refs:
        lines.append("lineage_warning: no direct evidence_ref attached.")
    return "\n".join(lines)


def apply_rerank_scores(
    candidates: list[RerankPreparedCandidate],
    scored_indexes: list[RerankScoredIndex],
) -> list[RetrievalHit]:
    """Map reranker result indexes back to hits and store reranker score metadata."""

    result: list[RetrievalHit] = []
    seen_indexes: set[int] = set()
    for item in scored_indexes:
        if item.index < 0 or item.index >= len(candidates):
            raise ValueError(f"reranker returned out-of-range index: {item.index}")
        if item.index in seen_indexes:
            raise ValueError(f"reranker returned duplicate index: {item.index}")
        seen_indexes.add(item.index)
        candidate = candidates[item.index]
        hit = candidate.hit
        raw_scores = dict(hit.raw_scores or {})
        raw_scores["reranker"] = float(item.relevance_score)
        result.append(
            hit.model_copy(
                update={
                    "score": float(item.relevance_score),
                    "source_channels": _ordered_unique([*(hit.source_channels or [hit.source]), "reranker"]),
                    "raw_scores": raw_scores,
                }
            )
        )
    return result


def rerank_index_payload(
    candidates: list[RerankPreparedCandidate],
    scored_indexes: list[RerankScoredIndex],
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for rank, scored in enumerate(scored_indexes, start=1):
        candidate = candidates[scored.index]
        payload.append(
            {
                "rank": rank,
                "candidate_key": candidate.candidate_key,
                "candidate_id": candidate.hit.hit_id,
                "type": candidate.hit.hit_type,
                "title": candidate.hit.title,
                "relevance_score": round(scored.relevance_score, 6),
                "family_key": candidate.family_key,
                "hygiene_flags": list(candidate.hygiene_flags),
            }
        )
    return payload


def _hard_filter_reason(hit: RetrievalHit) -> str:
    if not hit.hit_id.strip():
        return "missing_id"
    if not hit.title.strip() and not hit.snippet.strip():
        return "missing_readable_content"
    if hit.hit_type not in {"node", "edge", "path", "wiki", "evidence", "semantic_hybrid"}:
        return "unsupported_hit_type"
    return ""


def _hygiene_flags(hit: RetrievalHit) -> list[str]:
    flags: list[str] = []
    if not hit.evidence_refs:
        flags.append("no_evidence_refs")
    if hit.hit_type == "wiki":
        flags.append("background")
    if len(hit.source_channels or []) > 1:
        flags.append("multi_channel")
    return flags


def _candidate_meaning(hit: RetrievalHit) -> str:
    if hit.hit_type == "edge":
        return f"图谱关系候选，用于判断 query 中实体、行业或资产之间是否存在直接关系：{hit.title}"
    if hit.hit_type == "evidence":
        return f"原始证据候选，可能直接回答 query 或支撑相关节点：{hit.title}"
    if hit.hit_type == "wiki":
        return f"背景知识候选，只能补充解释，不能单独当作事实答案：{hit.title}"
    if hit.hit_type == "path":
        return f"图路径候选，用于解释多跳关系或影响链路：{hit.title}"
    return f"图谱节点候选，需判断它是否是 query 所问的主体、行业、资产、事件或政策：{hit.title}"


def _family_key(hit: RetrievalHit) -> str:
    if hit.evidence_refs:
        return f"evidence:{hit.evidence_refs[0]}"
    if hit.edge_refs:
        return f"edge:{hit.edge_refs[0]}"
    if hit.node_refs:
        return f"node:{hit.node_refs[0]}"
    return f"hit:{hit.hit_id}"


def _type_counts(hits: list[RetrievalHit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.hit_type] = counts.get(hit.hit_type, 0) + 1
    return counts


def _source_channel_counts(hits: list[RetrievalHit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        channels = hit.source_channels or [hit.source]
        for channel in channels:
            counts[channel] = counts.get(channel, 0) + 1
    return counts


def _clip_whitespace(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + f"...[truncated {len(compact) - limit} chars]"


def _ordered_unique(values) -> list:
    result: list = []
    seen: set = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
