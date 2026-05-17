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
    assert len(result.nodes) == 4
    assert len(result.edges) == 3
    assert len(result.evidence) == 1
    assert result.failed_records == []
    assert {node.node_type for node in result.nodes} == {"person", "project", "document"}
    assert {edge.relation_type for edge in result.edges} == {"owns", "references", "blocks"}
    assert all(edge.evidence_ids for edge in result.edges)


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

    assert len(result.nodes) == 4
    assert len(result.edges) == 3


@pytest.mark.asyncio
async def test_application_service_isolates_single_normalize_failure() -> None:
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    records.append({"payload": {}})

    result = await KnowledgeService().compile(ToyProjectAdapter(), records)

    assert len(result.nodes) == 4
    assert len(result.edges) == 3
    assert len(result.evidence) == 1
    assert len(result.failed_records) == 1
    assert result.failed_records[0].source_id == "normalize:1"
