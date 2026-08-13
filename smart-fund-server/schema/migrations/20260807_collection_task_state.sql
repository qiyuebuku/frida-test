ALTER TABLE ft_collection_state
    ADD COLUMN IF NOT EXISTS task_id VARCHAR(192),
    ADD COLUMN IF NOT EXISTS task_type VARCHAR(24),
    ADD COLUMN IF NOT EXISTS status VARCHAR(24) DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS last_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_received_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_source_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_persisted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_finished_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_duration_ms BIGINT,
    ADD COLUMN IF NOT EXISTS last_fetched_count BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_saved_count BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_received BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS runtime_details JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_ft_collection_state_task_id
    ON ft_collection_state(task_id);

UPDATE ft_collection_state
SET task_id = 'collect_' || aggregator || '_' || source_name,
    task_type = COALESCE(task_type, 'pull'),
    status = COALESCE(
        status,
        CASE
            WHEN consecutive_failures > 0 THEN 'failed'
            WHEN last_success_at IS NOT NULL THEN 'success'
            ELSE 'pending'
        END
    ),
    last_started_at = COALESCE(last_started_at, last_run_at),
    last_received_at = COALESCE(last_received_at, last_success_at),
    last_persisted_at = COALESCE(last_persisted_at, last_success_at),
    last_finished_at = COALESCE(last_finished_at, last_success_at),
    last_saved_count = COALESCE(last_saved_count, 0),
    total_received = COALESCE(total_received, total_runs, 0),
    runtime_details = COALESCE(runtime_details, '{}'::jsonb)
WHERE task_id IS NULL;

-- ADD COLUMN DEFAULT 会让 PostgreSQL 给历史行直接填入 pending，而不是
-- NULL。已有成功/失败事实时必须用历史字段纠正，避免观测页误报首次执行。
UPDATE ft_collection_state
SET status = CASE
        WHEN consecutive_failures > 0 OR COALESCE(last_error, '') <> '' THEN 'failed'
        WHEN last_success_at IS NOT NULL THEN 'success'
        ELSE status
    END,
    last_started_at = COALESCE(last_started_at, last_run_at),
    last_received_at = COALESCE(last_received_at, last_success_at),
    last_persisted_at = COALESCE(last_persisted_at, last_success_at),
    last_finished_at = COALESCE(last_finished_at, last_success_at)
WHERE status = 'pending'
  AND (last_success_at IS NOT NULL OR consecutive_failures > 0 OR COALESCE(last_error, '') <> '');
