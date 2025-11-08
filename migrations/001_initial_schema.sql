-- ============================================================================
-- Golden Architecture V5.1 - Initial Schema
-- Core tables for multi-agent orchestration system
-- ============================================================================

-- Tasks table
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

-- Agents table
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agents_role ON agents(role);

-- Agent prompts table (for learning)
CREATE TABLE IF NOT EXISTS agent_prompts (
    id SERIAL PRIMARY KEY,
    agent_role TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    version INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prompts_role ON agent_prompts(agent_role, version DESC);

-- Budget tracking
CREATE TABLE IF NOT EXISTS budget_limits (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    total_limit BIGINT NOT NULL,
    current_usage BIGINT DEFAULT 0,
    reserved BIGINT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tenant_id, project_id)
);

CREATE TABLE IF NOT EXISTS budget_transactions (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    amount BIGINT NOT NULL,
    type TEXT NOT NULL,  -- reserve/commit/release
    purpose TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_budget_tx_tenant
    ON budget_transactions(tenant_id, project_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_budget_tx_request
    ON budget_transactions(request_id);

-- DLQ messages
CREATE TABLE IF NOT EXISTS dlq_messages (
    id SERIAL PRIMARY KEY,
    original_subject TEXT NOT NULL,
    data TEXT NOT NULL,
    headers JSONB,
    error_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    resolution_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_dlq_created ON dlq_messages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dlq_unresolved ON dlq_messages(resolved) WHERE resolved = FALSE;

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    details JSONB,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);

-- Success message
DO $$
BEGIN
    RAISE NOTICE '✅ Initial schema migration complete';
END $$;
