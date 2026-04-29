"""Tests for first-version KG incremental refresh orchestration."""

from __future__ import annotations

import pytest

from src.application.dto.knowledge_dto import (
    KnowledgeBootstrapStockNewsCommand,
    KnowledgeBootstrapStocksCommand,
    KnowledgeCompileResultDTO,
    KnowledgeIncrementalRefreshCommand,
    KnowledgeRebuildIndexesCommand,
    KnowledgeRebuildIndexesResultDTO,
    KnowledgeRebuildWikiCommand,
    KnowledgeRebuildWikiResultDTO,
)
from src.application.services.knowledge_service import KnowledgeService


class _Service(KnowledgeService):
    def __init__(self):
        super().__init__(repository=None)
        self.calls: list[str] = []

    async def bootstrap_financial_stock_entities(self, command: KnowledgeBootstrapStocksCommand):
        self.calls.append(f"stocks:{command.target}:{','.join(command.codes)}")
        return KnowledgeCompileResultDTO(
            adapter_name="financial",
            run_id="kg_run:stocks",
            nodes=1,
            edges=0,
            evidence=1,
            failed_records=0,
            dry_run=command.dry_run,
        )

    async def bootstrap_financial_stock_news(self, command: KnowledgeBootstrapStockNewsCommand):
        self.calls.append(f"news:{command.limit}:{command.concurrency}")
        return KnowledgeCompileResultDTO(
            adapter_name="financial",
            run_id="kg_run:news",
            nodes=2,
            edges=1,
            evidence=1,
            failed_records=0,
            dry_run=command.dry_run,
        )

    async def rebuild_wiki_for(self, command: KnowledgeRebuildWikiCommand):
        self.calls.append(f"wiki:{command.adapter_name}")
        return KnowledgeRebuildWikiResultDTO(
            adapter_name=command.adapter_name,
            run_id="kg_run:wiki",
            pages=3,
            issues=0,
        )

    async def rebuild_indexes_for(self, command: KnowledgeRebuildIndexesCommand):
        self.calls.append("indexes:" + ",".join(command.index_types))
        return KnowledgeRebuildIndexesResultDTO(
            adapter_name=command.adapter_name,
            run_id="kg_run:indexes",
            graph_adjacency=1,
            evidence_chunks=1,
            hybrid_chunks=1,
        )


@pytest.mark.asyncio
async def test_incremental_refresh_runs_compile_wiki_and_indexes() -> None:
    service = _Service()

    result = await service.refresh_financial_incremental(
        KnowledgeIncrementalRefreshCommand(
            target="test",
            codes=["300750"],
            news_limit=7,
            concurrency=2,
        )
    )

    assert result.adapter_name == "financial"
    assert result.target == "test"
    assert result.dry_run is False
    assert [step["name"] for step in result.steps] == [
        "bootstrap_stocks",
        "bootstrap_stock_news",
        "rebuild_wiki",
        "rebuild_indexes",
    ]
    assert service.calls == [
        "stocks:test:300750",
        "news:7:2",
        "wiki:financial",
        "indexes:graph_adjacency,evidence_chunks,hybrid_chunks",
    ]


@pytest.mark.asyncio
async def test_incremental_refresh_dry_run_skips_rebuild_steps() -> None:
    service = _Service()

    result = await service.refresh_financial_incremental(
        KnowledgeIncrementalRefreshCommand(
            target="test",
            codes=["300750"],
            dry_run=True,
        )
    )

    assert [step["status"] for step in result.steps] == ["ok", "ok", "skipped", "skipped"]
    assert service.calls == ["stocks:test:300750", "news:20:1"]
