"""Integration tests for the generic knowledge fact store."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, inspect, select

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.graph_index import GraphIndexCommunity, GraphIndexFinding
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode
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
    KnowledgeGraphCommunity,
    KnowledgeGraphDelta,
    KnowledgeGraphFinding,
    KnowledgeNode,
    KnowledgeReviewItem,
    KnowledgeVersion,
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
    KnowledgeEdgeEvidenceChunk.__table__,
    KnowledgeVersion.__table__,
    KnowledgeReviewItem.__table__,
    KnowledgeCompilationRun.__table__,
    KnowledgeGraphAdjacency.__table__,
    KnowledgeEvidenceChunk.__table__,
    KnowledgeGraphCommunity.__table__,
    KnowledgeGraphFinding.__table__,
    KnowledgeGraphDelta.__table__,
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
        "kg_edge_evidence_chunks",
        "kg_versions",
        "kg_review_items",
        "kg_compilation_runs",
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
    assert repo.upsert_evidence_chunks([evidence]) == 1

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
        chunk_link = session.query(KnowledgeEdgeEvidenceChunk).filter_by(edge_id=edge.edge_id).one()

    assert node_count == 2
    assert edge_count == 1
    assert evidence_count == 1
    assert link_count == 1
    assert chunk_link.chunk_id == f"kg_chunk:{evidence.evidence_id}:0"

    _cleanup()


def test_edge_evidence_chunk_refs_prefer_matching_evidence_span() -> None:
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
        evidence_id="kg_ev:toy_it:note:n1:span",
        adapter_name="toy_it",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content=f"{'A' * 950}TARGET_RELATION owns Alpha.",
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:toy_it:owns:span",
        adapter_name="toy_it",
        source_node_id=nodes[0].node_id,
        target_node_id=nodes[1].node_id,
        relation_type="owns",
        properties={"evidence_spans": [{"field_name": "text", "text": "TARGET_RELATION owns Alpha"}]},
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_ids=[evidence.evidence_id],
        version="v1",
    )

    repo.upsert_nodes(nodes)
    repo.upsert_evidence([evidence])
    repo.upsert_edges([edge])
    assert repo.upsert_evidence_chunks([evidence]) == 2

    with get_session("test") as session:
        chunk_links = (
            session.query(KnowledgeEdgeEvidenceChunk)
            .filter_by(edge_id=edge.edge_id)
            .order_by(KnowledgeEdgeEvidenceChunk.chunk_id)
            .all()
        )

    assert [link.chunk_id for link in chunk_links] == [f"kg_chunk:{evidence.evidence_id}:1"]

    _cleanup()


def test_edge_evidence_chunk_refs_prefer_explicit_chunk_id() -> None:
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
        evidence_id="kg_ev:toy_it:note:n1:explicit",
        adapter_name="toy_it",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content=f"{'A' * 950}TARGET_RELATION owns Alpha.",
        version="v1",
    )
    explicit_chunk_id = f"kg_chunk:{evidence.evidence_id}:0"
    edge = CompiledEdge(
        edge_id="kg_edge:toy_it:owns:explicit",
        adapter_name="toy_it",
        source_node_id=nodes[0].node_id,
        target_node_id=nodes[1].node_id,
        relation_type="owns",
        properties={
            "evidence_spans": [
                {
                    "field_name": "text",
                    "text": "TARGET_RELATION owns Alpha",
                    "chunk_id": explicit_chunk_id,
                }
            ]
        },
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_ids=[evidence.evidence_id],
        version="v1",
    )

    repo.upsert_nodes(nodes)
    repo.upsert_evidence([evidence])
    repo.upsert_edges([edge])
    assert repo.upsert_evidence_chunks([evidence]) == 2

    with get_session("test") as session:
        chunk_links = (
            session.query(KnowledgeEdgeEvidenceChunk)
            .filter_by(edge_id=edge.edge_id)
            .order_by(KnowledgeEdgeEvidenceChunk.chunk_id)
            .all()
        )

    assert [link.chunk_id for link in chunk_links] == [explicit_chunk_id]

    _cleanup()


def test_fact_store_supersedes_old_same_source_evidence() -> None:
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
    old_evidence = CompiledEvidence(
        evidence_id="kg_ev:toy_it:note:n1:old",
        adapter_name="toy_it",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content="Alice owns Alpha old.",
        version="v1",
        source_fingerprint="old",
    )
    new_evidence = CompiledEvidence(
        evidence_id="kg_ev:toy_it:note:n1:new",
        adapter_name="toy_it",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content="Alice owns Alpha new.",
        version="v1",
        source_fingerprint="new",
    )
    old_edge = CompiledEdge(
        edge_id="kg_edge:toy_it:owns:old",
        adapter_name="toy_it",
        source_node_id=nodes[0].node_id,
        target_node_id=nodes[1].node_id,
        relation_type="owns",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_ids=[old_evidence.evidence_id],
        version="v1",
    )

    repo.upsert_nodes(nodes)
    repo.upsert_evidence([old_evidence])
    repo.upsert_edges([old_edge])
    repo.upsert_evidence([new_evidence])

    with get_session("test") as session:
        old_row = session.get(KnowledgeEvidence, old_evidence.evidence_id)
        new_row = session.get(KnowledgeEvidence, new_evidence.evidence_id)
        old_edge_row = session.get(KnowledgeEdge, old_edge.edge_id)

    assert old_row is not None
    assert old_row.status == "superseded"
    assert old_row.superseded_by == new_evidence.evidence_id
    assert new_row is not None
    assert new_row.status == "active"
    assert old_edge_row is not None
    assert old_edge_row.status == "deprecated"
    assert repo.get_evidence(old_evidence.evidence_id) is None
    assert repo.get_evidence(new_evidence.evidence_id) is not None
    assert [item.evidence_id for item in repo.list_evidence("toy_it")] == [new_evidence.evidence_id]
    assert {item.evidence_id for item in repo.list_evidence("toy_it", include_inactive=True)} == {
        old_evidence.evidence_id,
        new_evidence.evidence_id,
    }

    _cleanup()


def test_graph_index_replace_keeps_only_current_rows() -> None:
    _ensure_tables()
    _cleanup()

    repo = KnowledgeRepositoryImpl(target="test")
    community_v1 = GraphIndexCommunity(
        community_id="kg_community:toy_it:l0:alpha",
        version_id="kg_community:toy_it:l0:alpha:v1",
        adapter_name="toy_it",
        projection="default_graph_projection",
        level=0,
        parent_community_id="",
        title="Alpha",
        summary="old summary",
        member_node_ids=["kg:toy_it:node:alpha"],
        member_edge_ids=["kg_edge:toy_it:related_to:alpha"],
        evidence_ids=["kg_ev:toy_it:note:n1:abc"],
        chunk_ids=["kg_chunk:kg_ev:toy_it:note:n1:abc:0"],
        metrics={},
        lineage_id="kg_community_lineage:alpha",
    )
    finding_v1 = GraphIndexFinding(
        finding_id="kg_finding:toy_it:alpha",
        community_id=community_v1.community_id,
        adapter_name="toy_it",
        projection=community_v1.projection,
        finding_type="narrative_strengthening",
        title="Alpha finding",
        statement="old finding",
        cited_chunk_ids=community_v1.chunk_ids,
        cited_evidence_ids=community_v1.evidence_ids,
        supporting_edge_ids=community_v1.member_edge_ids,
        node_ids=community_v1.member_node_ids,
        confidence=0.8,
        version=community_v1.version_id,
    )
    community_v2 = GraphIndexCommunity(
        **{
            **community_v1.__dict__,
            "version_id": "kg_community:toy_it:l0:alpha:v2",
            "summary": "new summary",
            "previous_version_id": community_v1.version_id,
            "change_reason": "local_review",
        }
    )

    repo.replace_graph_index("toy_it", communities=[community_v1], findings=[finding_v1], deltas=[])
    repo.replace_graph_index("toy_it", communities=[community_v2], findings=[], deltas=[])

    with get_session("test") as session:
        current = session.get(KnowledgeGraphCommunity, community_v2.community_id)
        findings = session.scalars(
            select(KnowledgeGraphFinding).where(KnowledgeGraphFinding.adapter_name == "toy_it")
        ).all()

    assert current is not None
    assert current.version_id == community_v2.version_id
    assert current.summary == "new summary"
    assert findings == []

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
        session.execute(delete(KnowledgeGraphDelta).where(KnowledgeGraphDelta.adapter_name == "toy_it"))
        session.execute(delete(KnowledgeGraphFinding).where(KnowledgeGraphFinding.adapter_name == "toy_it"))
        session.execute(delete(KnowledgeGraphCommunity).where(KnowledgeGraphCommunity.adapter_name == "toy_it"))
        session.execute(
            delete(KnowledgeEdgeEvidenceChunk).where(KnowledgeEdgeEvidenceChunk.edge_id.like("kg_edge:toy_it:%"))
        )
        session.execute(
            delete(KnowledgeEdgeEvidenceChunk).where(
                KnowledgeEdgeEvidenceChunk.evidence_id.in_(
                    select(KnowledgeEvidence.evidence_id).where(KnowledgeEvidence.adapter_name == "toy_it")
                )
            )
        )

        session.execute(delete(KnowledgeEvidenceChunk).where(KnowledgeEvidenceChunk.adapter_name == "toy_it"))
        session.execute(delete(KnowledgeEdgeEvidence).where(KnowledgeEdgeEvidence.edge_id.like("kg_edge:toy_it:%")))
        session.execute(delete(KnowledgeEdge).where(KnowledgeEdge.adapter_name == "toy_it"))
        session.execute(delete(KnowledgeEvidence).where(KnowledgeEvidence.adapter_name == "toy_it"))
        session.execute(delete(KnowledgeNode).where(KnowledgeNode.adapter_name == "toy_it"))
        session.execute(delete(KnowledgeCompilationRun).where(KnowledgeCompilationRun.adapter_name == "toy_it"))
