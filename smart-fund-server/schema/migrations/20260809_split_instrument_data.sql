BEGIN;

CREATE TABLE IF NOT EXISTS public.ft_instrument_profiles (
    id bigserial PRIMARY KEY, code varchar(32) NOT NULL, data_type varchar(32) NOT NULL,
    provider varchar(32) NOT NULL, observed_at timestamptz, fetched_at timestamptz NOT NULL,
    data jsonb NOT NULL, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
    CONSTRAINT uq_ft_instrument_profiles_identity UNIQUE (code, data_type, provider)
);
CREATE TABLE IF NOT EXISTS public.ft_instrument_disclosures (
    id bigserial PRIMARY KEY, code varchar(32) NOT NULL, data_type varchar(32) NOT NULL,
    provider varchar(32) NOT NULL, report_date date NOT NULL, observed_at timestamptz,
    fetched_at timestamptz NOT NULL, payload_hash varchar(64) NOT NULL, data jsonb NOT NULL,
    created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
    CONSTRAINT uq_ft_instrument_disclosures_identity UNIQUE (code, data_type, provider, report_date)
);
CREATE TABLE IF NOT EXISTS public.ft_instrument_observations (
    id bigserial PRIMARY KEY, code varchar(32) NOT NULL, data_type varchar(32) NOT NULL,
    provider varchar(32) NOT NULL, observation_date date NOT NULL, observed_at timestamptz,
    fetched_at timestamptz NOT NULL, payload_hash varchar(64) NOT NULL, data jsonb NOT NULL,
    created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
    CONSTRAINT uq_ft_instrument_observations_identity UNIQUE (code, data_type, provider, observation_date)
);

DO $$
DECLARE unmapped bigint;
BEGIN
IF to_regclass('public.ft_watchlist_data') IS NOT NULL THEN
INSERT INTO public.ft_instrument_profiles
    (code, data_type, provider, observed_at, fetched_at, data, created_at, updated_at)
SELECT DISTINCT ON (code, data_type, COALESCE(NULLIF(data->>'source',''), 'ths'))
    lower(code), data_type, COALESCE(NULLIF(data->>'source',''), 'ths'), updated_at,
    updated_at, data, created_at, updated_at
FROM public.ft_watchlist_data
WHERE data_type IN ('fund_detail','style_preference','trade_rule','manager_info','plates')
ORDER BY code, data_type, COALESCE(NULLIF(data->>'source',''), 'ths'), updated_at DESC, id DESC
ON CONFLICT ON CONSTRAINT uq_ft_instrument_profiles_identity DO UPDATE SET
    data=EXCLUDED.data, observed_at=EXCLUDED.observed_at,
    fetched_at=EXCLUDED.fetched_at, updated_at=EXCLUDED.updated_at;

INSERT INTO public.ft_instrument_disclosures
    (code, data_type, provider, report_date, observed_at, fetched_at, payload_hash, data, created_at, updated_at)
SELECT lower(code), data_type, COALESCE(NULLIF(data->>'source',''), 'ths'),
    COALESCE(trade_date, created_at::date), updated_at, updated_at, md5(data::text), data, created_at, updated_at
FROM public.ft_watchlist_data
WHERE data_type IN ('holdings','scale','holder_ratio','dividend','holding_overview','asset_allocation','position_detail')
ON CONFLICT ON CONSTRAINT uq_ft_instrument_disclosures_identity DO UPDATE SET
    data=EXCLUDED.data, payload_hash=EXCLUDED.payload_hash, observed_at=EXCLUDED.observed_at,
    fetched_at=EXCLUDED.fetched_at, updated_at=EXCLUDED.updated_at;

INSERT INTO public.ft_instrument_observations
    (code, data_type, provider, observation_date, observed_at, fetched_at, payload_hash, data, created_at, updated_at)
SELECT lower(code), data_type,
    CASE WHEN data->>'source' <> '' THEN data->>'source'
         WHEN data_type='nav_sina' THEN 'sina'
         WHEN data_type IN ('valuation','guba_posts','research') THEN 'eastmoney'
         ELSE 'ths' END,
    COALESCE(trade_date, created_at::date), updated_at, updated_at, md5(data::text), data, created_at, updated_at
FROM public.ft_watchlist_data
WHERE data_type IN ('nav','nav_technical','performance','flow_trend','flow_trend_summary',
    'periodic_rate','profit_contribution','nav_sina','year_return','max_drawdown',
    'valuation','guba_posts','research')
ON CONFLICT ON CONSTRAINT uq_ft_instrument_observations_identity DO UPDATE SET
    data=EXCLUDED.data, payload_hash=EXCLUDED.payload_hash, observed_at=EXCLUDED.observed_at,
    fetched_at=EXCLUDED.fetched_at, updated_at=EXCLUDED.updated_at;

    SELECT count(*) INTO unmapped FROM public.ft_watchlist_data
    WHERE data_type NOT IN (
        'fund_detail','style_preference','trade_rule','manager_info','plates',
        'holdings','scale','holder_ratio','dividend','holding_overview','asset_allocation','position_detail',
        'nav','nav_technical','performance','flow_trend','flow_trend_summary','periodic_rate',
        'profit_contribution','nav_sina','year_return','max_drawdown','valuation','guba_posts','research'
    );
    IF unmapped <> 0 THEN
        RAISE EXCEPTION 'refusing to drop ft_watchlist_data: % unmapped rows', unmapped;
    END IF;

DROP TABLE public.ft_watchlist_data;
END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_ft_instrument_profiles_code_type ON public.ft_instrument_profiles(code, data_type);
CREATE INDEX IF NOT EXISTS ix_ft_instrument_disclosures_code_date ON public.ft_instrument_disclosures(code, data_type, report_date DESC);
CREATE INDEX IF NOT EXISTS ix_ft_instrument_observations_code_date ON public.ft_instrument_observations(code, data_type, observation_date DESC);
COMMIT;
