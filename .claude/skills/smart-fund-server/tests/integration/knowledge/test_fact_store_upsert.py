"""Integration tests for the generic knowledge fact store."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, inspect

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode
from src.infrastructure.connections import get_engine, get_session
from src.infrastructure.persistence.models import Base
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCompilationRun,
    KnowledgeEdge,
    KnowledgeEdgeEvidence,
    KnowledgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeGraphAdjacency,
    KnowledgeNode,
    KnowledgeReviewItem,
    KnowledgeVersion,
    KnowledgeWikiPage,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (
    KnowledgeRepositoryImpl,
)


pytestmark = pytest.mark.integration


KG_TABLES = [
    KnowledgeNode.__table__,
    KnowledgeEvidence.__table__,
    KnowledgeEdge.__table__,
    KnowledgeEdgeEvidence.__table__,
    KnowledgeVersion.__table__,
    KnowledgeReviewItem.__table__,
    KnowledgeCompilationRun.__table__,
    KnowledgeWikiPage.__table__,
    KnowledgeGraphAdjacency.__table__,
    KnowledgeEvidenceChunk.__table__,
]


def test_fact_store_schema_creates_all_kg_tables() -> None:
    _ensure_tables()

    inspector = inspect(get_engine("test"))
    table_names = set(inspector.get_table_names())

    assert {
        "kg_nodes",
        "kg_edges",
        "kg_evidence",
        "kg_edge_evidence",
        "kg_versions",
        "kg_review_items",
        "kg_compilation_runs",
        "kg_wiki_pages",
        "kg_graph_adjacency",
        "kg_evidence_chunks",
    }.issubset(table_names)


def test_fact_store_upsert_and_edge_evidence_roundtrip() -> None:
    _ensure_tables()
    _cleanup()

    repo = KnowledgeRepositoryImpl(target="test")
    nodes = [
        CompiledNode(
            node_id="kg:toy:person:alice_it",
            adapter_name="toy_it",
            node_type="person",
            canonical_name="Alice",
            status=NodeStatus.ACTIVE,
            version="v1",
        ),
        CompiledNode(
            node_id="kg:toy:project:alpha_it",
            adapter_name="toy_it",
            node_type="project",
            canonical_name="Alpha",
            status=NodeStatus.ACTIVE,
            version="v1",
        ),
    ]
    evidence = CompiledEvidence(
        evidence_id="kg_ev:toy_it:note:n1:abc",
        adapter_name="toy_it",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content="Alice owns Alpha.",
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:toy_it:owns:abc",
        adapter_name="toy_it",
        source_node_id=nodes[0].node_id,
        target_node_id=nodes[1].node_id,
        relation_type="owns",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_ids=[evidence.evidence_id],
        version="v1",
    )

    assert repo.upsert_nodes(nodes) == 2
    assert repo.upsert_evidence([evidence]) == 1
    assert repo.upsert_edges([edge]) == 1

    loaded_edge = repo.get_edge(edge.edge_id)
    loaded_evidence = repo.get_edge_evidence(edge.edge_id)

    assert loaded_edge is not None
    assert loaded_edge.evidence_ids == [evidence.evidence_id]
    assert [item.evidence_id for item in loaded_evidence] == [evidence.evidence_id]

    # Idempotency: same facts should not create duplicates.
    repo.upsert_nodes(nodes)
    repo.upsert_evidence([evidence])
    repo.upsert_edges([edge])

    with get_session("test") as session:
        node_count = session.query(KnowledgeNode).filter_by(adapter_name="toy_it").count()
        edge_count = session.query(KnowledgeEdge).filter_by(adapter_name="toy_it").count()
        evidence_count = session.query(KnowledgeEvidence).filter_by(adapter_name="toy_it").count()
        link_count = session.query(KnowledgeEdgeEvidence).filter_by(edge_id=edge.edge_id).count()

    assert node_count == 2
    assert edge_count == 1
    assert evidence_count == 1
    assert link_count == 1

    _cleanup()


def test_compilation_run_create_and_finish() -> None:
    _ensure_tables()
    _cleanup()

    repo = KnowledgeRepositoryImpl(target="test")
    run_id = repo.create_compilation_run(
        {
            "run_id": "kg_run:toy_it:1",
            "adapter_name": "toy_it",
            "adapter_version": "v1",
            "input_count": 1,
        }
    )
    repo.finish_compilation_run(
        run_id,
        {
            "status": "success",
            "input_count": 1,
            "node_count": 2,
            "edge_count": 1,
            "evidence_count": 1,
        },
    )

    with get_session("test") as session:
        run = session.get(KnowledgeCompilationRun, run_id)

    assert run is not None
    assert run.status == "success"
    assert run.node_count == 2

    _cleanup()


def _ensure_tables() -> None:
    Base.metadata.create_all(get_engine("test"), tables=KG_TABLES)


def _cleanup() -> None:
    with get_session("test") as session:
        session.execute(delete(KnowledgeEdgeEvidence).where(KnowledgeEdgeEvidence.edge_id.like("kg_edge:toy_it:%")))
        session.execute(delete(KnowledgeEdge).where(KnowledgeEdge.adapter_name == "toy_it"))
        session.execute(delete(KnowledgeEvidence).where(KnowledgeEvidence.adapter_name == "toy_it"))
        session.execute(delete(KnowledgeNode).where(KnowledgeNode.adapter_name == "toy_it"))
        session.execute(delete(KnowledgeCompilationRun).where(KnowledgeCompilationRun.adapter_name == "toy_it"))
