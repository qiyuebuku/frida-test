-- Split publish-blocking integrity failures from non-blocking quality findings.

ALTER TABLE agent_research_quality_evaluations
    ADD COLUMN IF NOT EXISTS advisory_findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS semantic_evaluation JSONB,
    ADD COLUMN IF NOT EXISTS semantic_evaluator_version VARCHAR(64),
    ADD COLUMN IF NOT EXISTS semantic_evaluated_at TIMESTAMPTZ;

COMMENT ON COLUMN agent_research_quality_evaluations.hard_failures IS
    'Only deterministic integrity violations that may block publication';
COMMENT ON COLUMN agent_research_quality_evaluations.advisory_findings IS
    'Non-blocking research-quality findings for longitudinal evaluation';
COMMENT ON COLUMN agent_research_quality_evaluations.semantic_evaluation IS
    'Independent semantic evaluator output; never fed back into the same Research run';

GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE agent_research_quality_evaluations
    TO jettask;
