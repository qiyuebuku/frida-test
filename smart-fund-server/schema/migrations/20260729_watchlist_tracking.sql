ALTER TABLE IF EXISTS public.ft_watchlist_data
    ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone DEFAULT now();

DO $$
BEGIN
    IF to_regclass('public.ft_watchlist_data') IS NOT NULL THEN
        COMMENT ON COLUMN public.ft_watchlist_data.updated_at
            IS '最近一次采集覆盖时间';
    END IF;
END $$;
