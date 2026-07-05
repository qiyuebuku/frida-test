-- smart-fund-server PostgreSQL DDL snapshot
-- File date: 260705
-- Scope: business database + jettask-rs queue database, schema-only, no data.
-- Generated from live PostgreSQL system catalogs; review before applying as migration.


-- ==============================================================================
-- Database: business (jettask)
-- ==============================================================================

-- Snapshot generated at: 2026-07-05T01:43:46+08:00
-- Tables included: 55


-- ==============================================================================
-- business: extensions
-- ==============================================================================

CREATE EXTENSION IF NOT EXISTS "pg_trgm" WITH SCHEMA "public";


-- ==============================================================================
-- business: sequences
-- ==============================================================================

CREATE SEQUENCE "public"."ft_alipay_decisions_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_alipay_positions_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_cache_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_collection_state_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_decisions_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_event_streams_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_events_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_fund_limits_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_index_fund_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_industry_index_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_industry_mapping_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_lessons_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_macro_indicators_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_macro_regime_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_market_cache_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_market_flow_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_news_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_pending_decisions_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_positions_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_raw_data_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 9223372036854775807
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_reviews_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_rule_thresholds_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_run_log_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_sentiment_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_signals_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_trades_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."ft_watchlist_data_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."kg_graph_community_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 9223372036854775807
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."sa_ocr_records_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;

CREATE SEQUENCE "public"."sa_tasks_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 2147483647
    CACHE 1 NO CYCLE;


-- ==============================================================================
-- business: support functions
-- ==============================================================================

CREATE OR REPLACE FUNCTION public.ensure_ft_raw_data_partition(p_year integer, p_month integer)
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
    part_name text := format('ft_raw_data_%s%s', p_year, lpad(p_month::text, 2, '0'));
    start_date date := make_date(p_year, p_month, 1);
    end_date date := (make_date(p_year, p_month, 1) + interval '1 month')::date;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext(format('ft_raw_data:%s%s', p_year, lpad(p_month::text, 2, '0'))));
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF ft_raw_data FOR VALUES FROM (%L) TO (%L)',
        part_name,
        start_date,
        end_date
    );
END;
$function$


-- ==============================================================================
-- business: tables
-- ==============================================================================

