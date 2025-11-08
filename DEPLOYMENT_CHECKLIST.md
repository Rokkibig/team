# ✅ Golden Architecture V5.1 - Deployment Checklist

**Complete this checklist before production deployment**

---

## 🔐 Pre-Deployment Security

### Secrets & Configuration
- [ ] Generate strong JWT secret: `openssl rand -hex 32`
- [ ] Store JWT_SECRET in K8s secrets (not .env)
- [ ] Rotate default database passwords
- [ ] Configure TLS certificates for all endpoints
- [ ] Review and restrict database user permissions
- [ ] Enable PostgreSQL SSL connections
- [ ] Configure Redis AUTH password
- [ ] Verify NATS authentication enabled

### Security Hardening
- [ ] Review all RBAC permissions
- [ ] Test rate limiting for all roles
- [ ] Verify sandbox isolation (try to break out)
- [ ] Run OWASP ZAP security scan
- [ ] Test LLM injection prevention
- [ ] Verify audit logging captures all actions
- [ ] Test circuit breaker failure scenarios
- [ ] Enable firewall rules for all services

---

## 💾 Database Setup

### Schema Migration
- [ ] Backup existing database (if upgrading)
- [ ] Run migration 001: `psql -f migrations/001_initial_schema.sql`
- [ ] Run migration 002: `psql -f migrations/002_peer_review.sql`
- [ ] Run migration 003: `psql -f migrations/003_learning_governance.sql`
- [ ] Verify all tables created: `\dt` in psql
- [ ] Verify all functions: `\df` in psql
- [ ] Test governance functions: `SELECT can_auto_update_prompt('developer');`

### Database Configuration
- [ ] Set `max_connections` appropriate for pod count
- [ ] Enable `synchronous_commit = on` for durability
- [ ] Configure connection pooling (e.g., PgBouncer)
- [ ] Setup daily automated backups
- [ ] Test backup restoration procedure
- [ ] Configure replication (if using HA)
- [ ] Set up monitoring for slow queries

---

## 🔧 Infrastructure Setup

### Redis
- [ ] Deploy Redis with persistence enabled
- [ ] Configure `maxmemory-policy allkeys-lru`
- [ ] Set appropriate `maxmemory` limit
- [ ] Enable AOF persistence
- [ ] Test failover (if using Redis Sentinel)
- [ ] Verify TTL expiration works

### NATS JetStream
- [ ] Deploy NATS with JetStream enabled
- [ ] Run setup script: `python -m messaging.jetstream_setup`
- [ ] Verify streams created: `nats stream ls`
  - [ ] PRC stream
  - [ ] ESCALATIONS stream
  - [ ] DLQ stream
- [ ] Verify consumers attached
- [ ] Test message publish/subscribe
- [ ] Configure file storage limits
- [ ] Test DLQ worker processing

### Kubernetes
- [ ] Create namespace: `kubectl create ns golden-architecture`
- [ ] Apply RBAC policies
- [ ] Create ConfigMaps for non-secret config
- [ ] Create Secrets for sensitive data
- [ ] Apply HPA configs: `kubectl apply -f k8s/hpa-configs.yaml`
- [ ] Verify HPA active: `kubectl get hpa -n golden-architecture`
- [ ] Configure resource requests/limits for all pods
- [ ] Set up Pod Disruption Budgets

---

## 📦 Application Deployment

### Build & Push Images
- [ ] Build Orchestrator image: `docker build -t orchestrator:v5.1 .`
- [ ] Build Peer Hub image: `docker build -t peer-hub:v5.1 .`
- [ ] Build Supervisor image: `docker build -t supervisor:v5.1 .`
- [ ] Build Sandbox Executor: `docker build -t sandbox:v5.1 ./sandbox_executor`
- [ ] Push to registry: `docker push <registry>/...`
- [ ] Scan images for vulnerabilities: `trivy image <image>`

### Deploy Services
- [ ] Deploy Orchestrator: `kubectl apply -f k8s/orchestrator-deployment.yaml`
- [ ] Deploy Peer Hub: `kubectl apply -f k8s/peer-hub-deployment.yaml`
- [ ] Deploy Supervisor: `kubectl apply -f k8s/supervisor-deployment.yaml`
- [ ] Deploy Sandbox Executor: `kubectl apply -f k8s/sandbox-deployment.yaml`
- [ ] Verify all pods running: `kubectl get pods -n golden-architecture`
- [ ] Check pod logs for errors: `kubectl logs -n golden-architecture <pod>`

