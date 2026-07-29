"""当前关系优先知识持久化模型测试。"""

from __future__ import annotations

from src.infrastructure.persistence.models import Base
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCardRelation,
    KnowledgeCognitiveCard,
    KnowledgeCompilationRun,
    KnowledgeEvidence,
    KnowledgeEvidenceChunk,
    KnowledgeNormalizationRule,
    KnowledgeReviewItem,
    KnowledgeVersion,
)


def test_current_knowledge_tables_registered_on_base_metadata() -> None:
    expected = {
        "kg_evidence",
        "kg_evidence_chunks",
        "kg_cognitive_cards",
        "kg_card_relations",
        "kg_versions",
        "kg_review_items",
        "kg_normalization_rules",
        "kg_compilation_runs",
    }

    assert expected.issubset(set(Base.metadata.tables))


def test_card_relation_has_verified_edge_current_state_columns() -> None:
    columns = set(KnowledgeCardRelation.__table__.columns.keys())

    assert {
        "id",
        "pair_key",
        "source_card_id",
        "target_card_id",
        "relation_kind",
        "relation_type",
        "direction",
        "decision_class",
        "basis",
        "source_evidence_refs",
        "target_evidence_refs",
        "relation_evidence_refs",
        "inference_mechanism",
        "confidence",
        "content_version",
        "semantic_synced_version",
        "graph_event_published_version",
        "status",
        "invalidated_at",
    }.issubset(columns)
    assert "adapter_name" not in columns


def test_evidence_card_and_chunk_manifests_do_not_restore_legacy_fact_graph() -> None:
    assert {"evidence_id", "source_type", "source_id", "status"}.issubset(
        set(KnowledgeEvidence.__table__.columns.keys())
    )
    assert {"chunk_id", "evidence_id", "chunk_index", "start_offset", "end_offset"}.issubset(
        set(KnowledgeEvidenceChunk.__table__.columns.keys())
    )
    assert {
        "cognitive_card_id",
        "fact_id",
        "focus_evidence_refs",
        "status",
    }.issubset(
        set(KnowledgeCognitiveCard.__table__.columns.keys())
    )
    assert "relation_probes" in KnowledgeCognitiveCard.__table__.columns
    assert not {"kg_nodes", "kg_edges", "kg_graph_adjacency"}.intersection(
        set(Base.metadata.tables)
    )


def test_knowledge_audit_tables_exist() -> None:
    assert "version_id" in KnowledgeVersion.__table__.columns
    assert "review_id" in KnowledgeReviewItem.__table__.columns
    assert "rule_id" in KnowledgeNormalizationRule.__table__.columns
    assert "run_id" in KnowledgeCompilationRun.__table__.columns
