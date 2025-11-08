# Golden Architecture V5.1 - Staging Deployment Guide

## ✅ Pre-Deployment Checklist

### Infrastructure Ready
- [ ] PostgreSQL 14+ доступний
- [ ] Redis 7+ доступний
- [ ] NATS JetStream доступний
- [ ] Python 3.11+ встановлено

### Configuration
- [ ] `.env` створено на основі `.env.example`
- [ ] `JWT_SECRET` ротовано (використати: `openssl rand -hex 32`)
- [ ] `CORS_ALLOW_ORIGINS` налаштовано для staging доменів
- [ ] `LOGIN_MAX_ATTEMPTS` і `LOGIN_LOCKOUT_TTL_SECONDS` встановлено
- [ ] Database credentials валідні

### Security
- [ ] TLS/HTTPS налаштовано
- [ ] `/metrics` endpoint захищено (NetworkPolicy/allowlist)
- [ ] Secrets в secret manager (не в .env файлі в репозиторії)
- [ ] Structured JSON logging увімкнено

## 📋 Staging Runbook

### 1. Підготовка Інфраструктури

**Docker Compose (локально/staging)**:
```bash
docker run -d -p 5432:5432 -e POSTGRES_DB=golden_arch -e POSTGRES_PASSWORD=secure_pass postgres:15
docker run -d -p 6379:6379 redis:7-alpine
docker run -d -p 4222:4222 nats:latest -js
```

**Перевірка доступності**:
```bash
nc -z localhost 5432  # PostgreSQL
nc -z localhost 6379  # Redis
nc -z localhost 4222  # NATS
```

### 2. Встановлення Залежностей

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Додаткові залежності для production
pip install bcrypt prometheus-client
```

### 3. Налаштування Environment

**Створити `.env`**:
```bash
cp .env.example .env
# Редагувати .env з реальними значеннями
```

**Критичні змінні**:
```env
DATABASE_URL=postgresql://user:secure_pass@postgres:5432/golden_arch
REDIS_URL=redis://redis:6379/0
NATS_URL=nats://nats:4222
JWT_SECRET=$(openssl rand -hex 32)
CORS_ALLOW_ORIGINS=https://staging.example.com
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCKOUT_TTL_SECONDS=900
```

### 4. Міграції БД

```bash
python scripts/migrate.py
```

**Перевірка**:
```sql
SELECT version, checksum, applied_at
FROM schema_migrations
ORDER BY applied_at;
```

Очікувані версії: `001`, `002`, `003`

### 5. Запуск Сервера

```bash
uvicorn demo_server:app --host 0.0.0.0 --port 8000
```

**Перевірка startup logs**:
- ✅ Database pool created
- ✅ Redis connected
- ✅ Rate limiter initialized with Redis storage
- ✅ CORS enabled for origins: [...]

### 6. Smoke Tests

**Health Check**:
```bash
curl -s http://localhost:8000/health | jq
```

**Expected**:
```json
{
  "status": "healthy",
  "components": {
    "api": "healthy",
    "database": "healthy",
    "redis": "healthy",
    "nats": "healthy"
  }
}
```

**Login Test (Success)**:
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | jq
```

**Expected**: JWT token + role + permissions

**Login Test (Fail - Wrong Password)**:
```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"wrong"}' | jq
```

**Expected**:
```json
{
  "error_code": "auth.invalid_credentials",
  "message": "auth.invalid_credentials: Invalid credentials",
  "request_id": "..."
}
```

**Login Test (Lockout after 5 attempts)**:
```bash
# Зробити 6 спроб з неправильним паролем
for i in {1..6}; do
  curl -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"wrong"}'
  echo ""
done
```

**Expected на 6-й спробі**:
```json
{
  "error_code": "rate_limit.exceeded",
  "message": "rate_limit.exceeded: Too many login attempts. Try again in 15 minutes",
  "request_id": "..."
}
```

**Budget Insufficient Test**:
```bash
# Отримати токен
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | jq -r '.token')

# Запит з надмірними токенами
curl -X POST http://localhost:8000/api/v1/budget/request \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "tenant_id":"test",
    "project_id":"demo",
    "task_id":"t1",
    "model":"gpt-4",
    "estimated_tokens":999999999
  }' | jq
```

**Expected**:
```json
{
  "error_code": "budget.insufficient",
  "message": "budget.insufficient: Available 100000, Requested 999999999",
  "request_id": "..."
}
```

**Prometheus Metrics**:
```bash
curl -s http://localhost:8000/metrics | grep "auth_logins_total"
```

**Expected**:
```
# HELP auth_logins_total Total authentication attempts
# TYPE auth_logins_total counter
auth_logins_total{result="success"} 1.0
auth_logins_total{result="fail"} 5.0
```

**DLQ List**:
```bash
curl -s http://localhost:8000/api/v1/dlq?resolved=false&limit=10 \
  -H "Authorization: Bearer $TOKEN" | jq
```

**Expected**: `[]` (порожній масив, якщо немає повідомлень)

**Circuit Breakers (Admin Only)**:
```bash
curl -X POST http://localhost:8000/api/v1/circuit-breakers/reset_all \
  -H "Authorization: Bearer $TOKEN" | jq
```

**Expected**:
```json
{
  "status": "success",
  "reset_count": 0,
  "breakers": []
}
```

