-- Replace legacy Topic Community / Insight state with relationship-first
-- Graph Community current state. Historical Topic membership is intentionally
-- not migrated because it is not verified Card-to-Card relation evidence.

DROP TABLE IF EXISTS public.kg_community_insights;
DROP TABLE IF EXISTS public.kg_graph_community_relations;
DROP TABLE IF EXISTS public.kg_graph_communities;
DROP SEQUENCE IF EXISTS public.kg_graph_community_id_seq;

CREATE TABLE public.kg_graph_communities (
    community_id varchar(180) PRIMARY KEY,
    adapter_name varchar(64) NOT NULL,
    identity_anchor_card_id varchar(180) NOT NULL,
    member_card_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    member_edge_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    graph_fingerprint varchar(64) NOT NULL,
    graph_version integer NOT NULL DEFAULT 1,
    graph_status varchar(32) NOT NULL DEFAULT 'active',
    title text NOT NULL DEFAULT '',
    fact_report text NOT NULL DEFAULT '',
    fact_referenced_card_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    fact_referenced_edge_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    fact_report_version integer NOT NULL DEFAULT 0,
    fact_report_generator_version varchar(96) NOT NULL DEFAULT '',
    fact_report_graph_fingerprint varchar(64) NOT NULL DEFAULT '',
    fact_report_status varchar(32) NOT NULL DEFAULT 'missing',
    fact_report_error text NOT NULL DEFAULT '',
    report_task_dispatched_fingerprint varchar(64) NOT NULL DEFAULT '',
    fact_semantic_synced_version varchar(64) NOT NULL DEFAULT '',
    conditional_projections jsonb NOT NULL DEFAULT '[]'::jsonb,
    projection_version integer NOT NULL DEFAULT 0,
    projection_generator_version varchar(96) NOT NULL DEFAULT '',
    projection_graph_fingerprint varchar(64) NOT NULL DEFAULT '',
    projection_fact_report_version integer NOT NULL DEFAULT 0,
    projection_status varchar(32) NOT NULL DEFAULT 'missing',
    projection_error text NOT NULL DEFAULT '',
    projection_task_dispatched_version integer NOT NULL DEFAULT 0,
    projection_semantic_synced_version varchar(64) NOT NULL DEFAULT '',
    graph_changed_at timestamptz DEFAULT now(),
    fact_report_generated_at timestamptz,
    projection_generated_at timestamptz,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT ck_kg_graph_communities_graph_status
        CHECK (graph_status IN ('active')),
    CONSTRAINT ck_kg_graph_communities_fact_report_status
        CHECK (fact_report_status IN (
            'missing', 'dirty', 'generating', 'publishing', 'ready', 'failed', 'stale'
        )),
    CONSTRAINT ck_kg_graph_communities_projection_status
        CHECK (projection_status IN (
            'missing', 'pending', 'generating', 'publishing', 'ready', 'empty',
            'failed', 'stale'
        ))
);

CREATE INDEX ix_kg_graph_communities_adapter_graph_status
    ON public.kg_graph_communities(adapter_name, graph_status);
CREATE INDEX ix_kg_graph_communities_anchor
    ON public.kg_graph_communities(identity_anchor_card_id);
CREATE INDEX ix_kg_graph_communities_fact_status
    ON public.kg_graph_communities(fact_report_status);
CREATE INDEX ix_kg_graph_communities_projection_status
    ON public.kg_graph_communities(projection_status);
CREATE INDEX ix_kg_graph_communities_graph_fingerprint
    ON public.kg_graph_communities(graph_fingerprint);

COMMENT ON TABLE public.kg_graph_communities IS
    '由 active verified Card Edge 图聚类形成的平行 Graph Community 当前态';
COMMENT ON COLUMN public.kg_graph_communities.identity_anchor_card_id IS
    '用于稳定复用 community_id 的身份锚点 Card ID';
COMMENT ON COLUMN public.kg_graph_communities.graph_fingerprint IS
    '由成员 Card、Edge identity 及 Edge content_version 计算的当前图指纹';
COMMENT ON COLUMN public.kg_graph_communities.fact_report IS
    '基于当前关系子图生成的完整事实性高级认知报告，不包含未来预测';
COMMENT ON COLUMN public.kg_graph_communities.conditional_projections IS
    '与事实报告隔离的条件性未来推演数组，不参与关系图和成员发现';

CREATE TABLE public.kg_graph_community_relations (
    id varchar(180) PRIMARY KEY,
    source_community_id varchar(180) NOT NULL,
    target_community_id varchar(180) NOT NULL,
    relation_kind varchar(48) NOT NULL,
    supporting_edge_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    observed_edge_count integer NOT NULL DEFAULT 0,
    inferred_edge_count integer NOT NULL DEFAULT 0,
    relation_fingerprint varchar(64) NOT NULL,
    status varchar(32) NOT NULL DEFAULT 'active',
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT uq_kg_graph_community_relations_pair_kind
        UNIQUE (source_community_id, target_community_id, relation_kind),
    CONSTRAINT ck_kg_graph_community_relations_distinct_endpoints
        CHECK (source_community_id <> target_community_id),
    CONSTRAINT ck_kg_graph_community_relations_status
        CHECK (status IN ('active'))
);

CREATE INDEX ix_kg_graph_community_relations_source_status
    ON public.kg_graph_community_relations(source_community_id, status);
CREATE INDEX ix_kg_graph_community_relations_target_status
    ON public.kg_graph_community_relations(target_community_id, status);
CREATE INDEX ix_kg_graph_community_relations_kind_status
    ON public.kg_graph_community_relations(relation_kind, status);

COMMENT ON TABLE public.kg_graph_community_relations IS
    '由跨平行 Community 的 active Card Edge 聚合生成的当前态关系投影';
COMMENT ON COLUMN public.kg_graph_community_relations.supporting_edge_ids IS
    '支持当前 Community 关系的 kg_card_relations.id 列表';
COMMENT ON COLUMN public.kg_graph_community_relations.relation_fingerprint IS
    '由 Community 端点、关系类型及底层 Edge 当前版本计算的投影指纹';
