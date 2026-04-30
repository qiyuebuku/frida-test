"""Tests for first-version KG incremental refresh orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.application.dto.knowledge_dto import (
    KnowledgeBootstrapStockNewsCommand,
    KnowledgeBootstrapStocksCommand,
    KnowledgeCompileResultDTO,
    KnowledgeIncrementalRefreshCommand,
    KnowledgeIncrementalRefreshResultDTO,
    KnowledgeRebuildIndexesCommand,
    KnowledgeRebuildIndexesResultDTO,
    KnowledgeRebuildWikiCommand,
    KnowledgeRebuildWikiResultDTO,
)
from src.application.services import knowledge_service as service_module
from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge.toy_adapter import ToyProjectAdapter


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "knowledge"


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


@pytest.mark.asyncio
async def test_compile_kg_refreshes_changed_indexes_incrementally(monkeypatch) -> None:
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    repository = _CompileRefreshRepository()
    hybrid_calls: list[dict] = []

    class FakeRetriever:
        async def upsert_index(self, **kwargs):
            hybrid_calls.append(kwargs)
            return (
                len(kwargs["chunks"])
                + len(kwargs["nodes"])
                + len(kwargs["edges"])
                + len(kwargs["wiki_pages"])
            )

    monkeypatch.setattr(service_module, "get_adapter", lambda _name: ToyProjectAdapter())
    monkeypatch.setattr(service_module, "MilvusSemanticHybridRetriever", lambda: FakeRetriever())

    result = await KnowledgeService(repository=repository).compile_kg(
        service_module.KnowledgeCompileCommand(
            adapter_name="toy",
            records=records,
            target="test",
        )
    )

    assert result.index_refresh["mode"] == "incremental"
    assert result.index_refresh["graph_adjacency"] == 3
    assert result.index_refresh["evidence_chunks"] == 1
    assert result.index_refresh["wiki_pages"] >= 1
    assert result.index_refresh["hybrid_chunks"] == len(hybrid_calls[0]["chunks"]) + len(
        hybrid_calls[0]["nodes"]
    ) + len(hybrid_calls[0]["edges"]) + len(hybrid_calls[0]["wiki_pages"])
    assert repository.calls[:3] == ["create_run", "upsert_nodes:4", "upsert_evidence:1"]
    assert "upsert_graph_adjacency:3" in repository.calls
    assert "upsert_evidence_chunks:1" in repository.calls


@pytest.mark.asyncio
async def test_incremental_refresh_task_can_run_and_persist_status() -> None:
    repository = _TaskRepository()
    service = _TaskService(repository=repository)

    task = await service.enqueue_financial_incremental_refresh_task(
        KnowledgeIncrementalRefreshCommand(target="test", codes=["300750"]),
        max_retries=2,
    )
    result = await service.run_financial_incremental_refresh_task(task.run_id)

    assert task.status == "pending"
    assert result.status == "success"
    assert result.attempt == 1
    assert result.result["adapter_name"] == "financial"
    assert repository.runs[task.run_id]["status"] == "success"


@pytest.mark.asyncio
async def test_incremental_refresh_task_records_failure_and_retry() -> None:
    repository = _TaskRepository()
    service = _TaskService(repository=repository, fail_once=True)

    task = await service.enqueue_financial_incremental_refresh_task(
        KnowledgeIncrementalRefreshCommand(target="test", codes=["300750"]),
        max_retries=2,
    )
    failed = await service.run_financial_incremental_refresh_task(task.run_id)
    retried = await service.retry_financial_incremental_refresh_task(task.run_id)

    assert failed.status == "failed"
    assert failed.attempt == 1
    assert failed.error == "boom"
    assert retried.status == "success"
    assert retried.attempt == 2


class _CompileRefreshRepository:
    def __init__(self):
        self.calls: list[str] = []
        self.nodes = []
        self.edges = []
        self.evidence = []
        self.wiki_pages = []

    def create_compilation_run(self, _run):
        self.calls.append("create_run")
        return "kg_run:test"

    def finish_compilation_run(self, _run_id, _result):
        self.calls.append("finish_run")

    def upsert_nodes(self, nodes):
        self.calls.append(f"upsert_nodes:{len(nodes)}")
        self.nodes = nodes
        return len(nodes)

    def upsert_edges(self, edges):
        self.calls.append(f"upsert_edges:{len(edges)}")
        self.edges = edges
        return len(edges)

    def upsert_evidence(self, evidence):
        self.calls.append(f"upsert_evidence:{len(evidence)}")
        self.evidence = evidence
        return len(evidence)

    def upsert_graph_adjacency(self, edges):
        self.calls.append(f"upsert_graph_adjacency:{len(edges)}")
        return len(edges)

    def upsert_evidence_chunks(self, evidence):
        self.calls.append(f"upsert_evidence_chunks:{len(evidence)}")
        return len(evidence)

    def upsert_wiki_pages(self, _adapter_name, pages):
        self.calls.append(f"upsert_wiki_pages:{len(pages)}")
        self.wiki_pages = pages
        return len(pages)

    def get_node(self, node_id):
        return next((node for node in self.nodes if node.node_id == node_id), None)

    def list_nodes(self, _adapter_name):
        return self.nodes

    def list_edges(self, _adapter_name):
        return self.edges

    def list_evidence(self, _adapter_name):
        return self.evidence

    def attach_edge_evidence(self, *_args):
        return 0


class _TaskRepository:
    def __init__(self):
        self.runs: dict[str, dict] = {}

    def create_compilation_run(self, run):
        run_id = run["run_id"]
        current = self.runs.get(run_id, {})
        metadata = run.get("metadata", current.get("metadata", {}))
        self.runs[run_id] = {
            **current,
            **run,
            "metadata": metadata,
        }
        return run_id

    def finish_compilation_run(self, run_id, result):
        current = self.runs[run_id]
        current["status"] = result.get("status", current.get("status"))
        current["metadata"] = result.get("metadata", current.get("metadata", {}))

    def get_compilation_run(self, run_id):
        return self.runs.get(run_id)

    def list_compilation_runs(self, **_kwargs):
        return list(self.runs.values())


class _TaskService(KnowledgeService):
    def __init__(self, repository, fail_once: bool = False):
        super().__init__(repository=repository)
        self.fail_once = fail_once
        self.calls = 0

    async def refresh_financial_incremental(self, command: KnowledgeIncrementalRefreshCommand):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RuntimeError("boom")
        return KnowledgeIncrementalRefreshResultDTO(
            adapter_name="financial",
            target=command.target,
            run_id="kg_run:incremental:test",
            dry_run=command.dry_run,
            steps=[
                {
                    "name": "bootstrap_stocks",
                    "status": "ok",
                    "result": {
                        "nodes": 1,
                        "edges": 0,
                        "evidence": 1,
                        "failed_records": 0,
                    },
                }
            ],
        )