CREATE TABLE "public"."ft_alipay_decisions" (
    "id" integer DEFAULT nextval('ft_alipay_decisions_id_seq'::regclass) NOT NULL,
    "fund_name" character varying(100) NOT NULL,
    "fund_code" character varying(10),
    "action" character varying(10) NOT NULL,
    "amount" numeric(12,2),
    "sell_pct" numeric(5,2),
    "reason" text,
    "confidence" character varying(10),
    "market_view" text,
    "decision_date" date DEFAULT CURRENT_DATE,
    "created_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_alipay_decisions_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_alipay_positions" (
    "id" integer DEFAULT nextval('ft_alipay_positions_id_seq'::regclass) NOT NULL,
    "fund_name" character varying(100) NOT NULL,
    "fund_code" character varying(10),
    "current_value" numeric(12,2) NOT NULL,
    "total_cost" numeric(12,2),
    "total_profit" numeric(12,2),
    "profit_rate" numeric(8,2),
    "daily_pnl" numeric(12,2),
    "category" character varying(20),
    "snapshot_date" date DEFAULT CURRENT_DATE NOT NULL,
    "created_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_alipay_positions_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_alipay_positions_fund_name_snapshot_date_key" UNIQUE (fund_name, snapshot_date)
);

CREATE TABLE "public"."ft_cache" (
    "id" integer DEFAULT nextval('ft_cache_id_seq'::regclass) NOT NULL,
    "fund_code" character varying(10) NOT NULL,
    "data_type" character varying(50) NOT NULL,
    "data" jsonb NOT NULL,
    "expires_at" timestamp without time zone NOT NULL,
    "created_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_cache_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_cache_fund_code_data_type_key" UNIQUE (fund_code, data_type)
);

CREATE TABLE "public"."ft_collection_state" (
    "id" integer DEFAULT nextval('ft_collection_state_id_seq'::regclass) NOT NULL,
    "aggregator" character varying(32) NOT NULL,
    "source_name" character varying(64) NOT NULL,
    "last_run_at" timestamp with time zone,
    "last_success_at" timestamp with time zone,
    "last_error" text DEFAULT ''::text,
    "consecutive_failures" integer DEFAULT 0,
    "enabled" boolean DEFAULT true,
    "interval_override" integer,
    "total_runs" bigint DEFAULT 0,
    "total_saved" bigint DEFAULT 0,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    "config" jsonb DEFAULT '{}'::jsonb,
    "mode" character varying(16) DEFAULT 'incremental'::character varying,
    "target_time" character varying(32),
    "newest_time" character varying(32),
    "oldest_time" character varying(32),
    "backfill_status" character varying(16),
    "cursor" jsonb,
    CONSTRAINT "ft_collection_state_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_collection_state_aggregator_source_name_key" UNIQUE (aggregator, source_name)
);

CREATE TABLE "public"."ft_config" (
    "id" integer DEFAULT 1 NOT NULL,
    "fund_pool" jsonb DEFAULT '[]'::jsonb,
    "total_capital" numeric(12,2) DEFAULT 20000,
    "max_single_position_pct" numeric(5,2) DEFAULT 30,
    "max_daily_buy_amount" numeric(12,2) DEFAULT 10000,
    "stop_loss_pct" numeric(5,2) DEFAULT '-20'::integer,
    "take_profit_pct" numeric(5,2) DEFAULT 50,
    "min_trade_amount" numeric(12,2) DEFAULT 10,
    "max_fund_count" integer DEFAULT 10,
    "min_cash_reserve" numeric(12,2) DEFAULT 1000,
    "cooldown_days" integer DEFAULT 1,
    "reverse_cooldown_days" integer DEFAULT 7,
    "min_hold_days" integer DEFAULT 7,
    "circuit_breaker_loss_pct" numeric(5,2) DEFAULT '-10'::integer,
    "circuit_breaker_loss_days" integer DEFAULT 5,
    "trade_cutoff_time" character varying(10) DEFAULT '14:50'::character varying,
    "trade_account" character varying(50) DEFAULT ''::character varying,
    "trade_password" character varying(100) DEFAULT ''::character varying,
    "alipay_fund_map" jsonb DEFAULT '{}'::jsonb,
    "auth_key1" character varying(100) DEFAULT ''::character varying,
    "auth_key2" character varying(100) DEFAULT ''::character varying,
    "auth_key3" character varying(100) DEFAULT ''::character varying,
    "auth_key4" character varying(20) DEFAULT 'auth'::character varying,
    "auth_key5" text DEFAULT ''::text,
    "auth_user_id" character varying(50) DEFAULT ''::character varying,
    "auth_session_id" character varying(200) DEFAULT ''::character varying,
    "auth_cookie" text DEFAULT ''::text,
    "auth_expires_at" bigint,
    "auth_last_sync" bigint,
    "auth_sync_source" character varying(50) DEFAULT ''::character varying,
    "updated_at" timestamp without time zone DEFAULT now(),
    "iwencai_hexin_v" text DEFAULT ''::text,
    "iwencai_hexin_v_time" bigint,
    "iwencai_hexin_v_source" character varying(50) DEFAULT ''::character varying,
    CONSTRAINT "ft_config_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_config_id_check" CHECK (id = 1)
);

CREATE TABLE "public"."ft_decisions" (
    "id" integer DEFAULT nextval('ft_decisions_id_seq'::regclass) NOT NULL,
    "fund_code" character varying(10) NOT NULL,
    "fund_name" character varying(100),
    "action" character varying(10) NOT NULL,
    "amount" numeric(12,2),
    "sell_pct" numeric(5,2),
    "reason" text,
    "confidence" character varying(10),
    "market_view" text,
    "risk_notes" text,
    "referenced_lesson_ids" integer[],
    "decision_date" date DEFAULT CURRENT_DATE,
    "created_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_decisions_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_event_streams" (
    "id" integer DEFAULT nextval('ft_event_streams_id_seq'::regclass) NOT NULL,
    "industry" character varying(64) NOT NULL,
    "state" character varying(16) DEFAULT 'emerging'::character varying NOT NULL,
    "momentum" character varying(16) DEFAULT 'stable'::character varying,
    "event_ids" integer[] DEFAULT '{}'::integer[],
    "event_count" integer DEFAULT 0,
    "avg_strength" double precision DEFAULT 0,
    "max_strength" double precision DEFAULT 0,
    "avg_sentiment" double precision DEFAULT 0.5,
    "first_event_at" timestamp with time zone,
    "last_event_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "ft_event_streams_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_events" (
    "id" integer DEFAULT nextval('ft_events_id_seq'::regclass) NOT NULL,
    "title" text NOT NULL,
    "summary" text DEFAULT ''::text,
    "event_type" character varying(32) NOT NULL,
    "event_subtype" character varying(32) DEFAULT ''::character varying,
    "industries" jsonb DEFAULT '[]'::jsonb,
    "companies" jsonb DEFAULT '[]'::jsonb,
    "organizations" jsonb DEFAULT '[]'::jsonb,
    "regions" jsonb DEFAULT '[]'::jsonb,
    "impact_direction" character varying(16),
    "impact_strength" double precision DEFAULT 0,
    "impact_scope" character varying(16),
    "impact_duration" character varying(16),
    "sentiment" double precision DEFAULT 0.5,
    "novelty" double precision DEFAULT 0.5,
    "certainty" double precision DEFAULT 0.5,
    "source_news_ids" integer[] DEFAULT '{}'::integer[],
    "embedding" bytea,
    "embedding_model" character varying(32) DEFAULT ''::character varying,
    "sector_change_1d" double precision,
    "sector_change_3d" double precision,
    "sector_volume_change" double precision,
    "north_flow_1d" double precision,
    "reaction_delay_minutes" integer,
    "feedback_at" timestamp with time zone,
    "event_time" timestamp with time zone NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "fingerprint" character varying(64) NOT NULL,
    "event_id" uuid DEFAULT gen_random_uuid(),
    "source_type" character varying(16) DEFAULT 'text'::character varying,
    "source_table" character varying(64) DEFAULT ''::character varying,
    "evidence_refs" jsonb DEFAULT '[]'::jsonb,
    "schema_version" character varying(16) DEFAULT 'v1.0'::character varying,
    "extractor_version" character varying(32) DEFAULT ''::character varying,
    "affected_stocks" jsonb DEFAULT '[]'::jsonb,
    "affected_industries" jsonb DEFAULT '[]'::jsonb,
    "affected_concepts" jsonb DEFAULT '[]'::jsonb,
    "affected_regions" jsonb DEFAULT '[]'::jsonb,
    "quality_flags" jsonb DEFAULT '[]'::jsonb,
    "dedup_key" character varying(128),
    CONSTRAINT "ft_events_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_events_dedup_key_key" UNIQUE (dedup_key),
    CONSTRAINT "ft_events_event_id_key" UNIQUE (event_id),
    CONSTRAINT "ft_events_fingerprint_key" UNIQUE (fingerprint)
);

CREATE TABLE "public"."ft_fund_limits" (
    "id" integer DEFAULT nextval('ft_fund_limits_id_seq'::regclass) NOT NULL,
    "fund_code" character varying(10) NOT NULL,
    "fund_name" character varying(100),
    "min_buy" numeric(12,2) DEFAULT 0,
    "max_buy" numeric(18,2) DEFAULT 0,
    "daily_limit" numeric(18,2) DEFAULT 0,
    "is_suspended" boolean DEFAULT false,
    "suspend_reason" text,
    "last_checked_at" timestamp without time zone DEFAULT now(),
    "created_at" timestamp without time zone DEFAULT now(),
    "updated_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_fund_limits_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_fund_limits_fund_code_key" UNIQUE (fund_code)
);

CREATE TABLE "public"."ft_index_fund" (
    "id" integer DEFAULT nextval('ft_index_fund_id_seq'::regclass) NOT NULL,
    "index_code" character varying(16) NOT NULL,
    "fund_code" character varying(16) NOT NULL,
    "fund_name" character varying(128),
    "fund_type" character varying(16) DEFAULT 'ETF'::character varying,
    "fee_rate" double precision DEFAULT 0,
    "tracking_error" double precision DEFAULT 0,
    "liquidity_score" double precision DEFAULT 0.5,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "ft_index_fund_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_index_fund_index_code_fund_code_key" UNIQUE (index_code, fund_code)
);

CREATE TABLE "public"."ft_industry_index" (
    "id" integer DEFAULT nextval('ft_industry_index_id_seq'::regclass) NOT NULL,
    "industry_id" character varying(64) NOT NULL,
    "index_code" character varying(16) NOT NULL,
    "index_name" character varying(64) NOT NULL,
    "weight" double precision DEFAULT 1.0,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "ft_industry_index_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_industry_index_industry_id_index_code_key" UNIQUE (industry_id, index_code)
);

CREATE TABLE "public"."ft_industry_mapping" (
    "id" integer DEFAULT nextval('ft_industry_mapping_id_seq'::regclass) NOT NULL,
    "industry_id" character varying(64) NOT NULL,
    "industry_name" character varying(64) NOT NULL,
    "keywords" jsonb DEFAULT '[]'::jsonb,
    "aliases" jsonb DEFAULT '[]'::jsonb,
    "priority" double precision DEFAULT 0.5,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "ft_industry_mapping_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_industry_mapping_industry_id_key" UNIQUE (industry_id)
);

CREATE TABLE "public"."ft_lessons" (
    "id" integer DEFAULT nextval('ft_lessons_id_seq'::regclass) NOT NULL,
    "category" character varying(20) NOT NULL,
    "trigger_pattern" text,
    "expected_outcome" text,
    "actual_outcome" text,
    "lesson_text" text NOT NULL,
    "confidence" character varying(10) DEFAULT 'low'::character varying,
    "status" character varying(10) DEFAULT 'active'::character varying,
    "verify_count" integer DEFAULT 1,
    "success_count" integer DEFAULT 0,
    "related_sectors" text[],
    "tags" jsonb,
    "source_review_ids" integer[],
    "superseded_by" integer,
    "created_at" timestamp without time zone DEFAULT now(),
    "updated_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_lessons_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_macro_indicators" (
    "id" integer DEFAULT nextval('ft_macro_indicators_id_seq'::regclass) NOT NULL,
    "indicator" character varying(32) NOT NULL,
    "period" character varying(16) NOT NULL,
    "value" double precision NOT NULL,
    "unit" character varying(16) DEFAULT ''::character varying,
    "prev_value" double precision,
    "source" character varying(32) DEFAULT ''::character varying,
    "published_at" date,
    "created_at" timestamp with time zone DEFAULT now(),
    "dim_tag" character varying(16) DEFAULT ''::character varying,
    "yoy" double precision,
    "mom" double precision,
    CONSTRAINT "ft_macro_indicators_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_macro_indicators_indicator_period_source_key" UNIQUE (indicator, period, source)
);

CREATE TABLE "public"."ft_macro_regime" (
    "id" integer DEFAULT nextval('ft_macro_regime_id_seq'::regclass) NOT NULL,
    "computed_at" timestamp with time zone DEFAULT now(),
    "snapshot_date" date NOT NULL,
    "regime" character varying(16) NOT NULL,
    "overall_score" double precision NOT NULL,
    "multiplier" double precision NOT NULL,
    "liquidity_score" double precision DEFAULT 0 NOT NULL,
    "growth_score" double precision DEFAULT 0 NOT NULL,
    "inflation_score" double precision DEFAULT 0 NOT NULL,
    "external_score" double precision DEFAULT 0 NOT NULL,
    "policy_score" double precision DEFAULT 0 NOT NULL,
    "contributors" jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT "ft_macro_regime_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_macro_regime_snapshot_date_key" UNIQUE (snapshot_date)
);

CREATE TABLE "public"."ft_market_cache" (
    "id" integer DEFAULT nextval('ft_market_cache_id_seq'::regclass) NOT NULL,
    "data_type" character varying(50) NOT NULL,
    "data" jsonb NOT NULL,
    "expires_at" timestamp without time zone NOT NULL,
    "created_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_market_cache_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_market_cache_data_type_key" UNIQUE (data_type)
);

CREATE TABLE "public"."ft_market_flow" (
    "id" integer DEFAULT nextval('ft_market_flow_id_seq'::regclass) NOT NULL,
    "data_type" character varying(32) NOT NULL,
    "trade_date" date NOT NULL,
    "data" jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "ft_market_flow_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_news" (
    "id" integer DEFAULT nextval('ft_news_id_seq'::regclass) NOT NULL,
    "title" text NOT NULL,
    "content" text DEFAULT ''::text,
    "source" character varying(32) NOT NULL,
    "source_name" character varying(50) DEFAULT ''::character varying,
    "source_reliability" double precision DEFAULT 0.5,
    "category" character varying(20) DEFAULT ''::character varying,
    "url" text DEFAULT ''::text,
    "tags" jsonb DEFAULT '[]'::jsonb,
    "related_stocks" jsonb DEFAULT '[]'::jsonb,
    "published_at" timestamp with time zone NOT NULL,
    "fingerprint" character varying(64) NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "summary" text DEFAULT ''::text,
    "event_extracted" boolean DEFAULT false,
    "l1_classified_at" timestamp with time zone,
    CONSTRAINT "ft_news_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_pending_decisions" (
    "id" integer DEFAULT nextval('ft_pending_decisions_id_seq'::regclass) NOT NULL,
    "fund_code" character varying(10) NOT NULL,
    "fund_name" character varying(100),
    "action" character varying(10) NOT NULL,
    "amount" numeric(12,2),
    "sell_pct" numeric(5,2),
    "reason" text,
    "confidence" character varying(10),
    "market_view" text,
    "market_phase" character varying(20),
    "risk_notes" text,
    "decision_date" date DEFAULT CURRENT_DATE,
    "status" character varying(20) DEFAULT 'pending'::character varying,
    "executed_at" timestamp without time zone,
    "cancelled_at" timestamp without time zone,
    "cancel_reason" text,
    "created_at" timestamp without time zone DEFAULT now(),
    "event_stream_id" integer,
    "source_event_ids" integer[],
    "score" double precision,
    "score_breakdown" jsonb DEFAULT '{}'::jsonb,
    "decision_source" character varying(20) DEFAULT 'llm'::character varying,
    "dry_run" boolean DEFAULT true,
    CONSTRAINT "ft_pending_decisions_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_positions" (
    "id" integer DEFAULT nextval('ft_positions_id_seq'::regclass) NOT NULL,
    "fund_code" character varying(10) NOT NULL,
    "fund_name" character varying(100),
    "total_cost" numeric(12,2) DEFAULT 0,
    "shares" numeric(14,4) DEFAULT 0,
    "avg_cost" numeric(10,4) DEFAULT 0,
    "current_nav" numeric(10,4) DEFAULT 0,
    "market_value" numeric(12,2) DEFAULT 0,
    "profit_pct" numeric(8,4) DEFAULT 0,
    "first_buy_date" date DEFAULT CURRENT_DATE,
    "add_count" integer DEFAULT 0,
    "updated_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_positions_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_positions_fund_code_key" UNIQUE (fund_code)
);

CREATE TABLE "public"."ft_reviews" (
    "id" integer DEFAULT nextval('ft_reviews_id_seq'::regclass) NOT NULL,
    "decision_id" integer,
    "fund_code" character varying(10) NOT NULL,
    "decision_date" date NOT NULL,
    "decision_action" character varying(10),
    "decision_reason" text,
    "nav_at_decision" numeric(10,4),
    "nav_t1" numeric(10,4),
    "nav_t2" numeric(10,4),
    "change_t1_pct" numeric(8,4),
    "change_t2_pct" numeric(8,4),
    "outcome" character varying(10) DEFAULT 'pending'::character varying,
    "review_notes" text,
    "lesson_extracted" boolean DEFAULT false,
    "reviewed_at" timestamp without time zone DEFAULT now(),
    "decision_source" character varying(20) DEFAULT 'llm'::character varying,
    "dry_run" boolean DEFAULT false,
    CONSTRAINT "ft_reviews_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_rule_thresholds" (
    "id" integer DEFAULT nextval('ft_rule_thresholds_id_seq'::regclass) NOT NULL,
    "rule_name" character varying(128) NOT NULL,
    "data_source" character varying(64) NOT NULL,
    "metric_name" character varying(64) NOT NULL,
    "window_days" integer DEFAULT 90,
    "percentile_95" double precision,
    "percentile_99" double precision,
    "sigma_value" double precision,
    "threshold_config" jsonb DEFAULT '{}'::jsonb,
    "last_computed_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "ft_rule_thresholds_pkey" PRIMARY KEY (id),
    CONSTRAINT "ft_rule_thresholds_rule_name_key" UNIQUE (rule_name)
);

CREATE TABLE "public"."ft_run_log" (
    "id" integer DEFAULT nextval('ft_run_log_id_seq'::regclass) NOT NULL,
    "run_date" date DEFAULT CURRENT_DATE,
    "decisions_count" integer DEFAULT 0,
    "trades_count" integer DEFAULT 0,
    "summary" text,
    "created_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_run_log_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_sentiment" (
    "id" integer DEFAULT nextval('ft_sentiment_id_seq'::regclass) NOT NULL,
    "data_type" character varying(32) NOT NULL,
    "trade_date" date NOT NULL,
    "data" jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "ft_sentiment_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_signals" (
    "id" integer DEFAULT nextval('ft_signals_id_seq'::regclass) NOT NULL,
    "fund_code" character varying(10) NOT NULL,
    "strategy" character varying(50) DEFAULT 'default'::character varying,
    "action" character varying(10),
    "confidence" character varying(10),
    "indicators" jsonb,
    "signal_date" date DEFAULT CURRENT_DATE,
    "created_at" timestamp without time zone DEFAULT now(),
    CONSTRAINT "ft_signals_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_trades" (
    "id" integer DEFAULT nextval('ft_trades_id_seq'::regclass) NOT NULL,
    "fund_code" character varying(10) NOT NULL,
    "fund_name" character varying(100),
    "action" character varying(10) NOT NULL,
    "amount" numeric(12,2),
    "shares" numeric(14,4),
    "order_no" character varying(50),
    "reason" text,
    "api_response" jsonb,
    "trade_date" date DEFAULT CURRENT_DATE,
    "created_at" timestamp without time zone DEFAULT now(),
    "dry_run" boolean DEFAULT true,
    "decision_id" integer,
    CONSTRAINT "ft_trades_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_watchlist_data" (
    "id" integer DEFAULT nextval('ft_watchlist_data_id_seq'::regclass) NOT NULL,
    "code" character varying(16) NOT NULL,
    "data_type" character varying(32) NOT NULL,
    "trade_date" date,
    "data" jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "ft_watchlist_data_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."kg_assignment_candidate_orders" (
    "order_id" character varying(180) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "target" character varying(32) NOT NULL,
    "query_key" character varying(96) NOT NULL,
    "ordered_community_ids" jsonb NOT NULL,
    "payload" jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_assignment_candidate_orders_pkey" PRIMARY KEY (order_id),
    CONSTRAINT "uq_kg_assignment_candidate_orders_scope" UNIQUE (adapter_name, target, query_key)
);

CREATE TABLE "public"."kg_cognitive_cards" (
    "cognitive_card_id" character varying(180) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "source_type" character varying(64) DEFAULT ''::character varying NOT NULL,
    "source_id" character varying(256) DEFAULT ''::character varying NOT NULL,
    "evidence_id" character varying(180) NOT NULL,
    "primary_chunk_id" character varying(220) NOT NULL,
    "chunk_ids" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "chunk_index" integer DEFAULT 0 NOT NULL,
    "summary" text DEFAULT ''::text NOT NULL,
    "title_candidates" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "topic_intents" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "risk_signals" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "local_impact_signals" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "actor_signals" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "supporting_text" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "system_pointers" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "schema_version" character varying(96) DEFAULT ''::character varying NOT NULL,
    "status" character varying(32) DEFAULT 'active'::character varying NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_cognitive_cards_pkey" PRIMARY KEY (cognitive_card_id)
);

CREATE TABLE "public"."kg_community_assignments" (
    "assignment_id" character varying(180) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "cognitive_card_id" character varying(180) NOT NULL,
    "intent_index" integer DEFAULT 0 NOT NULL,
    "intent_id" character varying(220) DEFAULT ''::character varying NOT NULL,
    "community_id" character varying(180) DEFAULT ''::character varying NOT NULL,
    "action" character varying(64) DEFAULT ''::character varying NOT NULL,
    "weight" double precision DEFAULT 0.0 NOT NULL,
    "confidence" double precision DEFAULT 0.0 NOT NULL,
    "matched_reason" text DEFAULT ''::text NOT NULL,
    "update_mode" character varying(32) DEFAULT ''::character varying NOT NULL,
    "reason" text DEFAULT ''::text NOT NULL,
    "topic_intent" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "decision" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "status" character varying(32) DEFAULT 'active'::character varying NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_community_assignments_pkey" PRIMARY KEY (assignment_id)
);

CREATE TABLE "public"."kg_compilation_runs" (
    "run_id" character varying(128) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "adapter_version" character varying(32) DEFAULT ''::character varying NOT NULL,
    "source_batch_id" character varying(128) DEFAULT ''::character varying NOT NULL,
    "status" character varying(32) NOT NULL,
    "started_at" timestamp with time zone DEFAULT now(),
    "finished_at" timestamp with time zone,
    "input_count" integer DEFAULT 0 NOT NULL,
    "node_count" integer DEFAULT 0 NOT NULL,
    "edge_count" integer DEFAULT 0 NOT NULL,
    "evidence_count" integer DEFAULT 0 NOT NULL,
    "failed_count" integer DEFAULT 0 NOT NULL,
    "metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT "kg_compilation_runs_pkey" PRIMARY KEY (run_id)
);

CREATE TABLE "public"."kg_evidence" (
    "evidence_id" character varying(180) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "source_type" character varying(64) NOT NULL,
    "source_id" character varying(256) NOT NULL,
    "evidence_type" character varying(32) NOT NULL,
    "content" text,
    "payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "span_start" integer,
    "span_end" integer,
    "version" character varying(64) NOT NULL,
    "metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "status" character varying(32) DEFAULT 'active'::character varying NOT NULL,
    "source_fingerprint" character varying(128),
    "superseded_by" character varying(180),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_evidence_pkey" PRIMARY KEY (evidence_id)
);

CREATE TABLE "public"."kg_evidence_chunks" (
    "chunk_id" character varying(220) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "evidence_id" character varying(180) NOT NULL,
    "chunk_index" integer DEFAULT 0 NOT NULL,
    "start_offset" integer,
    "end_offset" integer,
    "previous_chunk_id" character varying(220),
    "next_chunk_id" character varying(220),
    "text_hash" character varying(64) DEFAULT ''::character varying NOT NULL,
    "chunker_version" character varying(64) DEFAULT ''::character varying NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_evidence_chunks_pkey" PRIMARY KEY (chunk_id)
);

CREATE TABLE "public"."kg_graph_communities" (
    "community_id" character varying(180) NOT NULL,
    "version_id" character varying(220) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "projection" character varying(64) NOT NULL,
    "level" integer NOT NULL,
    "parent_community_id" character varying(180) NOT NULL,
    "title" text NOT NULL,
    "summary" text NOT NULL,
    "member_node_ids" jsonb NOT NULL,
    "member_edge_ids" jsonb NOT NULL,
    "evidence_ids" jsonb NOT NULL,
    "chunk_ids" jsonb NOT NULL,
    "metrics" jsonb NOT NULL,
    "status" character varying(32) NOT NULL,
    "previous_version_id" character varying(220) NOT NULL,
    "change_reason" character varying(64) NOT NULL,
    "lineage_id" character varying(180) NOT NULL,
    "previous_community_ids" jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    "id" bigint DEFAULT nextval('kg_graph_community_id_seq'::regclass),
    CONSTRAINT "kg_graph_communities_pkey" PRIMARY KEY (community_id)
);

CREATE TABLE "public"."kg_graph_deltas" (
    "delta_id" character varying(180) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "projection" character varying(64) NOT NULL,
    "window_name" character varying(32) NOT NULL,
    "started_at" timestamp with time zone NOT NULL,
    "ended_at" timestamp with time zone NOT NULL,
    "title" text NOT NULL,
    "summary" text NOT NULL,
    "community_ids" jsonb NOT NULL,
    "finding_ids" jsonb NOT NULL,
    "cited_chunk_ids" jsonb NOT NULL,
    "cited_evidence_ids" jsonb NOT NULL,
    "supporting_edge_ids" jsonb NOT NULL,
    "node_ids" jsonb NOT NULL,
    "metrics" jsonb NOT NULL,
    "status" character varying(32) NOT NULL,
    "version" character varying(220) NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_graph_deltas_pkey" PRIMARY KEY (delta_id)
);

CREATE TABLE "public"."kg_graph_findings" (
    "finding_id" character varying(180) NOT NULL,
    "community_id" character varying(180) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "projection" character varying(64) NOT NULL,
    "finding_type" character varying(64) NOT NULL,
    "title" text NOT NULL,
    "statement" text NOT NULL,
    "cited_chunk_ids" jsonb NOT NULL,
    "cited_evidence_ids" jsonb NOT NULL,
    "supporting_edge_ids" jsonb NOT NULL,
    "node_ids" jsonb NOT NULL,
    "confidence" double precision NOT NULL,
    "status" character varying(32) NOT NULL,
    "version" character varying(220) NOT NULL,
    "payload" jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_graph_findings_pkey" PRIMARY KEY (finding_id)
);

CREATE TABLE "public"."kg_graph_unassigned_signals" (
    "signal_id" character varying(180) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "projection" character varying(64) NOT NULL,
    "title" text DEFAULT ''::text NOT NULL,
    "reason" character varying(96) DEFAULT ''::character varying NOT NULL,
    "node_ids" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "edge_ids" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "evidence_ids" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "chunk_ids" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "topic_tags" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "impact_tags" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "event_type_tags" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "relation_types" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "support_score" double precision DEFAULT 0.0 NOT NULL,
    "metrics" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "status" character varying(32) DEFAULT 'active'::character varying NOT NULL,
    "promoted_community_id" character varying(180) DEFAULT ''::character varying NOT NULL,
    "promotion_attempts" integer DEFAULT 0 NOT NULL,
    "last_checked_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_graph_unassigned_signals_pkey" PRIMARY KEY (signal_id)
);

CREATE TABLE "public"."kg_normalization_rules" (
    "rule_id" character varying(160) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "rule_type" character varying(64) NOT NULL,
    "raw_value" text NOT NULL,
    "canonical_value" text NOT NULL,
    "status" character varying(32) NOT NULL,
    "confidence" double precision NOT NULL,
    "source" character varying(64) NOT NULL,
    "version" character varying(64) NOT NULL,
    "payload" jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_normalization_rules_pkey" PRIMARY KEY (rule_id),
    CONSTRAINT "uq_kg_normalization_rule_active_key" UNIQUE (adapter_name, rule_type, raw_value, status)
);

CREATE TABLE "public"."kg_retrieval_eval_metrics" (
    "metric_id" character varying(128) NOT NULL,
    "run_id" character varying(128) NOT NULL,
    "case_id" character varying(128) NOT NULL,
    "query" text NOT NULL,
    "metrics" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "failure_stage" character varying(64),
    "failure_details" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_retrieval_eval_metrics_pkey" PRIMARY KEY (metric_id),
    CONSTRAINT "uq_kg_retrieval_eval_metrics_run_case" UNIQUE (run_id, case_id)
);

CREATE TABLE "public"."kg_retrieval_eval_runs" (
    "run_id" character varying(128) NOT NULL,
    "strategy_name" character varying(64) NOT NULL,
    "strategy_version" character varying(64) NOT NULL,
    "status" character varying(32) NOT NULL,
    "config" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "aggregate_metrics" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "started_at" timestamp with time zone DEFAULT now(),
    "finished_at" timestamp with time zone,
    CONSTRAINT "kg_retrieval_eval_runs_pkey" PRIMARY KEY (run_id)
);

CREATE TABLE "public"."kg_retrieval_labels" (
    "label_id" character varying(128) NOT NULL,
    "snapshot_id" character varying(128),
    "case_id" character varying(128),
    "query" text NOT NULL,
    "expected_candidates" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "expected_answers" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "expected_evidence_refs" jsonb DEFAULT '[]'::jsonb NOT NULL,
    "coverage_requirements" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "failure_stage" character varying(64),
    "notes" text DEFAULT ''::text NOT NULL,
    "created_by" character varying(128) DEFAULT ''::character varying NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_retrieval_labels_pkey" PRIMARY KEY (label_id)
);

CREATE TABLE "public"."kg_retrieval_trace_snapshots" (
    "snapshot_id" character varying(128) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "target" character varying(16) DEFAULT 'prod'::character varying NOT NULL,
    "query" text NOT NULL,
    "query_hash" character varying(64) NOT NULL,
    "strategy_name" character varying(64) NOT NULL,
    "strategy_version" character varying(64) NOT NULL,
    "query_snapshot" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "recall_snapshot" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "package_snapshot" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "ranking_snapshot" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "judge_snapshot" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "context_snapshot" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "stop_snapshot" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_retrieval_trace_snapshots_pkey" PRIMARY KEY (snapshot_id)
);

CREATE TABLE "public"."kg_review_items" (
    "review_id" character varying(128) NOT NULL,
    "object_type" character varying(32) NOT NULL,
    "object_id" character varying(180) NOT NULL,
    "severity" character varying(16) NOT NULL,
    "reason" text NOT NULL,
    "status" character varying(32) DEFAULT 'open'::character varying NOT NULL,
    "payload" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    "updated_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_review_items_pkey" PRIMARY KEY (review_id)
);

CREATE TABLE "public"."kg_versions" (
    "version_id" character varying(128) NOT NULL,
    "adapter_name" character varying(64) NOT NULL,
    "adapter_version" character varying(32) DEFAULT ''::character varying NOT NULL,
    "schema_version" character varying(64) DEFAULT ''::character varying NOT NULL,
    "compiler_version" character varying(64) DEFAULT ''::character varying NOT NULL,
    "rules_hash" character varying(64) DEFAULT ''::character varying NOT NULL,
    "prompt_hash" character varying(64) DEFAULT ''::character varying NOT NULL,
    "metadata" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "created_at" timestamp with time zone DEFAULT now(),
    CONSTRAINT "kg_versions_pkey" PRIMARY KEY (version_id)
);

CREATE TABLE "public"."sa_ocr_records" (
    "id" integer DEFAULT nextval('sa_ocr_records_id_seq'::regclass) NOT NULL,
    "action" character varying(30) NOT NULL,
    "raw_text" text,
    "markdown_text" text,
    "image_path" character varying(500),
    "client_id" character varying(100),
    "created_at" timestamp without time zone DEFAULT now(),
    "structured_data" text,
    "flat_data" jsonb,
    CONSTRAINT "sa_ocr_records_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."sa_tasks" (
    "id" integer DEFAULT nextval('sa_tasks_id_seq'::regclass) NOT NULL,
    "task_type" character varying(30) NOT NULL,
    "status" character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    "progress" integer DEFAULT 0 NOT NULL,
    "progress_msg" character varying(200),
    "input_type" character varying(20),
    "image_path" character varying(500),
    "ocr_record_id" integer,
    "title" character varying(200),
    "summary" character varying(500),
    "result" text,
    "result_data" jsonb,
    "client_id" character varying(100),
    "error_msg" text,
    "created_at" timestamp without time zone DEFAULT now(),
    "started_at" timestamp without time zone,
    "completed_at" timestamp without time zone,
    "duration_sec" integer,
    "partial_result" text,
    "tool_calls" jsonb,
    "config" jsonb,
    "skill_name" character varying(64),
    "command_id" character varying(64),
    "session_id" character varying(64),
    "messages" jsonb DEFAULT '[]'::jsonb,
    "terminal_log" text,
    "terminal_log_expanded" text,
    CONSTRAINT "sa_tasks_pkey" PRIMARY KEY (id)
);

CREATE TABLE "public"."ft_raw_data" (
    "id" bigint DEFAULT nextval('ft_raw_data_id_seq'::regclass) NOT NULL,
    "source" character varying(32) NOT NULL,
    "method" character varying(64) NOT NULL,
    "params_hash" character varying(64) NOT NULL,
    "params" jsonb DEFAULT '{}'::jsonb NOT NULL,
    "data" jsonb NOT NULL,
    "data_count" integer DEFAULT 0,
    "fetched_at" timestamp with time zone DEFAULT now() NOT NULL,
    "expires_at" timestamp with time zone,
    "api_latency_ms" integer,
    "source_name" character varying(50) DEFAULT ''::character varying,
    "data_domain" character varying(32) DEFAULT ''::character varying,
    "data_frequency" character varying(16) DEFAULT ''::character varying,
    "market" character varying(16) DEFAULT ''::character varying,
    "related_codes" jsonb DEFAULT '[]'::jsonb,
    "trade_date" date,
    "is_success" boolean DEFAULT true,
    "error_msg" text DEFAULT ''::text,
    CONSTRAINT "ft_raw_data_pkey" PRIMARY KEY (id, fetched_at)
) PARTITION BY RANGE (fetched_at);


-- ==============================================================================
-- business: partitions
-- ==============================================================================

CREATE TABLE "public"."ft_raw_data_202603" PARTITION OF "public"."ft_raw_data"
    FOR VALUES FROM ('2026-02-28 16:00:00+00') TO ('2026-03-31 16:00:00+00');

CREATE TABLE "public"."ft_raw_data_202604" PARTITION OF "public"."ft_raw_data"
    FOR VALUES FROM ('2026-03-31 16:00:00+00') TO ('2026-04-30 16:00:00+00');

CREATE TABLE "public"."ft_raw_data_202605" PARTITION OF "public"."ft_raw_data"
    FOR VALUES FROM ('2026-04-30 16:00:00+00') TO ('2026-05-31 16:00:00+00');

CREATE TABLE "public"."ft_raw_data_202606" PARTITION OF "public"."ft_raw_data"
    FOR VALUES FROM ('2026-05-31 16:00:00+00') TO ('2026-06-30 16:00:00+00');

CREATE TABLE "public"."ft_raw_data_202607" PARTITION OF "public"."ft_raw_data"
    FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00');

CREATE TABLE "public"."ft_raw_data_202608" PARTITION OF "public"."ft_raw_data"
    FOR VALUES FROM ('2026-08-01 00:00:00+00') TO ('2026-09-01 00:00:00+00');

CREATE TABLE "public"."ft_raw_data_202609" PARTITION OF "public"."ft_raw_data"
    FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');

CREATE TABLE "public"."ft_raw_data_202610" PARTITION OF "public"."ft_raw_data"
    FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');


-- ==============================================================================
-- business: sequence ownership
-- ==============================================================================

ALTER SEQUENCE "public"."ft_alipay_decisions_id_seq" OWNED BY "public"."ft_alipay_decisions"."id";
ALTER SEQUENCE "public"."ft_alipay_positions_id_seq" OWNED BY "public"."ft_alipay_positions"."id";
ALTER SEQUENCE "public"."ft_cache_id_seq" OWNED BY "public"."ft_cache"."id";
ALTER SEQUENCE "public"."ft_collection_state_id_seq" OWNED BY "public"."ft_collection_state"."id";
ALTER SEQUENCE "public"."ft_decisions_id_seq" OWNED BY "public"."ft_decisions"."id";
ALTER SEQUENCE "public"."ft_event_streams_id_seq" OWNED BY "public"."ft_event_streams"."id";
ALTER SEQUENCE "public"."ft_events_id_seq" OWNED BY "public"."ft_events"."id";
ALTER SEQUENCE "public"."ft_fund_limits_id_seq" OWNED BY "public"."ft_fund_limits"."id";
ALTER SEQUENCE "public"."ft_index_fund_id_seq" OWNED BY "public"."ft_index_fund"."id";
ALTER SEQUENCE "public"."ft_industry_index_id_seq" OWNED BY "public"."ft_industry_index"."id";
ALTER SEQUENCE "public"."ft_industry_mapping_id_seq" OWNED BY "public"."ft_industry_mapping"."id";
ALTER SEQUENCE "public"."ft_lessons_id_seq" OWNED BY "public"."ft_lessons"."id";
ALTER SEQUENCE "public"."ft_macro_indicators_id_seq" OWNED BY "public"."ft_macro_indicators"."id";
ALTER SEQUENCE "public"."ft_macro_regime_id_seq" OWNED BY "public"."ft_macro_regime"."id";
ALTER SEQUENCE "public"."ft_market_cache_id_seq" OWNED BY "public"."ft_market_cache"."id";
ALTER SEQUENCE "public"."ft_market_flow_id_seq" OWNED BY "public"."ft_market_flow"."id";
ALTER SEQUENCE "public"."ft_news_id_seq" OWNED BY "public"."ft_news"."id";
ALTER SEQUENCE "public"."ft_pending_decisions_id_seq" OWNED BY "public"."ft_pending_decisions"."id";
ALTER SEQUENCE "public"."ft_positions_id_seq" OWNED BY "public"."ft_positions"."id";
ALTER SEQUENCE "public"."ft_raw_data_id_seq" OWNED BY "public"."ft_raw_data"."id";
ALTER SEQUENCE "public"."ft_reviews_id_seq" OWNED BY "public"."ft_reviews"."id";
ALTER SEQUENCE "public"."ft_rule_thresholds_id_seq" OWNED BY "public"."ft_rule_thresholds"."id";
ALTER SEQUENCE "public"."ft_run_log_id_seq" OWNED BY "public"."ft_run_log"."id";
ALTER SEQUENCE "public"."ft_sentiment_id_seq" OWNED BY "public"."ft_sentiment"."id";
ALTER SEQUENCE "public"."ft_signals_id_seq" OWNED BY "public"."ft_signals"."id";
ALTER SEQUENCE "public"."ft_trades_id_seq" OWNED BY "public"."ft_trades"."id";
ALTER SEQUENCE "public"."ft_watchlist_data_id_seq" OWNED BY "public"."ft_watchlist_data"."id";
ALTER SEQUENCE "public"."sa_ocr_records_id_seq" OWNED BY "public"."sa_ocr_records"."id";
ALTER SEQUENCE "public"."sa_tasks_id_seq" OWNED BY "public"."sa_tasks"."id";


-- ==============================================================================
-- business: indexes
-- ==============================================================================

CREATE INDEX idx_ft_alipay_decisions_date ON public.ft_alipay_decisions USING btree (decision_date);
CREATE INDEX idx_ft_alipay_date ON public.ft_alipay_positions USING btree (snapshot_date);
CREATE INDEX idx_ft_alipay_name ON public.ft_alipay_positions USING btree (fund_name);
CREATE INDEX idx_ft_cache_lookup ON public.ft_cache USING btree (fund_code, data_type);
CREATE INDEX idx_ft_collection_state_enabled ON public.ft_collection_state USING btree (enabled, aggregator);
CREATE INDEX idx_ft_decisions_date ON public.ft_decisions USING btree (decision_date);
CREATE INDEX idx_ft_event_streams_industry ON public.ft_event_streams USING btree (industry, last_event_at DESC);
CREATE INDEX idx_ft_event_streams_state ON public.ft_event_streams USING btree (state, momentum);
CREATE INDEX idx_ft_events_companies ON public.ft_events USING gin (companies);
CREATE INDEX idx_ft_events_event_subtype ON public.ft_events USING btree (event_subtype, event_time DESC) WHERE ((event_subtype IS NOT NULL) AND ((event_subtype)::text <> ''::text));
CREATE INDEX idx_ft_events_event_time ON public.ft_events USING btree (event_time DESC);
CREATE INDEX idx_ft_events_industries ON public.ft_events USING gin (industries);
CREATE INDEX idx_ft_events_source_type ON public.ft_events USING btree (source_type, event_time DESC);
CREATE INDEX idx_ft_events_type ON public.ft_events USING btree (event_type, event_time);
CREATE INDEX idx_ft_fund_limits_code ON public.ft_fund_limits USING btree (fund_code);
CREATE INDEX idx_ft_index_fund_index ON public.ft_index_fund USING btree (index_code);
CREATE INDEX idx_ft_industry_keywords ON public.ft_industry_mapping USING gin (keywords);
CREATE INDEX idx_ft_lessons_category ON public.ft_lessons USING btree (category);
CREATE INDEX idx_ft_lessons_status ON public.ft_lessons USING btree (status);
CREATE INDEX idx_ft_macro_ind_period ON public.ft_macro_indicators USING btree (indicator, period);
CREATE INDEX idx_macro_indicators_dim ON public.ft_macro_indicators USING btree (dim_tag, published_at DESC);
CREATE INDEX idx_macro_regime_date ON public.ft_macro_regime USING btree (snapshot_date DESC);
CREATE INDEX idx_ft_market_cache_type ON public.ft_market_cache USING btree (data_type);
CREATE INDEX idx_ft_market_flow_type_date ON public.ft_market_flow USING btree (data_type, trade_date);
CREATE UNIQUE INDEX uq_market_flow_dragon ON public.ft_market_flow USING btree (((data ->> 'source'::text)), ((data ->> 'code'::text)), trade_date) WHERE ((data_type)::text = 'dragon_tiger'::text);
CREATE UNIQUE INDEX uq_market_flow_northbound ON public.ft_market_flow USING btree (((data ->> 'mutual_type'::text)), trade_date) WHERE ((data_type)::text = 'northbound'::text);
CREATE UNIQUE INDEX uq_market_flow_sector ON public.ft_market_flow USING btree (((data ->> 'name'::text)), trade_date) WHERE ((data_type)::text = 'sector_flow'::text);
CREATE UNIQUE INDEX uq_market_flow_stock ON public.ft_market_flow USING btree (((data ->> 'code'::text)), trade_date) WHERE ((data_type)::text = 'stock_flow'::text);
CREATE INDEX idx_ft_news_category ON public.ft_news USING btree (category, published_at);
CREATE INDEX idx_ft_news_extracted ON public.ft_news USING btree (event_extracted, published_at DESC) WHERE (event_extracted = false);
CREATE UNIQUE INDEX idx_ft_news_fingerprint ON public.ft_news USING btree (fingerprint);
CREATE INDEX idx_ft_news_published_at ON public.ft_news USING btree (published_at);
CREATE INDEX idx_ft_news_source ON public.ft_news USING btree (source);
CREATE INDEX idx_ft_news_source_time ON public.ft_news USING btree (source, published_at);
CREATE INDEX idx_ft_pending_date ON public.ft_pending_decisions USING btree (decision_date);
CREATE INDEX idx_ft_pending_event_stream ON public.ft_pending_decisions USING btree (event_stream_id);
CREATE INDEX idx_ft_pending_source ON public.ft_pending_decisions USING btree (decision_source, status);
CREATE INDEX idx_ft_pending_status ON public.ft_pending_decisions USING btree (status);
CREATE UNIQUE INDEX uq_ft_pending_event_fund_date ON public.ft_pending_decisions USING btree (event_stream_id, fund_code, decision_date) WHERE ((decision_source)::text = 'event_driven'::text);
CREATE INDEX idx_raw_cache ON ONLY public.ft_raw_data USING btree (source, method, params_hash, fetched_at DESC);
CREATE INDEX idx_raw_domain_time ON ONLY public.ft_raw_data USING btree (data_domain, fetched_at);
CREATE INDEX idx_raw_trade_date ON ONLY public.ft_raw_data USING btree (trade_date) WHERE (trade_date IS NOT NULL);
CREATE INDEX ft_raw_data_202603_data_domain_fetched_at_idx ON public.ft_raw_data_202603 USING btree (data_domain, fetched_at);
CREATE INDEX ft_raw_data_202603_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202603 USING btree (source, method, params_hash, fetched_at DESC);
CREATE INDEX ft_raw_data_202603_trade_date_idx ON public.ft_raw_data_202603 USING btree (trade_date) WHERE (trade_date IS NOT NULL);
CREATE INDEX ft_raw_data_202604_data_domain_fetched_at_idx ON public.ft_raw_data_202604 USING btree (data_domain, fetched_at);
CREATE INDEX ft_raw_data_202604_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202604 USING btree (source, method, params_hash, fetched_at DESC);
CREATE INDEX ft_raw_data_202604_trade_date_idx ON public.ft_raw_data_202604 USING btree (trade_date) WHERE (trade_date IS NOT NULL);
CREATE INDEX ft_raw_data_202605_data_domain_fetched_at_idx ON public.ft_raw_data_202605 USING btree (data_domain, fetched_at);
CREATE INDEX ft_raw_data_202605_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202605 USING btree (source, method, params_hash, fetched_at DESC);
CREATE INDEX ft_raw_data_202605_trade_date_idx ON public.ft_raw_data_202605 USING btree (trade_date) WHERE (trade_date IS NOT NULL);
CREATE INDEX ft_raw_data_202606_data_domain_fetched_at_idx ON public.ft_raw_data_202606 USING btree (data_domain, fetched_at);
CREATE INDEX ft_raw_data_202606_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202606 USING btree (source, method, params_hash, fetched_at DESC);
CREATE INDEX ft_raw_data_202606_trade_date_idx ON public.ft_raw_data_202606 USING btree (trade_date) WHERE (trade_date IS NOT NULL);
CREATE INDEX ft_raw_data_202607_data_domain_fetched_at_idx ON public.ft_raw_data_202607 USING btree (data_domain, fetched_at);
CREATE INDEX ft_raw_data_202607_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202607 USING btree (source, method, params_hash, fetched_at DESC);
CREATE INDEX ft_raw_data_202607_trade_date_idx ON public.ft_raw_data_202607 USING btree (trade_date) WHERE (trade_date IS NOT NULL);
CREATE INDEX ft_raw_data_202608_data_domain_fetched_at_idx ON public.ft_raw_data_202608 USING btree (data_domain, fetched_at);
CREATE INDEX ft_raw_data_202608_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202608 USING btree (source, method, params_hash, fetched_at DESC);
CREATE INDEX ft_raw_data_202608_trade_date_idx ON public.ft_raw_data_202608 USING btree (trade_date) WHERE (trade_date IS NOT NULL);
CREATE INDEX ft_raw_data_202609_data_domain_fetched_at_idx ON public.ft_raw_data_202609 USING btree (data_domain, fetched_at);
CREATE INDEX ft_raw_data_202609_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202609 USING btree (source, method, params_hash, fetched_at DESC);
CREATE INDEX ft_raw_data_202609_trade_date_idx ON public.ft_raw_data_202609 USING btree (trade_date) WHERE (trade_date IS NOT NULL);
CREATE INDEX ft_raw_data_202610_data_domain_fetched_at_idx ON public.ft_raw_data_202610 USING btree (data_domain, fetched_at);
CREATE INDEX ft_raw_data_202610_source_method_params_hash_fetched_at_idx ON public.ft_raw_data_202610 USING btree (source, method, params_hash, fetched_at DESC);
CREATE INDEX ft_raw_data_202610_trade_date_idx ON public.ft_raw_data_202610 USING btree (trade_date) WHERE (trade_date IS NOT NULL);
CREATE INDEX idx_ft_reviews_date ON public.ft_reviews USING btree (decision_date);
CREATE INDEX idx_ft_reviews_outcome ON public.ft_reviews USING btree (outcome);
CREATE UNIQUE INDEX uq_ft_reviews_dec_fund ON public.ft_reviews USING btree (decision_id, fund_code) WHERE (decision_id IS NOT NULL);
CREATE UNIQUE INDEX uq_ft_reviews_paper ON public.ft_reviews USING btree (fund_code, decision_date, decision_source, decision_action) WHERE (decision_id IS NULL);
CREATE INDEX idx_ft_rule_thresholds_rule ON public.ft_rule_thresholds USING btree (rule_name);
CREATE INDEX idx_ft_sentiment_type_date ON public.ft_sentiment USING btree (data_type, trade_date);
CREATE INDEX idx_ft_signals_date ON public.ft_signals USING btree (fund_code, signal_date);
CREATE INDEX idx_ft_trades_date ON public.ft_trades USING btree (fund_code, trade_date);
CREATE INDEX idx_ft_trades_dry_run ON public.ft_trades USING btree (dry_run, trade_date);
CREATE INDEX idx_watchlist_data_code ON public.ft_watchlist_data USING btree (code, data_type);
CREATE UNIQUE INDEX idx_watchlist_data_unique ON public.ft_watchlist_data USING btree (code, data_type, trade_date);
CREATE INDEX ix_kg_assignment_candidate_orders_scope ON public.kg_assignment_candidate_orders USING btree (adapter_name, target);
CREATE INDEX ix_kg_cognitive_cards_adapter_status ON public.kg_cognitive_cards USING btree (adapter_name, status);
CREATE INDEX ix_kg_cognitive_cards_chunk ON public.kg_cognitive_cards USING btree (primary_chunk_id);
CREATE INDEX ix_kg_cognitive_cards_evidence ON public.kg_cognitive_cards USING btree (evidence_id);
CREATE INDEX ix_kg_cognitive_cards_source ON public.kg_cognitive_cards USING btree (adapter_name, source_type, source_id);
CREATE INDEX ix_kg_community_assignments_adapter_status ON public.kg_community_assignments USING btree (adapter_name, status);
CREATE INDEX ix_kg_community_assignments_card ON public.kg_community_assignments USING btree (cognitive_card_id);
CREATE INDEX ix_kg_community_assignments_community ON public.kg_community_assignments USING btree (community_id);
CREATE INDEX ix_kg_compilation_runs_adapter ON public.kg_compilation_runs USING btree (adapter_name, adapter_version);
CREATE INDEX ix_kg_compilation_runs_status ON public.kg_compilation_runs USING btree (status);
CREATE INDEX ix_kg_evidence_adapter ON public.kg_evidence USING btree (adapter_name);
CREATE INDEX ix_kg_evidence_source ON public.kg_evidence USING btree (source_type, source_id);
CREATE INDEX ix_kg_evidence_source_status ON public.kg_evidence USING btree (adapter_name, source_type, source_id, status);
CREATE INDEX ix_kg_evidence_status ON public.kg_evidence USING btree (status);
CREATE INDEX ix_kg_evidence_chunks_adapter ON public.kg_evidence_chunks USING btree (adapter_name);
CREATE INDEX ix_kg_evidence_chunks_evidence ON public.kg_evidence_chunks USING btree (evidence_id);
CREATE INDEX ix_kg_evidence_chunks_evidence_index ON public.kg_evidence_chunks USING btree (evidence_id, chunk_index);
CREATE INDEX ix_kg_graph_communities_adapter_projection ON public.kg_graph_communities USING btree (adapter_name, projection);
CREATE INDEX ix_kg_graph_communities_id ON public.kg_graph_communities USING btree (id);
CREATE INDEX ix_kg_graph_communities_parent ON public.kg_graph_communities USING btree (parent_community_id);
CREATE INDEX ix_kg_graph_communities_status ON public.kg_graph_communities USING btree (status);
CREATE INDEX ix_kg_graph_deltas_adapter_projection ON public.kg_graph_deltas USING btree (adapter_name, projection);
CREATE INDEX ix_kg_graph_deltas_status ON public.kg_graph_deltas USING btree (status);
CREATE INDEX ix_kg_graph_deltas_window ON public.kg_graph_deltas USING btree (window_name);
CREATE INDEX ix_kg_graph_findings_adapter_projection ON public.kg_graph_findings USING btree (adapter_name, projection);
CREATE INDEX ix_kg_graph_findings_community ON public.kg_graph_findings USING btree (community_id);
CREATE INDEX ix_kg_graph_findings_status ON public.kg_graph_findings USING btree (status);
CREATE INDEX ix_kg_graph_unassigned_signals_adapter_status ON public.kg_graph_unassigned_signals USING btree (adapter_name, status);
CREATE INDEX ix_kg_graph_unassigned_signals_projection ON public.kg_graph_unassigned_signals USING btree (projection);
CREATE INDEX ix_kg_graph_unassigned_signals_promoted ON public.kg_graph_unassigned_signals USING btree (promoted_community_id);
CREATE INDEX ix_kg_normalization_rules_adapter_type ON public.kg_normalization_rules USING btree (adapter_name, rule_type);
CREATE INDEX ix_kg_normalization_rules_status ON public.kg_normalization_rules USING btree (status);
CREATE INDEX ix_kg_retrieval_eval_metrics_case ON public.kg_retrieval_eval_metrics USING btree (case_id);
CREATE INDEX ix_kg_retrieval_eval_metrics_run ON public.kg_retrieval_eval_metrics USING btree (run_id);
CREATE INDEX ix_kg_retrieval_eval_runs_status ON public.kg_retrieval_eval_runs USING btree (status);
CREATE INDEX ix_kg_retrieval_eval_runs_strategy ON public.kg_retrieval_eval_runs USING btree (strategy_name, strategy_version);
CREATE INDEX ix_kg_retrieval_labels_case ON public.kg_retrieval_labels USING btree (case_id);
CREATE INDEX ix_kg_retrieval_labels_snapshot ON public.kg_retrieval_labels USING btree (snapshot_id);
CREATE INDEX ix_kg_retrieval_trace_snapshots_adapter ON public.kg_retrieval_trace_snapshots USING btree (adapter_name, target);
CREATE INDEX ix_kg_retrieval_trace_snapshots_query_hash ON public.kg_retrieval_trace_snapshots USING btree (query_hash);
CREATE INDEX ix_kg_retrieval_trace_snapshots_strategy ON public.kg_retrieval_trace_snapshots USING btree (strategy_name, strategy_version);
CREATE INDEX ix_kg_review_items_object ON public.kg_review_items USING btree (object_type, object_id);
CREATE INDEX ix_kg_review_items_status ON public.kg_review_items USING btree (status);
CREATE INDEX idx_sa_ocr_action ON public.sa_ocr_records USING btree (action);
CREATE INDEX idx_sa_ocr_created ON public.sa_ocr_records USING btree (created_at);
CREATE INDEX idx_sa_tasks_created ON public.sa_tasks USING btree (created_at DESC);
CREATE INDEX idx_sa_tasks_skill ON public.sa_tasks USING btree (skill_name);
CREATE INDEX idx_sa_tasks_status ON public.sa_tasks USING btree (status);
CREATE INDEX idx_sa_tasks_type ON public.sa_tasks USING btree (task_type);


-- ==============================================================================
-- Database: jettask_queue (jettask_queue)
-- ==============================================================================

-- Snapshot generated at: 2026-07-05T01:43:50+08:00
-- Tables included: 19


-- ==============================================================================
-- jettask_queue: sequences
-- ==============================================================================

CREATE SEQUENCE "public"."scheduled_tasks_id_seq"
    START WITH 1
    INCREMENT BY 1
    MINVALUE 1
    MAXVALUE 9223372036854775807
    CACHE 1 NO CYCLE;


-- ==============================================================================
-- jettask_queue: tables
-- ==============================================================================

CREATE TABLE "public"."queue_snapshot" (
    "time_bucket" timestamp with time zone NOT NULL,
    "namespace" character varying(100) DEFAULT 'default'::character varying NOT NULL,
    "queue" character varying(100) NOT NULL,
    "task_name" character varying(255) NOT NULL,
    "send_offset" bigint DEFAULT 0,
    "ack_offset" bigint DEFAULT 0,
    "read_offset" bigint DEFAULT 0,
    "backlog" integer DEFAULT 0,
    "pending_read" integer DEFAULT 0,
    "online_count" integer DEFAULT 0,
    "offline_count" integer DEFAULT 0,
    "total_count" integer DEFAULT 0,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "queue_snapshot_pkey" PRIMARY KEY (time_bucket, namespace, queue, task_name)
);

CREATE TABLE "public"."scheduled_tasks" (
    "id" bigint DEFAULT nextval('scheduled_tasks_id_seq'::regclass) NOT NULL,
    "scheduler_id" character varying(255) NOT NULL,
    "task_type" character varying(50),
    "queue_name" character varying(100),
    "namespace" character varying(100) DEFAULT 'default'::character varying,
    "task_args" jsonb DEFAULT '[]'::jsonb,
    "task_kwargs" jsonb DEFAULT '{}'::jsonb,
    "cron_expression" character varying(100),
    "interval_seconds" numeric(10,2),
    "next_run_time" timestamp with time zone,
    "next_trigger_time" timestamp with time zone,
    "last_run_time" timestamp with time zone,
    "enabled" boolean DEFAULT true,
    "max_retries" integer DEFAULT 3,
    "retry_delay" integer DEFAULT 60,
    "timeout" integer DEFAULT 300,
    "priority" integer,
    "description" text,
    "tags" jsonb DEFAULT '[]'::jsonb,
    "metadata" jsonb DEFAULT '{}'::jsonb,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    "updated_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    "max_runs" integer,
    "current_runs" integer DEFAULT 0,
    "loaded_by" character varying(128),
    "loaded_at" timestamp with time zone,
    "active_start_hour" integer,
    "active_end_hour" integer,
    CONSTRAINT "scheduled_tasks_pkey" PRIMARY KEY (id),
    CONSTRAINT "scheduled_tasks_scheduler_id_key" UNIQUE (scheduler_id)
);

CREATE TABLE "public"."task_metrics_minute" (
    "namespace" character varying(100) DEFAULT 'default'::character varying NOT NULL,
    "queue" character varying(100) NOT NULL,
    "time_bucket" timestamp with time zone NOT NULL,
    "task_count" integer DEFAULT 0,
    "updated_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "task_metrics_minute_pkey" PRIMARY KEY (namespace, queue, time_bucket)
) PARTITION BY RANGE (time_bucket);

CREATE TABLE "public"."task_runs" (
    "task_name" character varying(255) NOT NULL,
    "trigger_time" timestamp with time zone NOT NULL,
    "stream_id" character varying(64) NOT NULL,
    "status" character varying(20) DEFAULT 'PENDING'::character varying NOT NULL,
    "result" jsonb,
    "error" text,
    "error_type" character varying(128),
    "traceback" text,
    "started_at" timestamp with time zone,
    "completed_at" timestamp with time zone,
    "duration" double precision,
    "consumer" character varying(128),
    "retries" integer DEFAULT 0,
    "queue" character varying(100),
    "namespace" character varying(100) DEFAULT 'default'::character varying,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    "updated_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "task_runs_pkey" PRIMARY KEY (task_name, trigger_time, stream_id)
) PARTITION BY RANGE (trigger_time);

CREATE TABLE "public"."task_runs_metrics_minute" (
    "time_bucket" timestamp with time zone NOT NULL,
    "namespace" character varying(100) DEFAULT 'default'::character varying NOT NULL,
    "queue" character varying(100) NOT NULL,
    "task_name" character varying(255) NOT NULL,
    "total_count" integer DEFAULT 0,
    "success_count" integer DEFAULT 0,
    "failed_count" integer DEFAULT 0,
    "retry_count" integer DEFAULT 0,
    "total_duration" double precision DEFAULT 0,
    "max_duration" double precision DEFAULT 0,
    "min_duration" double precision DEFAULT 0,
    "total_delay" double precision DEFAULT 0,
    "max_delay" double precision DEFAULT 0,
    "min_delay" double precision DEFAULT 0,
    "running_concurrency" integer DEFAULT 0,
    "duration_sketch" bytea,
    "delay_sketch" bytea,
    "backlog" integer DEFAULT 0,
    "pending_read" integer DEFAULT 0,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    "updated_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "task_runs_metrics_minute_pkey" PRIMARY KEY (time_bucket, namespace, queue, task_name)
) PARTITION BY RANGE (time_bucket);

CREATE TABLE "public"."tasks" (
    "stream_id" character varying(64) NOT NULL,
    "trigger_time" timestamp with time zone NOT NULL,
    "queue" character varying(100) NOT NULL,
    "namespace" character varying(100) DEFAULT 'default'::character varying NOT NULL,
    "scheduled_task_id" character varying(255),
    "payload" jsonb NOT NULL,
    "priority" integer,
    "delay" integer,
    "source" character varying(100) DEFAULT 'sdk'::character varying NOT NULL,
    "metadata" jsonb,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT "tasks_pkey" PRIMARY KEY (stream_id, trigger_time)
) PARTITION BY RANGE (trigger_time);


-- ==============================================================================
-- jettask_queue: partitions
-- ==============================================================================

CREATE TABLE "public"."task_metrics_minute_2026_04" PARTITION OF "public"."task_metrics_minute"
    FOR VALUES FROM ('2026-03-31 16:00:00+00') TO ('2026-04-30 16:00:00+00');

CREATE TABLE "public"."task_runs_2026_04" PARTITION OF "public"."task_runs"
    FOR VALUES FROM ('2026-03-31 16:00:00+00') TO ('2026-04-30 16:00:00+00');

CREATE TABLE "public"."task_runs_metrics_minute_2026_04" PARTITION OF "public"."task_runs_metrics_minute"
    FOR VALUES FROM ('2026-03-31 16:00:00+00') TO ('2026-04-30 16:00:00+00');

CREATE TABLE "public"."tasks_2026_04" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-03-31 16:00:00+00') TO ('2026-04-30 16:00:00+00');

CREATE TABLE "public"."tasks_2026_05" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-04-30 16:00:00+00') TO ('2026-05-31 16:00:00+00');

CREATE TABLE "public"."tasks_2026_06" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-05-31 16:00:00+00') TO ('2026-06-30 16:00:00+00');

CREATE TABLE "public"."tasks_2026_07" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-06-30 16:00:00+00') TO ('2026-07-31 16:00:00+00');

CREATE TABLE "public"."tasks_2026_08" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-07-31 16:00:00+00') TO ('2026-08-31 16:00:00+00');

CREATE TABLE "public"."tasks_2026_09" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-08-31 16:00:00+00') TO ('2026-09-30 16:00:00+00');

CREATE TABLE "public"."tasks_2026_10" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-09-30 16:00:00+00') TO ('2026-10-31 16:00:00+00');

CREATE TABLE "public"."tasks_2026_11" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-10-31 16:00:00+00') TO ('2026-11-30 16:00:00+00');

CREATE TABLE "public"."tasks_2026_12" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-11-30 16:00:00+00') TO ('2026-12-31 16:00:00+00');

CREATE TABLE "public"."tasks_2027_01" PARTITION OF "public"."tasks"
    FOR VALUES FROM ('2026-12-31 16:00:00+00') TO ('2027-01-31 16:00:00+00');


-- ==============================================================================
-- jettask_queue: sequence ownership
-- ==============================================================================

ALTER SEQUENCE "public"."scheduled_tasks_id_seq" OWNED BY "public"."scheduled_tasks"."id";


-- ==============================================================================
-- jettask_queue: indexes
-- ==============================================================================

CREATE INDEX idx_qs_namespace_queue ON public.queue_snapshot USING btree (namespace, queue);
CREATE INDEX idx_qs_time_bucket ON public.queue_snapshot USING btree (time_bucket);
CREATE INDEX idx_scheduled_tasks_enabled ON public.scheduled_tasks USING btree (enabled);
CREATE INDEX idx_scheduled_tasks_namespace ON public.scheduled_tasks USING btree (namespace);
CREATE INDEX idx_scheduled_tasks_next_run ON public.scheduled_tasks USING btree (next_run_time) WHERE (enabled = true);
CREATE INDEX idx_scheduled_tasks_queue ON public.scheduled_tasks USING btree (queue_name);
CREATE INDEX idx_scheduled_tasks_task_type ON public.scheduled_tasks USING btree (task_type);
CREATE INDEX idx_tm_namespace_queue ON ONLY public.task_metrics_minute USING btree (namespace, queue);
CREATE INDEX idx_tm_time_bucket ON ONLY public.task_metrics_minute USING btree (time_bucket);
CREATE INDEX task_metrics_minute_2026_04_namespace_queue_idx ON public.task_metrics_minute_2026_04 USING btree (namespace, queue);
CREATE INDEX task_metrics_minute_2026_04_time_bucket_idx ON public.task_metrics_minute_2026_04 USING btree (time_bucket);
CREATE INDEX idx_task_runs_queue ON ONLY public.task_runs USING btree (queue);
CREATE INDEX idx_task_runs_started_at ON ONLY public.task_runs USING btree (started_at);
CREATE INDEX idx_task_runs_status ON ONLY public.task_runs USING btree (status);
CREATE INDEX idx_task_runs_stream_id ON ONLY public.task_runs USING btree (stream_id);
CREATE INDEX task_runs_2026_04_queue_idx ON public.task_runs_2026_04 USING btree (queue);
CREATE INDEX task_runs_2026_04_started_at_idx ON public.task_runs_2026_04 USING btree (started_at);
CREATE INDEX task_runs_2026_04_status_idx ON public.task_runs_2026_04 USING btree (status);
CREATE INDEX task_runs_2026_04_stream_id_idx ON public.task_runs_2026_04 USING btree (stream_id);
CREATE INDEX idx_trm_namespace_queue ON ONLY public.task_runs_metrics_minute USING btree (namespace, queue);
CREATE INDEX idx_trm_time_bucket ON ONLY public.task_runs_metrics_minute USING btree (time_bucket);
CREATE INDEX task_runs_metrics_minute_2026_04_namespace_queue_idx ON public.task_runs_metrics_minute_2026_04 USING btree (namespace, queue);
CREATE INDEX task_runs_metrics_minute_2026_04_time_bucket_idx ON public.task_runs_metrics_minute_2026_04 USING btree (time_bucket);
CREATE INDEX idx_tasks_created_at ON ONLY public.tasks USING btree (created_at);
CREATE INDEX idx_tasks_namespace ON ONLY public.tasks USING btree (namespace);
CREATE INDEX idx_tasks_queue ON ONLY public.tasks USING btree (queue);
CREATE INDEX idx_tasks_queue_namespace ON ONLY public.tasks USING btree (queue, namespace);
CREATE INDEX idx_tasks_scheduled_task_id ON ONLY public.tasks USING btree (scheduled_task_id);
CREATE INDEX idx_tasks_source ON ONLY public.tasks USING btree (source);
CREATE INDEX tasks_2026_04_created_at_idx ON public.tasks_2026_04 USING btree (created_at);
CREATE INDEX tasks_2026_04_namespace_idx ON public.tasks_2026_04 USING btree (namespace);
CREATE INDEX tasks_2026_04_queue_idx ON public.tasks_2026_04 USING btree (queue);
CREATE INDEX tasks_2026_04_queue_namespace_idx ON public.tasks_2026_04 USING btree (queue, namespace);
CREATE INDEX tasks_2026_04_scheduled_task_id_idx ON public.tasks_2026_04 USING btree (scheduled_task_id);
CREATE INDEX tasks_2026_04_source_idx ON public.tasks_2026_04 USING btree (source);
CREATE INDEX tasks_2026_05_created_at_idx ON public.tasks_2026_05 USING btree (created_at);
CREATE INDEX tasks_2026_05_namespace_idx ON public.tasks_2026_05 USING btree (namespace);
CREATE INDEX tasks_2026_05_queue_idx ON public.tasks_2026_05 USING btree (queue);
CREATE INDEX tasks_2026_05_queue_namespace_idx ON public.tasks_2026_05 USING btree (queue, namespace);
CREATE INDEX tasks_2026_05_scheduled_task_id_idx ON public.tasks_2026_05 USING btree (scheduled_task_id);
CREATE INDEX tasks_2026_05_source_idx ON public.tasks_2026_05 USING btree (source);
CREATE INDEX tasks_2026_06_created_at_idx ON public.tasks_2026_06 USING btree (created_at);
CREATE INDEX tasks_2026_06_namespace_idx ON public.tasks_2026_06 USING btree (namespace);
CREATE INDEX tasks_2026_06_queue_idx ON public.tasks_2026_06 USING btree (queue);
CREATE INDEX tasks_2026_06_queue_namespace_idx ON public.tasks_2026_06 USING btree (queue, namespace);
CREATE INDEX tasks_2026_06_scheduled_task_id_idx ON public.tasks_2026_06 USING btree (scheduled_task_id);
CREATE INDEX tasks_2026_06_source_idx ON public.tasks_2026_06 USING btree (source);
CREATE INDEX tasks_2026_07_created_at_idx ON public.tasks_2026_07 USING btree (created_at);
CREATE INDEX tasks_2026_07_namespace_idx ON public.tasks_2026_07 USING btree (namespace);
CREATE INDEX tasks_2026_07_queue_idx ON public.tasks_2026_07 USING btree (queue);
CREATE INDEX tasks_2026_07_queue_namespace_idx ON public.tasks_2026_07 USING btree (queue, namespace);
CREATE INDEX tasks_2026_07_scheduled_task_id_idx ON public.tasks_2026_07 USING btree (scheduled_task_id);
CREATE INDEX tasks_2026_07_source_idx ON public.tasks_2026_07 USING btree (source);
CREATE INDEX tasks_2026_08_created_at_idx ON public.tasks_2026_08 USING btree (created_at);
CREATE INDEX tasks_2026_08_namespace_idx ON public.tasks_2026_08 USING btree (namespace);
CREATE INDEX tasks_2026_08_queue_idx ON public.tasks_2026_08 USING btree (queue);
CREATE INDEX tasks_2026_08_queue_namespace_idx ON public.tasks_2026_08 USING btree (queue, namespace);
CREATE INDEX tasks_2026_08_scheduled_task_id_idx ON public.tasks_2026_08 USING btree (scheduled_task_id);
CREATE INDEX tasks_2026_08_source_idx ON public.tasks_2026_08 USING btree (source);
CREATE INDEX tasks_2026_09_created_at_idx ON public.tasks_2026_09 USING btree (created_at);
CREATE INDEX tasks_2026_09_namespace_idx ON public.tasks_2026_09 USING btree (namespace);
CREATE INDEX tasks_2026_09_queue_idx ON public.tasks_2026_09 USING btree (queue);
CREATE INDEX tasks_2026_09_queue_namespace_idx ON public.tasks_2026_09 USING btree (queue, namespace);
CREATE INDEX tasks_2026_09_scheduled_task_id_idx ON public.tasks_2026_09 USING btree (scheduled_task_id);
CREATE INDEX tasks_2026_09_source_idx ON public.tasks_2026_09 USING btree (source);
CREATE INDEX tasks_2026_10_created_at_idx ON public.tasks_2026_10 USING btree (created_at);
CREATE INDEX tasks_2026_10_namespace_idx ON public.tasks_2026_10 USING btree (namespace);
CREATE INDEX tasks_2026_10_queue_idx ON public.tasks_2026_10 USING btree (queue);
CREATE INDEX tasks_2026_10_queue_namespace_idx ON public.tasks_2026_10 USING btree (queue, namespace);
CREATE INDEX tasks_2026_10_scheduled_task_id_idx ON public.tasks_2026_10 USING btree (scheduled_task_id);
CREATE INDEX tasks_2026_10_source_idx ON public.tasks_2026_10 USING btree (source);
CREATE INDEX tasks_2026_11_created_at_idx ON public.tasks_2026_11 USING btree (created_at);
CREATE INDEX tasks_2026_11_namespace_idx ON public.tasks_2026_11 USING btree (namespace);
CREATE INDEX tasks_2026_11_queue_idx ON public.tasks_2026_11 USING btree (queue);
CREATE INDEX tasks_2026_11_queue_namespace_idx ON public.tasks_2026_11 USING btree (queue, namespace);
CREATE INDEX tasks_2026_11_scheduled_task_id_idx ON public.tasks_2026_11 USING btree (scheduled_task_id);
CREATE INDEX tasks_2026_11_source_idx ON public.tasks_2026_11 USING btree (source);
CREATE INDEX tasks_2026_12_created_at_idx ON public.tasks_2026_12 USING btree (created_at);
CREATE INDEX tasks_2026_12_namespace_idx ON public.tasks_2026_12 USING btree (namespace);
CREATE INDEX tasks_2026_12_queue_idx ON public.tasks_2026_12 USING btree (queue);
CREATE INDEX tasks_2026_12_queue_namespace_idx ON public.tasks_2026_12 USING btree (queue, namespace);
CREATE INDEX tasks_2026_12_scheduled_task_id_idx ON public.tasks_2026_12 USING btree (scheduled_task_id);
CREATE INDEX tasks_2026_12_source_idx ON public.tasks_2026_12 USING btree (source);
CREATE INDEX tasks_2027_01_created_at_idx ON public.tasks_2027_01 USING btree (created_at);
CREATE INDEX tasks_2027_01_namespace_idx ON public.tasks_2027_01 USING btree (namespace);
CREATE INDEX tasks_2027_01_queue_idx ON public.tasks_2027_01 USING btree (queue);
CREATE INDEX tasks_2027_01_queue_namespace_idx ON public.tasks_2027_01 USING btree (queue, namespace);
CREATE INDEX tasks_2027_01_scheduled_task_id_idx ON public.tasks_2027_01 USING btree (scheduled_task_id);
CREATE INDEX tasks_2027_01_source_idx ON public.tasks_2027_01 USING btree (source);


-- ==============================================================================
-- jettask_queue: comments
-- ==============================================================================

COMMENT ON TABLE "public"."queue_snapshot" IS '队列健康快照（每分钟一条，用于趋势分析和告警）';
COMMENT ON COLUMN "public"."queue_snapshot"."backlog" IS '队列积压量 = send_offset - ack_offset';
COMMENT ON COLUMN "public"."queue_snapshot"."pending_read" IS '未读取量 = send_offset - read_offset';
COMMENT ON TABLE "public"."scheduled_tasks" IS '定时任务定义表（v1 + v2 字段共存）';
COMMENT ON COLUMN "public"."scheduled_tasks"."scheduler_id" IS '唯一标识，v2 当 name 使用';
COMMENT ON COLUMN "public"."scheduled_tasks"."queue_name" IS '目标队列，v2 当 task_func_name 使用';
COMMENT ON COLUMN "public"."scheduled_tasks"."max_runs" IS 'v2 新增：最大执行次数，达到后标记 COMPLETED';
COMMENT ON COLUMN "public"."scheduled_tasks"."loaded_by" IS 'v2 新增：两层调度认领者 scheduler ID';
COMMENT ON COLUMN "public"."scheduled_tasks"."active_start_hour" IS 'v2 新增：执行时间窗口起始小时';
COMMENT ON TABLE "public"."task_metrics_minute" IS '任务创建指标分钟聚合（按 time_bucket 月分区）';
COMMENT ON TABLE "public"."task_runs" IS '任务执行记录（按 trigger_time 月分区）';
COMMENT ON COLUMN "public"."task_runs"."trigger_time" IS '从 stream_id（Redis Stream ID）中解析的毫秒时间戳';
COMMENT ON COLUMN "public"."task_runs"."duration" IS 'Worker 端计算的执行耗时（秒），非下游推算';
COMMENT ON TABLE "public"."task_runs_metrics_minute" IS '任务执行指标分钟聚合（按 time_bucket 月分区）';
COMMENT ON COLUMN "public"."task_runs_metrics_minute"."duration_sketch" IS 'DDSketch 序列化数据，用于计算 P50/P90/P99 分位数';
COMMENT ON COLUMN "public"."task_runs_metrics_minute"."backlog" IS '队列积压量 = send_offset - ack_offset';
COMMENT ON TABLE "public"."tasks" IS '任务原始消息归档（按 trigger_time 月分区）';
COMMENT ON COLUMN "public"."tasks"."trigger_time" IS '从 stream_id（Redis Stream ID）中解析的毫秒时间戳';
COMMENT ON COLUMN "public"."tasks"."payload" IS '原始任务消息 kwargs（JSONB）';
COMMENT ON COLUMN "public"."tasks"."source" IS '任务来源：sdk / scheduler / api';

-- ==============================================================================
-- Legacy KG cleanup: remove deprecated node/edge fact graph tables
-- ==============================================================================

DROP TABLE IF EXISTS public.kg_edge_evidence_chunks;
DROP TABLE IF EXISTS public.kg_edge_evidence;
DROP TABLE IF EXISTS public.kg_graph_adjacency;
DROP TABLE IF EXISTS public.kg_edges;
DROP TABLE IF EXISTS public.kg_nodes;
DROP TABLE IF EXISTS public.kg_retrieval_document_versions;
DROP TABLE IF EXISTS public.kg_retrieval_documents;
DROP TABLE IF EXISTS public.kg_wiki_pages;

-- Empty legacy cache table removed after API startup table initialization was
-- deleted. Runtime code should not create tables from FastAPI lifespan.
DROP TABLE IF EXISTS public.ft_cache;

-- ==============================================================================
-- Partition maintenance: 2026-07-05 future half-year
-- ==============================================================================

-- jettask_queue framework partition tables. Boundaries follow Asia/Shanghai month
-- starts, stored as UTC timestamps, matching existing jettask-rs partitions.

-- public.tasks
CREATE TABLE IF NOT EXISTS public.tasks_2026_04 PARTITION OF public.tasks FOR VALUES FROM ('2026-03-31 16:00:00+00') TO ('2026-04-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.tasks_2026_05 PARTITION OF public.tasks FOR VALUES FROM ('2026-04-30 16:00:00+00') TO ('2026-05-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.tasks_2026_06 PARTITION OF public.tasks FOR VALUES FROM ('2026-05-31 16:00:00+00') TO ('2026-06-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.tasks_2026_07 PARTITION OF public.tasks FOR VALUES FROM ('2026-06-30 16:00:00+00') TO ('2026-07-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.tasks_2026_08 PARTITION OF public.tasks FOR VALUES FROM ('2026-07-31 16:00:00+00') TO ('2026-08-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.tasks_2026_09 PARTITION OF public.tasks FOR VALUES FROM ('2026-08-31 16:00:00+00') TO ('2026-09-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.tasks_2026_10 PARTITION OF public.tasks FOR VALUES FROM ('2026-09-30 16:00:00+00') TO ('2026-10-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.tasks_2026_11 PARTITION OF public.tasks FOR VALUES FROM ('2026-10-31 16:00:00+00') TO ('2026-11-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.tasks_2026_12 PARTITION OF public.tasks FOR VALUES FROM ('2026-11-30 16:00:00+00') TO ('2026-12-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.tasks_2027_01 PARTITION OF public.tasks FOR VALUES FROM ('2026-12-31 16:00:00+00') TO ('2027-01-31 16:00:00+00');

-- public.task_runs
CREATE TABLE IF NOT EXISTS public.task_runs_2026_04 PARTITION OF public.task_runs FOR VALUES FROM ('2026-03-31 16:00:00+00') TO ('2026-04-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_2026_05 PARTITION OF public.task_runs FOR VALUES FROM ('2026-04-30 16:00:00+00') TO ('2026-05-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_2026_06 PARTITION OF public.task_runs FOR VALUES FROM ('2026-05-31 16:00:00+00') TO ('2026-06-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_2026_07 PARTITION OF public.task_runs FOR VALUES FROM ('2026-06-30 16:00:00+00') TO ('2026-07-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_2026_08 PARTITION OF public.task_runs FOR VALUES FROM ('2026-07-31 16:00:00+00') TO ('2026-08-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_2026_09 PARTITION OF public.task_runs FOR VALUES FROM ('2026-08-31 16:00:00+00') TO ('2026-09-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_2026_10 PARTITION OF public.task_runs FOR VALUES FROM ('2026-09-30 16:00:00+00') TO ('2026-10-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_2026_11 PARTITION OF public.task_runs FOR VALUES FROM ('2026-10-31 16:00:00+00') TO ('2026-11-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_2026_12 PARTITION OF public.task_runs FOR VALUES FROM ('2026-11-30 16:00:00+00') TO ('2026-12-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_2027_01 PARTITION OF public.task_runs FOR VALUES FROM ('2026-12-31 16:00:00+00') TO ('2027-01-31 16:00:00+00');

-- public.task_metrics_minute
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2026_04 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-03-31 16:00:00+00') TO ('2026-04-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2026_05 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-04-30 16:00:00+00') TO ('2026-05-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2026_06 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-05-31 16:00:00+00') TO ('2026-06-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2026_07 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-06-30 16:00:00+00') TO ('2026-07-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2026_08 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-07-31 16:00:00+00') TO ('2026-08-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2026_09 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-08-31 16:00:00+00') TO ('2026-09-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2026_10 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-09-30 16:00:00+00') TO ('2026-10-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2026_11 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-10-31 16:00:00+00') TO ('2026-11-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2026_12 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-11-30 16:00:00+00') TO ('2026-12-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_metrics_minute_2027_01 PARTITION OF public.task_metrics_minute FOR VALUES FROM ('2026-12-31 16:00:00+00') TO ('2027-01-31 16:00:00+00');

-- public.task_runs_metrics_minute
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2026_04 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-03-31 16:00:00+00') TO ('2026-04-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2026_05 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-04-30 16:00:00+00') TO ('2026-05-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2026_06 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-05-31 16:00:00+00') TO ('2026-06-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2026_07 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-06-30 16:00:00+00') TO ('2026-07-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2026_08 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-07-31 16:00:00+00') TO ('2026-08-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2026_09 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-08-31 16:00:00+00') TO ('2026-09-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2026_10 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-09-30 16:00:00+00') TO ('2026-10-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2026_11 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-10-31 16:00:00+00') TO ('2026-11-30 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2026_12 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-11-30 16:00:00+00') TO ('2026-12-31 16:00:00+00');
CREATE TABLE IF NOT EXISTS public.task_runs_metrics_minute_2027_01 PARTITION OF public.task_runs_metrics_minute FOR VALUES FROM ('2026-12-31 16:00:00+00') TO ('2027-01-31 16:00:00+00');

-- business public.ft_raw_data partitions. Use the SECURITY DEFINER helper so
-- the application role does not need to own the partitioned parent table.
SELECT public.ensure_ft_raw_data_partition(2026, 7);
SELECT public.ensure_ft_raw_data_partition(2026, 8);
SELECT public.ensure_ft_raw_data_partition(2026, 9);
SELECT public.ensure_ft_raw_data_partition(2026, 10);
SELECT public.ensure_ft_raw_data_partition(2026, 11);
SELECT public.ensure_ft_raw_data_partition(2026, 12);
SELECT public.ensure_ft_raw_data_partition(2027, 1);
