-- Cognitive Card and legacy Community Assignment tables.
-- Relationship-first kg_graph_communities is defined in 06_knowledge.sql and
-- replaced for existing databases by 18_relation_graph_communities.sql.

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

CREATE INDEX IF NOT EXISTS ix_kg_community_insights_adapter_status
    ON public.kg_community_insights(adapter_name, status);
CREATE INDEX IF NOT EXISTS ix_kg_community_insights_community
    ON public.kg_community_insights(community_id);
CREATE INDEX IF NOT EXISTS ix_kg_community_insights_updated
    ON public.kg_community_insights(updated_at);

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
    fact_id character varying(180) NOT NULL DEFAULT '',
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
COMMENT ON COLUMN public.kg_cognitive_cards.fact_id IS '由 active observed same_fact 关系投影得到的等价事实分组 ID；无匹配时为 Card 单例事实';

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
CREATE INDEX IF NOT EXISTS ix_kg_cognitive_cards_fact
    ON public.kg_cognitive_cards(adapter_name, fact_id, status);

CREATE INDEX IF NOT EXISTS ix_kg_community_assignments_adapter_status
    ON public.kg_community_assignments(adapter_name, status);
CREATE INDEX IF NOT EXISTS ix_kg_community_assignments_card
    ON public.kg_community_assignments(cognitive_card_id);
CREATE INDEX IF NOT EXISTS ix_kg_community_assignments_community
    ON public.kg_community_assignments(community_id);

CREATE INDEX IF NOT EXISTS ix_kg_assignment_candidate_orders_scope
    ON public.kg_assignment_candidate_orders(adapter_name, target);
