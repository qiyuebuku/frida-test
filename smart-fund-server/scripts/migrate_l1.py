"""L1 schema migration — run once on production database"""
import os
import sys

os.chdir('/home/yuyang/frida-test/smart-fund-server')
sys.path.insert(0, '.')

from src.infrastructure.connections import get_session, set_target

set_target('prod')

from sqlalchemy import text

MIGRATION_SQL = [
    # ft_events new columns
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS event_id UUID DEFAULT gen_random_uuid()",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS source_type VARCHAR(16) DEFAULT 'text'",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS source_table VARCHAR(64) DEFAULT ''",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS evidence_refs JSONB DEFAULT '[]'::jsonb",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS schema_version VARCHAR(16) DEFAULT 'v1.0'",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS extractor_version VARCHAR(32) DEFAULT ''",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS affected_stocks JSONB DEFAULT '[]'::jsonb",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS affected_industries JSONB DEFAULT '[]'::jsonb",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS affected_concepts JSONB DEFAULT '[]'::jsonb",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS affected_regions JSONB DEFAULT '[]'::jsonb",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS quality_flags JSONB DEFAULT '[]'::jsonb",
    "ALTER TABLE ft_events ADD COLUMN IF NOT EXISTS dedup_key VARCHAR(128)",
    # ft_news new column
    "ALTER TABLE ft_news ADD COLUMN IF NOT EXISTS l1_classified_at TIMESTAMP WITH TIME ZONE",
]

CONSTRAINTS_SQL = [
    "ALTER TABLE ft_events ADD CONSTRAINT ft_events_event_id_key UNIQUE (event_id)",
]

TABLES_SQL = [
    """CREATE TABLE IF NOT EXISTS ft_rule_thresholds (
        id SERIAL PRIMARY KEY,
        rule_name VARCHAR(128) NOT NULL UNIQUE,
        data_source VARCHAR(64) NOT NULL,
        metric_name VARCHAR(64) NOT NULL,
        window_days INTEGER DEFAULT 90,
        percentile_95 FLOAT,
        percentile_99 FLOAT,
        sigma_value FLOAT,
        threshold_config JSONB DEFAULT '{}'::jsonb,
        last_computed_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
    )""",
]

INDEXES_SQL = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ft_events_dedup_key ON ft_events(dedup_key) WHERE dedup_key IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_ft_events_source_type ON ft_events(source_type, event_time DESC)",
    """CREATE INDEX IF NOT EXISTS idx_ft_events_event_subtype
       ON ft_events(event_subtype, event_time DESC)
       WHERE event_subtype IS NOT NULL AND event_subtype != ''""",
    "CREATE INDEX IF NOT EXISTS idx_ft_rule_thresholds_rule ON ft_rule_thresholds(rule_name)",
]


def run():
    with get_session() as s:
        # 1. Add columns
        for sql in MIGRATION_SQL:
            s.execute(text(sql))
        print("ft_events + ft_news columns added")

        # 2. Add constraints
        for sql in CONSTRAINTS_SQL:
            try:
                s.execute(text(sql))
                print("event_id unique constraint added")
            except Exception as e:
                print(f"event_id constraint: {e}")

        # 3. Create tables
        for sql in TABLES_SQL:
            s.execute(text(sql))
        print("ft_rule_thresholds table created")

        # 4. Create indexes
        for sql in INDEXES_SQL:
            try:
                s.execute(text(sql))
            except Exception as e:
                print(f"index warning: {e}")
        print("indexes created")

    print("Migration complete!")


if __name__ == "__main__":
    run()
