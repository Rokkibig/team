# ⚡ Golden Architecture V5.1 - Quick Start Guide

**Get battle-hardened production system running in 30 minutes**

---

## 🚀 Prerequisites

- Docker with gVisor support (optional but recommended)
- PostgreSQL 14+
- Redis 7+
- NATS with JetStream enabled
- Python 3.11+
- Kubernetes cluster (for auto-scaling)

---

## 📦 Installation

### Step 1: Install Dependencies

```bash
# Install Python dependencies
pip install -r requirements.txt

# Additional security packages
pip install jsonschema slowapi python-jose[cryptography]
```

### Step 2: Setup Database

```bash
# Run all migrations
psql $DATABASE_URL -f migrations/001_initial_schema.sql
psql $DATABASE_URL -f migrations/002_peer_review.sql
psql $DATABASE_URL -f migrations/003_learning_governance.sql

# Verify
psql $DATABASE_URL -c "SELECT * FROM governance_status;"
```

### Step 3: Configure Environment

```bash
# Create .env file
cat > .env << EOF
# Security (CHANGE IN PRODUCTION!)
JWT_SECRET=$(openssl rand -hex 32)

# Database
DATABASE_URL=postgresql://user:pass@localhost/golden_arch

# Redis (for idempotency)
REDIS_URL=redis://localhost:6379/0

# NATS
NATS_URL=nats://localhost:4222

# Sandbox
SANDBOX_RATE_LIMIT=10/minute
SANDBOX_TIMEOUT=30

# Budget
DEFAULT_TENANT_LIMIT=1000000
EOF

# Load environment
export $(cat .env | xargs)
```

### Step 4: Start NATS with JetStream

```bash
# Using Docker
docker run -d --name nats \
  -p 4222:4222 \
  -p 8222:8222 \
  nats:latest \
  -js

# Verify JetStream is enabled
curl http://localhost:8222/jsz
```

### Step 5: Setup Redis

```bash
# Using Docker
docker run -d --name redis \
  -p 6379:6379 \
  redis:7-alpine

# Verify
redis-cli ping  # Should return PONG
```

---

## 🔧 Quick Configuration

### Initialize JetStream Streams

```python
# Run once to setup streams
python -c "
import asyncio
import nats
from messaging.jetstream_setup import setup_jetstream

async def init():
    nc = await nats.connect('nats://localhost:4222')
    await setup_jetstream(nc)
    print('✅ JetStream configured')
    await nc.close()

asyncio.run(init())
"
```

### Create Initial User Tokens

```python
# Generate admin token
python -c "
from api.security import rbac

token = rbac.generate_token(
    user_id='admin',
    role='admin',
    expires_in_hours=720  # 30 days
)

print('Admin Token:')
print(token)
"
```

### Setup Learning Governance

```sql
-- Already done by migration, but verify
SELECT agent_role, status FROM governance_status;

-- Should show:
-- security: requires_approval
-- developer: can_auto_update
-- reviewer: can_auto_update
-- tester: can_auto_update
```

---

## 🏃 Running Services

### 1. Start Sandbox Executor

```bash
# Terminal 1
cd sandbox_executor
uvicorn secure_executor:app --host 0.0.0.0 --port 8001

# Test
curl http://localhost:8001/health
```

### 2. Start Main API (with RBAC)

```bash
# Terminal 2
# Your main API server with RBAC middleware
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 3. Start DLQ Worker

```python
# Terminal 3
python -c "
import asyncio
import nats
from messaging.jetstream_setup import DLQWorker
import asyncpg

async def run_dlq_worker():
    # Connect to NATS
    nc = await nats.connect('nats://localhost:4222')

    # Connect to DB
    db_pool = await asyncpg.create_pool('$DATABASE_URL')

    # Start worker
    worker = DLQWorker(nc, db_pool)
    await worker.start()

asyncio.run(run_dlq_worker())
"
```

---

## 🧪 Testing

### Test 1: LLM Security

```python
from supervisor_optimizer.llm_utils import safe_parse_synthesis

# Valid response
valid = '''
{
  "synthesis_reasoning": "Need to add tests",
  "action_plan": [
    {
      "priority": 1,
      "type": "test",
      "issue": "Add unit tests",
      "agent": "tester"
    }
  ]
}
'''

result = safe_parse_synthesis(valid)
print("✅ Valid parsed:", result.action_plan)

# Invalid response (will raise ValueError)
invalid = "DROP TABLE users; SELECT * FROM secrets;"
try:
    safe_parse_synthesis(invalid)
except ValueError as e:
    print("✅ Attack blocked:", e)
```

### Test 2: RBAC

```bash
# Get admin token (from earlier)
export TOKEN="<your-admin-token>"

# Test authorized access
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/escalations

# Test unauthorized access
curl http://localhost:8000/escalations
# Should return 401 Unauthorized
```

### Test 3: Idempotent Budget

```python
from orchestrator.budget_controller import IdempotentBudgetController
import redis.asyncio as redis
import asyncpg

async def test_idempotency():
    r = await redis.from_url('redis://localhost:6379/0')
    db = await asyncpg.create_pool('$DATABASE_URL')

    budget = IdempotentBudgetController(r, db)

    # First request
    result1 = await budget.request_tokens(
        purpose="test",
        estimated_tokens=1000,
        model="gpt-4",
        task_id="task-123",
        project_id="proj-1",
        tenant_id="tenant-1",
        request_id="unique-123"  # Idempotency key
    )
    print("First request:", result1)

    # Duplicate request (same request_id)
    result2 = await budget.request_tokens(
        purpose="test",
        estimated_tokens=1000,
        model="gpt-4",
        task_id="task-123",
        project_id="proj-1",
        tenant_id="tenant-1",
        request_id="unique-123"  # Same key!
    )
    print("Duplicate request:", result2)

    # Should be identical and not double-charge
    assert result1.request_id == result2.request_id
    print("✅ Idempotency works!")

