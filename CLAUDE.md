# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Golden Architecture V5.1** is a production-grade, battle-hardened multi-agent orchestration system. The architecture implements five security layers, circuit breakers for fault tolerance, and SLO-based auto-scaling.

## Quick Start Commands

### Environment Setup

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # or .venv/bin/activate on Windows

# Install dependencies
pip install -r requirements-minimal.txt

# Setup environment variables
cp .env.example .env  # Edit with your values
# Required: JWT_SECRET, DATABASE_URL, REDIS_URL, NATS_URL
```

### Database Operations

```bash
# Run all migrations in order
psql $DATABASE_URL -f migrations/001_initial_schema.sql
psql $DATABASE_URL -f migrations/002_peer_review.sql
psql $DATABASE_URL -f migrations/003_learning_governance.sql

# Check governance status
psql -d golden_arch -c "SELECT * FROM governance_status;"

# Check if agent can auto-update
psql -d golden_arch -c "SELECT can_auto_update_prompt('developer');"
```

### Running Services

```bash
# Start infrastructure (requires Docker)
docker run -d --name nats -p 4222:4222 -p 8222:8222 nats:latest -js

# Start demo API server
.venv/bin/uvicorn demo_server:app --host 0.0.0.0 --port 8001

# Run API tests
bash test_api.sh
```

### Testing Individual Components

```bash
# Test LLM validation (no server needed)
python3 << 'EOF'
from supervisor_optimizer.llm_utils import safe_parse_synthesis, sanitize_llm_response

# Test valid response
valid = '{"synthesis_reasoning": "test", "action_plan": [{"priority": 1, "type": "test", "issue": "test", "agent": "tester"}]}'
result = safe_parse_synthesis(valid)
print(f"✅ Valid: {result.action_plan}")

# Test sanitization
malicious = "DROP TABLE users;"
clean = sanitize_llm_response(malicious)
print(f"🛡️ Sanitized: {clean}")
EOF

# Test circuit breaker (no server needed)
python3 << 'EOF'
import asyncio
from common.circuit_breaker import CircuitBreaker

async def test():
    breaker = CircuitBreaker(failure_threshold=3, name="test")
    async def fail(): raise Exception("test")

    for i in range(5):
        try:
            await breaker.call(fail)
        except Exception as e:
            print(f"Attempt {i+1}: {type(e).__name__}")

    stats = breaker.get_stats()
    print(f"State: {stats.state}, Failures: {stats.total_failures}")

asyncio.run(test())
EOF
```

## Architecture Patterns

### Multi-Layer Security

The system implements defense-in-depth with 5 layers:

1. **Network Layer**: TLS, WAF, DDoS protection
2. **API Layer** (`api/security.py`): JWT auth, RBAC, rate limiting
3. **Input Layer** (`supervisor_optimizer/llm_utils.py`): Schema validation, sanitization
4. **Execution Layer** (`sandbox_executor/secure_executor.py`): gVisor isolation
5. **Data Layer**: Encryption at rest/transit, audit logs

**Key Pattern**: Always validate LLM responses with `safe_parse_synthesis()` before processing.

### Fault Tolerance Flow

Every external request follows this pattern:

```
Request → Circuit Breaker → Idempotency Check → Processing → DLQ (if failed)
```

- **Circuit Breaker** (`common/circuit_breaker.py`): 3 states (CLOSED/OPEN/HALF_OPEN), prevents cascades
- **Idempotency** (`orchestrator/budget_controller.py`): Redis-backed with 5min TTL
- **DLQ** (`messaging/jetstream_setup.py`): Failed messages logged to database

**Key Pattern**: Wrap risky operations with circuit breakers. Use `request_id` for idempotency.

### Learning Governance

Agent learning is controlled by database-enforced rules:

```sql
-- Check if update allowed
SELECT can_auto_update_prompt('developer');  -- Returns TRUE/FALSE

-- Constraints checked:
-- 1. Daily limit (e.g., 5/day for developer)
-- 2. Cooldown period (e.g., 2 hours)
-- 3. Requires approval? (e.g., security role always requires)
```

**Key Files**:
- `migrations/003_learning_governance.sql`: Schema and functions
- Database table `learning_governance`: Per-role configuration
- View `governance_status`: Real-time status

**Key Pattern**: Before auto-learning, check governance with SQL function `can_auto_update_prompt()`.

### Data Flow: Budget Allocation

Idempotent budget requests with exactly-once semantics:

1. Client sends `request_id` (UUID for idempotency)
2. `budget_controller.py` checks Redis cache for duplicate
3. If new: atomic PostgreSQL update with constraint check
4. Cache result in Redis (5min TTL)
5. Duplicate requests get cached response

**Key Tables**:
- `budget_limits`: Tenant/project limits and current usage
- `budget_transactions`: Audit trail (reserve/commit/release)

**Key Pattern**: Always provide `request_id` for budget operations to ensure idempotency.

## Critical Components

### LLM Security (`supervisor_optimizer/llm_utils.py`)

All LLM responses MUST be validated:

```python
# Validates against JSON schema, sanitizes injections
result = safe_parse_synthesis(llm_response)

