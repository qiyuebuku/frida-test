-- 精确匹配“每任务/来源最新一次运行”的窄索引。
-- 历史审计仍保留在原表，不创建当前态表。
CREATE INDEX CONCURRENTLY IF NOT EXISTS
    ix_ft_collection_runs_task_source_started
ON public.ft_collection_runs (task_name, source_name, started_at DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ft_market_snapshots_bucket_at
ON public.ft_market_snapshots (bucket_at);
