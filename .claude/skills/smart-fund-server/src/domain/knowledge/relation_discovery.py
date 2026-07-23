"""关系优先图构建中候选发现与原文核验的运行对象。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal


RELATION_PROBE_ROLES = frozenset(
    {"same_event", "upstream", "downstream", "confirmation", "contradiction"}
)
RouteType = Literal["summary", "probe"]
RecallView = Literal["summary", "focus_evidence"]
RelationDecisionClass = Literal["observed", "inferred", "no_relation"]
RelationKind = Literal[
    "same_event",
    "confirmation",
    "contradiction",
    "temporal_progression",
    "causal_influence",
    "common_driver",
    "constraint",
]


@dataclass(frozen=True)
class RelationRoute:
    route_id: str
    source_card_id: str
    route_type: RouteType
    query: str
    role: str = "baseline"


@dataclass(frozen=True)
class RelationProbe:
    """用于跨 Chunk 候选发现的搜索假设，不代表正式关系。"""

    role: str
    query: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "query": self.query}


@dataclass(frozen=True)
class RelationRecallHit:
    candidate_card_id: str
    recall_view: RecallView
    recall_rank: int
    recall_score: float


@dataclass(frozen=True)
class RouteCandidateHit:
    candidate_card_id: str
    candidate_summary: str
    candidate_published_at: str
    route_id: str
    route_type: RouteType
    role: str
    query: str
    recall_hits: list[RelationRecallHit]
    rerank_rank: int
    rerank_score: float


@dataclass
class MergedRelationCandidate:
    candidate_card_id: str
    candidate_summary: str
    candidate_published_at: str
    route_hits: list[RouteCandidateHit] = field(default_factory=list)
    rrf_score: float = 0.0

    @property
    def recall_views(self) -> list[str]:
        return sorted(
            {
                recall.recall_view
                for route_hit in self.route_hits
                for recall in route_hit.recall_hits
            }
        )

    @property
    def roles(self) -> list[str]:
        return sorted({hit.role for hit in self.route_hits if hit.role})


@dataclass(frozen=True)
class PairEvidencePackage:
    source_card_id: str
    source_evidence_context: list[dict[str, Any]]
    source_focus_refs: list[str]
    source_published_at: str
    candidate_card_id: str
    candidate_evidence_context: list[dict[str, Any]]
    candidate_focus_refs: list[str]
    candidate_published_at: str
    source_chunk_summary: str = ""
    candidate_chunk_summary: str = ""


@dataclass(frozen=True)
class VerifiedRelationDecision:
    source_card_id: str
    target_card_id: str
    decision_class: RelationDecisionClass
    relation_kind: RelationKind | str
    relation_type: str
    direction: str
    basis: str
    source_evidence_refs: list[str]
    target_evidence_refs: list[str]
    inference_mechanism: str = ""
    confidence: float = 0.0
    relation_evidence_refs: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        if self.decision_class == "no_relation":
            return {"decision_class": "no_relation"}
        return {
            "source_card_id": self.source_card_id,
            "target_card_id": self.target_card_id,
            "decision_class": self.decision_class,
            "relation_kind": self.relation_kind,
            "relation_type": self.relation_type,
            "direction": self.direction,
            "basis": self.basis,
            "source_evidence_refs": list(self.source_evidence_refs),
            "target_evidence_refs": list(self.target_evidence_refs),
            "relation_evidence_refs": [dict(item) for item in self.relation_evidence_refs],
            "inference_mechanism": self.inference_mechanism,
            "confidence": self.confidence,
        }


def build_relation_routes(
    *,
    source_card_id: str,
    summary: str,
    relation_probes: Iterable[RelationProbe] = (),
    generator_version: str,
) -> list[RelationRoute]:
    """使用 Summary 基线路由和 Relation Probe 建立多路召回计划。"""

    raw_routes: list[tuple[RouteType, str, str]] = [("summary", "baseline", summary)]
    raw_routes.extend(
        ("probe", probe.role, probe.query)
        for probe in relation_probes
        if probe.role in RELATION_PROBE_ROLES
    )
    result: list[RelationRoute] = []
    seen: set[tuple[str, str]] = set()
    for route_type, role, query in raw_routes:
        normalized = " ".join(query.split())
        key = (role, normalized.casefold())
        if not normalized or key in seen:
            continue
        seen.add(key)
        signature = json.dumps(
            [source_card_id, route_type, role, normalized, generator_version],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        route_id = "kg_relation_route:" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
        result.append(
            RelationRoute(
                route_id=route_id,
                source_card_id=source_card_id,
                route_type=route_type,
                role=role,
                query=normalized,
            )
        )
    return result


def canonical_card_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))
