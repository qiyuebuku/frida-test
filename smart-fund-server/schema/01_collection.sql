-- AUTO-GENERATED from pg_dump
-- 来源: jettask 生产库 (119.23.227.187:5432/jettask)
-- 重构方案: ../docs/3. 实施方案/3. 系统重构/06-ORM与DDD重构方案.md
--
-- 不要手动编辑;如需更新,跑 schema/regenerate.sh

SET statement_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET client_min_messages = warning;
SET row_security = off;
SET default_tablespace = '';
SET default_table_access_method = heap;


-- TABLE: ft_collection_state
--
-- Name: ft_collection_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_collection_state (
    id integer NOT NULL,
    aggregator character varying(32) NOT NULL,
    source_name character varying(64) NOT NULL,
    last_run_at timestamp with time zone,
    last_success_at timestamp with time zone,
    last_error text DEFAULT ''::text,
    consecutive_failures integer DEFAULT 0,
    enabled boolean DEFAULT true,
    interval_override integer,
    total_runs bigint DEFAULT 0,
    total_saved bigint DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    config jsonb DEFAULT '{}'::jsonb,
    -- 采集进度（ALTER TABLE ADD 的列在末尾）
    mode character varying(16) DEFAULT 'incremental'::character varying,
    target_time character varying(32),
    newest_time character varying(32),
    oldest_time character varying(32),
    backfill_status character varying(16),
    cursor jsonb
);

-- SEQUENCE: ft_collection_state_id_seq
--
-- Name: ft_collection_state_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_collection_state_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_collection_state_id_seq
--
-- Name: ft_collection_state_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_collection_state_id_seq OWNED BY public.ft_collection_state.id;

-- TABLE: ft_macro_indicators
--
-- Name: ft_macro_indicators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_macro_indicators (
    id integer NOT NULL,
    indicator character varying(32) NOT NULL,
    period character varying(16) NOT NULL,
    value double precision NOT NULL,
    unit character varying(16) DEFAULT ''::character varying,
    prev_value double precision,
    source character varying(32) DEFAULT ''::character varying,
    published_at date,
    dim_tag character varying(16) DEFAULT ''::character varying,
    yoy double precision,
    mom double precision,
    created_at timestamp with time zone DEFAULT now()
);

