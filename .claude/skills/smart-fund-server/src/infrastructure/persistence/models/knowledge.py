"""Knowledge infrastructure ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, Sequence, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


GRAPH_COMMUNITY_ID_SEQUENCE = Sequence("kg_graph_community_id_seq", metadata=Base.metadata)


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
    source_node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_node_id: Mapped[str] = mapped_column(String(128), nullable=False)
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

    edge_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeEdgeEvidenceChunk(Base):
    """Chunk-level refs for edge evidence links."""

    __tablename__ = "kg_edge_evidence_chunks"
    __table_args__ = (
        Index("ix_kg_edge_evidence_chunks_evidence", "evidence_id"),
        Index("ix_kg_edge_evidence_chunks_chunk", "chunk_id"),
    )

    edge_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(220), primary_key=True)
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
    """Graph Index community identity/version state."""

    __tablename__ = "kg_graph_communities"
    __table_args__ = (
        Index("ix_kg_graph_communities_id", "id"),
        Index("ix_kg_graph_communities_adapter_projection", "adapter_name", "projection"),
        Index("ix_kg_graph_communities_parent", "parent_community_id"),
        Index("ix_kg_graph_communities_status", "status"),
    )

    community_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    id: Mapped[int | None] = mapped_column(BigInteger, server_default=GRAPH_COMMUNITY_ID_SEQUENCE.next_value())
    version_id: Mapped[str] = mapped_column(String(220), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    projection: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_community_id: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    member_node_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    member_edge_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    chunk_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    previous_version_id: Mapped[str] = mapped_column(String(220), nullable=False, default="")
    change_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="build")
    lineage_id: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    previous_community_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeCognitiveCard(Base):
    """Chunk-level cognitive signals used by high-level semantic indexes."""

    __tablename__ = "kg_cognitive_cards"
    __table_args__ = (
        Index("ix_kg_cognitive_cards_adapter_status", "adapter_name", "status"),
        Index("ix_kg_cognitive_cards_evidence", "evidence_id"),
        Index("ix_kg_cognitive_cards_chunk", "primary_chunk_id"),
        Index("ix_kg_cognitive_cards_source", "adapter_name", "source_type", "source_id"),
    )

    cognitive_card_id: Mapped[str] = mapped_column(String(180), primary_key=True)
    adapter_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_id: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    evidence_id: Mapped[str] = mapped_column(String(180), nullable=False)
    primary_chunk_id: Mapped[str] = mapped_column(String(220), nullable=False)
    chunk_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    title_candidates: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    topic_intents: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    risk_signals: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    local_impact_signals: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    actor_signals: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    supporting_text: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    system_pointers: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
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
    action: Mapped[str] = mapped_column(String(32), nullable=False, default="")
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
