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


-- TABLE: ft_lessons
--
-- Name: ft_lessons; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_lessons (
    id integer NOT NULL,
    category character varying(20) NOT NULL,
    trigger_pattern text,
    expected_outcome text,
    actual_outcome text,
    lesson_text text NOT NULL,
    confidence character varying(10) DEFAULT 'low'::character varying,
    status character varying(10) DEFAULT 'active'::character varying,
    verify_count integer DEFAULT 1,
    success_count integer DEFAULT 0,
    related_sectors text[],
    tags jsonb,
    source_review_ids integer[],
    superseded_by integer,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);

-- SEQUENCE: ft_lessons_id_seq
--
-- Name: ft_lessons_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_lessons_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_lessons_id_seq
--
-- Name: ft_lessons_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_lessons_id_seq OWNED BY public.ft_lessons.id;

-- TABLE: ft_reviews
--
-- Name: ft_reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ft_reviews (
    id integer NOT NULL,
    decision_id integer,
    fund_code character varying(10) NOT NULL,
    decision_date date NOT NULL,
    decision_action character varying(10),
    decision_reason text,
    nav_at_decision numeric(10,4),
    nav_t1 numeric(10,4),
    nav_t2 numeric(10,4),
    change_t1_pct numeric(8,4),
    change_t2_pct numeric(8,4),
    outcome character varying(10) DEFAULT 'pending'::character varying,
    review_notes text,
    lesson_extracted boolean DEFAULT false,
    reviewed_at timestamp without time zone DEFAULT now(),
    decision_source character varying(20) DEFAULT 'llm'::character varying,
    dry_run boolean DEFAULT false
);

-- SEQUENCE: ft_reviews_id_seq
--
-- Name: ft_reviews_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ft_reviews_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- SEQUENCE OWNED BY: ft_reviews_id_seq
--
-- Name: ft_reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ft_reviews_id_seq OWNED BY public.ft_reviews.id;

-- DEFAULT: ft_lessons id
--
-- Name: ft_lessons id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_lessons ALTER COLUMN id SET DEFAULT nextval('public.ft_lessons_id_seq'::regclass);

-- DEFAULT: ft_reviews id
--
-- Name: ft_reviews id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ft_reviews ALTER COLUMN id SET DEFAULT nextval('public.ft_reviews_id_seq'::regclass);
