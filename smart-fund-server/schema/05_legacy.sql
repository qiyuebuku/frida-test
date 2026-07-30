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


-- TABLE: ft_alipay_decisions
--
-- Name: ft_alipay_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_alipay_decisions (
    id integer NOT NULL,
    fund_name character varying(100) NOT NULL,
    fund_code character varying(10),
    action character varying(10) NOT NULL,
    amount numeric(12,2),
    sell_pct numeric(5,2),
    reason text,
    confidence character varying(10),
    market_view text,
    decision_date date DEFAULT CURRENT_DATE,
    created_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_alipay_decisions_id_seq
--
-- Name: ft_alipay_decisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_alipay_decisions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_alipay_decisions_id_seq
--
-- Name: ft_alipay_decisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_alipay_decisions_id_seq OWNED BY public.ft_alipay_decisions.id;

-- TABLE: ft_alipay_positions
--
-- Name: ft_alipay_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_alipay_positions (
    id integer NOT NULL,
    fund_name character varying(100) NOT NULL,
    fund_code character varying(10),
    current_value numeric(12,2) NOT NULL,
    total_cost numeric(12,2),
    total_profit numeric(12,2),
    profit_rate numeric(8,2),
    daily_pnl numeric(12,2),
    category character varying(20),
    snapshot_date date DEFAULT CURRENT_DATE NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_alipay_positions_id_seq
--
-- Name: ft_alipay_positions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_alipay_positions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_alipay_positions_id_seq
--
-- Name: ft_alipay_positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_alipay_positions_id_seq OWNED BY public.ft_alipay_positions.id;

-- TABLE: ft_cache
--
-- Name: ft_cache; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_cache (
    id integer NOT NULL,
    fund_code character varying(10) NOT NULL,
    data_type character varying(50) NOT NULL,
    data jsonb NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_cache_id_seq
--
-- Name: ft_cache_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_cache_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_cache_id_seq
--
-- Name: ft_cache_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_cache_id_seq OWNED BY public.ft_cache.id;

-- TABLE: ft_config
--
-- Name: ft_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_config (
    id integer DEFAULT 1 NOT NULL,
    fund_pool jsonb DEFAULT '[]'::jsonb,
    total_capital numeric(12,2) DEFAULT 20000,
    max_single_position_pct numeric(5,2) DEFAULT 30,
    max_daily_buy_amount numeric(12,2) DEFAULT 10000,
    stop_loss_pct numeric(5,2) DEFAULT '-20'::integer,
    take_profit_pct numeric(5,2) DEFAULT 50,
    min_trade_amount numeric(12,2) DEFAULT 10,
    max_fund_count integer DEFAULT 10,
    min_cash_reserve numeric(12,2) DEFAULT 1000,
    cooldown_days integer DEFAULT 1,
    reverse_cooldown_days integer DEFAULT 7,
    min_hold_days integer DEFAULT 7,
    circuit_breaker_loss_pct numeric(5,2) DEFAULT '-10'::integer,
    circuit_breaker_loss_days integer DEFAULT 5,
    trade_cutoff_time character varying(10) DEFAULT '14:50'::character varying,
    trade_account character varying(50) DEFAULT ''::character varying,
    trade_password character varying(100) DEFAULT ''::character varying,
    alipay_fund_map jsonb DEFAULT '{}'::jsonb,
    auth_key1 character varying(100) DEFAULT ''::character varying,
    auth_key2 character varying(100) DEFAULT ''::character varying,
    auth_key3 character varying(100) DEFAULT ''::character varying,
    auth_key4 character varying(20) DEFAULT 'auth'::character varying,
    auth_key5 text DEFAULT ''::text,
    auth_user_id character varying(50) DEFAULT ''::character varying,
    auth_session_id character varying(200) DEFAULT ''::character varying,
    auth_cookie text DEFAULT ''::text,
    auth_expires_at bigint,
    auth_last_sync bigint,
    auth_sync_source character varying(50) DEFAULT ''::character varying,
    updated_at timestamp without time zone DEFAULT now(),
    iwencai_hexin_v text DEFAULT ''::text,
    iwencai_hexin_v_time bigint,
    iwencai_hexin_v_source character varying(50) DEFAULT ''::character varying,
    CONSTRAINT ft_config_id_check CHECK ((id = 1))
);

-- TABLE: ft_raw_data
--
-- Name: ft_raw_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_raw_data (
    id bigint NOT NULL,
    source character varying(32) NOT NULL,
    method character varying(64) NOT NULL,
    params_hash character varying(64) NOT NULL,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    data jsonb NOT NULL,
    data_count integer DEFAULT 0,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    api_latency_ms integer,
    source_name character varying(50) DEFAULT ''::character varying,
    data_domain character varying(32) DEFAULT ''::character varying,
    data_frequency character varying(16) DEFAULT ''::character varying,
    market character varying(16) DEFAULT ''::character varying,
    related_codes jsonb DEFAULT '[]'::jsonb,
    trade_date date,
    is_success boolean DEFAULT true,
    error_msg text DEFAULT ''::text
)
PARTITION BY RANGE (fetched_at);

-- SEQUENCE: ft_raw_data_id_seq
--
-- Name: ft_raw_data_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_raw_data_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_raw_data_id_seq
--
-- Name: ft_raw_data_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_raw_data_id_seq OWNED BY public.ft_raw_data.id;

-- TABLE: ft_raw_data_202603
--
-- Name: ft_raw_data_202603; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_raw_data_202603 (
    id bigint DEFAULT nextval('public.ft_raw_data_id_seq'::regclass) NOT NULL,
    source character varying(32) NOT NULL,
    method character varying(64) NOT NULL,
    params_hash character varying(64) NOT NULL,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    data jsonb NOT NULL,
    data_count integer DEFAULT 0,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    api_latency_ms integer,
    source_name character varying(50) DEFAULT ''::character varying,
    data_domain character varying(32) DEFAULT ''::character varying,
    data_frequency character varying(16) DEFAULT ''::character varying,
    market character varying(16) DEFAULT ''::character varying,
    related_codes jsonb DEFAULT '[]'::jsonb,
    trade_date date,
    is_success boolean DEFAULT true,
    error_msg text DEFAULT ''::text
);

-- TABLE: ft_raw_data_202604
--
-- Name: ft_raw_data_202604; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_raw_data_202604 (
    id bigint DEFAULT nextval('public.ft_raw_data_id_seq'::regclass) NOT NULL,
    source character varying(32) NOT NULL,
    method character varying(64) NOT NULL,
    params_hash character varying(64) NOT NULL,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    data jsonb NOT NULL,
    data_count integer DEFAULT 0,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    api_latency_ms integer,
    source_name character varying(50) DEFAULT ''::character varying,
    data_domain character varying(32) DEFAULT ''::character varying,
    data_frequency character varying(16) DEFAULT ''::character varying,
    market character varying(16) DEFAULT ''::character varying,
    related_codes jsonb DEFAULT '[]'::jsonb,
    trade_date date,
    is_success boolean DEFAULT true,
    error_msg text DEFAULT ''::text
);

-- TABLE: ft_raw_data_202605
--
-- Name: ft_raw_data_202605; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_raw_data_202605 (
    id bigint DEFAULT nextval('public.ft_raw_data_id_seq'::regclass) NOT NULL,
    source character varying(32) NOT NULL,
    method character varying(64) NOT NULL,
    params_hash character varying(64) NOT NULL,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    data jsonb NOT NULL,
    data_count integer DEFAULT 0,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    api_latency_ms integer,
    source_name character varying(50) DEFAULT ''::character varying,
    data_domain character varying(32) DEFAULT ''::character varying,
    data_frequency character varying(16) DEFAULT ''::character varying,
    market character varying(16) DEFAULT ''::character varying,
    related_codes jsonb DEFAULT '[]'::jsonb,
    trade_date date,
    is_success boolean DEFAULT true,
    error_msg text DEFAULT ''::text
);

-- TABLE: ft_raw_data_202606
--
-- Name: ft_raw_data_202606; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_raw_data_202606 (
    id bigint DEFAULT nextval('public.ft_raw_data_id_seq'::regclass) NOT NULL,
    source character varying(32) NOT NULL,
    method character varying(64) NOT NULL,
    params_hash character varying(64) NOT NULL,
    params jsonb DEFAULT '{}'::jsonb NOT NULL,
    data jsonb NOT NULL,
    data_count integer DEFAULT 0,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    api_latency_ms integer,
    source_name character varying(50) DEFAULT ''::character varying,
    data_domain character varying(32) DEFAULT ''::character varying,
    data_frequency character varying(16) DEFAULT ''::character varying,
    market character varying(16) DEFAULT ''::character varying,
    related_codes jsonb DEFAULT '[]'::jsonb,
    trade_date date,
    is_success boolean DEFAULT true,
    error_msg text DEFAULT ''::text
);

-- TABLE: ft_run_log
--
-- Name: ft_run_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_run_log (
    id integer NOT NULL,
    run_date date DEFAULT CURRENT_DATE,
    decisions_count integer DEFAULT 0,
    trades_count integer DEFAULT 0,
    summary text,
    created_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_run_log_id_seq
--
-- Name: ft_run_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_run_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_run_log_id_seq
--
-- Name: ft_run_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_run_log_id_seq OWNED BY public.ft_run_log.id;

-- TABLE: ft_signals
--
-- Name: ft_signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_signals (
    id integer NOT NULL,
    fund_code character varying(10) NOT NULL,
    strategy character varying(50) DEFAULT 'default'::character varying,
    action character varying(10),
    confidence character varying(10),
    indicators jsonb,
    signal_date date DEFAULT CURRENT_DATE,
    created_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_signals_id_seq
--
-- Name: ft_signals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_signals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_signals_id_seq
--
-- Name: ft_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_signals_id_seq OWNED BY public.ft_signals.id;

-- TABLE ATTACH: ft_raw_data_202603
--
-- Name: ft_raw_data_202603; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data ATTACH PARTITION public.ft_raw_data_202603 FOR VALUES FROM ('2026-03-01 00:00:00+08') TO ('2026-04-01 00:00:00+08');

-- TABLE ATTACH: ft_raw_data_202604
--
-- Name: ft_raw_data_202604; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data ATTACH PARTITION public.ft_raw_data_202604 FOR VALUES FROM ('2026-04-01 00:00:00+08') TO ('2026-05-01 00:00:00+08');

-- TABLE ATTACH: ft_raw_data_202605
--
-- Name: ft_raw_data_202605; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data ATTACH PARTITION public.ft_raw_data_202605 FOR VALUES FROM ('2026-05-01 00:00:00+08') TO ('2026-06-01 00:00:00+08');

-- TABLE ATTACH: ft_raw_data_202606
--
-- Name: ft_raw_data_202606; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data ATTACH PARTITION public.ft_raw_data_202606 FOR VALUES FROM ('2026-06-01 00:00:00+08') TO ('2026-07-01 00:00:00+08');

-- DEFAULT: ft_alipay_decisions id
--
-- Name: ft_alipay_decisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_alipay_decisions ALTER COLUMN id SET DEFAULT nextval('public.ft_alipay_decisions_id_seq'::regclass);

-- DEFAULT: ft_alipay_positions id
--
-- Name: ft_alipay_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_alipay_positions ALTER COLUMN id SET DEFAULT nextval('public.ft_alipay_positions_id_seq'::regclass);

-- DEFAULT: ft_cache id
--
-- Name: ft_cache id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_cache ALTER COLUMN id SET DEFAULT nextval('public.ft_cache_id_seq'::regclass);

-- DEFAULT: ft_raw_data id
--
-- Name: ft_raw_data id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_raw_data ALTER COLUMN id SET DEFAULT nextval('public.ft_raw_data_id_seq'::regclass);

-- DEFAULT: ft_run_log id
--
-- Name: ft_run_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_run_log ALTER COLUMN id SET DEFAULT nextval('public.ft_run_log_id_seq'::regclass);

-- DEFAULT: ft_signals id
--
-- Name: ft_signals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_signals ALTER COLUMN id SET DEFAULT nextval('public.ft_signals_id_seq'::regclass);
