"""Unit tests for the knowledge compiler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.toy_adapter import ToyProjectAdapter


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "knowledge"


@pytest.mark.asyncio
async def test_compiler_runs_toy_adapter_minimum_closed_loop() -> None:
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    adapter = ToyProjectAdapter()

    result = await KnowledgeCompiler().compile(adapter, adapter.normalize(records))

    assert result.adapter_name == "toy"
    assert result.version == "v1"
    assert result.nodes == []
    assert result.edges == []
    assert len(result.evidence) == 1
    assert result.failed_records == []


@pytest.mark.asyncio
async def test_compiler_materializes_chunks_before_downstream_indexes() -> None:
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    materialized: list[str] = []

    async def materialize(_adapter_name, _version, evidence):
        for item in evidence:
            materialized.append(f"kg_chunk:{item.evidence_id}:0")

    adapter = ToyProjectAdapter()

    result = await KnowledgeCompiler(pre_extraction_chunk_materializer=materialize).compile(
        adapter,
        adapter.normalize(records[:1]),
    )

    assert result.failed_records == []
    assert result.nodes == []
    assert result.edges == []
    assert materialized


@pytest.mark.asyncio
async def test_application_service_reports_normalize_failure() -> None:
    result = await KnowledgeService().compile(ToyProjectAdapter(), [{"payload": {}}])

    assert result.nodes == []
    assert result.edges == []
    assert result.evidence == []
    assert len(result.failed_records) == 1
    assert result.failed_records[0].source_id == "normalize:0"
    assert result.failed_records[0].reason.startswith("normalize failed:")


@pytest.mark.asyncio
async def test_application_service_compile_delegates_to_compiler() -> None:
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))

    result = await KnowledgeService().compile(ToyProjectAdapter(), records)

    assert len(result.nodes) == 0
    assert len(result.edges) == 0
    assert len(result.evidence) == 1


@pytest.mark.asyncio
async def test_application_service_isolates_single_normalize_failure() -> None:
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    records.append({"payload": {}})

    result = await KnowledgeService().compile(ToyProjectAdapter(), records)

    assert len(result.nodes) == 0
    assert len(result.edges) == 0
    assert len(result.evidence) == 1
    assert len(result.failed_records) == 1
    assert result.failed_records[0].source_id == "normalize:1"
