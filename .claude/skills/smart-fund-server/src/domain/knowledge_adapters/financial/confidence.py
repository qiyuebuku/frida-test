"""Financial confidence defaults."""

from dataclasses import dataclass

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus


@dataclass(frozen=True)
class RelationConfidence:
    label: ConfidenceLabel
    score: float
    status: EdgeStatus


STRUCTURED_ACTIVE = RelationConfidence(
    label=ConfidenceLabel.EXTRACTED,
    score=1.0,
    status=EdgeStatus.ACTIVE,
)
TEXT_MENTION = RelationConfidence(
    label=ConfidenceLabel.EXTRACTED,
    score=0.85,
    status=EdgeStatus.ACTIVE,
)
TEXT_IMPACT_CANDIDATE = RelationConfidence(
    label=ConfidenceLabel.INFERRED,
    score=0.65,
    status=EdgeStatus.CANDIDATE,
)
WEAK_RELATED = RelationConfidence(
    label=ConfidenceLabel.INFERRED,
    score=0.4,
    status=EdgeStatus.CANDIDATE,
)


def default_relation_confidence(relation_type: str, *, source_type: str) -> RelationConfidence:
    if relation_type in {"belongs_to", "holds"}:
        return STRUCTURED_ACTIVE
    if relation_type == "mentions":
        return TEXT_MENTION
    if relation_type == "affects":
        return TEXT_IMPACT_CANDIDATE
    if relation_type in {"related_to", "causal_hint"}:
        return WEAK_RELATED
    return TEXT_IMPACT_CANDIDATE