-- TABLE: ft_macro_regime
--
-- Name: ft_macro_regime; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_macro_regime (
    id integer NOT NULL,
    computed_at timestamp with time zone DEFAULT now(),
    snapshot_date date NOT NULL,
    regime character varying(16) NOT NULL,
    overall_score double precision NOT NULL,
    multiplier double precision NOT NULL,
    liquidity_score double precision NOT NULL DEFAULT 0,
    growth_score double precision NOT NULL DEFAULT 0,
    inflation_score double precision NOT NULL DEFAULT 0,
    external_score double precision NOT NULL DEFAULT 0,
    policy_score double precision NOT NULL DEFAULT 0,
    contributors jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE SEQUENCE public.ft_macro_regime_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

ALTER SEQUENCE public.ft_macro_regime_id_seq OWNED BY public.ft_macro_regime.id;
ALTER TABLE ONLY public.ft_macro_regime ALTER COLUMN id SET DEFAULT nextval('public.ft_macro_regime_id_seq'::regclass);
ALTER TABLE ONLY public.ft_macro_regime ADD CONSTRAINT ft_macro_regime_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.ft_macro_regime ADD CONSTRAINT ft_macro_regime_snapshot_date_key UNIQUE (snapshot_date);
CREATE INDEX idx_macro_regime_date ON public.ft_macro_regime(snapshot_date DESC);

-- SEQUENCE: ft_macro_indicators_id_seq
--
-- Name: ft_macro_indicators_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_macro_indicators_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_macro_indicators_id_seq
--
-- Name: ft_macro_indicators_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_macro_indicators_id_seq OWNED BY public.ft_macro_indicators.id;

-- TABLE: ft_market_cache
--
-- Name: ft_market_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_market_cache (
    id integer NOT NULL,
    data_type character varying(50) NOT NULL,
    data jsonb NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_market_cache_id_seq
--
-- Name: ft_market_cache_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_market_cache_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_market_cache_id_seq
--
-- Name: ft_market_cache_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_market_cache_id_seq OWNED BY public.ft_market_cache.id;

-- TABLE: ft_market_flow
--
-- Name: ft_market_flow; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_market_flow (
    id integer NOT NULL,
    data_type character varying(32) NOT NULL,
    trade_date date NOT NULL,
    data jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);

-- SEQUENCE: ft_market_flow_id_seq
--
-- Name: ft_market_flow_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_market_flow_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_market_flow_id_seq
--
-- Name: ft_market_flow_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_market_flow_id_seq OWNED BY public.ft_market_flow.id;

-- TABLE: ft_news
--
-- Name: ft_news; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_news (
    id integer NOT NULL,
    title text NOT NULL,
    content text DEFAULT ''::text,
    source character varying(32) NOT NULL,
    source_name character varying(50) DEFAULT ''::character varying,
    source_reliability double precision DEFAULT 0.5,
    category character varying(20) DEFAULT ''::character varying,
    url text DEFAULT ''::text,
    tags jsonb DEFAULT '[]'::jsonb,
    related_stocks jsonb DEFAULT '[]'::jsonb,
    published_at timestamp with time zone NOT NULL,
    fingerprint character varying(64) NOT NULL,
    news_kind character varying(24) DEFAULT 'news'::character varying NOT NULL,
    dedup_key character varying(64) NOT NULL,
    content_fingerprint character varying(64),
    created_at timestamp with time zone DEFAULT now(),
    summary text DEFAULT ''::text,
    event_extracted boolean DEFAULT false
);

-- SEQUENCE: ft_news_id_seq
--
-- Name: ft_news_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_news_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_news_id_seq
--
-- Name: ft_news_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_news_id_seq OWNED BY public.ft_news.id;

-- TABLE: ft_sentiment
--
-- Name: ft_sentiment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_sentiment (
    id integer NOT NULL,
    data_type character varying(32) NOT NULL,
    trade_date date NOT NULL,
    data jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);

-- SEQUENCE: ft_sentiment_id_seq
--
-- Name: ft_sentiment_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_sentiment_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_sentiment_id_seq
--
-- Name: ft_sentiment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_sentiment_id_seq OWNED BY public.ft_sentiment.id;

-- DEFAULT: ft_collection_state id
--
-- Name: ft_collection_state id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_collection_state ALTER COLUMN id SET DEFAULT nextval('public.ft_collection_state_id_seq'::regclass);

-- DEFAULT: ft_macro_indicators id
--
-- Name: ft_macro_indicators id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_macro_indicators ALTER COLUMN id SET DEFAULT nextval('public.ft_macro_indicators_id_seq'::regclass);

-- DEFAULT: ft_market_cache id
--
-- Name: ft_market_cache id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_market_cache ALTER COLUMN id SET DEFAULT nextval('public.ft_market_cache_id_seq'::regclass);

-- DEFAULT: ft_market_flow id
--
-- Name: ft_market_flow id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_market_flow ALTER COLUMN id SET DEFAULT nextval('public.ft_market_flow_id_seq'::regclass);

-- DEFAULT: ft_news id
--
-- Name: ft_news id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_news ALTER COLUMN id SET DEFAULT nextval('public.ft_news_id_seq'::regclass);

-- DEFAULT: ft_sentiment id
--
-- Name: ft_sentiment id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_sentiment ALTER COLUMN id SET DEFAULT nextval('public.ft_sentiment_id_seq'::regclass);

-- Non-market facts for tracked instruments, split by update semantics.
CREATE TABLE public.ft_instrument_profiles (
    id bigserial PRIMARY KEY, code varchar(32) NOT NULL, data_type varchar(32) NOT NULL,
    provider varchar(32) NOT NULL, observed_at timestamptz, fetched_at timestamptz NOT NULL,
    data jsonb NOT NULL, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
    CONSTRAINT uq_ft_instrument_profiles_identity UNIQUE (code, data_type, provider)
);
CREATE TABLE public.ft_instrument_disclosures (
    id bigserial PRIMARY KEY, code varchar(32) NOT NULL, data_type varchar(32) NOT NULL,
    provider varchar(32) NOT NULL, report_date date NOT NULL, observed_at timestamptz,
    fetched_at timestamptz NOT NULL, payload_hash varchar(64) NOT NULL, data jsonb NOT NULL,
    created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
    CONSTRAINT uq_ft_instrument_disclosures_identity UNIQUE (code, data_type, provider, report_date)
);
CREATE TABLE public.ft_instrument_observations (
    id bigserial PRIMARY KEY, code varchar(32) NOT NULL, data_type varchar(32) NOT NULL,
    provider varchar(32) NOT NULL, observation_date date NOT NULL, observed_at timestamptz,
    fetched_at timestamptz NOT NULL, payload_hash varchar(64) NOT NULL, data jsonb NOT NULL,
    created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
    CONSTRAINT uq_ft_instrument_observations_identity UNIQUE (code, data_type, provider, observation_date)
);

-- TABLE: ft_market_snapshots
-- 盘中公共市场与标的历史快照；同一交易日不同时间桶不会互相覆盖。

CREATE TABLE public.ft_market_snapshots (
    id bigserial NOT NULL,
    data_type character varying(32) NOT NULL,
    subject_type character varying(16) NOT NULL,
    subject_id character varying(128) NOT NULL,
    market character varying(16) NOT NULL,
    provider character varying(32) NOT NULL,
    trade_date date NOT NULL,
    observed_at timestamp with time zone,
    fetched_at timestamp with time zone NOT NULL,
    bucket_at timestamp with time zone NOT NULL,
    freshness_status character varying(16) NOT NULL DEFAULT 'unknown',
    source_latency_seconds double precision,
    payload_hash character varying(64) NOT NULL,
    data jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

COMMENT ON TABLE public.ft_market_snapshots IS '盘中公共市场与标的历史快照';
COMMENT ON COLUMN public.ft_market_snapshots.observed_at IS '来源声明的数据时间；未知时为空';
COMMENT ON COLUMN public.ft_market_snapshots.fetched_at IS '本系统完成抓取时间';
COMMENT ON COLUMN public.ft_market_snapshots.bucket_at IS '按任务频率归一后的幂等时间桶';

-- TABLE: ft_etf_daily_shares
-- 沪深交易所官方 ETF 日级份额。

CREATE TABLE public.ft_etf_daily_shares (
    id bigserial NOT NULL,
    exchange character varying(8) NOT NULL,
    code character varying(16) NOT NULL,
    name character varying(128) NOT NULL DEFAULT '',
    trade_date date NOT NULL,
    shares numeric(30,4) NOT NULL,
    share_unit character varying(16) NOT NULL,
    provider character varying(32) NOT NULL,
    observed_at timestamp with time zone,
    fetched_at timestamp with time zone NOT NULL,
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

COMMENT ON TABLE public.ft_etf_daily_shares IS '沪深交易所官方 ETF 日级份额';

-- TABLE: ft_collection_runs
-- 每次采集执行的历史审计，ft_collection_state 继续保存最新断点。

CREATE TABLE public.ft_collection_runs (
    id bigserial NOT NULL,
    task_name character varying(64) NOT NULL,
    source_name character varying(64) NOT NULL,
    event_id character varying(128) NOT NULL DEFAULT '',
    status character varying(24) NOT NULL DEFAULT 'running',
    scheduled_at timestamp with time zone,
    started_at timestamp with time zone NOT NULL DEFAULT now(),
    finished_at timestamp with time zone,
    fetched_count integer NOT NULL DEFAULT 0,
    valid_count integer NOT NULL DEFAULT 0,
    saved_count integer NOT NULL DEFAULT 0,
    skipped_count integer NOT NULL DEFAULT 0,
    source_time_min timestamp with time zone,
    source_time_max timestamp with time zone,
    checkpoint_before jsonb NOT NULL DEFAULT '{}'::jsonb,
    checkpoint_after jsonb NOT NULL DEFAULT '{}'::jsonb,
    retry_count integer NOT NULL DEFAULT 0,
    error_type character varying(64) NOT NULL DEFAULT '',
    error_message text NOT NULL DEFAULT '',
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);

COMMENT ON TABLE public.ft_collection_runs IS '采集任务每次真实执行的历史审计';

-- TABLE: ft_sentiment_signal
-- L2 情绪派生信号日度快照，每日收盘后由 SentimentAggregator.materialize_snapshot 写入

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

ALTER TABLE ONLY public.ft_sentiment_signal ADD CONSTRAINT ft_sentiment_signal_pkey PRIMARY KEY (snapshot_date);

COMMENT ON TABLE public.ft_sentiment_signal IS 'L2 情绪派生信号日度快照，每日收盘后由 SentimentAggregator.materialize_snapshot 写入';
COMMENT ON COLUMN public.ft_sentiment_signal.snapshot_date IS '快照日期（PK），每日一行';
COMMENT ON COLUMN public.ft_sentiment_signal.market_temperature IS '市场温度 0-100';
COMMENT ON COLUMN public.ft_sentiment_signal.market_level IS 'cold/cool/warm/hot/extreme';
COMMENT ON COLUMN public.ft_sentiment_signal.market_trend IS 'rising/falling/stable';

CREATE INDEX IF NOT EXISTS idx_sentiment_signal_date_desc ON public.ft_sentiment_signal (snapshot_date DESC);
