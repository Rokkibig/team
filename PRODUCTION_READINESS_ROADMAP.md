# Production Readiness Roadmap
## Golden Architecture V5.1 - Наступні кроки

---

## ✅ Вже реалізовано (Production-Ready)

### 1. Unified Error Handling ✅
- **Файл**: [common/error_handlers.py](common/error_handlers.py)
- **Формат**: `{error_code, message, details?, request_id}`
- **Handlers**: HTTPException, ValidationError, RateLimitExceeded, Generic
- **Інтеграція**: `install_error_handlers(app)` в demo_server.py

### 2. Idempotent Migrations ✅
- **Файл**: [scripts/migrate.py](scripts/migrate.py)
- **Таблиця**: `schema_migrations(version, checksum, applied_at, duration_ms)`
- **Features**: SHA256 checksum, version extraction, transactional apply
- **Usage**: `python scripts/migrate.py`

### 3. Database Connection Pool ✅
- **Файл**: [demo_server.py](demo_server.py#L46-L52)
- **Pool**: `asyncpg.create_pool(min_size=2, max_size=10)`
- **Lifecycle**: Automatic startup/shutdown via lifespan

### 4. Redis Integration ✅
- **Файл**: [demo_server.py](demo_server.py#L55-L60)
- **Client**: `redis.asyncio.from_url(REDIS_URL)`
- **Usage**: Budget operations, rate limiting storage

### 5. API Versioning ✅
- **Prefix**: `/api/v1`
- **Endpoints**: Auth, Budget, DLQ, Circuit Breakers
- **OpenAPI**: Automatic documentation at `/docs`

### 6. CORS Middleware ✅
- **Файл**: [demo_server.py](demo_server.py#L89-L99)
- **Origins**: localhost:3000, localhost:5173, localhost:8080
- **⚠️ TODO**: Move to ENV configuration (CORS_ALLOW_ORIGINS)

### 7. RBAC + JWT ✅
- **Файл**: [api/security.py](api/security.py)
- **Roles**: admin, operator, developer, observer, anonymous
- **Permissions**: 17 permissions across resources
- **⚠️ TODO**: Add password hashing (bcrypt)

---

## 🔥 Високий пріоритет (Наступні кроки)

### 1. Rate Limiting (SlowAPI + Redis) ⏳

**Мета**: Захист від brute-force та DDoS атак

**Імплементація**:

```python
# demo_server.py: Після app initialization

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from jose import jwt

def rate_limit_key(request):
    """Prefer authenticated user ID, else IP"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1]
        try:
            payload = jwt.decode(
                token,
                os.getenv("JWT_SECRET"),
                algorithms=["HS256"]
            )
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except:
            pass
    return get_remote_address(request)

limiter = Limiter(
    key_func=rate_limit_key,
    storage_uri=os.getenv("REDIS_URL")
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

**Рольові ліміти** (api/security.py):

```python
class RoleBasedLimiter:
    def __init__(self, limiter: Limiter):
        self.limiter = limiter

    def limit_by_role(self):
        def decorator(func):
            async def wrapper(request, *args, user=Depends(rbac.verify_token), **kwargs):
                role = user.get("role", "observer")
                rate = RATE_LIMITS.get(role, RATE_LIMITS["observer"])
                limited = self.limiter.limit(rate)(func)
                return await limited(request, *args, user=user, **kwargs)
            return wrapper
        return decorator

# Usage:
role_limiter = RoleBasedLimiter(app.state.limiter)

@app.post("/api/v1/dlq/{id}/resolve")
@role_limiter.limit_by_role()
async def resolve_dlq(...):
    ...
```

**Окремі ліміти**:
- `POST /api/v1/auth/login`: `@limiter.limit("5/minute")`
- `POST /api/v1/budget/request`: `@limiter.limit("30/minute")`
- `POST /api/v1/circuit-breakers/reset_all`: `@limiter.limit("5/minute")`
- `GET /health`, `/stats`: `@limiter.limit("60/minute")`

**ENV змінні**:
```bash
REDIS_URL=redis://localhost:6379/0
```

---

### 2. Password Hashing (bcrypt) ⏳

**Мета**: Безпечне зберігання паролів

**Імплементація**:

```python
# api/new_endpoints.py

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Демо-користувачі з хешами
DEMO_USERS = {
    "admin": {
        "password_hash": pwd_context.hash("admin123"),
        "role": "admin"
    },
    "operator": {
        "password_hash": pwd_context.hash("operator123"),
        "role": "operator"
    },
}

@router.post("/auth/login")
async def login(request: LoginRequest):
    user = DEMO_USERS.get(request.username)

    if not user or not pwd_context.verify(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # ... generate JWT
```

**Login lockout** (через Redis):

```python
# Constants
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_TTL = int(os.getenv("LOGIN_LOCKOUT_TTL_SECONDS", "900"))  # 15 min

# In login handler
@router.post("/auth/login")
@limiter.limit("5/minute")  # SlowAPI limit
async def login(request: LoginRequest, req: Request):
    client_ip = get_remote_address(req)
    user_key = request.username.strip().lower()
    lock_key = f"login:attempts:{user_key}:{client_ip}"

    # Check lockout
    attempts = await req.app.state.redis.incr(lock_key)
    if attempts == 1:
        await req.app.state.redis.expire(lock_key, LOGIN_LOCKOUT_TTL)

    if attempts > LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts")

    # Verify credentials
    if not pwd_context.verify(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Success: reset attempts
    await req.app.state.redis.delete(lock_key)

    # ... generate JWT with short TTL
```

**JWT TTL**:
```python
# api/security.py: generate_token
expires_in_hours = 1  # Short TTL for production (15-60 min recommended)
```

**ENV змінні**:
```bash
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCKOUT_TTL_SECONDS=900
```

---

### 3. Prometheus Metrics ⏳

**Мета**: Моніторинг та спостережність

**Метрики модуль** (common/metrics.py):

```python
"""Prometheus metrics for Golden Architecture"""

from prometheus_client import Counter, Histogram

# HTTP metrics
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests",
    ["route", "method", "status"]
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Request duration",
    ["route", "method"]
)

# Business metrics
AUTH_LOGINS = Counter(
    "auth_logins_total",
    "Auth logins",
    ["result"]  # success/fail
)

BUDGET_REQUESTS = Counter(
    "budget_requests_total",
    "Budget requests",
    ["status"]  # approved/insufficient/reservation_failed
)

BUDGET_COMMITS = Counter("budget_commits_total", "Budget commits")
BUDGET_RELEASES = Counter("budget_releases_total", "Budget releases")
DLQ_RESOLVED = Counter("dlq_resolved_total", "DLQ messages resolved")
BREAKER_RESETS = Counter("breaker_resets_total", "Breaker resets")
```

**Middleware** (demo_server.py):

```python
import time
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from common.metrics import HTTP_REQUESTS, HTTP_REQUEST_DURATION

@app.middleware("http")
async def metrics_middleware(request, call_next):
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration = time.perf_counter() - start
        route = getattr(request.scope.get("route"), "path", request.url.path)
        method = request.method

        HTTP_REQUESTS.labels(
            route=route,
            method=method,
            status=str(status_code)
        ).inc()

        HTTP_REQUEST_DURATION.labels(
            route=route,
            method=method
        ).observe(duration)

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

**Використання в endpoints** (api/new_endpoints.py):

```python
from common.metrics import (
    AUTH_LOGINS,
    BUDGET_REQUESTS,
    BUDGET_COMMITS,
    BUDGET_RELEASES,
    DLQ_RESOLVED,
    BREAKER_RESETS
)

# Auth: Login
@router.post("/auth/login")
async def login(...):
    if not valid:
        AUTH_LOGINS.labels(result="fail").inc()
        raise HTTPException(401)

    AUTH_LOGINS.labels(result="success").inc()
    return LoginResponse(...)

# Budget: Request
@router.post("/budget/request")
async def budget_request(...):
    if available >= request.estimated_tokens:
        BUDGET_REQUESTS.labels(status="approved").inc()
        return BudgetResponse(approved=True, ...)
    else:
        BUDGET_REQUESTS.labels(status="insufficient").inc()
        return BudgetResponse(approved=False, ...)

# Budget: Commit
@router.post("/budget/commit")
async def budget_commit(...):
    BUDGET_COMMITS.inc()
    return {"status": "committed"}

# Budget: Release
@router.post("/budget/release")
async def budget_release(...):
    BUDGET_RELEASES.inc()
    return {"status": "released"}

# DLQ: Resolve
@router.post("/dlq/{id}/resolve")
async def resolve_dlq(...):
    # ... update DB
    DLQ_RESOLVED.inc()
    return {"status": "resolved"}

# Circuit Breakers: Reset
@router.post("/circuit-breakers/reset_all")
async def reset_all(...):
    reset_count = len(circuit_breaker_registry)
    BREAKER_RESETS.inc(reset_count)
    return {"reset_count": reset_count}
```

**Grafana Dashboard**:
- HTTP latency: `http_request_duration_seconds`
- Request rate: `rate(http_requests_total[5m])`
- Error rate: `http_requests_total{status=~"5.."}`
- Budget usage: `budget_requests_total{status="approved"}`
- Auth failures: `auth_logins_total{result="fail"}`

---

## 📊 Середній пріоритет

### 4. CORS via ENV ⏳

**Поточний стан**: Hardcoded origins
**Ціль**: Dynamic configuration

```python
# demo_server.py

origins_env = os.getenv("CORS_ALLOW_ORIGINS", "")
allow_origins = [o.strip() for o in origins_env.split(",") if o.strip()]

if allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
```

**ENV**:
```bash
CORS_ALLOW_ORIGINS=http://localhost:3000,http://localhost:5173,https://app.example.com
```

**⚠️ Важливо**: Не використовувати `*` при `allow_credentials=True`

---

### 5. Audit Logging ⏳

**Мета**: Відстеження критичних дій

**Імплементація**:

```python
# Критичні дії для аудиту:
# - Budget commit/release
# - DLQ resolve
# - Circuit breakers reset
# - Auth login (success/fail)

from api.security import AuditLogger

@router.post("/dlq/{id}/resolve")
async def resolve_dlq(
    message_id: str,
    resolve_req: DLQResolveModel,
    req: Request,
    user = Depends(rbac.verify_token)
):
    # ... update DB

    # Audit log
    audit = AuditLogger(req.app.state.db_pool)
    await audit.log(
        user_id=user["user_id"],
        role=user["role"],
        action="dlq.resolve",
        resource_type="dlq_message",
        resource_id=message_id,
        details={"note": resolve_req.note, "requeued": resolve_req.requeue},
        request_id=req.headers.get("X-Request-ID")
    )

    return {"status": "resolved"}
```

---

## 🔒 Низький пріоритет (Майбутнє)

### 6. JWT RS256/JWKS ⏳

**Поточний стан**: HS256 (symmetric)
**Ціль**: RS256 (asymmetric) для production

**План**:
1. Генерація RSA keypair
2. JWKS endpoint: `GET /.well-known/jwks.json`
3. `kid` в JWT header для ротації ключів
4. Верифікація через публічний ключ

**Переваги**:
- ✅ Можливість ротації ключів
- ✅ Сервіси можуть верифікувати без shared secret
- ✅ Відповідає стандарту OAuth 2.0/OIDC

---

### 7. Docker Compose ⏳

**docker-compose.yml**:

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:14
    environment:
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: golden_arch
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  nats:
    image: nats:latest
    command: ["-js"]
    ports:
      - "4222:4222"

  api:
    build: .
    environment:
      DATABASE_URL: postgresql://postgres:secret@postgres/golden_arch
      REDIS_URL: redis://redis:6379/0
      NATS_URL: nats://nats:4222
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis
      - nats

  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    depends_on:
      - prometheus

volumes:
  postgres_data:
```

---

### 8. CI/CD Pipeline ⏳

**GitHub Actions** (.github/workflows/ci.yml):

```yaml
name: CI/CD

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements-v5.1.txt
      - run: ruff check .
      - run: black --check .
      - run: mypy .
      - run: pytest tests/

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: pip install safety
      - run: safety check

  build:
    needs: [test, security]
    runs-on: ubuntu-latest
    steps:
      - uses: docker/build-push-action@v4
        with:
          push: true
          tags: myregistry/golden-arch:${{ github.sha }}
```

---

## 📝 ENV Variables Checklist

**Required**:
- ✅ `DATABASE_URL`
- ✅ `REDIS_URL`
- ✅ `JWT_SECRET`

**Optional (recommended)**:
- ⏳ `CORS_ALLOW_ORIGINS` (CSV)
- ⏳ `LOGIN_MAX_ATTEMPTS` (default: 5)
- ⏳ `LOGIN_LOCKOUT_TTL_SECONDS` (default: 900)
- ⏳ `RATE_LIMIT_STORAGE_URI` (same as REDIS_URL)

**Production secrets**:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `NATS_URL`

---

## 🧪 Testing Checklist

### Unit Tests
- [ ] LLM utils: `safe_parse_synthesis` positive/negative
- [ ] RBAC: permission checks for new endpoints
- [ ] Login: success/fail/lockout
- [ ] Budget: idempotency, insufficient funds

### Integration Tests
- [ ] Auth flow: login → JWT → protected endpoint
- [ ] Budget: request → commit → state
- [ ] DLQ: list → resolve (403 for non-admin, 200 for admin)
- [ ] Rate limiting: 429 after N+1 requests
- [ ] Error format: standard response for 400/401/403/404/409/429/500

### Load Tests
- [ ] Concurrent budget requests
- [ ] Rate limiting under load
- [ ] Database pool under stress

---

## 📊 Production Deployment Checklist

### Pre-deployment
- [ ] All tests passing
- [ ] Security audit (safety, bandit)
- [ ] ENV variables configured
- [ ] Database migrations applied
- [ ] Secrets in vault (not .env)

### Deployment
- [ ] Docker image built
- [ ] K8s manifests applied
- [ ] Health checks passing
- [ ] Metrics exporting
- [ ] Logs aggregated

### Post-deployment
- [ ] Smoke tests passed
- [ ] Metrics monitored (Grafana)
- [ ] Alerts configured (Prometheus)
- [ ] Rollback plan tested

---

## 🎯 Current Status Summary

| Component | Status | Priority |
|-----------|--------|----------|
| Error Handling | ✅ Complete | - |
| Migrations | ✅ Complete | - |
| DB Pool | ✅ Complete | - |
| Redis | ✅ Complete | - |
| API Versioning | ✅ Complete | - |
| CORS | ⚠️ Hardcoded | Medium |
| RBAC | ✅ Basic | High (add bcrypt) |
| Rate Limiting | ❌ Missing | High |
| Password Hashing | ❌ Plain text | High |
| Metrics | ❌ Missing | High |
| Audit Logging | ⚠️ Partial | Medium |
| JWT Security | ⚠️ HS256 | Low |
| Docker Compose | ❌ Missing | Low |
| CI/CD | ❌ Missing | Low |

---

**Створено**: 2025-01-08
**Версія**: 1.0
**Автор**: Golden Architecture Team
