"""Tests for the application source projection use case."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.application.dto.knowledge_dto import KnowledgeSourceProjectionCommand
from src.application.services.knowledge_source_projection_service import (
    KnowledgeSourceProjectionService,
)
from src.domain.knowledge.repositories import KnowledgeSourceProjectionRepository


class FakeProjectionRepository(KnowledgeSourceProjectionRepository):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_rows(self, source: str, *, limit: int, codes: list[str] | None = None) -> list[dict[str, Any]]:
        if source == "ft_news":
            return self.fetch_ft_news(limit=limit, codes=codes)
        if source == "ft_market_flow":
            return self.fetch_ft_market_flow(limit=limit)
        if source == "ft_market_cache":
            return self.fetch_ft_market_cache(limit=limit)
        if source == "ft_sentiment":
            return self.fetch_ft_sentiment(limit=limit)
        if source == "ft_macro_indicators":
            return self.fetch_ft_macro_indicators(limit=limit)
        raise ValueError(f"unsupported source: {source}")

    def fetch_ft_news(self, *, limit: int, codes: list[str] | None = None) -> list[dict[str, Any]]:
        self.calls.append(f"ft_news:{limit}:{','.join(codes or [])}")
        return [
            {
                "id": 1,
                "title": "宁德时代技术发布会简析",
                "source": "ths",
                "source_name": "同花顺",
                "category": "company",
                "tags": [],
                "related_stocks": ["300750"],
                "published_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
                "created_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            }
        ]

    def fetch_ft_market_flow(self, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append(f"ft_market_flow:{limit}")
        return [
            {
                "id": 2,
                "data_type": "stock_flow",
                "trade_date": "2026-05-01",
                "data": {"code": "300750", "net_amount": 1.2},
            }
        ]

    def fetch_ft_market_cache(self, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append(f"ft_market_cache:{limit}")
        return []

    def fetch_ft_sentiment(self, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append(f"ft_sentiment:{limit}")
        return []

    def fetch_ft_macro_indicators(self, *, limit: int) -> list[dict[str, Any]]:
        self.calls.append(f"ft_macro_indicators:{limit}")
        return []


def test_project_single_source_only_calls_requested_repository_method() -> None:
    repo = FakeProjectionRepository()
    result = KnowledgeSourceProjectionService(repo).project(
        KnowledgeSourceProjectionCommand(sources=["ft_news"], codes=["300750"], limit=3)
    )

    assert repo.calls == ["ft_news:3:300750"]
    assert result.total_records == 1
    assert result.source_counts == {"ft_news": 1}
    assert result.records[0]["source_type"] == "news_articles"


def test_project_multiple_sources_aggregates_records_without_compiling() -> None:
    repo = FakeProjectionRepository()
    result = KnowledgeSourceProjectionService(repo).project(
        KnowledgeSourceProjectionCommand(sources=["ft_news", "ft_market_flow"], limit=2)
    )

    assert repo.calls == ["ft_news:2:", "ft_market_flow:2"]
    assert result.total_records == 2
    assert [record["source_type"] for record in result.records] == [
        "news_articles",
        "derived_signal",
    ]
    assert result.coverage["ft_news"]["projection_rate"] == 1.0
    assert result.coverage["ft_market_flow"]["data_types"]["stock_flow"] == {
        "total": 1,
        "projected": 1,
        "skipped": 0,
    }


def test_project_reports_structured_projection_coverage_and_skip_reasons() -> None:
    class RepoWithSkipped(FakeProjectionRepository):
        def fetch_ft_market_flow(self, *, limit: int) -> list[dict[str, Any]]:
            self.calls.append(f"ft_market_flow:{limit}")
            return [
                {
                    "id": 2,
                    "data_type": "stock_flow",
                    "trade_date": "2026-05-01",
                    "data": {"code": "300750", "net_amount": 1.2},
                },
                {
                    "id": 3,
                    "data_type": "stock_flow",
                    "trade_date": "2026-05-01",
                    "data": {"code": "300750"},
                },
            ]

    result = KnowledgeSourceProjectionService(RepoWithSkipped()).project(
        KnowledgeSourceProjectionCommand(sources=["ft_market_flow"], limit=2, include_skipped=True)
    )

    assert result.total_records == 1
    assert result.coverage["ft_market_flow"]["total_rows"] == 2
    assert result.coverage["ft_market_flow"]["projection_rate"] == 0.5
    assert result.coverage["ft_market_flow"]["skip_reasons"] == {"missing_value": 1}
    assert result.skipped[0]["source"] == "ft_market_flow"
    assert result.skipped[0]["source_pk"] == 3
    assert result.skipped[0]["data_type"] == "stock_flow"
    assert result.skipped[0]["reason"] == "missing_value"
    assert result.skipped[0]["data_shape"] == "dict"


def test_project_unknown_source_fails_fast() -> None:
    repo = FakeProjectionRepository()

    try:
        KnowledgeSourceProjectionService(repo).project(
            KnowledgeSourceProjectionCommand(sources=["unknown"])
        )
    except ValueError as exc:
        assert "unsupported sources" in str(exc)
    else:
        raise AssertionError("expected unsupported source to fail")
