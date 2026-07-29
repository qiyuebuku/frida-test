"""Source Record contract tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.knowledge.enums import InputType, RecordKind
from src.domain.knowledge.schemas import KnowledgeInput
from src.domain.knowledge.source_record import resolve_record_kind, validate_source_record_contract
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter


def test_resolve_record_kind_prefers_explicit_value() -> None:
    assert (
        resolve_record_kind(
            source_type="custom",
            input_type=InputType.STRUCTURED_RECORD,
            explicit="entity_snapshot",
        )
        == RecordKind.ENTITY_SNAPSHOT
    )


def test_resolve_record_kind_uses_adapter_hints_without_core_domain_labels() -> None:
    assert (
        resolve_record_kind(
            source_type="custom_relation",
            input_type=InputType.STRUCTURED_RECORD,
            source_type_hints={"custom_relation": RecordKind.RELATION_ASSERTION},
        )
        == RecordKind.RELATION_ASSERTION
    )


def test_financial_adapter_sets_record_kind_from_source_type() -> None:
    item = FinancialKGAdapter().normalize(
        {
            "source_type": "news_articles",
            "payload": {
                "source_id": "news-1",
                "published_at": "2026-05-01T00:00:00+00:00",
                "title": "测试新闻",
            },
        }
    )[0]

    assert item.record_kind == RecordKind.TEXT_DOCUMENT


def test_text_document_requires_readable_text() -> None:
    item = KnowledgeInput(
        input_type=InputType.SEMI_STRUCTURED_RECORD,
        source_type="custom",
        source_id="source-1",
        observed_at=datetime.fromisoformat("2026-05-01T00:00:00+00:00"),
        adapter_name="demo",
        adapter_version="v1",
        record_kind=RecordKind.TEXT_DOCUMENT,
        payload={"id": "source-1"},
    )

    with pytest.raises(ValueError, match="text_document requires"):
        validate_source_record_contract(item)