### Start Workers
- [ ] Deploy DLQ worker as K8s Deployment
- [ ] Verify worker consuming from DLQ: check logs
- [ ] Test DLQ processing with failed message

---

## 📊 Monitoring & Observability

### Prometheus
- [ ] Deploy Prometheus: `kubectl apply -f k8s/prometheus.yaml`
- [ ] Configure ServiceMonitors for all services
- [ ] Verify metrics scraped: check Prometheus targets
- [ ] Import alert rules: `kubectl apply -f k8s/hpa-configs.yaml`
- [ ] Test alerts fire correctly (simulate failure)

### Grafana
- [ ] Deploy Grafana
- [ ] Add Prometheus datasource
- [ ] Import dashboards for:
  - [ ] System overview
  - [ ] Peer review metrics
  - [ ] Budget utilization
  - [ ] Sandbox performance
  - [ ] Circuit breaker states
- [ ] Configure alerting channels (Slack/PagerDuty)

### Logging
- [ ] Configure structured logging (JSON format)
- [ ] Setup log aggregation (ELK/Loki)
- [ ] Create log-based alerts for:
  - [ ] Circuit breaker OPEN
  - [ ] DLQ message count > 100
  - [ ] Security validation failures
  - [ ] Budget exhaustion

---

## 🧪 Testing

### Integration Tests
- [ ] Test full task flow end-to-end
- [ ] Test peer review consensus
- [ ] Test escalation handling
- [ ] Test learning governance
  - [ ] Auto-approve for developer role
  - [ ] Require approval for security role
  - [ ] Verify cooldown enforcement
  - [ ] Verify daily limits

### Security Tests
- [ ] Test unauthorized API access (should 401)
- [ ] Test insufficient permissions (should 403)
- [ ] Test rate limit enforcement (should 429)
- [ ] Test LLM injection attempts (should block)
- [ ] Test sandbox escape attempts (should fail)
- [ ] Test RBAC permission bypass (should fail)

### Reliability Tests
- [ ] Test circuit breaker opens after failures
- [ ] Test idempotent budget requests
- [ ] Test DLQ captures failed messages
- [ ] Test auto-fix for guard failures
- [ ] Verify message retry logic
- [ ] Test database connection pool exhaustion

### Performance Tests
- [ ] Load test with 10x expected traffic
- [ ] Verify HPA scales up under load
- [ ] Verify HPA scales down when idle
- [ ] Test p95 latency under load < SLO
- [ ] Test concurrent budget requests (no race conditions)
- [ ] Test sandbox execution queue

---

## 🚀 Deployment

### Pre-Production
- [ ] Deploy to staging environment
- [ ] Run smoke tests on staging
- [ ] Verify all integrations work
- [ ] Test DR procedures (backup/restore)
- [ ] Review deployment plan with team
- [ ] Schedule maintenance window (if needed)

### Production Rollout
- [ ] Create backup of current production (if upgrading)
- [ ] Deploy with canary (10% traffic first)
- [ ] Monitor error rates for 30 minutes
- [ ] Gradually increase traffic (50%, 100%)
- [ ] Monitor SLO metrics:
  - [ ] Peer review p95 < 180s
  - [ ] Task completion > 95%
  - [ ] Sandbox timeout < 5%
  - [ ] Error rate < 1%

### Post-Deployment
- [ ] Verify all services healthy
- [ ] Check circuit breaker states (should be CLOSED)
- [ ] Check DLQ message count (should be low)
- [ ] Verify budget operations working
- [ ] Test learning governance
- [ ] Check audit logs are written
- [ ] Verify metrics collected
- [ ] Test alerts fire (simulate issue)

---

## 🔍 Validation

### Functional Validation
```bash
# 1. Test API with valid token
export TOKEN="<admin-token>"
curl -H "Authorization: Bearer $TOKEN" \
  https://api.example.com/health

# 2. Test unauthorized access (should fail)
curl https://api.example.com/escalations
# Expected: 401 Unauthorized

# 3. Test idempotent budget
curl -X POST https://api.example.com/budget/request \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"request_id": "unique-123", "tokens": 1000, ...}'
# Run twice with same request_id, should return same result

# 4. Check HPA status
kubectl get hpa -n golden-architecture
# Should show current/desired replicas

# 5. Check circuit breaker stats
curl https://api.example.com/metrics | grep circuit_breaker_state

# 6. Check DLQ
psql -c "SELECT COUNT(*) FROM dlq_messages WHERE resolved = FALSE;"
# Should be 0 or very low

# 7. Test learning governance
psql -c "SELECT * FROM governance_status;"
# Should show all roles with correct status
```

