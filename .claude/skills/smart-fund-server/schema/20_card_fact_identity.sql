-- Separate lossless fact deduplication from same-event business relations.
-- Existing event_id groups are intentionally discarded because same_event did
-- not guarantee that member Cards were semantically substitutable.

ALTER TABLE public.kg_cognitive_cards
    ADD COLUMN IF NOT EXISTS fact_id varchar(180) NOT NULL DEFAULT '';

UPDATE public.kg_cognitive_cards
SET fact_id = 'kg_fact:' || split_part(cognitive_card_id, ':', 2)
WHERE fact_id = ''
  AND cognitive_card_id LIKE 'kg_cognitive_card:%';

UPDATE public.kg_cognitive_cards
SET fact_id = 'kg_fact:' || substr(md5(cognitive_card_id), 1, 20)
WHERE fact_id = '';

CREATE INDEX IF NOT EXISTS ix_kg_cognitive_cards_fact
    ON public.kg_cognitive_cards(adapter_name, fact_id, status);

COMMENT ON COLUMN public.kg_cognitive_cards.fact_id IS
    '由 active observed same_fact 关系投影得到的等价事实分组 ID；无匹配时为 Card 单例事实';

DROP INDEX IF EXISTS public.ix_kg_cognitive_cards_event;

ALTER TABLE public.kg_cognitive_cards
    DROP COLUMN IF EXISTS event_id;
