-- Research Agent authoritative state, immutable revisions, and outcome feedback.
-- Object references are logical; the project intentionally does not add physical FKs.

CREATE TABLE IF NOT EXISTS agent_research_runs (
    run_id VARCHAR(180) PRIMARY KEY,
    trigger_id VARCHAR(180) NOT NULL,
    trigger_slot VARCHAR(32) NOT NULL,
    source_frame_id VARCHAR(180) NOT NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL,
    publishable BOOLEAN NOT NULL DEFAULT FALSE,
    proposal_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_research_runs_cutoff
    ON agent_research_runs (cutoff_at);
CREATE INDEX IF NOT EXISTS ix_agent_research_runs_status
    ON agent_research_runs (status, created_at);

CREATE TABLE IF NOT EXISTS agent_current_research_reports (
    report_id VARCHAR(180) PRIMARY KEY,
    current_revision_id VARCHAR(180),
    current_cutoff_at TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ NOT NULL,
    last_check_status VARCHAR(32) NOT NULL,
    last_no_change_reason TEXT,
    version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_research_report_revisions (
    revision_id VARCHAR(180) PRIMARY KEY,
    report_id VARCHAR(180) NOT NULL,
    base_revision_id VARCHAR(180),
    run_id VARCHAR(180) NOT NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    source_frame_id VARCHAR(180) NOT NULL,
    report_summary TEXT NOT NULL,
    research_question TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_research_report_revisions_report
    ON agent_research_report_revisions (report_id, cutoff_at);
CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_research_report_revisions_run
    ON agent_research_report_revisions (run_id);

CREATE TABLE IF NOT EXISTS agent_investment_views (
    view_id VARCHAR(180) PRIMARY KEY,
    current_revision_id VARCHAR(180) NOT NULL,
    current_cutoff_at TIMESTAMPTZ NOT NULL,
    title VARCHAR(300) NOT NULL,
    status VARCHAR(32) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_investment_views_status
    ON agent_investment_views (status, updated_at);

CREATE TABLE IF NOT EXISTS agent_investment_view_revisions (
    revision_id VARCHAR(180) PRIMARY KEY,
    view_id VARCHAR(180) NOT NULL,
    base_revision_id VARCHAR(180),
    run_id VARCHAR(180) NOT NULL,
    event VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    title VARCHAR(300) NOT NULL,
    thesis TEXT NOT NULL,
    scope JSONB NOT NULL,
    hypotheses JSONB NOT NULL,
    evidence_plan JSONB NOT NULL,
    mechanism_chain JSONB NOT NULL DEFAULT '[]'::jsonb,
    market_structure JSONB,
    decision_boundary JSONB,
    invalidation_conditions JSONB NOT NULL,
    confidence JSONB NOT NULL,
    valid_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_view_revisions_view
    ON agent_investment_view_revisions (view_id, cutoff_at);
CREATE INDEX IF NOT EXISTS ix_agent_view_revisions_run
    ON agent_investment_view_revisions (run_id);

CREATE TABLE IF NOT EXISTS agent_research_claims (
    claim_id VARCHAR(180) PRIMARY KEY,
    revision_id VARCHAR(180) NOT NULL,
    claim_type VARCHAR(32) NOT NULL,
    epistemic_status VARCHAR(32) NOT NULL,
    statement TEXT NOT NULL,
    thesis_effect VARCHAR(32) NOT NULL DEFAULT 'context',
    confidence VARCHAR(32) NOT NULL,
    evidence_refs JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_research_claims_revision
    ON agent_research_claims (revision_id);
CREATE INDEX IF NOT EXISTS ix_agent_research_claims_type_status
    ON agent_research_claims (claim_type, epistemic_status);

CREATE TABLE IF NOT EXISTS agent_research_forecasts (
    forecast_id VARCHAR(180) PRIMARY KEY,
    revision_id VARCHAR(180) NOT NULL,
    subject_id VARCHAR(180) NOT NULL,
    metric VARCHAR(300) NOT NULL,
    expected_direction VARCHAR(32) NOT NULL,
    benchmark_subject_id VARCHAR(180),
    baseline_value DOUBLE PRECISION,
    expected_min_value DOUBLE PRECISION,
    expected_max_value DOUBLE PRECISION,
    evaluation_start_at TIMESTAMPTZ NOT NULL,
    evaluation_end_at TIMESTAMPTZ NOT NULL,
    invalidation_condition TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_research_forecasts_revision
    ON agent_research_forecasts (revision_id);
CREATE INDEX IF NOT EXISTS ix_agent_research_forecasts_due
    ON agent_research_forecasts (evaluation_end_at);

CREATE TABLE IF NOT EXISTS agent_research_observation_requirements (
    requirement_id VARCHAR(180) PRIMARY KEY,
    run_id VARCHAR(180) NOT NULL,
    subject_id VARCHAR(180) NOT NULL,
    metric_or_event VARCHAR(500) NOT NULL,
    reason TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    source_preference VARCHAR(32) NOT NULL,
    related_view_id VARCHAR(180),
    related_forecast_id VARCHAR(180),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_observation_requirements_due
    ON agent_research_observation_requirements (status, due_at);
CREATE INDEX IF NOT EXISTS ix_agent_observation_requirements_view
    ON agent_research_observation_requirements (related_view_id);

CREATE TABLE IF NOT EXISTS agent_research_outcome_observations (
    observation_id VARCHAR(180) PRIMARY KEY,
    forecast_id VARCHAR(180) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    actual_value DOUBLE PRECISION,
    benchmark_value DOUBLE PRECISION,
    invalidation_condition_hit BOOLEAN NOT NULL,
    evidence_refs JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_outcome_observations_forecast
    ON agent_research_outcome_observations (forecast_id, observed_at);

CREATE TABLE IF NOT EXISTS agent_research_outcome_evaluations (
    evaluation_id VARCHAR(180) PRIMARY KEY,
    forecast_id VARCHAR(180) NOT NULL,
    observation_id VARCHAR(180) NOT NULL,
    status VARCHAR(32) NOT NULL,
    direction_correct BOOLEAN,
    range_correct BOOLEAN,
    benchmark_outperformance BOOLEAN,
    invalidation_condition_hit BOOLEAN NOT NULL,
    fact_assessment VARCHAR(32) NOT NULL,
    mechanism_assessment VARCHAR(32) NOT NULL,
    timing_assessment VARCHAR(32) NOT NULL,
    expression_assessment VARCHAR(32) NOT NULL,
    pricing_assessment VARCHAR(32) NOT NULL,
    summary TEXT NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_outcome_evaluations_forecast
    ON agent_research_outcome_evaluations (forecast_id, evaluated_at);
CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_outcome_evaluations_observation
    ON agent_research_outcome_evaluations (observation_id);

CREATE TABLE IF NOT EXISTS agent_research_quality_evaluations (
    evaluation_id VARCHAR(220) PRIMARY KEY,
    run_id VARCHAR(180) NOT NULL,
    evaluator_version VARCHAR(64) NOT NULL,
    overall_score DOUBLE PRECISION NOT NULL,
    grade VARCHAR(32) NOT NULL,
    passed BOOLEAN NOT NULL,
    scores JSONB NOT NULL,
    hard_failures JSONB NOT NULL,
    advisory_findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    semantic_evaluation JSONB,
    semantic_evaluator_version VARCHAR(64),
    semantic_evaluated_at TIMESTAMPTZ,
    improvement_actions JSONB NOT NULL,
    tool_coverage JSONB NOT NULL,
    evidence_reference_count INTEGER NOT NULL,
    outcome_adjusted_score DOUBLE PRECISION,
    evaluated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_research_quality_run
    ON agent_research_quality_evaluations (run_id);
CREATE INDEX IF NOT EXISTS ix_agent_research_quality_score
    ON agent_research_quality_evaluations (passed, overall_score);

COMMENT ON TABLE agent_research_runs IS
    '每次 Research Agent Run 的完整审计结果；阻塞结果只留痕，不发布为当前报告';
COMMENT ON TABLE agent_current_research_reports IS
    '整个交易系统唯一 Current Research Report 的当前指针与检查状态';
COMMENT ON TABLE agent_research_report_revisions IS
    '可发布 Current Research Report 的不可覆盖历史版本';
COMMENT ON TABLE agent_investment_views IS
    'Investment View 稳定身份及其当前有效 Revision 指针';
COMMENT ON TABLE agent_investment_view_revisions IS
    'Research Agent 对观点当时认知的不可覆盖 Revision';
COMMENT ON TABLE agent_research_claims IS
    'Revision 内显式分型、带认知状态和证据引用的逐条主张';
COMMENT ON TABLE agent_research_forecasts IS
    '观点形成时预先声明的对象、指标、窗口、基准和失效条件';
COMMENT ON TABLE agent_research_observation_requirements IS
    '等待未来事实的观察要求，不是 Research Agent 外包当前查询的请求';
COMMENT ON TABLE agent_research_outcome_observations IS
    '验证窗口内实际观察到的结果及其证据定位符';
COMMENT ON TABLE agent_research_outcome_evaluations IS
    '事实、机制、方向、时机、表达和定价分解后的研究结果评估';
COMMENT ON TABLE agent_research_quality_evaluations IS
    '每次研究在发布前的分项质量评分、硬性失败和改进动作；后续可叠加结果校准分';

CREATE TABLE IF NOT EXISTS agent_role_memory_items (
    memory_id VARCHAR(180) PRIMARY KEY,
    role VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    summary TEXT NOT NULL,
    applicability TEXT NOT NULL,
    counterexample TEXT NOT NULL,
    evidence_references JSONB NOT NULL,
    confidence VARCHAR(32) NOT NULL,
    scope JSONB NOT NULL DEFAULT '{}'::jsonb,
    valid_from TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_role_memory_lookup
    ON agent_role_memory_items (role, status, valid_from, expires_at);

CREATE TABLE IF NOT EXISTS agent_role_memory_cases (
    case_id VARCHAR(180) PRIMARY KEY,
    memory_id VARCHAR(180) NOT NULL,
    role VARCHAR(32) NOT NULL,
    decision_ref VARCHAR(300) NOT NULL,
    outcome_refs JSONB NOT NULL,
    context JSONB NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_role_memory_cases_memory
    ON agent_role_memory_cases (memory_id, created_at);

CREATE TABLE IF NOT EXISTS agent_runtime_runs (
    run_id VARCHAR(180) PRIMARY KEY,
    role VARCHAR(32) NOT NULL,
    task VARCHAR(64) NOT NULL,
    run_mode VARCHAR(32) NOT NULL,
    cutoff_at TIMESTAMPTZ NOT NULL,
    authorized_tools JSONB NOT NULL,
    account_ids JSONB NOT NULL,
    status VARCHAR(32) NOT NULL,
    budget JSONB NOT NULL DEFAULT '{}'::jsonb,
    checkpoint JSONB NOT NULL DEFAULT '{}'::jsonb,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    last_activity_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_agent_runtime_runs_role_status
    ON agent_runtime_runs (role, status);
CREATE INDEX IF NOT EXISTS ix_agent_runtime_runs_cutoff
    ON agent_runtime_runs (cutoff_at);

CREATE TABLE IF NOT EXISTS agent_tool_invocations (
    invocation_id VARCHAR(180) PRIMARY KEY,
    run_id VARCHAR(180) NOT NULL,
    tool_name VARCHAR(180) NOT NULL,
    status VARCHAR(32) NOT NULL,
    response_digest VARCHAR(64),
    evidence_refs JSONB NOT NULL,
    error_type VARCHAR(180),
    called_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_agent_tool_invocations_run
    ON agent_tool_invocations (run_id, called_at);
CREATE INDEX IF NOT EXISTS ix_agent_tool_invocations_tool
    ON agent_tool_invocations (tool_name, called_at);

COMMENT ON TABLE agent_role_memory_items IS
    '经确定性评测和治理晋升后的角色经验；Research、Portfolio、Trading 命名空间隔离';
COMMENT ON TABLE agent_role_memory_cases IS
    '正式经验关联的原决策、当时上下文和实际结果案例';
COMMENT ON TABLE agent_runtime_runs IS
    '由服务端根据签名运行授权自动建立的运行状态和可恢复检查点';
COMMENT ON TABLE agent_tool_invocations IS
    '服务端实际执行的模型工具调用与证据台账；模型不可自行写入';
