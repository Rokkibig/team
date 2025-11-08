-- ============================================================================
-- Golden Architecture V5.1 - Peer Review Schema
-- Tables for peer review consensus and voting
-- ============================================================================

-- Peer review sessions
CREATE TABLE IF NOT EXISTS peer_review_sessions (
    session_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    initiator TEXT NOT NULL,
    quorum FLOAT DEFAULT 0.75,
    status TEXT DEFAULT 'pending',  -- pending/completed/failed
    consensus_score FLOAT DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_peer_sessions_task ON peer_review_sessions(task_id);
CREATE INDEX IF NOT EXISTS idx_peer_sessions_status ON peer_review_sessions(status);

-- Individual votes
CREATE TABLE IF NOT EXISTS peer_votes (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES peer_review_sessions(session_id),
    agent_id TEXT NOT NULL,
    agent_role TEXT NOT NULL,
    vote TEXT NOT NULL,  -- approve/reject/conditional
    reasoning TEXT,
    weight FLOAT DEFAULT 0.0,
    voted_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_votes_session ON peer_votes(session_id);
CREATE INDEX IF NOT EXISTS idx_votes_agent ON peer_votes(agent_id);

-- Escalations
CREATE TABLE IF NOT EXISTS escalations (
    session_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    pattern_type TEXT,  -- skill_gap/agent_conflict/process_issue
    suggested_fix TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    resolved_by TEXT,
    resolution_data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_escalations_resolved ON escalations(resolved);
CREATE INDEX IF NOT EXISTS idx_escalations_created ON escalations(created_at DESC);

-- Success message
DO $$
BEGIN
    RAISE NOTICE '✅ Peer review schema migration complete';
END $$;
