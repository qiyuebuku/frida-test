"""Financial confidence rule tests."""

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus
from src.domain.knowledge_adapters.financial.confidence import default_relation_confidence


def test_structured_relation_defaults_to_active_extracted() -> None:
    result = default_relation_confidence("belongs_to", source_type="concept_components")

    assert result.label == ConfidenceLabel.EXTRACTED
    assert result.status == EdgeStatus.ACTIVE


def test_text_impact_defaults_to_candidate_inferred() -> None:
    result = default_relation_confidence("affects", source_type="policy")

    assert result.label == ConfidenceLabel.INFERRED
    assert result.status == EdgeStatus.CANDIDATE
