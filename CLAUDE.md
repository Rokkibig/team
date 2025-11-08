# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Golden Architecture V5.1** is a production-grade, battle-hardened multi-agent orchestration system with:
- 5-layer security (LLM validation, RBAC + JWT, Sandbox isolation, Input sanitization, Data encryption)
- Circuit breakers for fault tolerance and cascading failure prevention
- Idempotent operations with exactly-once semantics
- SLO-based auto-scaling (2-20 pods)
- Unified error handling with standard `{error_code, message, details?, request_id}` format
- Idempotent migration system with SHA256 checksum validation

**Codebase**: ~10,500 lines across 32 files | **Database**: 11 tables | **API**: 8+ endpoints with `/api/v1` versioning

## Quick Start Commands

### Environment Setup

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements-v5.1.txt

# Setup environment variables
cp .env.example .env  # Edit with your actual values
# Required: DATABASE_URL, REDIS_URL, JWT_SECRET
# Optional: NATS_URL, CORS_ALLOW_ORIGINS
```

### Database Operations

```bash
# Run idempotent migrations (RECOMMENDED - tracks in schema_migrations table)
python scripts/migrate.py

# Check migration status
psql -d golden_arch -c "SELECT version, applied_at, duration_ms FROM schema_migrations ORDER BY version;"

# OR manually run migrations (not recommended - no tracking)
psql $DATABASE_URL -f migrations/001_core_schema.sql
psql $DATABASE_URL -f migrations/002_circuit_breaker.sql
psql $DATABASE_URL -f migrations/003_learning_governance.sql

# Check governance status
psql -d golden_arch -c "SELECT * FROM governance_status;"

# Check if agent can auto-update
psql -d golden_arch -c "SELECT can_auto_update_prompt('developer');"
```

### Running Services

```bash
# Start infrastructure (requires Docker)
docker run -d --name redis -p 6379:6379 redis:7-alpine
docker run -d --name nats -p 4222:4222 nats:latest -js

# Start demo API server
uvicorn demo_server:app --host 0.0.0.0 --port 8000

# Run API tests
PORT=8000 bash test_api.sh
```

### API Testing

```bash
# Health check
curl http://localhost:8000/health

# Login (get JWT token)
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# Use token for protected endpoints
TOKEN="eyJ..."
curl http://localhost:8000/api/v1/budget/state?tenant_id=org1&project_id=proj1 \
  -H "Authorization: Bearer $TOKEN"
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

# Test unified error handling
python3 << 'EOF'
from common.error_handlers import ErrorResponse, create_error_response

