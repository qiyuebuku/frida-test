-- Research MCP role memory and server-side run/evidence audit.

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
