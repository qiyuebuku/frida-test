"""Text extraction pipeline tests."""

from __future__ import annotations

import pytest

from src.domain.knowledge.adapter import ValidationResult
from src.domain.knowledge.extraction import (
    CandidateFactEntity,
    CandidateFactPackage,
    EvidenceSpan,
    ExtractedEntity,
    TextExtractionInput,
    TextExtractionPipeline,
    TextExtractionResult,
)


class DummyStrategy:
    name = "dummy"
    version = "v1"

    def __init__(self, result: TextExtractionResult):
        self.result = result

    async def extract(self, item: TextExtractionInput) -> TextExtractionResult:
        del item
        return self.result

    def validate_result(self, result: TextExtractionResult, item: TextExtractionInput) -> ValidationResult:
        del result, item
        return ValidationResult.success()


@pytest.mark.asyncio
async def test_text_extraction_pipeline_preserves_valid_result() -> None:
    item = TextExtractionInput(source_id="doc-1", source_type="doc", text="Alpha mentions Beta")
    result = TextExtractionResult(
        mentioned_entities=[
            ExtractedEntity(
                entity_type="thing",
                canonical_name="Beta",
                confidence=0.9,
                evidence_spans=[EvidenceSpan(field_name="text", text="Beta", start=15, end=19)],
            )
        ]
    )

    output = await TextExtractionPipeline().extract(item, DummyStrategy(result))

    assert output.mentioned_entities[0].canonical_name == "Beta"
    assert output.warnings == []


@pytest.mark.asyncio
async def test_text_extraction_pipeline_warns_when_span_cannot_be_bound() -> None:
    item = TextExtractionInput(source_id="doc-1", source_type="doc", text="Alpha mentions Beta")
    result = TextExtractionResult(
        mentioned_entities=[
            ExtractedEntity(
                entity_type="thing",
                canonical_name="Gamma",
                confidence=0.9,
                evidence_spans=[EvidenceSpan(field_name="text", text="Gamma")],
            )
        ]
    )

    output = await TextExtractionPipeline().extract(item, DummyStrategy(result))

    assert "span text not found in field: text" in output.warnings


@pytest.mark.asyncio
async def test_text_extraction_pipeline_validates_candidate_package_spans() -> None:
    item = TextExtractionInput(source_id="doc-1", source_type="doc", text="Alpha mentions Beta")
    result = TextExtractionResult(
        candidate_package=CandidateFactPackage(
            entities=[
                CandidateFactEntity(
                    entity_type="thing",
                    canonical_name="Gamma",
                    confidence=0.8,
                    evidence_spans=[EvidenceSpan(field_name="text", text="Gamma")],
                )
            ]
        )
    )

    output = await TextExtractionPipeline().extract(item, DummyStrategy(result))

    assert "span text not found in field: text" in output.warnings
