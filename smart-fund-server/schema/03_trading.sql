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


-- TABLE: ft_decisions
--
-- Name: ft_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_decisions (
    id integer NOT NULL,
    fund_code character varying(10) NOT NULL,
    fund_name character varying(100),
    action character varying(10) NOT NULL,
    amount numeric(12,2),
    sell_pct numeric(5,2),
    reason text,
    confidence character varying(10),
    market_view text,
    risk_notes text,
    referenced_lesson_ids integer[],
    decision_date date DEFAULT CURRENT_DATE,
    created_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_decisions_id_seq
--
-- Name: ft_decisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_decisions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_decisions_id_seq
--
-- Name: ft_decisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_decisions_id_seq OWNED BY public.ft_decisions.id;

-- TABLE: ft_fund_limits
--
-- Name: ft_fund_limits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_fund_limits (
    id integer NOT NULL,
    fund_code character varying(10) NOT NULL,
    fund_name character varying(100),
    min_buy numeric(12,2) DEFAULT 0,
    max_buy numeric(18,2) DEFAULT 0,
    daily_limit numeric(18,2) DEFAULT 0,
    is_suspended boolean DEFAULT false,
    suspend_reason text,
    last_checked_at timestamp without time zone DEFAULT now(),
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_fund_limits_id_seq
--
-- Name: ft_fund_limits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_fund_limits_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_fund_limits_id_seq
--
-- Name: ft_fund_limits_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_fund_limits_id_seq OWNED BY public.ft_fund_limits.id;

-- TABLE: ft_index_fund
--
-- Name: ft_index_fund; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_index_fund (
    id integer NOT NULL,
    index_code character varying(16) NOT NULL,
    fund_code character varying(16) NOT NULL,
    fund_name character varying(128),
    fund_type character varying(16) DEFAULT 'ETF'::character varying,
    fee_rate double precision DEFAULT 0,
    tracking_error double precision DEFAULT 0,
    liquidity_score double precision DEFAULT 0.5,
    created_at timestamp with time zone DEFAULT now()
);

-- SEQUENCE: ft_index_fund_id_seq
--
-- Name: ft_index_fund_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_index_fund_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_index_fund_id_seq
--
-- Name: ft_index_fund_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_index_fund_id_seq OWNED BY public.ft_index_fund.id;

-- TABLE: ft_industry_index
--
-- Name: ft_industry_index; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_industry_index (
    id integer NOT NULL,
    industry_id character varying(64) NOT NULL,
    index_code character varying(16) NOT NULL,
    index_name character varying(64) NOT NULL,
    weight double precision DEFAULT 1.0,
    created_at timestamp with time zone DEFAULT now()
);

-- SEQUENCE: ft_industry_index_id_seq
--
-- Name: ft_industry_index_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_industry_index_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_industry_index_id_seq
--
-- Name: ft_industry_index_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_industry_index_id_seq OWNED BY public.ft_industry_index.id;

-- TABLE: ft_industry_mapping
--
-- Name: ft_industry_mapping; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_industry_mapping (
    id integer NOT NULL,
    industry_id character varying(64) NOT NULL,
    industry_name character varying(64) NOT NULL,
    keywords jsonb DEFAULT '[]'::jsonb,
    aliases jsonb DEFAULT '[]'::jsonb,
    priority double precision DEFAULT 0.5,
    created_at timestamp with time zone DEFAULT now()
);

-- SEQUENCE: ft_industry_mapping_id_seq
--
-- Name: ft_industry_mapping_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_industry_mapping_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_industry_mapping_id_seq
--
-- Name: ft_industry_mapping_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_industry_mapping_id_seq OWNED BY public.ft_industry_mapping.id;

-- TABLE: ft_pending_decisions
--
-- Name: ft_pending_decisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_pending_decisions (
    id integer NOT NULL,
    fund_code character varying(10) NOT NULL,
    fund_name character varying(100),
    action character varying(10) NOT NULL,
    amount numeric(12,2),
    sell_pct numeric(5,2),
    reason text,
    confidence character varying(10),
    market_view text,
    market_phase character varying(20),
    risk_notes text,
    decision_date date DEFAULT CURRENT_DATE,
    status character varying(20) DEFAULT 'pending'::character varying,
    executed_at timestamp without time zone,
    cancelled_at timestamp without time zone,
    cancel_reason text,
    created_at timestamp without time zone DEFAULT now(),
    score double precision,
    score_breakdown jsonb DEFAULT '{}'::jsonb,
    decision_source character varying(20) DEFAULT 'llm'::character varying,
    dry_run boolean DEFAULT true
);

-- SEQUENCE: ft_pending_decisions_id_seq
--
-- Name: ft_pending_decisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_pending_decisions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_pending_decisions_id_seq
--
-- Name: ft_pending_decisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_pending_decisions_id_seq OWNED BY public.ft_pending_decisions.id;

-- TABLE: ft_positions
--
-- Name: ft_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_positions (
    id integer NOT NULL,
    fund_code character varying(10) NOT NULL,
    fund_name character varying(100),
    total_cost numeric(12,2) DEFAULT 0,
    shares numeric(14,4) DEFAULT 0,
    avg_cost numeric(10,4) DEFAULT 0,
    current_nav numeric(10,4) DEFAULT 0,
    market_value numeric(12,2) DEFAULT 0,
    profit_pct numeric(8,4) DEFAULT 0,
    first_buy_date date DEFAULT CURRENT_DATE,
    add_count integer DEFAULT 0,
    updated_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_positions_id_seq
--
-- Name: ft_positions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_positions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_positions_id_seq
--
-- Name: ft_positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_positions_id_seq OWNED BY public.ft_positions.id;

-- TABLE: ft_trades
--
-- Name: ft_trades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_trades (
    id integer NOT NULL,
    fund_code character varying(10) NOT NULL,
    fund_name character varying(100),
    action character varying(10) NOT NULL,
    amount numeric(12,2),
    shares numeric(14,4),
    order_no character varying(50),
    reason text,
    api_response jsonb,
    trade_date date DEFAULT CURRENT_DATE,
    created_at timestamp without time zone DEFAULT now(),
    dry_run boolean DEFAULT true,
    decision_id integer
);

-- SEQUENCE: ft_trades_id_seq
--
-- Name: ft_trades_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_trades_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_trades_id_seq
--
-- Name: ft_trades_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_trades_id_seq OWNED BY public.ft_trades.id;

-- DEFAULT: ft_decisions id
--
-- Name: ft_decisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_decisions ALTER COLUMN id SET DEFAULT nextval('public.ft_decisions_id_seq'::regclass);

-- DEFAULT: ft_fund_limits id
--
-- Name: ft_fund_limits id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_fund_limits ALTER COLUMN id SET DEFAULT nextval('public.ft_fund_limits_id_seq'::regclass);

-- DEFAULT: ft_index_fund id
--
-- Name: ft_index_fund id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_index_fund ALTER COLUMN id SET DEFAULT nextval('public.ft_index_fund_id_seq'::regclass);

-- DEFAULT: ft_industry_index id
--
-- Name: ft_industry_index id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_industry_index ALTER COLUMN id SET DEFAULT nextval('public.ft_industry_index_id_seq'::regclass);

-- DEFAULT: ft_industry_mapping id
--
-- Name: ft_industry_mapping id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_industry_mapping ALTER COLUMN id SET DEFAULT nextval('public.ft_industry_mapping_id_seq'::regclass);

-- DEFAULT: ft_pending_decisions id
--
-- Name: ft_pending_decisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_pending_decisions ALTER COLUMN id SET DEFAULT nextval('public.ft_pending_decisions_id_seq'::regclass);

-- DEFAULT: ft_positions id
--
-- Name: ft_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_positions ALTER COLUMN id SET DEFAULT nextval('public.ft_positions_id_seq'::regclass);

-- DEFAULT: ft_trades id
--
-- Name: ft_trades id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_trades ALTER COLUMN id SET DEFAULT nextval('public.ft_trades_id_seq'::regclass);
