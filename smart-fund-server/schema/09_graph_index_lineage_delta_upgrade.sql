-- Graph Index lineage and rolling delta schema upgrade.
-- This file is idempotent and contains no physical foreign keys.

ALTER TABLE IF EXISTS public.kg_graph_communities
    ADD COLUMN IF NOT EXISTS lineage_id character varying(180) NOT NULL DEFAULT '';

ALTER TABLE IF EXISTS public.kg_graph_communities
    ADD COLUMN IF NOT EXISTS previous_community_ids jsonb NOT NULL DEFAULT '[]'::jsonb;

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

CREATE INDEX IF NOT EXISTS ix_kg_graph_deltas_adapter_projection
    ON public.kg_graph_deltas(adapter_name, projection);

CREATE INDEX IF NOT EXISTS ix_kg_graph_deltas_window
    ON public.kg_graph_deltas(window_name);

CREATE INDEX IF NOT EXISTS ix_kg_graph_deltas_status
    ON public.kg_graph_deltas(status);

DROP TABLE IF EXISTS public.kg_graph_delta_versions;
DROP TABLE IF EXISTS public.kg_graph_finding_versions;
DROP TABLE IF EXISTS public.kg_graph_community_versions;
