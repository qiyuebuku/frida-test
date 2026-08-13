ALTER TABLE public.ft_news
    ADD COLUMN IF NOT EXISTS news_kind character varying(24)
        DEFAULT 'news' NOT NULL,
    ADD COLUMN IF NOT EXISTS dedup_key character varying(64),
    ADD COLUMN IF NOT EXISTS content_fingerprint character varying(64);

UPDATE public.ft_news
SET dedup_key = fingerprint
WHERE dedup_key IS NULL;

ALTER TABLE public.ft_news
    ALTER COLUMN dedup_key SET NOT NULL;

COMMENT ON COLUMN public.ft_news.news_kind
    IS '稳定内容类型: news/market_recap/market_preview/research_report';
COMMENT ON COLUMN public.ft_news.dedup_key
    IS '业务日+归一化标题的跨来源去重键';
COMMENT ON COLUMN public.ft_news.content_fingerprint
    IS '非空完整正文归一化后的 SHA256';

CREATE UNIQUE INDEX IF NOT EXISTS idx_ft_news_dedup_key
    ON public.ft_news (dedup_key);
CREATE INDEX IF NOT EXISTS idx_ft_news_content_fingerprint
    ON public.ft_news (content_fingerprint)
    WHERE content_fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ft_news_kind_time
    ON public.ft_news (news_kind, published_at DESC);
