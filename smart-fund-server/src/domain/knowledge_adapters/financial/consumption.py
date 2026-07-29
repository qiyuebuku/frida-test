"""Financial consumption guardrails."""

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus


HARD_RELATIONS = {"belongs_to", "holds"}
NEVER_HARD_RELATIONS = {"related_to", "causal_hint", "alias_of"}


def can_hard_consume(
    relation_type: str,
    confidence_label: ConfidenceLabel,
    status: EdgeStatus,
) -> bool:
    if relation_type in NEVER_HARD_RELATIONS:
        return False
    if relation_type not in HARD_RELATIONS:
        return confidence_label == ConfidenceLabel.HUMAN_VERIFIED and status == EdgeStatus.ACTIVE
    return confidence_label in {ConfidenceLabel.EXTRACTED, ConfidenceLabel.HUMAN_VERIFIED} and status == EdgeStatus.ACTIVE
