-- Knowledge infrastructure fact store.
-- Source of truth for generic nodes, edges, evidence, versions, reviews, and compile runs.

CREATE TABLE IF NOT EXISTS public.kg_nodes (
    node_id character varying(128) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    adapter_version character varying(32) NOT NULL DEFAULT '',
    node_type character varying(64) NOT NULL,
    stable_key character varying(256) NOT NULL,
    canonical_name text NOT NULL,
    aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
    external_ids jsonb NOT NULL DEFAULT '{}'::jsonb,
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    status character varying(32) NOT NULL,
    version character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT uq_kg_nodes_adapter_type_key UNIQUE (adapter_name, node_type, stable_key)
);

CREATE TABLE IF NOT EXISTS public.kg_edges (
    edge_id character varying(160) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    adapter_version character varying(32) NOT NULL DEFAULT '',
    source_node_id character varying(128) NOT NULL REFERENCES public.kg_nodes(node_id),
    target_node_id character varying(128) NOT NULL REFERENCES public.kg_nodes(node_id),
    relation_type character varying(64) NOT NULL,
    properties jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence_label character varying(32) NOT NULL,
    confidence_score double precision NOT NULL,
    status character varying(32) NOT NULL,
    valid_from timestamp with time zone,
    valid_to timestamp with time zone,
    version character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

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
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_edge_evidence (
    edge_id character varying(160) NOT NULL REFERENCES public.kg_edges(edge_id),
    evidence_id character varying(180) NOT NULL REFERENCES public.kg_evidence(evidence_id),
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (edge_id, evidence_id)
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

CREATE TABLE IF NOT EXISTS public.kg_wiki_pages (
    page_id character varying(160) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    page_type character varying(64) NOT NULL,
    subject_type character varying(64),
    subject_id character varying(180),
    title text NOT NULL,
    summary text NOT NULL,
    content text NOT NULL,
    source_node_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_edge_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    version character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_graph_adjacency (
    adapter_name character varying(64) NOT NULL,
    source_node_id character varying(128) NOT NULL,
    target_node_id character varying(128) NOT NULL,
    edge_id character varying(160) NOT NULL,
    relation_type character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    PRIMARY KEY (adapter_name, source_node_id, target_node_id, edge_id)
);

CREATE TABLE IF NOT EXISTS public.kg_evidence_chunks (
    chunk_id character varying(220) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    evidence_id character varying(180) NOT NULL REFERENCES public.kg_evidence(evidence_id),
    content text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_kg_nodes_adapter_type ON public.kg_nodes(adapter_name, node_type);
CREATE INDEX IF NOT EXISTS ix_kg_nodes_status ON public.kg_nodes(status);

CREATE INDEX IF NOT EXISTS ix_kg_edges_source ON public.kg_edges(source_node_id);
CREATE INDEX IF NOT EXISTS ix_kg_edges_target ON public.kg_edges(target_node_id);
CREATE INDEX IF NOT EXISTS ix_kg_edges_relation ON public.kg_edges(relation_type);
CREATE INDEX IF NOT EXISTS ix_kg_edges_status ON public.kg_edges(status);

CREATE INDEX IF NOT EXISTS ix_kg_evidence_source ON public.kg_evidence(source_type, source_id);
CREATE INDEX IF NOT EXISTS ix_kg_evidence_adapter ON public.kg_evidence(adapter_name);

CREATE INDEX IF NOT EXISTS ix_kg_review_items_status ON public.kg_review_items(status);
CREATE INDEX IF NOT EXISTS ix_kg_review_items_object ON public.kg_review_items(object_type, object_id);

CREATE INDEX IF NOT EXISTS ix_kg_compilation_runs_adapter
    ON public.kg_compilation_runs(adapter_name, adapter_version);
CREATE INDEX IF NOT EXISTS ix_kg_compilation_runs_status ON public.kg_compilation_runs(status);

CREATE INDEX IF NOT EXISTS ix_kg_wiki_pages_adapter_type
    ON public.kg_wiki_pages(adapter_name, page_type);
CREATE INDEX IF NOT EXISTS ix_kg_wiki_pages_subject
    ON public.kg_wiki_pages(subject_type, subject_id);

CREATE INDEX IF NOT EXISTS ix_kg_graph_adjacency_adapter_source
    ON public.kg_graph_adjacency(adapter_name, source_node_id);
CREATE INDEX IF NOT EXISTS ix_kg_graph_adjacency_target
    ON public.kg_graph_adjacency(target_node_id);

CREATE INDEX IF NOT EXISTS ix_kg_evidence_chunks_adapter
    ON public.kg_evidence_chunks(adapter_name);
CREATE INDEX IF NOT EXISTS ix_kg_evidence_chunks_evidence
    ON public.kg_evidence_chunks(evidence_id);
