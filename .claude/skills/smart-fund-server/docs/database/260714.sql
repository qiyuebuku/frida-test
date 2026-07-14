BEGIN;

-- Relation Probe 属于跨 Chunk 关系发现的临时检索计划，不属于 Card manifest。
ALTER TABLE public.kg_cognitive_cards
    DROP COLUMN IF EXISTS relation_probes;

COMMIT;
