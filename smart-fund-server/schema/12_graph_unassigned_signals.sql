-- Graph Index weak signals waiting for future community promotion.
-- This table stores refs and metrics only; readable text remains in Milvus chunk/entity/relation targets.

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

CREATE INDEX IF NOT EXISTS ix_kg_graph_unassigned_signals_adapter_status
    ON public.kg_graph_unassigned_signals(adapter_name, status);
CREATE INDEX IF NOT EXISTS ix_kg_graph_unassigned_signals_projection
    ON public.kg_graph_unassigned_signals(projection);
CREATE INDEX IF NOT EXISTS ix_kg_graph_unassigned_signals_promoted
    ON public.kg_graph_unassigned_signals(promoted_community_id);
