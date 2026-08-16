"""Knowledge infrastructure ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


class KnowledgeEvidence(Base):
    """Source evidence used by Cognitive Cards and high-level graph indexes."""

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


class KnowledgeEvidenceChunk(Base):
    """Chunk manifest derived from evidence; readable text lives in Milvus."""

    __tablename__ = "kg_evidence_chunks"
    __table_args__ = (
        Index("ix_kg_evidence_chunks_adapter", "adapter_name"),
        Index("ix_kg_evidence_chunks_evidence", "evidence_id"),
        Index("ix_kg_evidence_chunks_evidence_index", "evidence_id", "chunk_index"),
    )

    chunk_id: Mapped[str] = mapped_column(String(220), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(180), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_offset: Mapped[int | None] = mapped_column(Integer)
    end_offset: Mapped[int | None] = mapped_column(Integer)
    previous_chunk_id: Mapped[str | None] = mapped_column(String(220))
    next_chunk_id: Mapped[str | None] = mapped_column(String(220))
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    chunker_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeGraphCommunity(Base):
    """Current relationship-connected Graph Community state."""

    __tablename__ = "kg_graph_communities"
    __table_args__ = (
        Index("ix_kg_graph_communities_adapter_graph_status", "adapter_name", "graph_status"),
        Index("ix_kg_graph_communities_anchor", "identity_anchor_card_id"),
        Index("ix_kg_graph_communities_fact_status", "fact_report_status"),
        Index("ix_kg_graph_communities_projection_status", "projection_status"),
        Index("ix_kg_graph_communities_graph_fingerprint", "graph_fingerprint"),
        Index(
            "ix_kg_graph_communities_member_cards_gin",
            "member_card_ids",
            postgresql_using="gin",
        ),
    )

    community_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_anchor_card_id: Mapped[str] = mapped_column(String(180), nullable=False)
    member_card_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    member_edge_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    graph_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    graph_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fact_report: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fact_referenced_card_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    fact_referenced_edge_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    fact_report_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fact_report_generator_version: Mapped[str] = mapped_column(
        String(96), nullable=False, default=""
    )
    fact_report_graph_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    fact_report_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="missing"
    )
    fact_report_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    report_task_dispatched_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    fact_semantic_synced_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    conditional_projections: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    projection_generator_version: Mapped[str] = mapped_column(
        String(96), nullable=False, default=""
    )
    projection_graph_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    projection_fact_report_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    projection_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="missing"
    )
    projection_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    projection_task_dispatched_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    projection_semantic_synced_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    graph_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    fact_report_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projection_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeGraphCommunityRelation(Base):
    """Current relation projection between flat Graph Communities."""

    __tablename__ = "kg_graph_community_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_community_id",
            "target_community_id",
            "relation_kind",
            name="uq_kg_graph_community_relations_pair_kind",
        ),
        CheckConstraint(
            "source_community_id <> target_community_id",
            name="ck_kg_graph_community_relations_distinct_endpoints",
        ),
        CheckConstraint(
            "status IN ('active')",
            name="ck_kg_graph_community_relations_status",
        ),
        Index(
            "ix_kg_graph_community_relations_source_status",
            "source_community_id",
            "status",
        ),
        Index(
            "ix_kg_graph_community_relations_target_status",
            "target_community_id",
            "status",
        ),
        Index(
            "ix_kg_graph_community_relations_kind_status",
            "relation_kind",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    source_community_id: Mapped[str] = mapped_column(String(180), nullable=False)
    target_community_id: Mapped[str] = mapped_column(String(180), nullable=False)
    relation_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    supporting_edge_ids: Mapped[list] = mapped_column(
        JSONB,
        default=list,
        nullable=False,
    )
    observed_edge_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    inferred_edge_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    relation_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class KnowledgeGraphCommunityMembership(Base):
    """Normalized current Card-to-Graph-Community membership projection."""

    __tablename__ = "kg_graph_community_memberships"
    __table_args__ = (
        Index("ix_kg_graph_community_memberships_community", "community_id"),
        Index(
            "ix_kg_graph_community_memberships_adapter_community",
            "adapter_name",
            "community_id",
        ),
    )

    adapter_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    card_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    community_id: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeCommunityInsight(Base):
    """Current high-level cognitive report for a graph community."""

    __tablename__ = "kg_community_insights"
    __table_args__ = (
        UniqueConstraint("community_id", name="uq_kg_community_insights_community"),
        Index("ix_kg_community_insights_adapter_status", "adapter_name", "status"),
        Index("ix_kg_community_insights_community", "community_id"),
        Index("ix_kg_community_insights_updated", "updated_at"),
    )

    insight_id: Mapped[str] = mapped_column(String(220), primary_key=True)
    community_id: Mapped[str] = mapped_column(String(180), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    projection: Mapped[str] = mapped_column(String(64), nullable=False)
    insight_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    insight_full_report: Mapped[str] = mapped_column(Text, nullable=False, default="")
    report_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cognitive_card_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assignment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    chunk_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    cognitive_card_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeCognitiveCard(Base):
    """原子 Cognitive Card 的 PG manifest；可读正文保存在 Milvus。"""

    __tablename__ = "kg_cognitive_cards"
    __table_args__ = (
        Index("ix_kg_cognitive_cards_adapter_status", "adapter_name", "status"),
        Index("ix_kg_cognitive_cards_evidence", "evidence_id"),
        Index("ix_kg_cognitive_cards_chunk", "primary_chunk_id"),
        Index("ix_kg_cognitive_cards_source", "adapter_name", "source_type", "source_id"),
        Index("ix_kg_cognitive_cards_fact", "adapter_name", "fact_id", "status"),
    )

    cognitive_card_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_id: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    evidence_id: Mapped[str] = mapped_column(String(180), nullable=False)
    primary_chunk_id: Mapped[str] = mapped_column(String(220), nullable=False)
    chunk_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    focus_evidence_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    focus_span_offsets: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    relation_probes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    fact_id: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    schema_version: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    generator_version: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeCardRelation(Base):
    """原子 Cognitive Card 之间经过原文核验的正式 Edge 当前态。"""

    __tablename__ = "kg_card_relations"
    __table_args__ = (
        Index("ix_kg_card_relations_pair_status", "pair_key", "status"),
        Index("ix_kg_card_relations_source_status", "source_card_id", "status"),
        Index("ix_kg_card_relations_target_status", "target_card_id", "status"),
        Index("ix_kg_card_relations_kind_status", "relation_kind", "status"),
    )

    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    pair_key: Mapped[str] = mapped_column(String(380), nullable=False)
    source_card_id: Mapped[str] = mapped_column(String(180), nullable=False)
    target_card_id: Mapped[str] = mapped_column(String(180), nullable=False)
    relation_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(160), nullable=False)
    direction: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    decision_class: Mapped[str] = mapped_column(String(16), nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    source_evidence_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    target_evidence_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    relation_evidence_refs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    inference_mechanism: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pipeline_version: Mapped[str] = mapped_column(String(96), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(96), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(96), nullable=False)
    content_version: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_synced_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    graph_event_published_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeCommunityAssignment(Base):
    """LLM community attachment/create decision for a Cognitive Card intent."""

    __tablename__ = "kg_community_assignments"
    __table_args__ = (
        Index("ix_kg_community_assignments_adapter_status", "adapter_name", "status"),
        Index("ix_kg_community_assignments_card", "cognitive_card_id"),
        Index("ix_kg_community_assignments_community", "community_id"),
    )

    assignment_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    cognitive_card_id: Mapped[str] = mapped_column(String(180), nullable=False)
    intent_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    intent_id: Mapped[str] = mapped_column(String(220), nullable=False, default="")
    community_id: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    matched_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    update_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    topic_intent: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    decision: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeAssignmentCandidateOrder(Base):
    """Stable candidate order memory for Community Assignment prompts."""

    __tablename__ = "kg_assignment_candidate_orders"
    __table_args__ = (
        UniqueConstraint(
            "adapter_name",
            "target",
            "query_key",
            name="uq_kg_assignment_candidate_orders_scope",
        ),
        Index("ix_kg_assignment_candidate_orders_scope", "adapter_name", "target"),
    )

    order_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    query_key: Mapped[str] = mapped_column(String(96), nullable=False)
    ordered_community_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeGraphFinding(Base):
    """Graph Index finding/narrative state with chunk refs."""

    __tablename__ = "kg_graph_findings"
    __table_args__ = (
        Index("ix_kg_graph_findings_adapter_projection", "adapter_name", "projection"),
        Index("ix_kg_graph_findings_community", "community_id"),
        Index("ix_kg_graph_findings_status", "status"),
    )

    finding_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    community_id: Mapped[str] = mapped_column(String(180), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    projection: Mapped[str] = mapped_column(String(64), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    cited_chunk_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    cited_evidence_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    supporting_edge_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    node_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    version: Mapped[str] = mapped_column(String(220), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeGraphDelta(Base):
    """Rolling Graph Index delta window state."""

    __tablename__ = "kg_graph_deltas"
    __table_args__ = (
        Index("ix_kg_graph_deltas_adapter_projection", "adapter_name", "projection"),
        Index("ix_kg_graph_deltas_window", "window_name"),
        Index("ix_kg_graph_deltas_status", "status"),
    )

    delta_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    projection: Mapped[str] = mapped_column(String(64), nullable=False)
    window_name: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    community_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    finding_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    cited_chunk_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    cited_evidence_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    supporting_edge_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    node_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    version: Mapped[str] = mapped_column(String(220), nullable=False, default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeGraphUnassignedSignal(Base):
    """Graph Index weak signal state waiting for future community promotion."""

    __tablename__ = "kg_graph_unassigned_signals"
    __table_args__ = (
        Index("ix_kg_graph_unassigned_signals_adapter_status", "adapter_name", "status"),
        Index("ix_kg_graph_unassigned_signals_projection", "projection"),
        Index("ix_kg_graph_unassigned_signals_promoted", "promoted_community_id"),
    )

    signal_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    projection: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    node_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    edge_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    chunk_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    topic_tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    impact_tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    event_type_tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    relation_types: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    support_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    promoted_community_id: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    promotion_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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