# Available parsers:
# - safe_parse_synthesis() - Task action plans
# - safe_parse_patterns() - Pattern analysis
# - safe_parse_consensus() - Voting decisions
```

Each parser enforces strict JSON schemas and removes SQL/script/command injections.

### RBAC System (`api/security.py`)

5 roles with different permissions and rate limits:

| Role | Rate Limit | Key Permissions |
|------|-----------|-----------------|
| admin | 100/min | All permissions |
| operator | 50/min | Escalations, tasks |
| developer | 30/min | Task CRUD |
| observer | 20/min | Read-only |
| anonymous | 5/min | Minimal |

```python
# Protect endpoints with permissions
@app.post("/escalations/{id}/resolve")
@rbac.require_permission(Permission.ESCALATION_RESOLVE)
async def resolve_escalation(user=Depends(rbac.verify_token)):
    # User guaranteed to have permission
    ...
```

### Circuit Breaker Registry (`common/circuit_breaker.py`)

Register breakers for monitoring:

```python
from common.circuit_breaker import CircuitBreaker, circuit_breaker_registry

breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30, name="external_api")
circuit_breaker_registry.register("external_api", breaker)

# Use breaker
result = await breaker.call(risky_function, args)

# Get all breaker stats
stats = circuit_breaker_registry.get_all_stats()
```

## Database Schema Key Tables

- **tasks**: Task state machine (id, state, metadata)
- **agents**: Agent registry (id, role, status)
- **agent_prompts**: Learning history with governance columns
- **peer_review_sessions**: Consensus voting sessions
- **peer_votes**: Individual agent votes with weights
- **escalations**: Failed consensus requiring supervisor
- **budget_limits**: Per-tenant/project token budgets
- **budget_transactions**: Audit trail for budget operations
- **dlq_messages**: Failed messages with retry count
- **audit_log**: Security-relevant actions

**Important**: `learning_governance` table controls auto-learning rates per role.

## When Modifying Code

### Adding New LLM Response Type

1. Define JSON schema in `supervisor_optimizer/llm_utils.py`
2. Create parser function (e.g., `safe_parse_new_type`)
3. Add to documentation

### Adding New RBAC Permission

1. Add to `Permission` class in `api/security.py`
2. Update `ROLE_PERMISSIONS` mapping
3. Document in role table

### Adding New Agent Role

1. Add governance rule: `INSERT INTO learning_governance (agent_role, ...) VALUES (...)`
2. Update `ROLE_PERMISSIONS` in `api/security.py` if needed
3. Test with `SELECT can_auto_update_prompt('new_role')`

### Modifying Budget Logic

ALWAYS maintain idempotency:
- Use `request_id` for deduplication
- Update both `budget_limits` and `budget_transactions`
- Invalidate Redis cache after DB changes

## Documentation

Entry points by use case:

- **New developers**: Start with `LAUNCH_SUCCESS.md` (if system is running) or `README_V5.1.md`
- **Quick setup**: `QUICK_START_V5.1.md` (30-minute guide)
- **Architecture understanding**: `ARCHITECTURE_V5.1_DIAGRAM.md` (diagrams)
- **Production deployment**: `DEPLOYMENT_CHECKLIST.md` (step-by-step)
- **API reference**: `demo_server.py` (example endpoints)
- **File navigation**: `INDEX_V5.1.md` (complete index)

## Common Patterns to Follow

### Security Validation
```python
# ALWAYS sanitize and validate LLM responses
from supervisor_optimizer.llm_utils import safe_parse_synthesis

result = safe_parse_synthesis(llm_response)  # Raises ValueError if invalid
```

### Fault Tolerance
```python
# Wrap external calls with circuit breakers
from common.circuit_breaker import CircuitBreaker

breaker = CircuitBreaker(failure_threshold=5, name="service")
result = await breaker.call(external_service, args)
```

### Idempotent Operations
```python
# Always use request_id for idempotency
from orchestrator.budget_controller import IdempotentBudgetController

decision = await budget.request_tokens(
    request_id=str(uuid.uuid4()),  # Idempotency key
    ...
)
```

### Governance Checks
```sql
-- Check before auto-learning
SELECT can_auto_update_prompt('agent_role');
```

## Infrastructure Dependencies

- **PostgreSQL 14+**: Primary data store (11 tables)
- **Redis 7+**: Cache + idempotency layer
- **NATS with JetStream**: Message bus with DLQ
- **Docker**: For NATS and optional gVisor sandbox

## Environment Variables (.env)

Required variables:
- `JWT_SECRET`: Strong random secret for JWT tokens
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `NATS_URL`: NATS server URL

Optional:
- `SANDBOX_RATE_LIMIT`: Rate limit for sandbox (default: 10/minute)
- `DEFAULT_TENANT_LIMIT`: Default token budget (default: 1000000)
- `HOST`, `PORT`: Server binding (default: 0.0.0.0:8000)
