"""Candidate judgement and noise suppression for KG retrieval."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from src.domain.knowledge.retrieval_anchor import QueryAnchor, anchor_terms
from src.domain.knowledge.retrieval_router import RetrievalQualityMetrics
from src.domain.knowledge.schemas import KnowledgeBaseModel

CandidateDecision = Literal["keep", "weak_keep", "drop"]
CandidateRole = Literal["answer", "support", "background", "drop"]
JudgeSource = Literal["deterministic", "llm", "fallback"]


class CandidateJudgement(KnowledgeBaseModel):
    candidate_id: str
    decision: CandidateDecision
    role: CandidateRole = "background"
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    can_expand_graph: bool = False
    anchor_coverage: dict[str, float] = Field(default_factory=dict)
    topic_drift: bool = False
    reason: str
    reason_code: str = "unspecified"
    judge_source: JudgeSource = "deterministic"


class CandidateJudge(Protocol):
    async def judge(
        self,
        *,
        query: str,
        anchor: QueryAnchor,
        hits: list[Any],
    ) -> list[CandidateJudgement]:
        """Judge candidate relevance before candidates can enter Query Context."""


class DeterministicCandidateJudge:
    """Small deterministic judge for tests and explicit offline fallback only."""

    async def judge(
        self,
        *,
        query: str,
        anchor: QueryAnchor,
        hits: list[Any],
    ) -> list[CandidateJudgement]:
        return judge_hits(anchor, hits)


def judge_hits(anchor: QueryAnchor, hits: list[Any]) -> list[CandidateJudgement]:
    terms = anchor_terms(anchor)
    return [_judge_hit(anchor, hit, terms) for hit in hits]


def filter_hits_by_judgement(
    hits: list[Any],
    judgements: list[CandidateJudgement],
    *,
    include_weak: bool = True,
) -> list[Any]:
    judgement_by_id = {item.candidate_id: item for item in judgements}
    allowed = {"keep", "weak_keep"} if include_weak else {"keep"}
    return [
        hit
        for hit in hits
        if judgement_by_id.get(hit.hit_id) is None
        or judgement_by_id[hit.hit_id].decision in allowed
    ]


def retrieval_quality_metrics(
    *,
    anchor: QueryAnchor,
    hits: list[Any],
    judgements: list[CandidateJudgement],
) -> RetrievalQualityMetrics:
    total = len(judgements)
    dropped = sum(1 for item in judgements if item.decision == "drop")
    keep = sum(1 for item in judgements if item.decision in {"keep", "weak_keep"})
    strong_keep = sum(1 for item in judgements if item.decision == "keep")
    evidence_refs = len(_ordered_unique(ref for hit in hits for ref in hit.evidence_refs))
    anchor_coverage = max(
        [item.anchor_coverage.get("overall", 0.0) for item in judgements],
        default=0.0,
    )
    context_precision = strong_keep / total if total else 1.0
    return RetrievalQualityMetrics(
        anchor_coverage=anchor_coverage if anchor_terms(anchor) else 1.0,
        keep_candidates=keep,
        drop_ratio=dropped / total if total else 0.0,
        evidence_refs=evidence_refs,
        topic_conflict=any(item.topic_drift for item in judgements if item.decision == "drop"),
        forbidden_hit=False,
        context_precision=context_precision,
    )


def _judge_hit(anchor: QueryAnchor, hit: Any, terms: list[str]) -> CandidateJudgement:
    hit = _candidate(hit)
    text = _hit_text(hit)
    strong_match = _strong_constraint_match(anchor, hit, text)
    overlap = _overlap_score(terms, text)
    if strong_match:
        return CandidateJudgement(
            candidate_id=hit.hit_id,
            decision="keep",
            role="support" if hit.hit_type in {"node", "edge", "wiki"} else "answer",
            relevance_score=max(0.9, overlap),
            can_expand_graph=True,
            anchor_coverage={"overall": max(0.9, overlap)},
            topic_drift=False,
            reason="strong_constraint_match",
            reason_code="strong_constraint_match",
        )
    if not terms:
        return CandidateJudgement(
            candidate_id=hit.hit_id,
            decision="weak_keep",
            role="background",
            relevance_score=0.5,
            can_expand_graph=hit.source in {"entity_resolve", "graph"},
            anchor_coverage={"overall": 0.5},
            topic_drift=False,
            reason="no_anchor_terms",
            reason_code="no_anchor_terms",
        )
    if overlap >= 0.45:
        return CandidateJudgement(
            candidate_id=hit.hit_id,
            decision="keep",
            role="support" if hit.hit_type in {"node", "edge", "wiki"} else "answer",
            relevance_score=overlap,
            can_expand_graph=True,
            anchor_coverage={"overall": overlap},
            topic_drift=False,
            reason="anchor_overlap",
            reason_code="anchor_overlap",
        )
    if hit.source == "graph" and hit.evidence_refs:
        return CandidateJudgement(
            candidate_id=hit.hit_id,
            decision="weak_keep",
            role="background",
            relevance_score=max(0.35, overlap),
            can_expand_graph=False,
            anchor_coverage={"overall": overlap},
            topic_drift=False,
            reason="graph_seed_context",
            reason_code="graph_seed_context",
        )
    if hit.source == "entity_resolve" and overlap > 0:
        return CandidateJudgement(
            candidate_id=hit.hit_id,
            decision="weak_keep",
            role="background",
            relevance_score=overlap,
            can_expand_graph=True,
            anchor_coverage={"overall": overlap},
            topic_drift=False,
            reason="partial_entity_overlap",
            reason_code="partial_entity_overlap",
        )
    return CandidateJudgement(
        candidate_id=hit.hit_id,
        decision="drop",
        role="drop",
        relevance_score=overlap,
        can_expand_graph=False,
        anchor_coverage={"overall": overlap},
        topic_drift=True,
        reason="no_anchor_overlap",
        reason_code="no_anchor_overlap",
    )


def _candidate(hit: Any) -> Any:
    return getattr(hit, "candidate", hit)


def _strong_constraint_match(anchor: QueryAnchor, hit: Any, text: str) -> bool:
    refs = set(hit.evidence_refs or [])
    for constraint in anchor.guard_constraints:
        value = constraint.value.lower()
        if constraint.constraint_type == "evidence_id" and value in {ref.lower() for ref in refs}:
            return True
        if constraint.constraint_type == "source_id" and value in text:
            return True
        if constraint.constraint_type in {"instrument_code", "exact_entity"} and value in text:
            return True
    return False


def _overlap_score(terms: list[str], text: str) -> float:
    if not terms:
        return 0.0
    matched = [term for term in terms if term.lower() in text]
    return min(1.0, len(matched) / max(len(terms), 1))


def _hit_text(hit: Any) -> str:
    return "\n".join(
        [
            str(getattr(hit, "hit_id", "")),
            str(getattr(hit, "title", "")),
            str(getattr(hit, "snippet", "")),
            str(getattr(hit, "source", "")),
            " ".join(getattr(hit, "node_refs", []) or []),
            " ".join(getattr(hit, "edge_refs", []) or []),
            " ".join(getattr(hit, "evidence_refs", []) or []),
        ]
    ).lower()


def _ordered_unique(values) -> list:
    result: list = []
    seen: set = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
