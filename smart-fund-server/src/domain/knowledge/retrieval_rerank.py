"""Domain helpers for KG candidate reranking.

This module stays infrastructure-free: it prepares reranker input documents,
applies deterministic hygiene before model rerank, and maps reranker indexes
back to RetrievalHit objects.
"""

from __future__ import annotations

import re
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
        family_counts[family_key] = family_counts.get(family_key, 0) + 1
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
        "family_count": len(family_counts),
        "max_documents": max_documents,
        "type_counts": _type_counts(filtered),
        "source_channel_counts": _source_channel_counts(filtered),
        "document_samples": _document_samples(candidates),
    }
    return RerankPreparation(candidates=candidates, diagnostics=diagnostics)


def build_rerank_document(query: str, hit: RetrievalHit) -> str:
    """Build a compact, query-aware reranker document for one candidate."""

    lines = [
        "候选类型: 证据分片",
        f"标题: {hit.title.strip()}",
    ]
    if hit.snippet:
        lines.append(f"片段: {_clip_whitespace(hit.snippet, 900)}")
    channel_summary = _channel_summary(hit)
    if channel_summary:
        lines.append(f"命中线索: {channel_summary}")
    if not hit.evidence_refs:
        lines.append("证据警告: 当前候选没有直接证据引用，相关性应保守判断。")
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
    if hit.hit_type != "evidence":
        return "non_evidence_chunk"
    if not (hit.hit_id.startswith("kg_chunk:") or hit.evidence_refs):
        return "missing_evidence_chain"
    return ""


def _hygiene_flags(hit: RetrievalHit) -> list[str]:
    flags: list[str] = []
    if not hit.evidence_refs:
        flags.append("no_evidence_refs")
    if len(hit.source_channels or []) > 1:
        flags.append("multi_channel")
    return flags


def _channel_summary(hit: RetrievalHit) -> str:
    channels = hit.source_channels or [hit.source]
    parts: list[str] = []
    if channels:
        parts.append("召回通道=" + ",".join(channels))
    if hit.node_refs:
        parts.append(f"关联节点数={len(hit.node_refs)}")
    if hit.edge_refs:
        parts.append(f"关联关系数={len(hit.edge_refs)}")
    if hit.matched_terms:
        parts.append("命中词=" + ",".join(hit.matched_terms[:6]))
    return "；".join(parts)


def _hit_type_label(hit_type: str) -> str:
    labels = {
        "node": "图谱节点",
        "edge": "图谱关系",
        "path": "图路径",
        "wiki": "背景知识",
        "evidence": "原始证据",
        "semantic_hybrid": "语义候选",
    }
    return labels.get(hit_type, hit_type)


def _relation_summary(hit: RetrievalHit) -> str:
    if hit.hit_type != "edge":
        return ""
    relation_type = _relation_type_from_title(hit.title)
    explanations = {
        "affects": "影响；优先用于判断资产、行业、风险或利好利空。",
        "benefits_from": "受益；优先用于判断利好、受益主体或资产影响。",
        "hurt_by": "受损；优先用于判断负面事件、风险和利空影响。",
        "related_to": "弱相关；需依赖证据短句确认相关性。",
        "causal_hint": "因果线索；需证据精读确认。",
        "belongs_to": "归属；用于定位分类，不等同于事件影响。",
        "holds": "持有；用于资产暴露和持仓关联。",
    }
    explanation = explanations.get(relation_type)
    if not explanation:
        return ""
    return f"{relation_type}: {explanation}"


def _relation_type_from_title(title: str) -> str:
    relation_types = [
        "benefits_from",
        "related_to",
        "causal_hint",
        "mentions",
        "affects",
        "hurt_by",
        "belongs_to",
        "holds",
    ]
    for relation_type in relation_types:
        if f" {relation_type} " in title or f"--{relation_type}-->" in title:
            return relation_type
    return ""


def _focus_summary(query: str, hit: RetrievalHit) -> str:
    if hit.hit_type == "edge":
        edge_fact = _parse_edge_fact(hit)
        if not edge_fact:
            return ""
        target_name = edge_fact["target_name"]
        target_type = edge_fact["target_type"]
        relation_type = edge_fact["relation_type"]
        role = _node_role_label(target_type)
        answer_fit = _answer_fit_summary(query, target_name=target_name, target_type=target_type)
        return f"关系目标={target_name}；目标角色={role}；关系类型={relation_type}；{answer_fit}"
    node_type = _node_type_from_snippet(hit.snippet)
    if node_type:
        return f"节点角色={_node_role_label(node_type)}；{_answer_fit_summary(query, target_name=hit.title, target_type=node_type)}"
    return ""


def _parse_edge_fact(hit: RetrievalHit) -> dict[str, str] | None:
    text = hit.snippet or hit.title
    match = re.search(r"关系事实:\s*(.*?)（(.*?)）\s*--([a-zA-Z_]+)-->\s*(.*?)（(.*?)）", text)
    if match:
        return {
            "source_name": match.group(1).strip(),
            "source_type": match.group(2).strip(),
            "relation_type": match.group(3).strip(),
            "target_name": match.group(4).strip(),
            "target_type": match.group(5).strip(),
        }
    title_match = re.search(r"(.+?)\s+([a-zA-Z_]+)\s+(.+)", hit.title)
    if not title_match:
        return None
    return {
        "source_name": title_match.group(1).strip(),
        "source_type": "unknown",
        "relation_type": title_match.group(2).strip(),
        "target_name": title_match.group(3).strip(),
        "target_type": "unknown",
    }


def _node_type_from_snippet(snippet: str) -> str:
    match = re.search(r"节点类型:\s*([^；\n]+)", snippet or "")
    return match.group(1).strip() if match else ""


def _node_role_label(node_type: str) -> str:
    return node_type or "未知"


def _answer_fit_summary(query: str, *, target_name: str, target_type: str) -> str:
    query_text = query or ""
    source_like_terms = ["资讯", "日报", "证券报", "新闻", "媒体", "数据"]
    if any(term in target_name for term in source_like_terms):
        return "答案适配=低：更像信息来源或数据来源，通常不应作为主体/行业/资产影响的主答案"
    if target_name and target_name in query_text:
        return "答案适配=高：候选名称直接出现在 query 中"
    if any(term in query_text for term in ["主体", "行业", "板块", "产业", "资产", "标的", "影响", "利好", "利空", "受益", "风险", "负面"]):
        return "答案适配=中高：query 明确询问对象或影响，需要结合证据判断"
    return "答案适配=待判断：需要结合证据短句判断是否回答 query"


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


def _document_samples(candidates: list[RerankPreparedCandidate], *, limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "candidate_key": candidate.candidate_key,
            "type": candidate.hit.hit_type,
            "title": candidate.hit.title,
            "document": _clip_whitespace(candidate.document, 1200),
        }
        for candidate in candidates[:limit]
    ]


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
