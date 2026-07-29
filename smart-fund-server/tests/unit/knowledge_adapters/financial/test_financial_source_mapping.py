"""Financial source mapping tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter


FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "knowledge" / "financial"


@pytest.mark.asyncio
async def test_stock_basics_normalizes_and_extracts_stock_node() -> None:
    adapter = FinancialKGAdapter()
    record = _load("stock_basics.json")[0]

    item = adapter.normalize(record)[0]
    nodes = await adapter.extract_node_drafts(item)
    evidence = adapter.extract_evidence_drafts(item)

    assert item.source_type == "stock_basics"
    assert nodes[0].node_type == "stock"
    assert nodes[0].stable_key == "SZ:300750"
    assert evidence[0].source_id == item.source_id


def test_missing_required_source_field_fails_fast() -> None:
    adapter = FinancialKGAdapter()
    record = {"source_type": "stock_basics", "payload": {"code": "300750", "exchange": "SZ"}}

    with pytest.raises(ValueError, match="missing required fields"):
        adapter.normalize(record)


def _load(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