### 7. Observability Verification

**Prometheus Metrics Endpoint**:
```bash
curl -s http://localhost:8000/metrics | head -50
```

**Verify metrics exist**:
- `http_requests_total{route,method,status}`
- `http_request_duration_seconds{route,method}`
- `auth_logins_total{result}`
- `budget_requests_total{status}`
- `budget_commits_total`
- `budget_releases_total`
- `dlq_resolved_total`
- `breaker_resets_total`

**Audit Logging**:
```sql
SELECT user_id, role, action, resource_type, created_at
FROM audit_log
ORDER BY created_at DESC
LIMIT 10;
```

**Expected actions**:
- `auth.login.success`
- `auth.login.fail`
- `budget.commit`
- `budget.release`
- `dlq.resolve`
- `breakers.reset_all`

### 8. RBAC & Rate Limits

**Test 403 (Non-admin trying DLQ resolve)**:
```bash
# Login as operator
OP_TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"operator","password":"operator123"}' | jq -r '.token')

# Try to resolve DLQ (requires SYSTEM_ADMIN permission)
curl -X POST http://localhost:8000/api/v1/dlq/123/resolve \
  -H "Authorization: Bearer $OP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"note":"test","requeue":false}' | jq
```

**Expected**:
```json
{
  "error_code": "auth.forbidden",
  "message": "Insufficient permissions",
  "request_id": "..."
}
```

**Test 429 (Rate Limit)**:
```bash
# Make 10 rapid login attempts
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username":"test","password":"test"}' &
done
wait
```

**Expected**: Some requests should return 429

## 🚨 Common Issues & Fixes

### 500 при зверненні до БД
**Причина**: Неправильний `DATABASE_URL` або БД недоступна
**Рішення**: Перевірити `nc -z localhost 5432` і credentials

### 401/403 неочікувано
**Причина**: JWT expired або неправильні permissions
**Рішення**: Перевірити `exp` в токені, `ROLE_PERMISSIONS` в `api/security.py`

### CORS помилки
**Причина**: Origin не в `CORS_ALLOW_ORIGINS`
**Рішення**: Додати origin до ENV змінної

### Метрики не експортуються
**Причина**: `prometheus-client` не встановлено
**Рішення**: `pip install prometheus-client`

### Redis lockout не працює
**Причина**: Redis недоступний або rate limiter не використовує Redis storage
**Рішення**: Перевірити `REDIS_URL` та startup logs для "Rate limiter initialized with Redis storage"

## 📊 Production Cutover Checklist

### Pre-Cutover
- [ ] Секрети ротовані через secret manager
- [ ] `JWT_SECRET` змінено, TTL токенів скорочено
- [ ] `CORS_ALLOW_ORIGINS` встановлено тільки для prod доменів
- [ ] TLS сертифікати валідні
- [ ] `/metrics` endpoint обмежено (не публічний)
- [ ] Structured JSON logs налаштовано

### Database
- [ ] Backup створено перед deployment
- [ ] `python scripts/migrate.py` виконано успішно (0 failed, 0 checksum mismatch)
- [ ] Індекси для `dlq_messages.created_at`, `audit_log.created_at` створено
- [ ] Retention policy для audit/DLQ визначено

### Deployment (K8s)
- [ ] Container images побудовані з pinned tags
- [ ] Readiness/liveness probes налаштовано
- [ ] CPU/Memory limits встановлено
- [ ] HPA налаштовано з реальними метриками
- [ ] Debug режими вимкнено (LOG_LEVEL=INFO/WARN)

### Monitoring
- [ ] Prometheus scrape `/metrics` endpoint
- [ ] Grafana dashboards імпортовано
- [ ] Alerts налаштовано (5xx >1%, latency P95 >1s, 429 rate)
- [ ] ServiceMonitor створено (якщо K8s)

### Post-Cutover
- [ ] E2E flows протестовано (login → protected endpoint)
- [ ] Latency у межах SLO
- [ ] 5xx/4xx rate нормальний
- [ ] Audit logs пишуться
- [ ] On-call team notified
- [ ] Rollback plan готовий

## 🔄 Rollback Plan

**Швидкий rollback** (якщо deployment fails):
```bash
# K8s
kubectl rollout undo deployment/golden-api -n golden-architecture

# Docker Compose
docker compose down
docker compose up -d --build <previous-tag>
```

**Database rollback**:
- Restore з backup
- Уникайте руйнівних змін схеми без міграції назад

**Feature flags**:
- Тимчасово вимкнути критичні route через feature toggle (якщо реалізовано)

## 📞 Support Contacts

- **On-call**: [slack-channel] або [pagerduty]
- **Incident Response**: [runbook-link]
- **Logs**: `kubectl logs -n golden-architecture deployment/golden-api -f`
- **Metrics**: [grafana-dashboard-url]

## 📈 Success Criteria

**24-48 hours after cutover**:
- [ ] Latency P95 < 500ms
- [ ] Error rate < 0.5%
- [ ] No critical incidents
- [ ] Audit logs populated
- [ ] 429 rate acceptable (<1% of traffic)
- [ ] All monitoring alerts green

---

**Document Version**: 1.0
**Last Updated**: 2025-11-08
**Maintained by**: DevOps Team
