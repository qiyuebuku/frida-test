-- 原子 Cognitive Card 之间经过原文核验的正式 Edge 当前态。

CREATE TABLE IF NOT EXISTS public.kg_card_relations (
    id varchar(180) PRIMARY KEY,
    pair_key varchar(380) NOT NULL,
    source_card_id varchar(180) NOT NULL,
    target_card_id varchar(180) NOT NULL,
    relation_kind varchar(48) NOT NULL,
    relation_type varchar(160) NOT NULL,
    direction varchar(240) NOT NULL DEFAULT '',
    decision_class varchar(16) NOT NULL,
    basis text NOT NULL,
    source_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    target_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    relation_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    inference_mechanism text NOT NULL DEFAULT '',
    confidence double precision NOT NULL DEFAULT 0,
    pipeline_version varchar(96) NOT NULL,
    model_name varchar(128) NOT NULL,
    prompt_version varchar(96) NOT NULL,
    schema_version varchar(96) NOT NULL,
    content_version varchar(64) NOT NULL,
    semantic_synced_version varchar(64) NOT NULL DEFAULT '',
    graph_event_published_version varchar(64) NOT NULL DEFAULT '',
    status varchar(32) NOT NULL DEFAULT 'active',
    invalidated_at timestamptz,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    CONSTRAINT ck_kg_card_relations_distinct_endpoints CHECK (source_card_id <> target_card_id),
    CONSTRAINT ck_kg_card_relations_decision_class CHECK (decision_class IN ('observed', 'inferred')),
    CONSTRAINT ck_kg_card_relations_status CHECK (status IN ('active', 'inactive')),
    CONSTRAINT ck_kg_card_relations_confidence CHECK (confidence >= 0 AND confidence <= 1)
);

ALTER TABLE public.kg_card_relations
    ADD COLUMN IF NOT EXISTS relation_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS ix_kg_card_relations_pair_status
    ON public.kg_card_relations(pair_key, status);
CREATE INDEX IF NOT EXISTS ix_kg_card_relations_source_status
    ON public.kg_card_relations(source_card_id, status);
CREATE INDEX IF NOT EXISTS ix_kg_card_relations_target_status
    ON public.kg_card_relations(target_card_id, status);
CREATE INDEX IF NOT EXISTS ix_kg_card_relations_kind_status
    ON public.kg_card_relations(relation_kind, status);

COMMENT ON TABLE public.kg_card_relations IS '原子 Cognitive Card 之间经过原文核验的正式关系 Edge 当前态';
COMMENT ON COLUMN public.kg_card_relations.id IS '由规范化端点和稳定 relation_kind 生成的 Edge ID，同时作为 Milvus target_id';
COMMENT ON COLUMN public.kg_card_relations.pair_key IS '与方向无关的 Card Pair identity，用于重判和失效处理';
COMMENT ON COLUMN public.kg_card_relations.relation_kind IS '稳定关系枚举，不从自由文本 relation_type 推断';
COMMENT ON COLUMN public.kg_card_relations.relation_evidence_refs IS '直接证明关系连接成立的原文坐标，按 chunk_id 与 refs 保存';
COMMENT ON COLUMN public.kg_card_relations.semantic_synced_version IS '已成功同步到 Milvus 的 content_version';
COMMENT ON COLUMN public.kg_card_relations.graph_event_published_version IS '已成功投递图变化事件的 content_version';
