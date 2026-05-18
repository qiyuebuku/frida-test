"""Ranking cascade for agentic retrieval candidates."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from pydantic import Field

from src.domain.knowledge.retrieval import RetrievalHit, dedupe_hits
from src.domain.knowledge.schemas import KnowledgeBaseModel


class RecallCandidate(KnowledgeBaseModel):
    candidate_id: str
    candidate_type: str
    title: str
    source_channels: list[str] = Field(default_factory=list)
    channel_ranks: dict[str, int] = Field(default_factory=dict)
    raw_scores: dict[str, float] = Field(default_factory=dict)
    matched_terms: list[str] = Field(default_factory=list)
    matched_fields: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    node_refs: list[str] = Field(default_factory=list)
    edge_refs: list[str] = Field(default_factory=list)


class RankedCandidate(KnowledgeBaseModel):
    candidate: RecallCandidate
    hit: RetrievalHit
    fusion_score: float = 0.0
    feature_score: float = 0.0
    coverage_bonus: float = 0.0
    redundancy_penalty: float = 0.0
    final_score: float = 0.0
    rank_reasons: list[str] = Field(default_factory=list)
    coverage_terms: list[str] = Field(default_factory=list)
    new_coverage_terms: list[str] = Field(default_factory=list)


class JudgePreselectResult(KnowledgeBaseModel):
    selected: list[RankedCandidate] = Field(default_factory=list)
    remaining_high_potential: list[RankedCandidate] = Field(default_factory=list)
    missed_coverage_terms: list[str] = Field(default_factory=list)
    top_k_requested: int
    top_k_reason: str
    strategy_name: str = "rrf_feature_coverage"
    channel_contribution: dict[str, Any] = Field(default_factory=dict)


def judge_preselect(
    hits: list[RetrievalHit],
    *,
    query: str,
    anchor: Any,
    search_plan: Any,
    top_k_simple: int,
    top_k_complex: int,
    top_k_max: int,
) -> JudgePreselectResult:
    deduped = dedupe_hits(hits)
    if not deduped:
        return JudgePreselectResult(top_k_requested=0, top_k_reason="no_candidates")

    top_k, top_k_reason = _dynamic_top_k(
        query=query,
        anchor=anchor,
        search_plan=search_plan,
        top_k_simple=top_k_simple,
        top_k_complex=top_k_complex,
        top_k_max=top_k_max,
        candidate_count=len(deduped),
    )
    ranked = _rank_candidates(deduped, query=query, anchor=anchor, search_plan=search_plan)
    selected = _coverage_select(ranked, top_k=top_k, search_plan=search_plan)
    selected_ids = {item.candidate.candidate_id for item in selected}
    remaining = [item for item in ranked if item.candidate.candidate_id not in selected_ids]
    selected_coverage = _ordered_unique(term for item in selected for term in item.coverage_terms)
    missed_coverage_terms = [
        term for term in _coverage_requirements(search_plan) if term not in selected_coverage
    ]
    return JudgePreselectResult(
        selected=selected,
        remaining_high_potential=_diversify_remaining_high_potential(
            remaining,
            selected=selected,
            limit=max(top_k, 1),
        ),
        missed_coverage_terms=missed_coverage_terms,
        top_k_requested=top_k,
        top_k_reason=top_k_reason,
        channel_contribution=_channel_contribution(hits, selected),
    )


def _rank_candidates(
    hits: list[RetrievalHit],
    *,
    query: str,
    anchor: Any,
    search_plan: Any,
) -> list[RankedCandidate]:
    channel_ranks = _channel_ranks(hits)
    requirements = _coverage_requirements(search_plan)
    ranked: list[RankedCandidate] = []
    for hit in hits:
        source_channels = _hit_channels(hit)
        hit_channel_ranks = {
            channel: int(hit.channel_ranks.get(channel, channel_ranks.get((channel, hit.hit_id), 9999)))
            for channel in source_channels
        }
        candidate = RecallCandidate(
            candidate_id=hit.hit_id,
            candidate_type=hit.hit_type,
            title=hit.title,
            source_channels=source_channels,
            channel_ranks=hit_channel_ranks,
            raw_scores={
                channel: float(hit.raw_scores.get(channel, hit.score if channel == hit.source else 0.0) or 0.0)
                for channel in source_channels
            },
            matched_terms=hit.matched_terms,
            matched_fields=hit.matched_fields,
            evidence_refs=hit.evidence_refs,
            node_refs=hit.node_refs,
            edge_refs=hit.edge_refs,
        )
        fusion_score = _fusion_score(candidate.channel_ranks)
        feature_score, reasons = _feature_score(hit, query=query, anchor=anchor, search_plan=search_plan)
        matched_requirements = _matched_coverage_terms(hit, requirements)
        inferred_units = _candidate_coverage_units(hit, search_plan=search_plan, anchor=anchor)
        coverage_terms = _ordered_unique([*matched_requirements, *inferred_units])
        coverage_bonus = min((len(matched_requirements) * 0.35) + (len(inferred_units) * 0.18), 1.4)
        redundancy_penalty = 0.0
        final_score = fusion_score + feature_score + coverage_bonus - redundancy_penalty
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                hit=hit,
                fusion_score=fusion_score,
                feature_score=feature_score,
                coverage_bonus=coverage_bonus,
                redundancy_penalty=redundancy_penalty,
                final_score=final_score,
                rank_reasons=reasons,
                coverage_terms=coverage_terms,
            )
        )
    return sorted(ranked, key=lambda item: (-item.final_score, item.hit.hit_id))


def _coverage_select(
    ranked: list[RankedCandidate],
    *,
    top_k: int,
    search_plan: Any,
) -> list[RankedCandidate]:
    requirements = _coverage_requirements(search_plan)
    selected: list[RankedCandidate] = []
    selected_ids: set[str] = set()
    selected_semantic_keys: set[str] = set()
    covered_terms: set[str] = set()
    used_evidence: set[str] = set()
    cluster_counts: Counter[str] = Counter()
    cluster_edge_counts: Counter[str] = Counter()
    cluster_total_limit = _cluster_total_limit(top_k)
    answer_quota = max(1, (top_k + 1) // 2)
    background_quota = max(1, top_k // 5)
    answer_count = 0
    background_count = 0

    for term in requirements:
        candidate = _best_candidate_for_term(
            ranked,
            term,
            selected_ids,
            selected_semantic_keys,
        )
        if candidate is not None:
            candidate.new_coverage_terms = [term for term in candidate.coverage_terms if term not in covered_terms]
            selected.append(candidate)
            selected_ids.add(candidate.candidate.candidate_id)
            selected_semantic_keys.add(_selection_semantic_key(candidate))
            covered_terms.update(candidate.coverage_terms)
            used_evidence.update(candidate.candidate.evidence_refs)
            _record_cluster_selection(candidate, cluster_counts, cluster_edge_counts)
            role = _candidate_role(candidate.hit)
            if role == "answer":
                answer_count += 1
            elif role == "background":
                background_count += 1

    while len(selected) < top_k:
        best: RankedCandidate | None = None
        best_score = float("-inf")
        for candidate in ranked:
            if candidate.candidate.candidate_id in selected_ids:
                continue
            if _selection_semantic_key(candidate) in selected_semantic_keys:
                continue
            role = _candidate_role(candidate.hit)
            if role == "answer" and answer_count >= answer_quota and not _adds_coverage(candidate, covered_terms):
                continue
            if role == "background" and background_count >= background_quota:
                continue
            if not _passes_diversity_limits(
                candidate,
                covered_terms=covered_terms,
                cluster_counts=cluster_counts,
                cluster_edge_counts=cluster_edge_counts,
                cluster_total_limit=cluster_total_limit,
            ):
                continue
            score = candidate.final_score
            new_terms = _new_coverage_terms(candidate, covered_terms)
            if new_terms:
                score += min(len(new_terms) * 0.55, 1.4)
            if set(candidate.candidate.evidence_refs) & used_evidence:
                score -= 0.4
            if role == "answer":
                score += 0.2
            if score > best_score:
                best = candidate
                best_score = score
        if best is None:
            for candidate in ranked:
                if candidate.candidate.candidate_id not in selected_ids:
                    if _selection_semantic_key(candidate) in selected_semantic_keys:
                        continue
                    if not _passes_diversity_limits(
                        candidate,
                        covered_terms=covered_terms,
                        cluster_counts=cluster_counts,
                        cluster_edge_counts=cluster_edge_counts,
                        cluster_total_limit=cluster_total_limit,
                        allow_coverage_override=False,
                    ):
                        continue
                    best = candidate
                    break
        if best is None:
            break
        best.new_coverage_terms = _new_coverage_terms(best, covered_terms)
        selected.append(best)
        selected_ids.add(best.candidate.candidate_id)
        selected_semantic_keys.add(_selection_semantic_key(best))
        covered_terms.update(best.coverage_terms)
        used_evidence.update(best.candidate.evidence_refs)
        _record_cluster_selection(best, cluster_counts, cluster_edge_counts)
        role = _candidate_role(best.hit)
        if role == "answer":
            answer_count += 1
        elif role == "background":
            background_count += 1
    return selected


def _cluster_total_limit(top_k: int) -> int:
    if top_k <= 3:
        return top_k
    return max(3, min(5, (top_k + 2) // 3))


def _passes_diversity_limits(
    candidate: RankedCandidate,
    *,
    covered_terms: set[str],
    cluster_counts: Counter[str],
    cluster_edge_counts: Counter[str],
    cluster_total_limit: int,
    allow_coverage_override: bool = True,
) -> bool:
    cluster_key = _candidate_cluster_key(candidate)
    new_terms = _new_coverage_terms(candidate, covered_terms)
    if allow_coverage_override and new_terms:
        return True
    if candidate.hit.hit_type == "edge" and cluster_edge_counts[cluster_key] >= 1:
        return False
    return cluster_counts[cluster_key] < cluster_total_limit


def _record_cluster_selection(
    candidate: RankedCandidate,
    cluster_counts: Counter[str],
    cluster_edge_counts: Counter[str],
) -> None:
    cluster_key = _candidate_cluster_key(candidate)
    cluster_counts[cluster_key] += 1
    if candidate.hit.hit_type == "edge":
        cluster_edge_counts[cluster_key] += 1


def _diversify_remaining_high_potential(
    remaining: list[RankedCandidate],
    *,
    selected: list[RankedCandidate],
    limit: int,
) -> list[RankedCandidate]:
    selected_ids = {item.candidate.candidate_id for item in selected}
    selected_semantic_keys = {_selection_semantic_key(item) for item in selected}
    selected_cluster_counts: Counter[str] = Counter(_candidate_cluster_key(item) for item in selected)
    selected_edge_counts: Counter[str] = Counter(
        _candidate_cluster_key(item) for item in selected if item.hit.hit_type == "edge"
    )
    result: list[RankedCandidate] = []
    result_cluster_counts: Counter[str] = Counter()
    result_edge_counts: Counter[str] = Counter()
    seen_semantic_keys: set[str] = set()
    for candidate in remaining:
        if candidate.candidate.candidate_id in selected_ids:
            continue
        semantic_key = _selection_semantic_key(candidate)
        if semantic_key in selected_semantic_keys:
            continue
        if semantic_key in seen_semantic_keys:
            continue
        cluster_key = _candidate_cluster_key(candidate)
        if candidate.hit.hit_type == "edge":
            if selected_edge_counts[cluster_key] or result_edge_counts[cluster_key] >= 1:
                continue
        if selected_cluster_counts[cluster_key] + result_cluster_counts[cluster_key] >= 6:
            continue
        result.append(candidate)
        seen_semantic_keys.add(semantic_key)
        result_cluster_counts[cluster_key] += 1
        if candidate.hit.hit_type == "edge":
            result_edge_counts[cluster_key] += 1
        if len(result) >= limit:
            break
    return result


def _candidate_cluster_key(candidate: RankedCandidate) -> str:
    evidence_key = _primary_evidence_ref(candidate.hit)
    if evidence_key:
        return f"evidence:{evidence_key}"
    if candidate.hit.node_refs:
        return f"node:{candidate.hit.node_refs[0]}"
    return f"candidate:{candidate.candidate.candidate_id}"


def _candidate_semantic_key(candidate: RankedCandidate) -> str:
    title = re.sub(r"\s+", "", candidate.hit.title or "")
    title = re.sub(r"[:：，,。；;（）()\\[\\]{}\"']", "", title)
    evidence_key = _primary_evidence_ref(candidate.hit)
    if candidate.hit.hit_type == "edge" and evidence_key:
        return f"edge:{evidence_key}:{title}"
    if evidence_key and candidate.hit.hit_type in {"evidence", "wiki"}:
        return f"{candidate.hit.hit_type}:{evidence_key}:{title}"
    return f"{candidate.hit.hit_type}:{title or candidate.candidate.candidate_id}"


def _selection_semantic_key(candidate: RankedCandidate) -> str:
    """Collapse equivalent candidates before sending them to LLM judge.

    Retrieval may surface the same fact as both a structured node and its backing
    evidence document. They are useful as separate recall paths, but judging both
    wastes topK budget and repeats context.
    """

    cluster = _event_cluster(candidate.hit)
    if _candidate_role(candidate.hit) == "answer" and cluster:
        return f"answer:{cluster}"
    return _candidate_semantic_key(candidate)


def _primary_evidence_ref(hit: RetrievalHit) -> str:
    return sorted(hit.evidence_refs)[0] if hit.evidence_refs else ""


def _dynamic_top_k(
    *,
    query: str,
    anchor: Any,
    search_plan: Any,
    top_k_simple: int,
    top_k_complex: int,
    top_k_max: int,
    candidate_count: int,
) -> tuple[int, str]:
    coverage_terms = _coverage_requirements(search_plan)
    text = "\n".join(
        [
            query,
            _search_plan_text(search_plan),
            " ".join(str(getattr(item, "text", "")) for item in getattr(anchor, "core_topics", []) or []),
        ]
    )
    if len(coverage_terms) >= 4:
        requested = top_k_max
        reason = "enumerated_coverage"
    elif any(token in text for token in ("哪些", "影响哪些", "覆盖哪些", "有哪些")):
        requested = top_k_complex
        reason = "broad_which_query"
    elif _is_simple_anchor(anchor):
        requested = top_k_simple
        reason = "simple_anchor"
    else:
        requested = top_k_complex
        reason = "complex_query"
    return min(max(1, requested), top_k_max, candidate_count), reason


def _channel_ranks(hits: list[RetrievalHit]) -> dict[tuple[str, str], int]:
    by_channel: dict[str, list[RetrievalHit]] = {}
    for hit in hits:
        for channel in _hit_channels(hit):
            by_channel.setdefault(channel, []).append(hit)
    ranks: dict[tuple[str, str], int] = {}
    for channel, channel_hits in by_channel.items():
        ordered = sorted(channel_hits, key=lambda hit: (-float(hit.score or 0.0), hit.hit_id))
        for rank, hit in enumerate(ordered, start=1):
            ranks[(channel, hit.hit_id)] = rank
    return ranks


def _fusion_score(channel_ranks: dict[str, int], *, rrf_k: int = 60) -> float:
    weights = {
        "semantic_hybrid": 1.35,
        "graph": 1.25,
        "entity_resolve": 1.15,
        "keyword": 0.95,
        "wiki": 0.55,
    }
    score = 0.0
    for channel, rank in channel_ranks.items():
        score += weights.get(channel, 1.0) / (rrf_k + max(rank, 1))
    if len(channel_ranks) >= 2:
        score += min((len(channel_ranks) - 1) * 0.025, 0.08)
    return score


def _feature_score(hit: RetrievalHit, *, query: str, anchor: Any, search_plan: Any) -> tuple[float, list[str]]:
    text = _hit_text(hit)
    score = 0.0
    reasons: list[str] = []
    anchor_terms = _anchor_terms(query, anchor)
    anchor_matches = [term for term in anchor_terms if term.lower() in text]
    if anchor_matches:
        score += min(len(anchor_matches) * 0.35, 1.8)
        reasons.append("anchor_match")
    strong_anchor_terms = _strong_anchor_terms(anchor)
    strong_anchor_matches = [term for term in strong_anchor_terms if term.lower() in text]
    if strong_anchor_matches:
        score += min(len(strong_anchor_matches) * 0.35, 1.2)
        reasons.append("strong_anchor_match")
    elif strong_anchor_terms:
        score -= 1.8
        reasons.append("anchor_miss")
        if _generic_only_match(hit, query=query, search_plan=search_plan):
            score -= 1.2
            reasons.append("generic_only_no_anchor")
    role = _candidate_role(hit)
    if role == "answer":
        score += 1.2
        reasons.append("answer_type")
    elif role == "support":
        score += 0.5
        reasons.append("support_type")
    elif role == "background":
        score -= 0.3
        reasons.append("background_limited")
    if hit.evidence_refs:
        score += 0.8
        reasons.append("has_evidence")
    if hit.edge_refs or "affects" in text or "影响" in text:
        score += 0.4
        reasons.append("relation_signal")
    plan_terms = _plan_terms(
        [
            *_list_attr(search_plan, "answer_targets"),
            *_list_attr(search_plan, "expected_evidence"),
            *_list_attr(search_plan, "relation_intents"),
        ]
    )
    if any(term.lower() in text for term in plan_terms):
        score += 0.6
        reasons.append("search_plan_match")
    negative_terms = _plan_terms(_list_attr(search_plan, "negative_boundaries"))
    if any(term.lower() in text for term in negative_terms):
        score -= 2.0
        reasons.append("negative_boundary")
    if hit.hit_type == "edge":
        score -= 0.8
        reasons.append("edge_support_only")
    if hit.hit_type == "wiki":
        score -= 0.8
        reasons.append("wiki_background_limited")
    if hit.source == "keyword" and hit.matched_terms:
        informative_terms = [term for term in hit.matched_terms if not _generic_term(term)]
        score += min(len(informative_terms) * 0.25, 1.5)
        reasons.append("keyword_terms")
    return score, reasons


def _candidate_role(hit: RetrievalHit) -> str:
    text = _hit_text(hit)
    if hit.hit_type == "edge":
        return "support"
    if hit.hit_type == "wiki":
        return "background"
    if hit.hit_type == "evidence":
        return "answer"
    if hit.hit_type == "node" and _looks_like_event_hit(text):
        return "answer"
    if hit.hit_type == "node":
        return "support"
    return "background"


def _coverage_requirements(search_plan: Any) -> list[str]:
    values = [
        *_list_attr(search_plan, "answer_targets"),
        *_list_attr(search_plan, "expected_evidence"),
        *_list_attr(search_plan, "relation_intents"),
    ]
    values.extend(_enumerated_stop_terms(str(getattr(search_plan, "stop_condition", "") or "")))
    terms: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        terms.append(text)
        for token in re.split(r"[\s,，。；;:：、/()（）]+", text):
            token = token.strip()
            if len(token) >= 2:
                terms.append(token)
    return _ordered_unique(term for term in terms if not _generic_term(term))[:24]


def _matched_coverage_terms(hit: RetrievalHit, requirements: list[str]) -> list[str]:
    text = _hit_text(hit)
    return [term for term in requirements if term.lower() in text]


def _best_candidate_for_term(
    ranked: list[RankedCandidate],
    term: str,
    selected_ids: set[str],
    selected_semantic_keys: set[str] | None = None,
) -> RankedCandidate | None:
    selected_semantic_keys = selected_semantic_keys or set()
    candidates = [
        item
        for item in ranked
        if item.candidate.candidate_id not in selected_ids and term in item.coverage_terms
        and _selection_semantic_key(item) not in selected_semantic_keys
    ]
    return candidates[0] if candidates else None


def _adds_coverage(candidate: RankedCandidate, covered_terms: set[str]) -> bool:
    return any(term not in covered_terms for term in candidate.coverage_terms)


def _new_coverage_terms(candidate: RankedCandidate, covered_terms: set[str]) -> list[str]:
    return [term for term in candidate.coverage_terms if term not in covered_terms]


def _anchor_terms(query: str, anchor: Any) -> list[str]:
    values = [
        query,
        *(str(getattr(item, "text", "")) for item in getattr(anchor, "core_entities", []) or []),
        *(str(getattr(item, "text", "")) for item in getattr(anchor, "core_topics", []) or []),
        *(str(getattr(item, "value", "")) for item in getattr(anchor, "guard_constraints", []) or [] if getattr(item, "must_preserve", False)),
    ]
    return _plan_terms(values)


def _strong_anchor_terms(anchor: Any) -> list[str]:
    values = [
        str(getattr(item, "text", ""))
        for item in getattr(anchor, "core_entities", []) or []
        if getattr(item, "strength", "") == "strong"
    ]
    values.extend(
        str(getattr(item, "value", ""))
        for item in getattr(anchor, "guard_constraints", []) or []
        if getattr(item, "must_preserve", False)
    )
    return _ordered_unique(term for term in _plan_terms(values) if not _generic_term(term))


def _search_plan_text(search_plan: Any) -> str:
    return "\n".join(
        [
            *(_list_attr(search_plan, "answer_targets")),
            *(_list_attr(search_plan, "expected_evidence")),
            *(_list_attr(search_plan, "negative_boundaries")),
            *(_list_attr(search_plan, "relation_intents")),
            str(getattr(search_plan, "stop_condition", "") or ""),
        ]
    )


def _plan_terms(values: list[str]) -> list[str]:
    terms: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        terms.append(text)
        for token in re.split(r"[\s,，。；;:：、/()（）]+", text):
            token = token.strip()
            if len(token) >= 2:
                terms.append(token)
    return _ordered_unique(terms)


def _enumerated_stop_terms(stop_condition: str) -> list[str]:
    text = str(stop_condition or "")
    if not text or not any(marker in text for marker in ("覆盖", "包括", "包含", "涵盖")):
        return []
    normalized = (
        text.replace("以及", "、")
        .replace("及", "、")
        .replace("和", "、")
        .replace("，", "、")
        .replace(",", "、")
        .replace("；", "、")
        .replace(";", "、")
    )
    terms: list[str] = []
    for raw in re.split(r"[、\s]+", normalized):
        term = _clean_coverage_term(raw)
        if term:
            terms.append(term)
    return _ordered_unique(terms)


def _clean_coverage_term(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^.*?(?:覆盖|包括|包含|涵盖)", "", text)
    text = re.sub(r"^至少", "", text)
    text = re.sub(r"^[且并需要]+", "", text)
    text = re.sub(r"^[如例如比如]", "", text)
    text = re.sub(r"^找到?\d+个受影响", "", text)
    text = re.sub(r"[一二三四五六七八九十\d]+类.*$", "", text)
    text = re.sub(r"等.*$", "", text)
    text = re.sub(r"的.*$", "", text)
    text = re.sub(r"(资产类别|资产|行业|板块|维度|关键影响|影响|明确证据|证据|不同|具体)$", "", text)
    text = text.strip("：:，,。、；; ")
    if len(text) < 2:
        return ""
    if _generic_term(text):
        return ""
    return text


def _candidate_coverage_units(hit: RetrievalHit, *, search_plan: Any, anchor: Any) -> list[str]:
    units: list[str] = []
    role = _candidate_role(hit)
    if role == "answer":
        cluster = _event_cluster(hit)
        if cluster:
            units.append(f"answer:{cluster}")
    evidence_type = _evidence_type_unit(hit, search_plan)
    if evidence_type:
        units.append(evidence_type)
        units.append(f"evidence_type:{evidence_type}")
    relation_unit = _relation_intent_unit(hit, search_plan)
    if relation_unit:
        units.append(relation_unit)
        units.append(f"relation:{relation_unit}")
    anchor_unit = _anchor_unit(hit, anchor)
    if anchor_unit:
        units.append(anchor_unit)
    return _ordered_unique(units)


def _event_cluster(hit: RetrievalHit) -> str:
    text = re.sub(r"\s+", "", hit.title or hit.snippet or "")
    text = re.sub(r"[:：，,。；;（）()\\[\\]{}\"']", "", text)
    if not text:
        return ""
    return text[:24]


def _evidence_type_unit(hit: RetrievalHit, search_plan: Any) -> str:
    text = _hit_text(hit)
    expected = _list_attr(search_plan, "expected_evidence")
    if any("公告" in item for item in expected) and "公告" in text:
        return "公告"
    if any("研报" in item for item in expected) and ("研报" in text or "研究" in text):
        return "研报"
    if any("新闻" in item or "报道" in item for item in expected):
        if hit.hit_type == "evidence" or "news" in text or "新闻" in text or "报道" in text:
            return "新闻报道"
    if hit.hit_type == "evidence":
        return "evidence"
    return ""


def _relation_intent_unit(hit: RetrievalHit, search_plan: Any) -> str:
    text = _hit_text(hit)
    for intent in _list_attr(search_plan, "relation_intents"):
        if _generic_term(intent):
            continue
        lowered = intent.lower()
        if lowered in text or (lowered == "impact" and ("影响" in text or "affects" in text)):
            return intent
    return ""


def _anchor_unit(hit: RetrievalHit, anchor: Any) -> str:
    text = _hit_text(hit)
    for term in _strong_anchor_terms(anchor):
        if term.lower() in text:
            return f"anchor:{term}"
    return ""


def _generic_only_match(hit: RetrievalHit, *, query: str, search_plan: Any) -> bool:
    matched = _ordered_unique([*hit.matched_terms, *_plan_terms([query])])
    meaningful = [term for term in matched if len(term) >= 2 and not _generic_term(term)]
    if meaningful:
        text = _hit_text(hit)
        if any(term.lower() in text for term in meaningful):
            return False
    plan_terms = _plan_terms(
        [
            *_list_attr(search_plan, "answer_targets"),
            *_list_attr(search_plan, "expected_evidence"),
            *_list_attr(search_plan, "relation_intents"),
        ]
    )
    meaningful_plan_terms = [term for term in plan_terms if not _generic_term(term)]
    text = _hit_text(hit)
    return not any(term.lower() in text for term in meaningful_plan_terms)


def _list_attr(value: Any, name: str) -> list[str]:
    attr = getattr(value, name, [])
    return [str(item) for item in attr or [] if str(item).strip()]


def _is_simple_anchor(anchor: Any) -> bool:
    strong_entities = [item for item in getattr(anchor, "core_entities", []) or [] if getattr(item, "strength", "") == "strong"]
    strong_constraints = [item for item in getattr(anchor, "guard_constraints", []) or [] if getattr(item, "must_preserve", False)]
    return bool(strong_entities or strong_constraints or getattr(anchor, "source_hints", []) or getattr(anchor, "time_hints", []))


def _hit_text(hit: RetrievalHit) -> str:
    return "\n".join(
        [
            hit.title,
            hit.snippet,
            hit.source,
            hit.hit_type,
            " ".join(hit.matched_terms),
            " ".join(hit.matched_fields),
            " ".join(hit.node_refs),
            " ".join(hit.edge_refs),
            " ".join(hit.evidence_refs),
        ]
    ).lower()


def _looks_like_event_hit(text: str) -> bool:
    return any(marker in text for marker in ('"type": "event"', '"type":"event"', "类型：event", "type：event", "event"))


def _generic_term(term: str) -> bool:
    normalized = str(term or "").strip().lower()
    if not normalized:
        return True
    return normalized in {
        "近期",
        "最近",
        "最新",
        "哪些",
        "什么",
        "影响",
        "事件",
        "消息",
        "动态",
        "重大",
        "市场",
        "股价",
        "利好",
        "利空",
        "带动",
        "受益",
        "关联",
        "新闻报道",
        "事件公告",
        "公司公告",
        "股价波动关联",
        "impact",
    }


def _channel_contribution(
    all_hits: list[RetrievalHit],
    selected: list[RankedCandidate],
) -> dict[str, Any]:
    pool_counts: dict[str, int] = {}
    selected_counts: dict[str, int] = {}
    channels_by_id: dict[str, set[str]] = {}
    for hit in all_hits:
        for channel in _hit_channels(hit):
            pool_counts[channel] = pool_counts.get(channel, 0) + 1
            channels_by_id.setdefault(hit.hit_id, set()).add(channel)
    for item in selected:
        for channel in channels_by_id.get(item.hit.hit_id, set(item.candidate.source_channels)):
            selected_counts[channel] = selected_counts.get(channel, 0) + 1
    keyword_ids = {hit.hit_id for hit in all_hits if "keyword" in _hit_channels(hit)}
    semantic_ids = {hit.hit_id for hit in all_hits if "semantic_hybrid" in _hit_channels(hit)}
    graph_ids = {hit.hit_id for hit in all_hits if "graph" in _hit_channels(hit)}
    selected_ids = [item.hit.hit_id for item in selected]
    return {
        "pool_counts": pool_counts,
        "selected_counts": selected_counts,
        "selected_primary_source_counts": _primary_source_counts([item.hit for item in selected]),
        "keyword_only_selected": sum(
            1 for hit_id in selected_ids if hit_id in keyword_ids and hit_id not in semantic_ids and hit_id not in graph_ids
        ),
        "semantic_only_added_selected": sum(
            1 for hit_id in selected_ids if hit_id in semantic_ids and hit_id not in keyword_ids
        ),
        "graph_confirmed_selected": sum(
            1 for hit_id in selected_ids if hit_id in graph_ids and (hit_id in keyword_ids or hit_id in semantic_ids)
        ),
        "multi_channel_selected": sum(
            1 for hit_id in selected_ids if len(channels_by_id.get(hit_id, set())) >= 2
        ),
    }


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _hit_channels(hit: RetrievalHit) -> list[str]:
    return _ordered_unique([*(hit.source_channels or []), hit.source or "unknown"])


def _primary_source_counts(hits: list[RetrievalHit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        source = hit.source or "unknown"
        counts[source] = counts.get(source, 0) + 1
    return counts
