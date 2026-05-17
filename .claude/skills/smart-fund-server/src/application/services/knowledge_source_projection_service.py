"""Application service for projecting business raw rows into KG Source Records."""

from __future__ import annotations

from typing import Any, Callable

from src.application.dto.knowledge_dto import (
    KnowledgeSourceProjectionCommand,
    KnowledgeSourceProjectionResultDTO,
)
from src.domain.knowledge.repositories import KnowledgeSourceProjectionRepository
from src.domain.knowledge_adapters.financial.source_projection import (
    explain_projection_skip,
    project_ft_macro_indicator_row,
    project_ft_market_cache_row,
    project_ft_market_flow_row,
    project_ft_news_row,
    project_ft_sentiment_row,
)

DEFAULT_SOURCES = [
    "ft_news",
    "ft_market_flow",
    "ft_market_cache",
    "ft_sentiment",
    "ft_macro_indicators",
]


class KnowledgeSourceProjectionService:
    """Coordinates source reads and pure projection functions."""

    def __init__(self, repository: KnowledgeSourceProjectionRepository):
        self.repository = repository

    def project(self, command: KnowledgeSourceProjectionCommand) -> KnowledgeSourceProjectionResultDTO:
        sources = _normalize_sources(command.sources)
        limit = _limit(command.limit)
        records: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        warnings: list[str] = []
        source_counts: dict[str, int] = {}
        coverage: dict[str, Any] = {}

        for source in sources:
            try:
                raw_rows = self._fetch(source, limit=limit, codes=command.codes)
            except Exception as exc:
                warnings.append(f"{source}: {exc}")
                continue
            projector = self._projector(source)
            source_count = 0
            source_coverage = _empty_source_coverage(total_rows=len(raw_rows))
            for row in raw_rows:
                data_type = _row_data_type(row)
                _coverage_seen(source_coverage, data_type)
                record = projector(row)
                if record is None:
                    skip_info = explain_projection_skip(source, row)
                    _coverage_skipped(source_coverage, data_type, skip_info["reason"])
                    if command.include_skipped:
                        skipped.append(
                            {
                                "source": source,
                                "source_pk": row.get("id"),
                                "data_type": data_type,
                                **skip_info,
                            }
                        )
                    continue
                records.append(record)
                source_count += 1
                _coverage_projected(source_coverage, data_type)
            source_counts[source] = source_count
            coverage[source] = source_coverage

        return KnowledgeSourceProjectionResultDTO(
            records=records,
            total_records=len(records),
            source_counts=source_counts,
            skipped=skipped,
            warnings=warnings,
            coverage=coverage,
        )

    def _fetch(self, source: str, *, limit: int, codes: list[str]) -> list[dict[str, Any]]:
        return self.repository.fetch_rows(source, limit=limit, codes=codes or None)

    def _projector(self, source: str) -> Callable[[dict[str, Any]], dict[str, Any] | None]:
        if source == "ft_news":
            return project_ft_news_row
        if source == "ft_market_flow":
            return project_ft_market_flow_row
        if source == "ft_market_cache":
            return project_ft_market_cache_row
        if source == "ft_sentiment":
            return project_ft_sentiment_row
        if source == "ft_macro_indicators":
            return project_ft_macro_indicator_row
        raise ValueError(f"unsupported source: {source}")


def _normalize_sources(sources: list[str] | None) -> list[str]:
    result = DEFAULT_SOURCES if not sources else sources
    unknown = sorted(set(result) - set(DEFAULT_SOURCES))
    if unknown:
        raise ValueError(f"unsupported sources: {', '.join(unknown)}")
    return list(dict.fromkeys(result))


def _limit(value: int) -> int:
    return max(1, min(int(value), 5000))


def _row_data_type(row: dict[str, Any]) -> str:
    return str(row.get("data_type") or row.get("indicator") or "unknown")


def _empty_source_coverage(*, total_rows: int) -> dict[str, Any]:
    return {
        "total_rows": total_rows,
        "projected": 0,
        "skipped": 0,
        "projection_rate": 0.0,
        "data_types": {},
        "skip_reasons": {},
    }


def _coverage_seen(coverage: dict[str, Any], data_type: str) -> None:
    item = coverage["data_types"].setdefault(data_type, {"total": 0, "projected": 0, "skipped": 0})
    item["total"] += 1


def _coverage_projected(coverage: dict[str, Any], data_type: str) -> None:
    coverage["projected"] += 1
    coverage["projection_rate"] = round(coverage["projected"] / max(coverage["total_rows"], 1), 4)
    coverage["data_types"][data_type]["projected"] += 1


def _coverage_skipped(coverage: dict[str, Any], data_type: str, reason: str) -> None:
    coverage["skipped"] += 1
    coverage["projection_rate"] = round(coverage["projected"] / max(coverage["total_rows"], 1), 4)
    coverage["data_types"][data_type]["skipped"] += 1
    coverage["skip_reasons"][reason] = coverage["skip_reasons"].get(reason, 0) + 1
