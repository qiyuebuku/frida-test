-- Versioned Research quality gate and long-term score history.

ALTER TABLE agent_investment_view_revisions
    ADD COLUMN IF NOT EXISTS mechanism_chain JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS market_structure JSONB,
    ADD COLUMN IF NOT EXISTS decision_boundary JSONB;

ALTER TABLE agent_research_claims
    ADD COLUMN IF NOT EXISTS thesis_effect VARCHAR(32) NOT NULL DEFAULT 'context';

CREATE TABLE IF NOT EXISTS agent_research_quality_evaluations (
    evaluation_id VARCHAR(220) PRIMARY KEY,
    run_id VARCHAR(180) NOT NULL,
    evaluator_version VARCHAR(64) NOT NULL,
    overall_score DOUBLE PRECISION NOT NULL,
    grade VARCHAR(32) NOT NULL,
    passed BOOLEAN NOT NULL,
    scores JSONB NOT NULL,
    hard_failures JSONB NOT NULL,
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

COMMENT ON TABLE agent_research_quality_evaluations IS
    '每次研究在发布前的分项质量评分、硬性失败和改进动作；后续可叠加结果校准分';

-- 生产迁移由 postgres 执行，业务服务使用 jettask 连接。
-- 新表不会自动继承旧表权限，因此在迁移中显式授权，保证重复执行安全。
GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE agent_research_quality_evaluations
    TO jettask;
