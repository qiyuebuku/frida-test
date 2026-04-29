"""Financial consumption guardrail tests."""

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus
from src.domain.knowledge_adapters.financial.consumption import can_hard_consume


def test_related_to_cannot_hard_consume() -> None:
    assert not can_hard_consume("related_to", ConfidenceLabel.HUMAN_VERIFIED, EdgeStatus.ACTIVE)


def test_structured_holds_can_hard_consume_when_active() -> None:
    assert can_hard_consume("holds", ConfidenceLabel.EXTRACTED, EdgeStatus.ACTIVE)


def test_inferred_affects_cannot_hard_consume_without_human_verification() -> None:
    assert not can_hard_consume("affects", ConfidenceLabel.INFERRED, EdgeStatus.CANDIDATE)
