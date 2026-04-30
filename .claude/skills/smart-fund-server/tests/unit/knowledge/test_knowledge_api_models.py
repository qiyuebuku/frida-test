"""Tests for knowledge API request models."""

import pytest
from pydantic import ValidationError

from src.interfaces.api.routes.knowledge import (
    KGCompileRequest,
    KGFinancialIncrementalRefreshRequest,
    KGResearchContextRequest,
    KGRebuildIndexesRequest,
)


def test_compile_request_requires_records() -> None:
    with pytest.raises(ValidationError):
        KGCompileRequest(records=[])

    req = KGCompileRequest(records=[{"source_type": "demo"}], concurrency=2)
    assert req.concurrency == 2


def test_research_context_request_validates_limits() -> None:
    with pytest.raises(ValidationError):
        KGResearchContextRequest(query="x", graph_depth=0)

    req = KGResearchContextRequest(query="固态电池")
    assert req.adapter_name == "financial"
    assert req.target == "prod"
    assert req.retrieval_mode == "deterministic_plan"


def test_rebuild_indexes_defaults_to_minimal_indexes() -> None:
    req = KGRebuildIndexesRequest()

    assert req.index_types == ["graph_adjacency", "evidence_chunks"]


def test_financial_incremental_refresh_request_defaults_are_bounded() -> None:
    req = KGFinancialIncrementalRefreshRequest(codes=["300750"])

    assert req.codes == ["300750"]
    assert req.stock_limit == 500
    assert req.news_limit == 20
    assert req.rebuild_indexes is True
