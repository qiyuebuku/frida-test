"""Unit tests for generic knowledge persistence models."""

from __future__ import annotations

from src.infrastructure.persistence.models import Base
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCompilationRun,
    KnowledgeEdge,
    KnowledgeEdgeEvidence,
    KnowledgeEvidenceChunk,
    KnowledgeEvidence,
    KnowledgeGraphAdjacency,
    KnowledgeNode,
    KnowledgeNormalizationRule,
    KnowledgeReviewItem,
    KnowledgeVersion,
    KnowledgeWikiPage,
)


def test_knowledge_tables_registered_on_base_metadata() -> None:
    expected = {
        "kg_nodes",
        "kg_edges",
        "kg_evidence",
        "kg_edge_evidence",
        "kg_versions",
        "kg_review_items",
        "kg_normalization_rules",
        "kg_compilation_runs",
        "kg_wiki_pages",
        "kg_graph_adjacency",
        "kg_evidence_chunks",
    }

    assert expected.issubset(set(Base.metadata.tables))


def test_knowledge_node_has_generic_identity_columns() -> None:
    columns = set(KnowledgeNode.__table__.columns.keys())

    assert {
        "node_id",
        "adapter_name",
        "adapter_version",
        "node_type",
        "stable_key",
        "canonical_name",
        "aliases",
        "external_ids",
        "properties",
        "status",
        "version",
    }.issubset(columns)


def test_knowledge_edge_has_generic_relation_columns() -> None:
    columns = set(KnowledgeEdge.__table__.columns.keys())

    assert {
        "edge_id",
        "adapter_name",
        "adapter_version",
        "source_node_id",
        "target_node_id",
        "relation_type",
        "properties",
        "confidence_label",
        "confidence_score",
        "status",
        "valid_from",
        "valid_to",
        "version",
    }.issubset(columns)


def test_knowledge_evidence_and_link_tables_exist() -> None:
    evidence_columns = set(KnowledgeEvidence.__table__.columns.keys())
    link_columns = set(KnowledgeEdgeEvidence.__table__.columns.keys())

    assert {
        "evidence_id",
        "source_type",
        "source_id",
        "evidence_type",
        "payload",
        "status",
        "source_fingerprint",
        "superseded_by",
    }.issubset(evidence_columns)
    assert {"edge_id", "evidence_id"}.issubset(link_columns)


def test_knowledge_audit_tables_exist() -> None:
    assert "version_id" in KnowledgeVersion.__table__.columns
    assert "review_id" in KnowledgeReviewItem.__table__.columns
    assert "rule_id" in KnowledgeNormalizationRule.__table__.columns
    assert "run_id" in KnowledgeCompilationRun.__table__.columns


def test_knowledge_generated_tables_exist() -> None:
    assert {"page_id", "content", "source_evidence_ids"}.issubset(
        set(KnowledgeWikiPage.__table__.columns.keys())
    )
    assert {"source_node_id", "target_node_id", "edge_id"}.issubset(
        set(KnowledgeGraphAdjacency.__table__.columns.keys())
    )
    assert {"chunk_id", "evidence_id", "content"}.issubset(
        set(KnowledgeEvidenceChunk.__table__.columns.keys())
    )
