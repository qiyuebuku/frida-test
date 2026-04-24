-- AUTO-GENERATED from pg_dump
-- 来源: jettask 生产库 (119.23.227.187:5432/jettask)
-- 重构方案: docs/3. 实施方案/3. 系统重构/06-ORM与DDD重构方案.md
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


-- TABLE: ft_event_streams
--
-- Name: ft_event_streams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_event_streams (
    id integer NOT NULL,
    industry character varying(64) NOT NULL,
    state character varying(16) DEFAULT 'emerging'::character varying NOT NULL,
    momentum character varying(16) DEFAULT 'stable'::character varying,
    event_ids integer[] DEFAULT '{}'::integer[],
    event_count integer DEFAULT 0,
    avg_strength double precision DEFAULT 0,
    max_strength double precision DEFAULT 0,
    avg_sentiment double precision DEFAULT 0.5,
    first_event_at timestamp with time zone,
    last_event_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

-- SEQUENCE: ft_event_streams_id_seq
--
-- Name: ft_event_streams_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_event_streams_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_event_streams_id_seq
--
-- Name: ft_event_streams_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_event_streams_id_seq OWNED BY public.ft_event_streams.id;

-- TABLE: ft_events
--
-- Name: ft_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_events (
    id integer NOT NULL,
    title text NOT NULL,
    summary text DEFAULT ''::text,
    event_type character varying(32) NOT NULL,
    event_subtype character varying(32) DEFAULT ''::character varying,
    industries jsonb DEFAULT '[]'::jsonb,
    companies jsonb DEFAULT '[]'::jsonb,
    organizations jsonb DEFAULT '[]'::jsonb,
    regions jsonb DEFAULT '[]'::jsonb,
    impact_direction character varying(16),
    impact_strength double precision DEFAULT 0,
    impact_scope character varying(16),
    impact_duration character varying(16),
    sentiment double precision DEFAULT 0.5,
    novelty double precision DEFAULT 0.5,
    certainty double precision DEFAULT 0.5,
    source_news_ids integer[] DEFAULT '{}'::integer[],
    embedding bytea,
    embedding_model character varying(32) DEFAULT ''::character varying,
    sector_change_1d double precision,
    sector_change_3d double precision,
    sector_volume_change double precision,
    north_flow_1d double precision,
    reaction_delay_minutes integer,
    feedback_at timestamp with time zone,
    event_time timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    fingerprint character varying(64) NOT NULL,

    -- L1 新增字段（双路径事件识别）
    event_id uuid DEFAULT gen_random_uuid(),
    source_type character varying(16) DEFAULT 'text',
    source_table character varying(64) DEFAULT ''::character varying,
    evidence_refs jsonb DEFAULT '[]'::jsonb,
    schema_version character varying(16) DEFAULT 'v1.0'::character varying,
    extractor_version character varying(32) DEFAULT ''::character varying,
    affected_stocks jsonb DEFAULT '[]'::jsonb,
    affected_industries jsonb DEFAULT '[]'::jsonb,
    affected_concepts jsonb DEFAULT '[]'::jsonb,
    affected_regions jsonb DEFAULT '[]'::jsonb,
    quality_flags jsonb DEFAULT '[]'::jsonb,
    dedup_key character varying(128)
);

COMMENT ON COLUMN public.ft_events.feedback_at IS
  '首次尝试回填市场反应的时间戳（非完整性标志）。即使所有反应字段为 NULL，该时间戳也会被写入以避免重复拉取。下游模块不得用 feedback_at IS NOT NULL 判断字段完整性。';

-- SEQUENCE: ft_events_id_seq
--
-- Name: ft_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_events_id_seq
--
-- Name: ft_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_events_id_seq OWNED BY public.ft_events.id;

-- CONSTRAINT: ft_events event_id unique
ALTER TABLE ONLY public.ft_events ADD CONSTRAINT ft_events_event_id_key UNIQUE (event_id);

-- TABLE: ft_rule_thresholds
CREATE TABLE public.ft_rule_thresholds (
    id integer NOT NULL,
    rule_name character varying(128) NOT NULL,
    data_source character varying(64) NOT NULL,
    metric_name character varying(64) NOT NULL,
    window_days integer DEFAULT 90,
    percentile_95 double precision,
    percentile_99 double precision,
    sigma_value double precision,
    threshold_config jsonb DEFAULT '{}'::jsonb,
    last_computed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);

-- SEQUENCE: ft_rule_thresholds_id_seq
CREATE SEQUENCE public.ft_rule_thresholds_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.ft_rule_thresholds_id_seq OWNED BY public.ft_rule_thresholds.id;

-- CONSTRAINT: ft_rule_thresholds rule_name unique
ALTER TABLE ONLY public.ft_rule_thresholds ADD CONSTRAINT ft_rule_thresholds_rule_name_key UNIQUE (rule_name);

-- DEFAULT: ft_rule_thresholds id
ALTER TABLE ONLY public.ft_rule_thresholds ALTER COLUMN id SET DEFAULT nextval('public.ft_rule_thresholds_id_seq'::regclass);

-- DEFAULT: ft_event_streams id
--
-- Name: ft_event_streams id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_event_streams ALTER COLUMN id SET DEFAULT nextval('public.ft_event_streams_id_seq'::regclass);

-- DEFAULT: ft_events id
--
-- Name: ft_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_events ALTER COLUMN id SET DEFAULT nextval('public.ft_events_id_seq'::regclass);
