BEGIN;

-- factual_anchors 没有独立消费者，删除旧字段。
ALTER TABLE public.kg_cognitive_cards
    DROP COLUMN IF EXISTS factual_anchors;

COMMIT;