### Performance Validation
```bash
# Load test with Apache Bench
ab -n 1000 -c 10 -H "Authorization: Bearer $TOKEN" \
  https://api.example.com/tasks

# Monitor during load test
kubectl top pods -n golden-architecture
watch kubectl get hpa -n golden-architecture

# Check p95 latency in Grafana
# Should be < SLO targets
```

### Security Validation
```bash
# Try SQL injection
curl -X POST https://api.example.com/tasks \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"name": "test; DROP TABLE users;"}'
# Should be sanitized/blocked

# Try sandbox escape
curl -X POST https://api.example.com/sandbox/execute \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"code": "import os; os.system(\"cat /etc/passwd\")"}'
# Should fail with permission denied
```

---

## 📋 Operational Runbooks

### Circuit Breaker Stuck OPEN
```bash
# 1. Check why it's open
kubectl logs -n golden-architecture <orchestrator-pod> | grep "circuit.*OPEN"

# 2. Fix underlying issue (e.g., downstream service)

# 3. Manual reset (if safe)
curl -X POST https://api.example.com/admin/circuit-breaker/reset \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"name": "external_api"}'
```

### DLQ Messages Piling Up
```sql
-- 1. Check DLQ
SELECT original_subject, error_count, data
FROM dlq_messages
WHERE resolved = FALSE
ORDER BY created_at DESC
LIMIT 10;

-- 2. Investigate common failures
SELECT original_subject, COUNT(*)
FROM dlq_messages
WHERE resolved = FALSE
GROUP BY original_subject;

-- 3. Fix issue, then manually retry
-- (Republish messages after fix)
```

### Budget Exhausted
```sql
-- 1. Check current usage
SELECT * FROM budget_limits WHERE tenant_id = 'tenant-123';

-- 2. Review recent transactions
SELECT * FROM budget_transactions
WHERE tenant_id = 'tenant-123'
ORDER BY timestamp DESC
LIMIT 20;

-- 3. Increase limit (if legitimate)
UPDATE budget_limits
SET total_limit = 2000000
WHERE tenant_id = 'tenant-123';
```

### Learning Updates Blocked
```sql
-- 1. Check status
SELECT * FROM governance_status WHERE agent_role = 'developer';

-- 2. Check pending approvals
SELECT * FROM agent_prompts
WHERE approval_status = 'pending_review';

-- 3. Approve/reject
SELECT approve_prompt_update(123, 'admin', 'Reviewed and approved');

-- Or reset cooldown (emergency)
UPDATE learning_governance
SET last_update_at = NULL
WHERE agent_role = 'developer';
```

---

## 🎯 Success Criteria

### System Health
- [ ] All pods in Running state
- [ ] All circuit breakers in CLOSED state
- [ ] DLQ message count < 10
- [ ] No errors in logs (last 1 hour)
- [ ] All HPA functioning correctly

### Performance
- [ ] Peer review p95 < 180s
- [ ] Task completion rate > 95%
- [ ] Sandbox execution p95 < 30s
- [ ] API response p95 < 1s
- [ ] Database query p95 < 100ms

### Security
- [ ] All API calls authenticated
- [ ] RBAC enforced correctly
- [ ] Rate limiting active
- [ ] Audit logs written
- [ ] No security alerts

### Reliability
- [ ] Circuit breakers tested
- [ ] Idempotency verified
- [ ] DLQ processing
- [ ] Auto-fix working
- [ ] Backups running

---

## 📞 Emergency Contacts

- **On-call Engineer**: [Slack/Phone]
- **Database Admin**: [Contact]
- **Security Team**: [Contact]
- **Platform Team**: [Contact]

---

## 📚 Documentation Links

- [Implementation Guide](GOLDEN_ARCHITECTURE_V5.1_IMPLEMENTATION.md)
- [Quick Start](QUICK_START_V5.1.md)
- [Architecture Diagram](ARCHITECTURE_V5.1_DIAGRAM.md)
- [Summary](V5.1_SUMMARY.md)

---

**Deployment Checklist Complete! ✅**

**Sign-off:**
- [ ] Technical Lead: _________________ Date: _______
- [ ] Security Review: ________________ Date: _______
- [ ] Operations: ____________________ Date: _______
- [ ] Product Owner: _________________ Date: _______

**Production deployment approved! 🚀**
