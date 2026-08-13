CREATE TABLE IF NOT EXISTS public.ft_sentiment_signal (
    snapshot_date date NOT NULL,
    market_temperature integer NOT NULL,
    market_level character varying(8) NOT NULL,
    market_trend character varying(16),
    signals jsonb NOT NULL,
    overheat_codes jsonb,
    leading_theme jsonb,
    sentiment_agg jsonb,
    contributors jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ft_sentiment_signal_pkey'
          AND conrelid = 'public.ft_sentiment_signal'::regclass
    ) THEN
        ALTER TABLE public.ft_sentiment_signal
            ADD CONSTRAINT ft_sentiment_signal_pkey
            PRIMARY KEY (snapshot_date);
    END IF;
END
$$;

COMMENT ON TABLE public.ft_sentiment_signal
    IS 'L2 情绪派生信号日度快照，每日收盘后由 SentimentAggregator.materialize_snapshot 写入';
COMMENT ON COLUMN public.ft_sentiment_signal.snapshot_date
    IS '快照日期（PK），每日一行';
COMMENT ON COLUMN public.ft_sentiment_signal.market_temperature
    IS '市场温度 0-100';
COMMENT ON COLUMN public.ft_sentiment_signal.market_level
    IS 'cold/cool/warm/hot/extreme';
COMMENT ON COLUMN public.ft_sentiment_signal.market_trend
    IS 'rising/falling/stable';

CREATE INDEX IF NOT EXISTS idx_sentiment_signal_date_desc
    ON public.ft_sentiment_signal (snapshot_date DESC);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jettask') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON TABLE public.ft_sentiment_signal TO jettask;
    END IF;
END
$$;
