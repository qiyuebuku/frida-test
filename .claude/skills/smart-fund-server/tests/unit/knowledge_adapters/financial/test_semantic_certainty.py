"""Financial semantic certainty routing tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.knowledge.enums import InputType, RecordKind
from src.domain.knowledge.schemas import KnowledgeInput
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
from src.domain.knowledge_adapters.financial.news_extraction import (
    FinancialNewsExtractionStrategy,
    enrich_financial_text_payload,
)
from src.domain.knowledge_adapters.financial.semantic_certainty import (
    SemanticCertaintyAssessment,
    assess_semantic_certainty,
)


class _FailingLLMPort:
    async def extract(self, request):  # pragma: no cover - assertion path
        raise AssertionError(f"LLM should not be called: {request}")


def _input(
    *,
    record_kind: RecordKind,
    source_type: str = "news_articles",
    payload: dict | None = None,
    raw_text: str | None = None,
) -> KnowledgeInput:
    return KnowledgeInput(
        input_type=InputType.SEMI_STRUCTURED_RECORD,
        source_type=source_type,
        source_id="source-1",
        observed_at=datetime.fromisoformat("2026-05-01T00:00:00+00:00"),
        adapter_name="financial",
        adapter_version="v1",
        record_kind=record_kind,
        payload=payload or {"source_id": "source-1", "published_at": "2026-05-01T00:00:00+00:00"},
        raw_text=raw_text,
    )


def test_structured_relation_assertion_is_rule_only() -> None:
    item = _input(
        record_kind=RecordKind.RELATION_ASSERTION,
        source_type="industry_components",
        payload={
            "taxonomy": "citics",
            "component_name": "新能源车",
            "member_stock_code": "300750",
            "member_stock_exchange": "SZ",
        },
    )

    assessment = assess_semantic_certainty(item)

    assert assessment.decision == "rule_only"
    assert assessment.required_llm_tasks == []


def test_text_document_with_impact_language_is_llm_candidate() -> None:
    item = _input(
        record_kind=RecordKind.TEXT_DOCUMENT,
        raw_text="低利率环境利好成长资产，但高股息资产可能承压。",
    )

    assessment = assess_semantic_certainty(item)

    assert assessment.decision == "llm_candidate"
    assert "resolve_ambiguity" in assessment.required_llm_tasks
    assert any(item.startswith("relation_semantics:") for item in assessment.ambiguous_items)


@pytest.mark.asyncio
async def test_rule_only_assessment_does_not_call_llm_port() -> None:
    payload = {
        "source_id": "source-1",
        "published_at": "2026-05-01T00:00:00+00:00",
        "symbols": [{"exchange": "SZ", "code": "300750", "name": "宁德时代"}],
    }
    assessment = SemanticCertaintyAssessment(
        decision="rule_only",
        confidence=0.9,
        reasons=["deterministic_entity_hints_without_text"],
    )

    enriched = await enrich_financial_text_payload(
        payload,
        source_id="source-1",
        source_type="news_articles",
        pipeline=FinancialKGAdapter().text_extraction_pipeline,
        strategy=FinancialNewsExtractionStrategy(llm_port=_FailingLLMPort()),
        semantic_assessment=assessment,
    )

    assert enriched["mentioned_entities"] == [
        {"type": "stock", "name": "宁德时代", "confidence": 0.9, "exchange": "SZ", "code": "300750"}
    ]
