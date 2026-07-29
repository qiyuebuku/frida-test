-- Add a reversible event projection to source-specific Cognitive Cards.
-- Existing Cards begin as singleton events; active observed same_event edges are
-- projected by CardEventRepository after deployment/backfill.

ALTER TABLE public.kg_cognitive_cards
    ADD COLUMN IF NOT EXISTS event_id varchar(180) NOT NULL DEFAULT '';

UPDATE public.kg_cognitive_cards
SET event_id = 'kg_event:' || split_part(cognitive_card_id, ':', 2)
WHERE event_id = ''
  AND split_part(cognitive_card_id, ':', 2) <> '';

UPDATE public.kg_cognitive_cards
SET event_id = 'kg_event:' || substr(md5(cognitive_card_id), 1, 20)
WHERE event_id = '';

CREATE INDEX IF NOT EXISTS ix_kg_cognitive_cards_event
    ON public.kg_cognitive_cards(adapter_name, event_id, status);

COMMENT ON COLUMN public.kg_cognitive_cards.event_id IS
    '由 active same_event 关系投影得到的事件分组 ID；无匹配时为 Card 单例事件';
