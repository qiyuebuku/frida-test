-- Knowledge infrastructure store.
-- Current KG architecture stores source evidence/chunk manifests in PG,
-- readable/searchable text in Milvus, and high-level indexes in Cognitive
-- Cards, Community Assignments, and Graph Communities.

CREATE TABLE IF NOT EXISTS public.kg_evidence (
    evidence_id character varying(180) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    source_type character varying(64) NOT NULL,
    source_id character varying(256) NOT NULL,
    evidence_type character varying(32) NOT NULL,
    content text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    span_start integer,
    span_end integer,
    version character varying(64) NOT NULL,
    status character varying(32) NOT NULL DEFAULT 'active',
    source_fingerprint character varying(128),
    superseded_by character varying(180),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_versions (
    version_id character varying(128) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    adapter_version character varying(32) NOT NULL DEFAULT '',
    schema_version character varying(64) NOT NULL DEFAULT '',
    compiler_version character varying(64) NOT NULL DEFAULT '',
    rules_hash character varying(64) NOT NULL DEFAULT '',
    prompt_hash character varying(64) NOT NULL DEFAULT '',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_review_items (
    review_id character varying(128) PRIMARY KEY,
    object_type character varying(32) NOT NULL,
    object_id character varying(180) NOT NULL,
    severity character varying(16) NOT NULL,
    reason text NOT NULL,
    status character varying(32) NOT NULL DEFAULT 'open',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_normalization_rules (
    rule_id character varying(160) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    rule_type character varying(64) NOT NULL,
    raw_value text NOT NULL,
    canonical_value text NOT NULL DEFAULT '',
    status character varying(32) NOT NULL DEFAULT 'candidate',
    confidence double precision NOT NULL DEFAULT 0.0,
    source character varying(64) NOT NULL DEFAULT '',
    version character varying(64) NOT NULL DEFAULT 'v1',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT uq_kg_normalization_rule_active_key UNIQUE (adapter_name, rule_type, raw_value, status)
);

CREATE TABLE IF NOT EXISTS public.kg_compilation_runs (
    run_id character varying(128) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    adapter_version character varying(32) NOT NULL DEFAULT '',
    source_batch_id character varying(128) NOT NULL DEFAULT '',
    status character varying(32) NOT NULL,
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone,
    input_count integer NOT NULL DEFAULT 0,
    node_count integer NOT NULL DEFAULT 0,
    edge_count integer NOT NULL DEFAULT 0,
    evidence_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS public.kg_evidence_chunks (
    chunk_id character varying(220) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    evidence_id character varying(180) NOT NULL,
    chunk_index integer NOT NULL DEFAULT 0,
    start_offset integer,
    end_offset integer,
    previous_chunk_id character varying(220),
    next_chunk_id character varying(220),
    text_hash character varying(64) NOT NULL DEFAULT '',
    chunker_version character varying(64) NOT NULL DEFAULT '',
    created_at timestamp with time zone DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS public.kg_graph_community_id_seq;

CREATE TABLE IF NOT EXISTS public.kg_graph_communities (
    community_id character varying(180) PRIMARY KEY,
    id bigint DEFAULT nextval('public.kg_graph_community_id_seq'::regclass),
    version_id character varying(220) NOT NULL,
    adapter_name character varying(64) NOT NULL,
    projection character varying(64) NOT NULL,
    level integer NOT NULL DEFAULT 0,
    parent_community_id character varying(180) NOT NULL DEFAULT '',
    title text NOT NULL,
    summary text NOT NULL DEFAULT '',
    member_node_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    member_edge_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    status character varying(32) NOT NULL DEFAULT 'active',
    previous_version_id character varying(220) NOT NULL DEFAULT '',
    change_reason character varying(64) NOT NULL DEFAULT 'build',
    lineage_id character varying(180) NOT NULL DEFAULT '',
    previous_community_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    last_insight_generated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

ALTER TABLE IF EXISTS public.kg_graph_communities
    ADD COLUMN IF NOT EXISTS id bigint;
ALTER TABLE IF EXISTS public.kg_graph_communities
    ALTER COLUMN id SET DEFAULT nextval('public.kg_graph_community_id_seq'::regclass);
ALTER TABLE IF EXISTS public.kg_graph_communities
    ADD COLUMN IF NOT EXISTS last_insight_generated_at timestamp with time zone;

CREATE TABLE IF NOT EXISTS public.kg_community_insights (
    insight_id character varying(220) PRIMARY KEY,
    community_id character varying(180) NOT NULL,
    adapter_name character varying(64) NOT NULL,
    projection character varying(64) NOT NULL,
    insight_version integer NOT NULL DEFAULT 1,
    title text NOT NULL DEFAULT '',
    insight_full_report text NOT NULL DEFAULT '',
    report_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_count integer NOT NULL DEFAULT 0,
    cognitive_card_count integer NOT NULL DEFAULT 0,
    assignment_count integer NOT NULL DEFAULT 0,
    evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    cognitive_card_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    status character varying(32) NOT NULL DEFAULT 'active',
    error_message text NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT uq_kg_community_insights_community UNIQUE (community_id)
);

CREATE TABLE IF NOT EXISTS public.kg_cognitive_cards (
    cognitive_card_id character varying(180) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    source_type character varying(64) NOT NULL DEFAULT '',
    source_id character varying(256) NOT NULL DEFAULT '',
    evidence_id character varying(180) NOT NULL,
    primary_chunk_id character varying(220) NOT NULL,
    chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    chunk_index integer NOT NULL DEFAULT 0,
    focus_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    focus_span_offsets jsonb NOT NULL DEFAULT '[]'::jsonb,
    relation_probes jsonb NOT NULL DEFAULT '[]'::jsonb,
    schema_version character varying(96) NOT NULL DEFAULT '',
    generator_version character varying(96) NOT NULL DEFAULT '',
    status character varying(32) NOT NULL DEFAULT 'active',
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

COMMENT ON TABLE public.kg_cognitive_cards IS '原子 Cognitive Card manifest；证据引用保存在 PG，完整可读文本保存在 Milvus';
COMMENT ON COLUMN public.kg_cognitive_cards.focus_evidence_refs IS 'Card 焦点原文 Span Ref 列表';
COMMENT ON COLUMN public.kg_cognitive_cards.focus_span_offsets IS '焦点 Span 在 primary chunk 中的 offset 指针';
COMMENT ON COLUMN public.kg_cognitive_cards.relation_probes IS '跨 Chunk 关系发现使用的候选事件搜索方向，不代表正式关系';

CREATE TABLE IF NOT EXISTS public.kg_community_assignments (
    assignment_id character varying(180) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    cognitive_card_id character varying(180) NOT NULL,
    intent_index integer NOT NULL DEFAULT 0,
    intent_id character varying(220) NOT NULL DEFAULT '',
    community_id character varying(180) NOT NULL DEFAULT '',
    action character varying(64) NOT NULL DEFAULT '',
    weight double precision NOT NULL DEFAULT 0.0,
    confidence double precision NOT NULL DEFAULT 0.0,
    matched_reason text NOT NULL DEFAULT '',
    update_mode character varying(32) NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    topic_intent jsonb NOT NULL DEFAULT '{}'::jsonb,
    decision jsonb NOT NULL DEFAULT '{}'::jsonb,
    status character varying(32) NOT NULL DEFAULT 'active',
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_graph_findings (
    finding_id character varying(180) PRIMARY KEY,
    community_id character varying(180) NOT NULL,
    adapter_name character varying(64) NOT NULL,
    projection character varying(64) NOT NULL,
    finding_type character varying(64) NOT NULL,
    title text NOT NULL,
    statement text NOT NULL,
    cited_chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    cited_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    supporting_edge_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    node_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    confidence double precision NOT NULL DEFAULT 0.0,
    status character varying(32) NOT NULL DEFAULT 'active',
    version character varying(220) NOT NULL DEFAULT '',
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_graph_deltas (
    delta_id character varying(180) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    projection character varying(64) NOT NULL,
    window_name character varying(32) NOT NULL,
    started_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone NOT NULL,
    title text NOT NULL,
    summary text NOT NULL DEFAULT '',
    community_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    finding_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    cited_chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    cited_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    supporting_edge_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    node_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    status character varying(32) NOT NULL DEFAULT 'active',
    version character varying(220) NOT NULL DEFAULT '',
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_graph_unassigned_signals (
    signal_id character varying(180) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    projection character varying(64) NOT NULL,
    title text NOT NULL DEFAULT '',
    reason character varying(96) NOT NULL DEFAULT '',
    node_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    edge_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    topic_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    impact_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    event_type_tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    relation_types jsonb NOT NULL DEFAULT '[]'::jsonb,
    support_score double precision NOT NULL DEFAULT 0.0,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    status character varying(32) NOT NULL DEFAULT 'active',
    promoted_community_id character varying(180) NOT NULL DEFAULT '',
    promotion_attempts integer NOT NULL DEFAULT 0,
    last_checked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_retrieval_trace_snapshots (
    snapshot_id character varying(128) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    target character varying(16) NOT NULL DEFAULT 'prod',
    query text NOT NULL,
    query_hash character varying(64) NOT NULL,
    strategy_name character varying(64) NOT NULL,
    strategy_version character varying(64) NOT NULL,
    query_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    recall_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    package_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    ranking_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    judge_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    context_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    stop_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_retrieval_labels (
    label_id character varying(128) PRIMARY KEY,
    snapshot_id character varying(128),
    case_id character varying(128),
    query text NOT NULL,
    expected_candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
    expected_answers jsonb NOT NULL DEFAULT '[]'::jsonb,
    expected_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    coverage_requirements jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure_stage character varying(64),
    notes text NOT NULL DEFAULT '',
    created_by character varying(128) NOT NULL DEFAULT '',
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_retrieval_eval_runs (
    run_id character varying(128) PRIMARY KEY,
    strategy_name character varying(64) NOT NULL,
    strategy_version character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    aggregate_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamp with time zone DEFAULT now(),
    finished_at timestamp with time zone
);

CREATE TABLE IF NOT EXISTS public.kg_retrieval_eval_metrics (
    metric_id character varying(128) PRIMARY KEY,
    run_id character varying(128) NOT NULL,
    case_id character varying(128) NOT NULL,
    query text NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure_stage character varying(64),
    failure_details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT uq_kg_retrieval_eval_metrics_run_case UNIQUE (run_id, case_id)
);

CREATE INDEX IF NOT EXISTS ix_kg_evidence_source ON public.kg_evidence(source_type, source_id);
CREATE INDEX IF NOT EXISTS ix_kg_evidence_adapter ON public.kg_evidence(adapter_name);
CREATE INDEX IF NOT EXISTS ix_kg_evidence_status ON public.kg_evidence(status);
CREATE INDEX IF NOT EXISTS ix_kg_evidence_source_status
    ON public.kg_evidence(adapter_name, source_type, source_id, status);

CREATE INDEX IF NOT EXISTS ix_kg_review_items_status ON public.kg_review_items(status);
CREATE INDEX IF NOT EXISTS ix_kg_review_items_object ON public.kg_review_items(object_type, object_id);

CREATE INDEX IF NOT EXISTS ix_kg_normalization_rules_adapter_type
    ON public.kg_normalization_rules(adapter_name, rule_type);
CREATE INDEX IF NOT EXISTS ix_kg_normalization_rules_status
    ON public.kg_normalization_rules(status);

CREATE INDEX IF NOT EXISTS ix_kg_compilation_runs_adapter
    ON public.kg_compilation_runs(adapter_name, adapter_version);
CREATE INDEX IF NOT EXISTS ix_kg_compilation_runs_status ON public.kg_compilation_runs(status);

CREATE INDEX IF NOT EXISTS ix_kg_evidence_chunks_adapter
    ON public.kg_evidence_chunks(adapter_name);
CREATE INDEX IF NOT EXISTS ix_kg_evidence_chunks_evidence
    ON public.kg_evidence_chunks(evidence_id);
CREATE INDEX IF NOT EXISTS ix_kg_evidence_chunks_evidence_index
    ON public.kg_evidence_chunks(evidence_id, chunk_index);

CREATE INDEX IF NOT EXISTS ix_kg_graph_communities_adapter_projection
    ON public.kg_graph_communities(adapter_name, projection);
CREATE INDEX IF NOT EXISTS ix_kg_graph_communities_id
    ON public.kg_graph_communities(id);
CREATE INDEX IF NOT EXISTS ix_kg_graph_communities_parent
    ON public.kg_graph_communities(parent_community_id);
CREATE INDEX IF NOT EXISTS ix_kg_graph_communities_status
    ON public.kg_graph_communities(status);
CREATE INDEX IF NOT EXISTS ix_kg_community_insights_adapter_status
    ON public.kg_community_insights(adapter_name, status);
CREATE INDEX IF NOT EXISTS ix_kg_community_insights_community
    ON public.kg_community_insights(community_id);
CREATE INDEX IF NOT EXISTS ix_kg_community_insights_updated
    ON public.kg_community_insights(updated_at);

CREATE INDEX IF NOT EXISTS ix_kg_cognitive_cards_adapter_status
    ON public.kg_cognitive_cards(adapter_name, status);
CREATE INDEX IF NOT EXISTS ix_kg_cognitive_cards_evidence
    ON public.kg_cognitive_cards(evidence_id);
CREATE INDEX IF NOT EXISTS ix_kg_cognitive_cards_chunk
    ON public.kg_cognitive_cards(primary_chunk_id);
CREATE INDEX IF NOT EXISTS ix_kg_cognitive_cards_source
    ON public.kg_cognitive_cards(adapter_name, source_type, source_id);

CREATE INDEX IF NOT EXISTS ix_kg_community_assignments_adapter_status
    ON public.kg_community_assignments(adapter_name, status);
CREATE INDEX IF NOT EXISTS ix_kg_community_assignments_card
    ON public.kg_community_assignments(cognitive_card_id);
CREATE INDEX IF NOT EXISTS ix_kg_community_assignments_community
    ON public.kg_community_assignments(community_id);

CREATE INDEX IF NOT EXISTS ix_kg_graph_findings_adapter_projection
    ON public.kg_graph_findings(adapter_name, projection);
CREATE INDEX IF NOT EXISTS ix_kg_graph_findings_community
    ON public.kg_graph_findings(community_id);
CREATE INDEX IF NOT EXISTS ix_kg_graph_findings_status
    ON public.kg_graph_findings(status);

CREATE INDEX IF NOT EXISTS ix_kg_graph_deltas_adapter_projection
    ON public.kg_graph_deltas(adapter_name, projection);
CREATE INDEX IF NOT EXISTS ix_kg_graph_deltas_window
    ON public.kg_graph_deltas(window_name);
CREATE INDEX IF NOT EXISTS ix_kg_graph_deltas_status
    ON public.kg_graph_deltas(status);

CREATE INDEX IF NOT EXISTS ix_kg_graph_unassigned_signals_adapter_status
    ON public.kg_graph_unassigned_signals(adapter_name, status);
CREATE INDEX IF NOT EXISTS ix_kg_graph_unassigned_signals_projection
    ON public.kg_graph_unassigned_signals(projection);
CREATE INDEX IF NOT EXISTS ix_kg_graph_unassigned_signals_promoted
    ON public.kg_graph_unassigned_signals(promoted_community_id);

CREATE INDEX IF NOT EXISTS ix_kg_retrieval_trace_snapshots_adapter
    ON public.kg_retrieval_trace_snapshots(adapter_name, target);
CREATE INDEX IF NOT EXISTS ix_kg_retrieval_trace_snapshots_query_hash
    ON public.kg_retrieval_trace_snapshots(query_hash);
CREATE INDEX IF NOT EXISTS ix_kg_retrieval_trace_snapshots_strategy
    ON public.kg_retrieval_trace_snapshots(strategy_name, strategy_version);
CREATE INDEX IF NOT EXISTS ix_kg_retrieval_labels_snapshot
    ON public.kg_retrieval_labels(snapshot_id);
CREATE INDEX IF NOT EXISTS ix_kg_retrieval_labels_case
    ON public.kg_retrieval_labels(case_id);
CREATE INDEX IF NOT EXISTS ix_kg_retrieval_eval_runs_strategy
    ON public.kg_retrieval_eval_runs(strategy_name, strategy_version);
CREATE INDEX IF NOT EXISTS ix_kg_retrieval_eval_runs_status
    ON public.kg_retrieval_eval_runs(status);
CREATE INDEX IF NOT EXISTS ix_kg_retrieval_eval_metrics_run
    ON public.kg_retrieval_eval_metrics(run_id);
CREATE INDEX IF NOT EXISTS ix_kg_retrieval_eval_metrics_case
    ON public.kg_retrieval_eval_metrics(case_id);
