-- Drop legacy event-chain tables and queues.
--
-- Current architecture uses Knowledge / Cognitive Card / Community Topic for
-- high-level event and topic abstraction. The old ft_events -> ft_event_streams
-- chain is intentionally removed.

DROP INDEX IF EXISTS public.uq_ft_pending_event_fund_date;
DROP INDEX IF EXISTS public.idx_ft_pending_event_stream;

ALTER TABLE IF EXISTS public.ft_pending_decisions
    DROP COLUMN IF EXISTS event_stream_id,
    DROP COLUMN IF EXISTS source_event_ids;

DROP TABLE IF EXISTS public.ft_rule_thresholds CASCADE;
DROP TABLE IF EXISTS public.ft_event_streams CASCADE;
DROP TABLE IF EXISTS public.ft_events CASCADE;

DROP SEQUENCE IF EXISTS public.ft_rule_thresholds_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.ft_event_streams_id_seq CASCADE;
DROP SEQUENCE IF EXISTS public.ft_events_id_seq CASCADE;

DO $$
BEGIN
    IF to_regclass('public.tasks') IS NOT NULL THEN
        DELETE FROM public.tasks
        WHERE queue IN (
            'agg_event_extraction',
            'agg_event_stream',
            'agg_event_feedback',
            'l1b_detect_fund_flow',
            'l1b_detect_macro',
            'l1b_detect_sentiment',
            'l1b_detect_market',
            'l1_refresh_thresholds',
            'l1a_classify_news',
            'l1a_extract_bucket',
            'trade_decision',
            'trade_execution',
            'trade_monitor',
            'review_decision'
        );
    END IF;
END $$;
