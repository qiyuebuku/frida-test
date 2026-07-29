BEGIN;

-- 原 topic-intent Card 无法可靠迁移为原子事件，先清理旧 Card 和旧 Assignment。
TRUNCATE TABLE public.kg_community_assignments;
TRUNCATE TABLE public.kg_cognitive_cards;

ALTER TABLE public.kg_cognitive_cards
    DROP COLUMN IF EXISTS summary,
    DROP COLUMN IF EXISTS title_candidates,
    DROP COLUMN IF EXISTS topic_intents,
    DROP COLUMN IF EXISTS risk_signals,
    DROP COLUMN IF EXISTS local_impact_signals,
    DROP COLUMN IF EXISTS actor_signals,
    DROP COLUMN IF EXISTS supporting_text,
    DROP COLUMN IF EXISTS system_pointers,
    DROP COLUMN IF EXISTS payload,
    ADD COLUMN IF NOT EXISTS focus_evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS focus_span_offsets jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS factual_anchors jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS generator_version character varying(96) NOT NULL DEFAULT '';

COMMENT ON TABLE public.kg_cognitive_cards IS
    '原子 Cognitive Card manifest；完整 Summary 和 Relation Probe 由 Milvus 保存';
COMMENT ON COLUMN public.kg_cognitive_cards.focus_evidence_refs IS
    'Card 焦点原文 Span Ref 列表';
COMMENT ON COLUMN public.kg_cognitive_cards.focus_span_offsets IS
    '焦点 Span 在 primary chunk 中的 offset 指针';
COMMENT ON COLUMN public.kg_cognitive_cards.factual_anchors IS
    '由焦点原文明示的紧凑事实锚点';

COMMIT;