# Create standard error
error = create_error_response(
    error_code="budget.insufficient",
    message="Not enough tokens",
    details={"available": 1000, "requested": 5000},
    request_id="550e8400-e29b-41d4-a716-446655440000"
)
print(f"✅ Error format: {error}")
EOF
```

## Architecture Patterns

### Multi-Layer Security

The system implements defense-in-depth with 5 layers:

1. **Network Layer**: TLS, WAF, DDoS protection
2. **API Layer** (`api/security.py`): JWT auth, RBAC (5 roles, 17 permissions), rate limiting
3. **Input Layer** (`supervisor_optimizer/llm_utils.py`): JSON schema validation, SQL/script injection prevention
4. **Execution Layer** (`sandbox_executor/secure_executor.py`): gVisor isolation, no-new-privileges, read-only FS
5. **Data Layer**: Encryption at rest/transit, audit logs

**Key Pattern**: Always validate LLM responses with `safe_parse_synthesis()` before processing.

### Unified Error Handling

All API errors follow standard format (`common/error_handlers.py`):

```json
{
  "error_code": "budget.insufficient",
  "message": "Human-readable message",
  "details": {"available": 1000, "requested": 5000},
  "request_id": "550e8400-..."
}
```

**Error code hierarchy**:
- `validation.*` (400/422) - Invalid input
- `auth.*` (401/403) - Authentication/authorization
- `resource.not_found` (404)
- `state.conflict` (409) - Budget, idempotency conflicts
- `rate_limit.exceeded` (429)
- `internal.error` (500)

**Key Pattern**: Use `install_error_handlers(app)` in FastAPI to auto-format all errors.

### Fault Tolerance Flow

Every external request follows this pattern:

```
Request → Circuit Breaker → Idempotency Check → Processing → DLQ (if failed)
```

- **Circuit Breaker** (`common/circuit_breaker.py`): 3 states (CLOSED/OPEN/HALF_OPEN), prevents cascades
- **Idempotency** (`orchestrator/budget_controller.py`): Redis-backed with 5min TTL
- **DLQ** (`messaging/jetstream_setup.py`): Failed messages logged to database

**Key Pattern**: Wrap risky operations with circuit breakers. Use `request_id` for idempotency.

### Idempotent Migrations

Migration system tracks applied migrations in `schema_migrations` table:

```sql
CREATE TABLE schema_migrations (
    version TEXT PRIMARY KEY,        -- "001", "002", "003"
    checksum TEXT NOT NULL,          -- SHA256 hash
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    duration_ms INTEGER
);
```

**Features**:
- SHA256 checksum validation (detects modified files)
- Transactional apply (atomic SQL + tracking)
- Idempotent (safe to run multiple times)
- Performance tracking (duration in ms)

**Key Pattern**: Always use `python scripts/migrate.py` instead of manual psql.

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

**API endpoints** (`/api/v1`):
- `POST /budget/request` - Reserve tokens
- `POST /budget/commit` - Confirm usage
- `POST /budget/release` - Cancel reservation
- `GET /budget/state` - Check balance

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

5 roles with different permissions:

| Role | Permissions | Description |
|------|-------------|-------------|
| admin | 17 permissions | Full system access, including DLQ resolve, breaker reset |
| operator | 11 permissions | Escalations, tasks, budget view, DLQ read |
| developer | 7 permissions | Task CRUD, budget view |
| observer | 4 permissions | Read-only access |
| anonymous | 1 permission | Health check only |

**New permissions** (recently added):
- `Permission.READ_DLQ` - View dead letter queue
- `Permission.RESOLVE_DLQ` - Resolve DLQ messages (admin only)

```python
# Protect endpoints with permissions
@app.post("/api/v1/dlq/{id}/resolve")
@rbac.require_permission(Permission.RESOLVE_DLQ)
async def resolve_dlq(user=Depends(rbac.verify_token)):
    # User guaranteed to have RESOLVE_DLQ permission
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

# Reset all breakers (admin only via API)
# POST /api/v1/circuit-breakers/reset_all
```

## Database Schema Key Tables

**Core tables**:
- `tasks` - Task state machine (id, state, metadata)
- `agents` - Agent registry (id, role, status)
- `schema_migrations` - Migration tracking (version, checksum, applied_at)

**Budget system**:
- `budget_limits` - Tenant/project limits and current usage
- `budget_transactions` - Audit trail (reserve/commit/release)

**Reliability**:
- `dlq_messages` - Failed messages with retry count
- `circuit_breaker_state` - Persisted breaker states

**Governance**:
- `learning_governance` - Per-role learning rate limits
- `learning_history` - Prompt update audit trail
- `governance_status` (VIEW) - Real-time governance status

**Audit**:
- `audit_log` - Security-relevant actions (who, what, when)

## API Endpoints (`/api/v1`)

**Auth**:
- `POST /auth/login` - Get JWT token (demo users: admin/admin123, operator/operator123)

**Budget**:
- `POST /budget/request` - Reserve tokens
- `POST /budget/commit` - Confirm usage
- `POST /budget/release` - Cancel reservation
- `GET /budget/state?tenant_id=X&project_id=Y` - Check balance

**DLQ**:
- `GET /dlq?resolved=false&limit=50` - List messages
- `GET /dlq/{id}` - Message details
- `POST /dlq/{id}/resolve` - Resolve (admin only)

**Circuit Breakers**:
- `GET /circuit-breakers` - All breaker states
- `POST /circuit-breakers/reset_all` - Reset all (admin only)

**System**:
- `GET /health` - Health check
- `GET /stats` - System statistics
- `GET /governance/status` - Governance rules status

## When Modifying Code

### Adding New API Endpoint

1. Add to `api/new_endpoints.py` (or create new router)
2. Include router in `demo_server.py` with `/api/v1` prefix
3. Add permission check if protected: `@rbac.require_permission(Permission.XXX)`
4. Use unified error format: raise `HTTPException` with appropriate status code
5. Document in this file

### Adding New LLM Response Type

1. Define JSON schema in `supervisor_optimizer/llm_utils.py`
2. Create parser function (e.g., `safe_parse_new_type`)
3. Add to documentation

### Adding New RBAC Permission

1. Add to `Permission` class in `api/security.py`
2. Update `ROLE_PERMISSIONS` mapping for relevant roles
3. Document in role table above

### Adding New Agent Role

1. Add governance rule: `INSERT INTO learning_governance (agent_role, ...) VALUES (...)`
2. Update `ROLE_PERMISSIONS` in `api/security.py` if needed
3. Test with `SELECT can_auto_update_prompt('new_role')`

### Adding New Database Migration

1. Create `migrations/00X_description.sql` (increment number)
2. Include rollback comments for reference
3. Run `python scripts/migrate.py` to apply
4. Migration will be tracked in `schema_migrations` table

### Modifying Budget Logic

ALWAYS maintain idempotency:
- Use `request_id` for deduplication
- Update both `budget_limits` and `budget_transactions`
- Invalidate Redis cache after DB changes

## Documentation

Entry points by use case:

- **Overview**: `README_V5.1.md` - Project introduction
- **Next steps**: `PRODUCTION_READINESS_ROADMAP.md` - High-priority improvements
- **Complete summary**: `FINAL_SUMMARY.md` - Full project overview
- **Code audit**: `AUDIT_FIXES_AND_FRONTEND_PLAN.md` - Fixes + frontend plan
- **Architecture**: `ARCHITECTURE_V5.1_DIAGRAM.md` - System diagrams
- **Environment**: `.env.example` - Required/optional variables

## Common Patterns to Follow

### Security Validation
```python
# ALWAYS sanitize and validate LLM responses
from supervisor_optimizer.llm_utils import safe_parse_synthesis

