"""Knowledge infrastructure ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


class KnowledgeNode(Base):
    """Generic knowledge node persisted as a versioned fact."""

    __tablename__ = "kg_nodes"
    __table_args__ = (
        UniqueConstraint(
            "adapter_name",
            "node_type",
            "stable_key",
            name="uq_kg_nodes_adapter_type_key",
        ),
        Index("ix_kg_nodes_adapter_type", "adapter_name", "node_type"),
        Index("ix_kg_nodes_status", "status"),
    )

    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    stable_key: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    external_ids: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeEdge(Base):
    """Generic directed relationship persisted as a versioned fact."""

    __tablename__ = "kg_edges"
    __table_args__ = (
        Index("ix_kg_edges_source", "source_node_id"),
        Index("ix_kg_edges_target", "target_node_id"),
        Index("ix_kg_edges_relation", "relation_type"),
        Index("ix_kg_edges_status", "status"),
    )

    edge_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_node_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("kg_nodes.node_id"), nullable=False
    )
    target_node_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("kg_nodes.node_id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    confidence_label: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeEvidence(Base):
    """Evidence backing nodes or edges."""

    __tablename__ = "kg_evidence"
    __table_args__ = (
        Index("ix_kg_evidence_source", "source_type", "source_id"),
        Index("ix_kg_evidence_adapter", "adapter_name"),
        Index("ix_kg_evidence_status", "status"),
        Index("ix_kg_evidence_source_status", "adapter_name", "source_type", "source_id", "status"),
    )

    evidence_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    source_fingerprint: Mapped[str | None] = mapped_column(String(128))
    superseded_by: Mapped[str | None] = mapped_column(String(180))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeEdgeEvidence(Base):
    """Many-to-many link between edges and evidence."""

    __tablename__ = "kg_edge_evidence"

    edge_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("kg_edges.edge_id"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(
        String(180), ForeignKey("kg_evidence.evidence_id"), primary_key=True
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeVersion(Base):
    """Version metadata for schema, adapters, compiler rules, and prompts."""

    __tablename__ = "kg_versions"

    version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    compiler_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    rules_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeReviewItem(Base):
    """Review item for high-risk or conflicting knowledge objects."""

    __tablename__ = "kg_review_items"
    __table_args__ = (
        Index("ix_kg_review_items_status", "status"),
        Index("ix_kg_review_items_object", "object_type", "object_id"),
    )

    review_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str] = mapped_column(String(180), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeNormalizationRule(Base):
    """Versioned normalization rule maintained in the database."""

    __tablename__ = "kg_normalization_rules"
    __table_args__ = (
        UniqueConstraint(
            "adapter_name",
            "rule_type",
            "raw_value",
            "status",
            name="uq_kg_normalization_rule_active_key",
        ),
        Index("ix_kg_normalization_rules_adapter_type", "adapter_name", "rule_type"),
        Index("ix_kg_normalization_rules_status", "status"),
    )

    rule_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeCompilationRun(Base):
    """Audit record for one knowledge compile execution."""

    __tablename__ = "kg_compilation_runs"
    __table_args__ = (
        Index("ix_kg_compilation_runs_adapter", "adapter_name", "adapter_version"),
        Index("ix_kg_compilation_runs_status", "status"),
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source_batch_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class KnowledgeWikiPage(Base):
    """Generated page derived from fact store records."""

    __tablename__ = "kg_wiki_pages"
    __table_args__ = (
        Index("ix_kg_wiki_pages_adapter_type", "adapter_name", "page_type"),
        Index("ix_kg_wiki_pages_subject", "subject_type", "subject_id"),
    )

    page_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    page_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(64))
    subject_id: Mapped[str | None] = mapped_column(String(180))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_node_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_edge_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_evidence_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeGraphAdjacency(Base):
    """Generated graph adjacency row derived from directed edges."""

    __tablename__ = "kg_graph_adjacency"
    __table_args__ = (
        Index("ix_kg_graph_adjacency_adapter_source", "adapter_name", "source_node_id"),
        Index("ix_kg_graph_adjacency_target", "target_node_id"),
    )

    adapter_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    target_node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    edge_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeEvidenceChunk(Base):
    """Generated readable chunk derived from evidence."""

    __tablename__ = "kg_evidence_chunks"
    __table_args__ = (
        Index("ix_kg_evidence_chunks_adapter", "adapter_name"),
        Index("ix_kg_evidence_chunks_evidence", "evidence_id"),
    )

    chunk_id: Mapped[str] = mapped_column(String(220), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_id: Mapped[str] = mapped_column(
        String(180), ForeignKey("kg_evidence.evidence_id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeRetrievalDocument(Base):
    """Generated retrieval document derived from fact store records."""

    __tablename__ = "kg_retrieval_documents"
    __table_args__ = (
        Index("ix_kg_retrieval_documents_adapter_target", "adapter_name", "target"),
        Index("ix_kg_retrieval_documents_source", "source_fact_type", "source_fact_id"),
        Index("ix_kg_retrieval_documents_answer_type", "answer_candidate_type"),
    )

    document_id: Mapped[str] = mapped_column(String(260), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(16), nullable=False, default="prod")
    source_fact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_fact_id: Mapped[str] = mapped_column(String(220), nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    node_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    edge_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    key_phrases: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(96))
    relation_intents: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    impact_direction: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    asset_classes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    time_tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    source_type_tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    readable_relations: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answer_candidate_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    generated_by: Mapped[str] = mapped_column(String(16), nullable=False, default="rule")
    generation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeRetrievalDocumentVersion(Base):
    """Generation metadata for retrieval document batches."""

    __tablename__ = "kg_retrieval_document_versions"
    __table_args__ = (
        Index("ix_kg_retrieval_document_versions_adapter", "adapter_name", "target"),
    )

    version_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(16), nullable=False, default="prod")
    generation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_fact_set: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    field_coverage: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeRetrievalTraceSnapshot(Base):
    """Replayable retrieval trace snapshot for quality evaluation."""

    __tablename__ = "kg_retrieval_trace_snapshots"
    __table_args__ = (
        Index("ix_kg_retrieval_trace_snapshots_adapter", "adapter_name", "target"),
        Index("ix_kg_retrieval_trace_snapshots_query_hash", "query_hash"),
        Index("ix_kg_retrieval_trace_snapshots_strategy", "strategy_name", "strategy_version"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(16), nullable=False, default="prod")
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    query_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    recall_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    package_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    ranking_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    judge_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    context_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    stop_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeRetrievalLabel(Base):
    """Human labels for a retrieval trace or bad case."""

    __tablename__ = "kg_retrieval_labels"
    __table_args__ = (
        Index("ix_kg_retrieval_labels_snapshot", "snapshot_id"),
        Index("ix_kg_retrieval_labels_case", "case_id"),
    )

    label_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(128))
    case_id: Mapped[str | None] = mapped_column(String(128))
    query: Mapped[str] = mapped_column(Text, nullable=False)
    expected_candidates: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    expected_answers: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    expected_evidence_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    coverage_requirements: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    failure_stage: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeRetrievalEvalRun(Base):
    """One strategy replay/evaluation run."""

    __tablename__ = "kg_retrieval_eval_runs"
    __table_args__ = (
        Index("ix_kg_retrieval_eval_runs_strategy", "strategy_name", "strategy_version"),
        Index("ix_kg_retrieval_eval_runs_status", "status"),
    )

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    aggregate_metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeRetrievalEvalMetric(Base):
    """Case-level metrics for one retrieval evaluation run."""

    __tablename__ = "kg_retrieval_eval_metrics"
    __table_args__ = (
        Index("ix_kg_retrieval_eval_metrics_run", "run_id"),
        Index("ix_kg_retrieval_eval_metrics_case", "case_id"),
        UniqueConstraint("run_id", "case_id", name="uq_kg_retrieval_eval_metrics_run_case"),
    )

    metric_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    failure_stage: Mapped[str | None] = mapped_column(String(64))
    failure_details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
