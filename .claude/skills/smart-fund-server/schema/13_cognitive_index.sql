-- Cognitive Card and Community Assignment tables.
-- PG stores structured cognitive signals and refs only; readable chunk/card text stays in Milvus.

CREATE SEQUENCE IF NOT EXISTS public.kg_graph_community_id_seq;

ALTER TABLE IF EXISTS public.kg_graph_communities
    ADD COLUMN IF NOT EXISTS id bigint;
ALTER TABLE IF EXISTS public.kg_graph_communities
    ALTER COLUMN id SET DEFAULT nextval('public.kg_graph_community_id_seq'::regclass);

CREATE INDEX IF NOT EXISTS ix_kg_graph_communities_id
    ON public.kg_graph_communities(id);

CREATE TABLE IF NOT EXISTS public.kg_cognitive_cards (
    cognitive_card_id character varying(180) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    source_type character varying(64) NOT NULL DEFAULT '',
    source_id character varying(256) NOT NULL DEFAULT '',
    evidence_id character varying(180) NOT NULL,
    primary_chunk_id character varying(220) NOT NULL,
    chunk_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    chunk_index integer NOT NULL DEFAULT 0,
    summary text NOT NULL DEFAULT '',
    title_candidates jsonb NOT NULL DEFAULT '[]'::jsonb,
    topic_intents jsonb NOT NULL DEFAULT '[]'::jsonb,
    risk_signals jsonb NOT NULL DEFAULT '[]'::jsonb,
    local_impact_signals jsonb NOT NULL DEFAULT '[]'::jsonb,
    actor_signals jsonb NOT NULL DEFAULT '{}'::jsonb,
    supporting_text jsonb NOT NULL DEFAULT '[]'::jsonb,
    system_pointers jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    schema_version character varying(96) NOT NULL DEFAULT '',
    status character varying(32) NOT NULL DEFAULT 'active',
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.kg_community_assignments (
    assignment_id character varying(180) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    cognitive_card_id character varying(180) NOT NULL,
    intent_index integer NOT NULL DEFAULT 0,
    intent_id character varying(220) NOT NULL DEFAULT '',
    community_id character varying(180) NOT NULL DEFAULT '',
    action character varying(32) NOT NULL DEFAULT '',
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

CREATE TABLE IF NOT EXISTS public.kg_assignment_candidate_orders (
    order_id character varying(180) PRIMARY KEY,
    adapter_name character varying(64) NOT NULL,
    target character varying(32) NOT NULL DEFAULT '',
    query_key character varying(96) NOT NULL,
    ordered_community_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT uq_kg_assignment_candidate_orders_scope
        UNIQUE (adapter_name, target, query_key)
);

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

CREATE INDEX IF NOT EXISTS ix_kg_assignment_candidate_orders_scope
    ON public.kg_assignment_candidate_orders(adapter_name, target);