result = safe_parse_synthesis(llm_response)  # Raises ValueError if invalid
```

### Error Handling
```python
# Use standard error format
from fastapi import HTTPException

raise HTTPException(
    status_code=409,
    detail="budget.insufficient"  # Will be formatted by error handlers
)
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

### Database Connections
```python
# Use connection pool from app state
async with app.state.db_pool.acquire() as conn:
    rows = await conn.fetch("SELECT * FROM tasks")
```

### Governance Checks
```sql
-- Check before auto-learning
SELECT can_auto_update_prompt('agent_role');
```

## Infrastructure Dependencies

- **PostgreSQL 14+**: Primary data store (11 tables)
- **Redis 7+**: Cache, idempotency, rate limiting storage
- **NATS with JetStream**: Message bus with DLQ (optional)
- **Docker**: For Redis, NATS, and optional gVisor sandbox

## Environment Variables (.env)

**Required**:
- `DATABASE_URL` - PostgreSQL connection string
- `REDIS_URL` - Redis connection string
- `JWT_SECRET` - Strong random secret for JWT tokens (use `openssl rand -hex 32`)

**Optional**:
- `NATS_URL` - NATS server URL (for DLQ)
- `CORS_ALLOW_ORIGINS` - Comma-separated list of allowed origins
- `HOST` - Server bind address (default: 0.0.0.0)
- `PORT` - Server port (default: 8000)
- `LOGIN_MAX_ATTEMPTS` - Login brute-force limit (default: 5)
- `LOGIN_LOCKOUT_TTL_SECONDS` - Lockout duration (default: 900)

## Production Readiness Status

✅ **Complete**:
- Unified error handling
- Idempotent migrations
- DB connection pool
- Redis integration
- API versioning (`/api/v1`)
- CORS middleware
- RBAC + JWT

⏳ **Recommended additions** (see PRODUCTION_READINESS_ROADMAP.md):
- Rate limiting (SlowAPI + Redis storage)
- Password hashing (bcrypt + login lockout)
- Prometheus metrics (`/metrics` endpoint)
- CORS via ENV configuration
- Structured JSON logging

## Testing

```bash
# Run all API tests
bash test_api.sh

# Expected output:
# ✅ 1. Health Check: PASS
# ✅ 2. Root Endpoint: PASS
# ✅ 3. Governance Status: PASS
# ✅ 4. System Stats: PASS
# ✅ 5. SQL Injection Test: PASS
```

## Git Workflow

```bash
# View commit history
git log --oneline

# Recent commits:
# 951f58e 📊 Final Project Summary
# 990b81e 📋 Production Readiness Roadmap
# e363102 🗄️ Idempotent Migration System
# 0e2dcfe ✨ Unified Error Handling
# 0532fed 🔧 Redis, CORS, API Versioning
```
