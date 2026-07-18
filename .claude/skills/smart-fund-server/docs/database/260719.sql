-- 恢复原子 Cognitive Card 的跨 Chunk 关系搜索方向。
-- Probe 仅用于候选召回，不代表正式 Edge，也不写入 Milvus。

ALTER TABLE public.kg_cognitive_cards
    ADD COLUMN IF NOT EXISTS relation_probes jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN public.kg_cognitive_cards.relation_probes IS
    '跨 Chunk 关系发现使用的候选事件搜索方向，不代表正式关系';
