-- ============================================================================
-- Learning Governance System
-- Controls automatic learning with approval workflows and rate limits
-- ============================================================================

-- Add governance columns to agent_prompts
ALTER TABLE agent_prompts
ADD COLUMN IF NOT EXISTS auto_learned BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS learned_from_pattern TEXT,
ADD COLUMN IF NOT EXISTS approval_status TEXT DEFAULT 'auto_approved'
    CHECK (approval_status IN ('auto_approved', 'pending_review', 'human_approved', 'rejected')),
ADD COLUMN IF NOT EXISTS approved_by TEXT,
ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS rejection_reason TEXT;

-- Learning limits per agent role
CREATE TABLE IF NOT EXISTS learning_governance (
    agent_role TEXT PRIMARY KEY,
    max_updates_per_day INT DEFAULT 5,
    cooldown_hours INT DEFAULT 2,
    require_human_approval BOOLEAN DEFAULT FALSE,
    last_update_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Default governance rules
INSERT INTO learning_governance (agent_role, require_human_approval, max_updates_per_day, cooldown_hours)
VALUES
    ('security', TRUE, 1, 24),      -- Security: Always require approval, max 1/day
    ('developer', FALSE, 5, 2),     -- Developer: Auto-approve, max 5/day, 2h cooldown
    ('reviewer', FALSE, 3, 4),      -- Reviewer: Auto-approve, max 3/day, 4h cooldown
    ('tester', FALSE, 5, 2),        -- Tester: Auto-approve, max 5/day, 2h cooldown
    ('architect', TRUE, 2, 12)      -- Architect: Require approval, max 2/day, 12h cooldown
ON CONFLICT (agent_role) DO NOTHING;

-- ============================================================================
-- FUNCTIONS
-- ============================================================================

-- Check if agent can auto-update prompt
CREATE OR REPLACE FUNCTION can_auto_update_prompt(
    p_agent_role TEXT
) RETURNS BOOLEAN AS $$
DECLARE
    v_governance learning_governance%ROWTYPE;
    v_updates_today INT;
    v_time_since_last_update INTERVAL;
BEGIN
    -- Get governance rules
    SELECT * INTO v_governance
    FROM learning_governance
    WHERE agent_role = p_agent_role;

    -- If no governance rule, default to requiring approval
    IF v_governance IS NULL THEN
        RETURN FALSE;
    END IF;

    -- Always block if requires human approval
    IF v_governance.require_human_approval THEN
        RETURN FALSE;
    END IF;

    -- Check cooldown period
    IF v_governance.last_update_at IS NOT NULL THEN
        v_time_since_last_update := NOW() - v_governance.last_update_at;

        IF v_time_since_last_update < INTERVAL '1 hour' * v_governance.cooldown_hours THEN
            RAISE NOTICE 'Cooldown active: % hours remaining',
                v_governance.cooldown_hours - EXTRACT(EPOCH FROM v_time_since_last_update)/3600;
            RETURN FALSE;
        END IF;
    END IF;

    -- Check daily limit
    SELECT COUNT(*) INTO v_updates_today
    FROM agent_prompts
    WHERE agent_role = p_agent_role
      AND auto_learned = TRUE
      AND created_at > NOW() - INTERVAL '24 hours';

    IF v_updates_today >= v_governance.max_updates_per_day THEN
        RAISE NOTICE 'Daily limit reached: % / %',
            v_updates_today, v_governance.max_updates_per_day;
        RETURN FALSE;
    END IF;

    -- All checks passed
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- Record prompt update and update governance state
CREATE OR REPLACE FUNCTION record_prompt_update(
    p_agent_role TEXT,
    p_prompt_id INT
) RETURNS VOID AS $$
BEGIN
    -- Update last_update_at in governance
    UPDATE learning_governance
    SET
        last_update_at = NOW(),
        updated_at = NOW()
    WHERE agent_role = p_agent_role;

    -- If no row was updated, create one
    IF NOT FOUND THEN
        INSERT INTO learning_governance (agent_role, last_update_at)
        VALUES (p_agent_role, NOW());
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Get pending approvals count
CREATE OR REPLACE FUNCTION get_pending_approvals_count()
RETURNS TABLE (
    agent_role TEXT,
    pending_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        ap.agent_role,
        COUNT(*) as pending_count
    FROM agent_prompts ap
    WHERE ap.approval_status = 'pending_review'
    GROUP BY ap.agent_role;
END;
$$ LANGUAGE plpgsql;

-- Approve prompt update
CREATE OR REPLACE FUNCTION approve_prompt_update(
    p_prompt_id INT,
    p_approved_by TEXT,
    p_notes TEXT DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    UPDATE agent_prompts
    SET
        approval_status = 'human_approved',
        approved_by = p_approved_by,
        approved_at = NOW(),
        rejection_reason = p_notes
    WHERE id = p_prompt_id;
END;
$$ LANGUAGE plpgsql;

-- Reject prompt update
CREATE OR REPLACE FUNCTION reject_prompt_update(
    p_prompt_id INT,
    p_rejected_by TEXT,
    p_reason TEXT
) RETURNS VOID AS $$
BEGIN
    UPDATE agent_prompts
    SET
        approval_status = 'rejected',
        approved_by = p_rejected_by,
        approved_at = NOW(),
        rejection_reason = p_reason
    WHERE id = p_prompt_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- VIEWS
-- ============================================================================

-- View of learning statistics
CREATE OR REPLACE VIEW learning_stats AS
SELECT
    agent_role,
    COUNT(*) FILTER (WHERE auto_learned = TRUE) as auto_learned_count,
    COUNT(*) FILTER (WHERE approval_status = 'pending_review') as pending_count,
    COUNT(*) FILTER (WHERE approval_status = 'human_approved') as approved_count,
    COUNT(*) FILTER (WHERE approval_status = 'rejected') as rejected_count,
    MAX(created_at) FILTER (WHERE auto_learned = TRUE) as last_auto_learn,
    COUNT(*) FILTER (
        WHERE auto_learned = TRUE
        AND created_at > NOW() - INTERVAL '24 hours'
    ) as auto_learned_today
FROM agent_prompts
GROUP BY agent_role;

-- View of governance status
CREATE OR REPLACE VIEW governance_status AS
SELECT
    lg.agent_role,
    lg.max_updates_per_day,
    lg.cooldown_hours,
    lg.require_human_approval,
    lg.last_update_at,
    ls.auto_learned_today,
    ls.pending_count,
    CASE
        WHEN lg.require_human_approval THEN 'requires_approval'
        WHEN ls.auto_learned_today >= lg.max_updates_per_day THEN 'daily_limit_reached'
        WHEN lg.last_update_at > NOW() - INTERVAL '1 hour' * lg.cooldown_hours THEN 'cooldown_active'
        ELSE 'can_auto_update'
    END as status
FROM learning_governance lg
LEFT JOIN learning_stats ls ON lg.agent_role = ls.agent_role;

-- ============================================================================
-- INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_agent_prompts_approval_status
    ON agent_prompts(approval_status)
    WHERE approval_status = 'pending_review';

CREATE INDEX IF NOT EXISTS idx_agent_prompts_auto_learned
    ON agent_prompts(agent_role, created_at DESC)
    WHERE auto_learned = TRUE;

CREATE INDEX IF NOT EXISTS idx_agent_prompts_role_status
    ON agent_prompts(agent_role, approval_status);

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Automatically record prompt updates
CREATE OR REPLACE FUNCTION trigger_record_prompt_update()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.auto_learned = TRUE AND (OLD IS NULL OR OLD.auto_learned = FALSE) THEN
        PERFORM record_prompt_update(NEW.agent_role, NEW.id);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER agent_prompt_auto_learned
AFTER INSERT OR UPDATE ON agent_prompts
FOR EACH ROW
EXECUTE FUNCTION trigger_record_prompt_update();

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE learning_governance IS
    'Controls automatic learning rates and approval requirements per agent role';

COMMENT ON FUNCTION can_auto_update_prompt IS
    'Checks if agent role can auto-update prompts based on governance rules';

COMMENT ON VIEW governance_status IS
    'Real-time status of learning governance for each agent role';
