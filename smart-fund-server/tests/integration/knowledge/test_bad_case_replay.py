"""Integration tests for replay and rebuild checks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from src.application.services.knowledge_service import KnowledgeService
from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.retrieval import RetrievalOptions
from src.domain.knowledge.toy_adapter import ToyProjectAdapter
from src.infrastructure.connections import get_engine, get_session
from src.infrastructure.persistence.models import Base
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCompilationRun,
    KnowledgeEdge,
    KnowledgeEdgeEvidence,
    KnowledgeEdgeEvidenceChunk,
    KnowledgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeGraphAdjacency,
    KnowledgeNode,
    KnowledgeReviewItem,
    KnowledgeVersion,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import KnowledgeRepositoryImpl


pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "knowledge"
KG_TABLES = [
    KnowledgeNode.__table__,
    KnowledgeEvidence.__table__,
    KnowledgeEdge.__table__,
    KnowledgeEdgeEvidence.__table__,
    KnowledgeEdgeEvidenceChunk.__table__,
    KnowledgeVersion.__table__,
    KnowledgeReviewItem.__table__,
    KnowledgeCompilationRun.__table__,
    KnowledgeGraphAdjacency.__table__,
    KnowledgeEvidenceChunk.__table__,
]


def test_bad_case_replay_and_rebuild_keeps_context_refs() -> None:
    _ensure_tables()
    _cleanup()
    repo = KnowledgeRepositoryImpl(target="test")
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    first = _compile_toy(repo, records)
    second = _compile_toy(repo, records)
    service = KnowledgeService(repository=repo)
    asyncio.run(service.rebuild_indexes("toy"))
    before = asyncio.run(service.build_answer_context("Alpha", RetrievalOptions(adapter_name="toy")))
    asyncio.run(service.rebuild_indexes("toy"))
    after = asyncio.run(service.build_answer_context("Alpha", RetrievalOptions(adapter_name="toy")))
    replay = asyncio.run(
        service.replay_bad_case(
            case_id="toy-alpha",
            query="Alpha",
            expected_refs=[before.hits[0].hit_id],
            actual_refs=[hit.hit_id for hit in after.hits],
        )
    )
    diff = asyncio.run(service.compare_compile_results(first, second))

    assert replay.passed
    assert diff["same_node_ids"]
    assert diff["same_edge_ids"]

    _cleanup()


def _ensure_tables() -> None:
    Base.metadata.create_all(get_engine("test"), tables=KG_TABLES)


def _compile_toy(repo: KnowledgeRepositoryImpl, records: list[dict]):
    adapter = ToyProjectAdapter()
    return asyncio.run(KnowledgeCompiler(repository=repo).compile(adapter, adapter.normalize(records)))


def _cleanup() -> None:
    with get_session("test") as session:
        edge_ids = select(KnowledgeEdge.edge_id).where(KnowledgeEdge.adapter_name == "toy")
        session.execute(delete(KnowledgeEdgeEvidence).where(KnowledgeEdgeEvidence.edge_id.in_(edge_ids)))
        session.execute(delete(KnowledgeGraphAdjacency).where(KnowledgeGraphAdjacency.adapter_name == "toy"))
        session.execute(

            delete(KnowledgeEdgeEvidenceChunk).where(

                KnowledgeEdgeEvidenceChunk.evidence_id.in_(

                    select(KnowledgeEvidence.evidence_id).where(KnowledgeEvidence.adapter_name == "toy")

                )

            )

        )

        session.execute(delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.adapter_name == "toy"))
        session.execute(delete(KnowledgeReviewItem).where(KnowledgeReviewItem.object_id.like("kg:toy:%")))
        session.execute(delete(KnowledgeEdge).where(KnowledgeEdge.adapter_name == "toy"))
        session.execute(delete(KnowledgeEvidence).where(KnowledgeEvidence.adapter_name == "toy"))
        session.execute(delete(KnowledgeNode).where(KnowledgeNode.adapter_name == "toy"))
        session.execute(delete(KnowledgeCompilationRun).where(KnowledgeCompilationRun.adapter_name == "toy"))
