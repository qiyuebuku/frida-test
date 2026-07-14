"""原子 Cognitive Card 之间的正式关系 Edge 契约。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from src.domain.knowledge.relation_discovery import VerifiedRelationDecision


RelationKind = Literal[
    "same_event",
    "confirmation",
    "contradiction",
    "temporal_progression",
    "causal_influence",
    "common_driver",
    "constraint",
]

RELATION_KINDS = frozenset(
    {
        "same_event",
        "confirmation",
        "contradiction",
        "temporal_progression",
        "causal_influence",
        "common_driver",
        "constraint",
    }
)
SYMMETRIC_RELATION_KINDS = frozenset(
    {"same_event", "confirmation", "contradiction", "common_driver"}
)
CARD_RELATION_SCHEMA_VERSION = "card_relation_edge_v1"


@dataclass(frozen=True)
class CardRelationEdge:
    id: str
    pair_key: str
    source_card_id: str
    target_card_id: str
    relation_kind: RelationKind
    relation_type: str
    direction: str
    decision_class: Literal["observed", "inferred"]
    basis: str
    source_evidence_refs: list[str]
    target_evidence_refs: list[str]
    inference_mechanism: str
    confidence: float
    pipeline_version: str
    model_name: str
    prompt_version: str
    schema_version: str
    content_version: str
    status: str = "active"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "pair_key": self.pair_key,
            "source_card_id": self.source_card_id,
            "target_card_id": self.target_card_id,
            "relation_kind": self.relation_kind,
            "relation_type": self.relation_type,
            "direction": self.direction,
            "decision_class": self.decision_class,
            "basis": self.basis,
            "source_evidence_refs": list(self.source_evidence_refs),
            "target_evidence_refs": list(self.target_evidence_refs),
            "inference_mechanism": self.inference_mechanism,
            "confidence": self.confidence,
            "pipeline_version": self.pipeline_version,
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "content_version": self.content_version,
            "status": self.status,
        }


def card_pair_key(left_card_id: str, right_card_id: str) -> str:
    if not left_card_id or not right_card_id or left_card_id == right_card_id:
        raise ValueError("Card Relation 两端必须是不同且非空的 Card ID")
    return "::".join(sorted((left_card_id, right_card_id)))


def build_card_relation_edge(
    decision: VerifiedRelationDecision,
    *,
    pipeline_version: str,
    model_name: str,
    prompt_version: str,
) -> CardRelationEdge:
    """校验正关系、规范对称端点并生成稳定 Edge identity。"""

    if decision.decision_class not in {"observed", "inferred"}:
        raise ValueError("只有 observed/inferred 可以生成正式 Edge")
    if decision.relation_kind not in RELATION_KINDS:
        raise ValueError(f"未知 relation_kind: {decision.relation_kind}")
    source_id = decision.source_card_id
    target_id = decision.target_card_id
    source_refs = list(dict.fromkeys(decision.source_evidence_refs))
    target_refs = list(dict.fromkeys(decision.target_evidence_refs))
    if decision.relation_kind in SYMMETRIC_RELATION_KINDS and source_id > target_id:
        source_id, target_id = target_id, source_id
        source_refs, target_refs = target_refs, source_refs
    pair_key = card_pair_key(source_id, target_id)
    identity_payload = {
        "pair_key": pair_key,
        "relation_kind": decision.relation_kind,
        "source_card_id": source_id,
        "target_card_id": target_id,
    }
    identity_hash = _stable_hash(identity_payload)
    edge_id = f"kg_card_relation:{identity_hash[:20]}"
    content_payload = {
        **identity_payload,
        "relation_type": decision.relation_type,
        "direction": decision.direction,
        "decision_class": decision.decision_class,
        "basis": decision.basis,
        "source_evidence_refs": source_refs,
        "target_evidence_refs": target_refs,
        "inference_mechanism": decision.inference_mechanism,
        "confidence": round(float(decision.confidence), 6),
        "pipeline_version": pipeline_version,
        "model_name": model_name,
        "prompt_version": prompt_version,
        "schema_version": CARD_RELATION_SCHEMA_VERSION,
        "status": "active",
    }
    return CardRelationEdge(
        id=edge_id,
        pair_key=pair_key,
        source_card_id=source_id,
        target_card_id=target_id,
        relation_kind=decision.relation_kind,
        relation_type=decision.relation_type,
        direction=decision.direction,
        decision_class=decision.decision_class,
        basis=decision.basis,
        source_evidence_refs=source_refs,
        target_evidence_refs=target_refs,
        inference_mechanism=decision.inference_mechanism,
        confidence=round(float(decision.confidence), 6),
        pipeline_version=pipeline_version,
        model_name=model_name,
        prompt_version=prompt_version,
        schema_version=CARD_RELATION_SCHEMA_VERSION,
        content_version=_stable_hash(content_payload),
    )


def inactive_content_version(edge_id: str, previous_content_version: str) -> str:
    return _stable_hash(
        {
            "id": edge_id,
            "previous_content_version": previous_content_version,
            "status": "inactive",
        }
    )


def _stable_hash(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
