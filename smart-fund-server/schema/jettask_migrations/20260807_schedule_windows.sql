-- JetTask minute-level, timezone-aware execution windows and exchange calendars.
ALTER TABLE scheduled_tasks
    ADD COLUMN IF NOT EXISTS schedule_timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
    ADD COLUMN IF NOT EXISTS active_windows JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS calendar_config JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN scheduled_tasks.schedule_timezone IS 'IANA timezone used to evaluate execution windows';
COMMENT ON COLUMN scheduled_tasks.active_windows IS 'Minute-level local-time execution windows';
COMMENT ON COLUMN scheduled_tasks.calendar_config IS 'Weekdays, exchange closures, exceptional sessions and validity range';