asyncio.run(test_idempotency())
```

### Test 4: Circuit Breaker

```python
from common.circuit_breaker import CircuitBreaker, CircuitOpenException
import asyncio

breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=5,
    name="test"
)

async def flaky_function():
    raise Exception("Simulated failure")

async def test_breaker():
    # Try until circuit opens
    for i in range(5):
        try:
            await breaker.call(flaky_function)
        except Exception as e:
            print(f"Attempt {i+1}: {e}")
            if isinstance(e, CircuitOpenException):
                print("✅ Circuit breaker opened!")
                break
        await asyncio.sleep(0.1)

    # Check state
    stats = breaker.get_stats()
    print(f"State: {stats.state}, Failures: {stats.total_failures}")

asyncio.run(test_breaker())
```

### Test 5: Sandbox Security

```bash
# Try to execute safe code
curl -X POST http://localhost:8001/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "print(\"Hello World\")",
    "timeout": 10
  }'

# Try malicious code (should be blocked)
curl -X POST http://localhost:8001/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import os; os.system(\"rm -rf /\")",
    "timeout": 10
  }'
# Should fail due to disabled os module
```

---

## 📊 Monitoring Setup (Optional)

### Prometheus

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'golden-architecture'
    static_configs:
      - targets:
        - 'localhost:8000'  # Main API
        - 'localhost:8001'  # Sandbox
    metrics_path: '/metrics'
    scrape_interval: 30s
```

### Grafana Dashboard

Import dashboard template from `k8s/grafana-dashboard.json` (create separately)

Key panels:
- Peer review consensus time (p95)
- Active tasks per orchestrator
- Circuit breaker states
- Budget usage per tenant
- DLQ message count

---

## 🐳 Docker Compose (All-in-One)

```yaml
# docker-compose.yml
version: '3.8'

services:
  postgres:
    image: postgres:14
    environment:
      POSTGRES_PASSWORD: changeme
      POSTGRES_DB: golden_arch
    ports:
      - "5432:5432"
    volumes:
      - ./migrations:/docker-entrypoint-initdb.d

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  nats:
    image: nats:latest
    command: ["-js"]
    ports:
      - "4222:4222"
      - "8222:8222"

  sandbox:
    build: ./sandbox_executor
    ports:
      - "8001:8001"
    environment:
      - SANDBOX_RATE_LIMIT=10/minute
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL

  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://postgres:changeme@postgres/golden_arch
      - REDIS_URL=redis://redis:6379/0
      - NATS_URL=nats://nats:4222
      - JWT_SECRET=${JWT_SECRET}
    depends_on:
      - postgres
      - redis
      - nats
```

```bash
# Start everything
docker-compose up -d

# Check logs
docker-compose logs -f
```

---

## ✅ Verification Checklist

Run these commands to verify everything works:

```bash
# 1. Database
psql $DATABASE_URL -c "SELECT COUNT(*) FROM learning_governance;"
# Should return 5 rows

# 2. Redis
redis-cli ping
# Should return PONG

# 3. NATS JetStream
curl http://localhost:8222/jsz | jq .streams
# Should show PRC, ESCALATIONS, DLQ streams

# 4. Sandbox
curl http://localhost:8001/health
# Should return {"status":"healthy"}

# 5. API with auth
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/health
# Should return 200 OK

# 6. Circuit breaker registry
python -c "from common.circuit_breaker import circuit_breaker_registry; print(circuit_breaker_registry.get_all_stats())"
# Should return {}

# 7. Learning governance
psql $DATABASE_URL -c "SELECT * FROM can_auto_update_prompt('developer');"
# Should return TRUE
```

---

## 🔥 Common Issues

### Issue: "Circuit breaker stuck OPEN"
```python
# Manual reset
from common.circuit_breaker import circuit_breaker_registry
await circuit_breaker_registry.reset_all()
```

### Issue: "DLQ messages piling up"
```sql
-- Check DLQ
SELECT COUNT(*) FROM dlq_messages WHERE resolved = FALSE;

-- Check errors
SELECT original_subject, error_count FROM dlq_messages ORDER BY created_at DESC LIMIT 10;
```

### Issue: "Budget exhausted"
```sql
-- Check current budget
SELECT * FROM budget_limits WHERE tenant_id = 'tenant-1';

-- Increase limit
UPDATE budget_limits SET total_limit = 2000000 WHERE tenant_id = 'tenant-1';
```

### Issue: "Learning updates blocked"
```sql
-- Check status
SELECT * FROM governance_status WHERE agent_role = 'developer';

-- Reset cooldown (emergency)
UPDATE learning_governance SET last_update_at = NULL WHERE agent_role = 'developer';
```

---

## 🎯 Next Steps

1. ✅ All services running
2. ✅ Tests passing
3. 📊 Setup monitoring dashboards
4. 🧪 Run load tests
5. 💥 Chaos testing
6. 🚀 Deploy to staging
7. 🏆 Production rollout

---

## 📞 Support

- Documentation: [`GOLDEN_ARCHITECTURE_V5.1_IMPLEMENTATION.md`](GOLDEN_ARCHITECTURE_V5.1_IMPLEMENTATION.md)
- Architecture: See main architecture docs
- Issues: Check logs in each service

---

**You're now running a battle-hardened production system! 🛡️⚔️**
