-- 公共 LLM Gateway 调用日志。
-- 完整请求与响应用于质量分析和训练数据构建；高频分析维度单独列化。

CREATE TABLE IF NOT EXISTS llm_call_logs (
    id VARCHAR(36) PRIMARY KEY,
    task VARCHAR(128),
    source_type VARCHAR(64),
    source_id VARCHAR(256),
    provider VARCHAR(64),
    requested_model VARCHAR(128),
    resolved_model VARCHAR(128),
    upstream_model VARCHAR(128),
    route_reason VARCHAR(64),
    status VARCHAR(24) NOT NULL,
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    cache_store VARCHAR(32),
    request_hash VARCHAR(64),
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    reasoning_content TEXT,
    usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens BIGINT NOT NULL DEFAULT 0,
    reasoning_tokens BIGINT NOT NULL DEFAULT 0,
    prompt_cache_hit_tokens BIGINT NOT NULL DEFAULT 0,
    prompt_cache_miss_tokens BIGINT NOT NULL DEFAULT 0,
    cache_creation_tokens BIGINT NOT NULL DEFAULT 0,
    cache_read_tokens BIGINT NOT NULL DEFAULT 0,
    input_cost NUMERIC(20, 10),
    output_cost NUMERIC(20, 10),
    cache_cost NUMERIC(20, 10),
    total_cost NUMERIC(20, 10),
    currency VARCHAR(16),
    cost_source VARCHAR(32),
    cost_details JSONB NOT NULL DEFAULT '{}'::jsonb,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    session_id VARCHAR(128),
    error_type VARCHAR(128),
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_llm_call_logs_created_at
    ON llm_call_logs (created_at);
CREATE INDEX IF NOT EXISTS ix_llm_call_logs_task_created
    ON llm_call_logs (task, created_at);
CREATE INDEX IF NOT EXISTS ix_llm_call_logs_model_created
    ON llm_call_logs (resolved_model, created_at);
CREATE INDEX IF NOT EXISTS ix_llm_call_logs_status_created
    ON llm_call_logs (status, created_at);
CREATE INDEX IF NOT EXISTS ix_llm_call_logs_source
    ON llm_call_logs (source_type, source_id);

COMMENT ON TABLE llm_call_logs IS '公共 LLM Gateway 逻辑调用日志，用于质量、训练与成本分析';
COMMENT ON COLUMN llm_call_logs.request_payload IS '脱敏后的完整模型请求参数';
COMMENT ON COLUMN llm_call_logs.response_payload IS '完整模型返回、结构化输出和 Provider 诊断';
COMMENT ON COLUMN llm_call_logs.reasoning_content IS 'Provider 返回的完整模型思考过程；逻辑调用包含多次物理请求时按阶段拼接';
COMMENT ON COLUMN llm_call_logs.usage IS 'Provider 返回的原始标准化 Usage；本地缓存命中时保留被复用响应的 Usage';
COMMENT ON COLUMN llm_call_logs.total_cost IS '当前调用实际费用；Provider 未返回费用时为空';
COMMENT ON COLUMN llm_call_logs.cost_source IS 'provider_reported、local_cache 或 unavailable';
