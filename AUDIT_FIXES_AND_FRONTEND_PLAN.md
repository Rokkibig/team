# Аудит коду, виправлення та план фронтенда

## ✅ Виправлені критичні помилки

### 1. JWT імпорт та JSON (api/security.py:6-13)
**Проблема**: Використовувався `import jwt` (PyJWT), але в requirements був `python-jose`
**Виправлення**: Змінено на `from jose import jwt` + додано `import json`

### 2. Порт у тестовому скрипті (test_api.sh)
**Проблема**: Захардкоджений порт 8001
**Виправлення**: Додано змінну `PORT=${PORT:-8000}` з можливістю перевизначення

### 3. ServiceMonitor apiVersion (k8s/hpa-configs.yaml:246,263,280,297)
**Проблема**: Всі 4 ServiceMonitor мали `apiVersion: v1`
**Виправлення**: Змінено на `apiVersion: monitoring.coreos.com/v1`

---

## 🔧 Рішення для інших проблем

### 4. Rate Limiting у RoleBasedLimiter (api/security.py:220+)

**Проблема**: Некоректна подвійна інтеграція з SlowAPI

**Рішення**:
```python
# У demo_server.py (startup)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Спрощена інтеграція в api/security.py
class RoleBasedLimiter:
    def __init__(self, app):
        self.limiter = app.state.limiter

    def limit_by_role(self, role: str):
        rate = RATE_LIMITS.get(role, "5/minute")
        return self.limiter.limit(rate)

# Використання
@app.get("/protected")
@role_limiter.limit_by_role("developer")
async def endpoint():
    ...
```

### 5. Пул підключень до Postgres (demo_server.py)

**Проблема**: Кожен запит створює нове asyncpg.connect()

**Рішення**:
```python
import asyncpg
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.db_pool = await asyncpg.create_pool(
        os.getenv("DATABASE_URL"),
        min_size=2,
        max_size=10,
        command_timeout=60
    )
    yield
    # Shutdown
    await app.state.db_pool.close()

app = FastAPI(lifespan=lifespan)

# У endpoints:
async with app.state.db_pool.acquire() as conn:
    rows = await conn.fetch("SELECT * FROM governance_status")
```

### 6. Скрипт міграцій (scripts/migrate.py)

```python
#!/usr/bin/env python3
"""Database migration runner"""
import asyncio
import asyncpg
import os
from pathlib import Path

async def run_migrations():
    conn = await asyncpg.connect(os.getenv("DATABASE_URL"))

    migrations_dir = Path(__file__).parent.parent / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))

    for sql_file in sql_files:
        print(f"Running {sql_file.name}...")
        sql = sql_file.read_text()
        await conn.execute(sql)
        print(f"✅ {sql_file.name} completed")

    await conn.close()
    print("🎉 All migrations complete!")

if __name__ == "__main__":
    asyncio.run(run_migrations())
```

### 7. Sandbox Executor Security (sandbox_executor/secure_executor.py:264)

**Проблема**: `--security-opt=seccomp=default` некоректний

**Виправлення**:
```python
# Видалити або замінити на:
"security_opt": [
    "no-new-privileges:true",
    "seccomp=unconfined"  # Якщо gVisor вже обмежує
]

# Або прибрати взагалі - Docker застосує дефолтний profil
```

### 8. .env.example

```bash
# .env.example (версія без секретів)
DATABASE_URL=postgresql://user:password@localhost/golden_arch
REDIS_URL=redis://localhost:6379/0
NATS_URL=nats://localhost:4222
JWT_SECRET=CHANGE_ME_IN_PRODUCTION
OPENAI_API_KEY=sk-your-key-here
ANTHROPIC_API_KEY=sk-ant-your-key-here
PORT=8000
HOST=0.0.0.0
```

---

## 🚀 API Endpoints для фронтенда

### Auth
```python
POST /auth/login
Body: {"username": "admin", "password": "secret"}
Response: {"token": "eyJ...", "role": "admin", "permissions": [...]}
```

### Budget (новий модуль)
```python
POST /budget/request
Body: {
  "tenant_id": "org1",
  "project_id": "proj1",
  "task_id": "task1",
  "model": "gpt-4",
  "estimated_tokens": 1000
}
Response: {"approved": true, "reservation_id": "uuid", "allocated": 1000}

POST /budget/commit
Body: {"reservation_id": "uuid", "actual_tokens": 800}

POST /budget/release
Body: {"reservation_id": "uuid"}

GET /budget/state?tenant_id=org1&project_id=proj1
Response: {"total": 100000, "used": 45000, "reserved": 5000}
```

### DLQ (новий модуль)
```python
GET /dlq?resolved=false&limit=50&offset=0
Response: {
  "items": [
    {
      "id": "uuid",
      "original_subject": "task.execute",
      "payload": {...},
      "error": "Circuit breaker open",
      "attempts": 3,
      "created_at": "2025-01-08T10:00:00Z"
    }
  ],
  "total": 125
}

GET /dlq/{id}
POST /dlq/{id}/resolve
Body: {"note": "Fixed underlying issue", "requeue": true}
```

### Circuit Breakers
```python
GET /circuit-breakers
Response: {
  "breakers": {
    "openai_api": {"state": "CLOSED", "failures": 0},
    "database": {"state": "OPEN", "failures": 5, "next_retry": "..."}
  }
}

POST /circuit-breakers/reset_all  (admin only)
```

### Sandbox Execute
```python
POST /execute  (окремий сервіс на 8001)
Body: {"code": "print('hello')", "language": "python", "timeout": 5}
Response: {"stdout": "hello\n", "stderr": "", "exit_code": 0, "duration_ms": 234}
```

---

## 🎨 План фронтенда

### Технології
- **Framework**: React 18 + TypeScript + Vite
- **UI Kit**: MUI (Material-UI) або Chakra UI
- **State**: TanStack Query (React Query) + Zustand
- **Routing**: React Router v6
- **Charts**: Recharts або Visx
- **Code Editor**: Monaco Editor (для sandbox)
- **HTTP**: Axios з інтерсепторами

### Структура проєкту
```
frontend/
├── src/
│   ├── api/
│   │   ├── client.ts          # Axios instance з JWT
│   │   ├── auth.ts            # login, logout
│   │   ├── budget.ts          # budget endpoints
│   │   ├── dlq.ts             # DLQ endpoints
│   │   └── monitoring.ts      # stats, breakers, governance
│   ├── components/
│   │   ├── Layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── Header.tsx
│   │   │   └── ProtectedRoute.tsx
│   │   ├── Dashboard/
│   │   │   ├── HealthCard.tsx
│   │   │   ├── StatsChart.tsx
│   │   │   └── AlertsList.tsx
│   │   ├── Governance/
│   │   │   ├── StatusTable.tsx
│   │   │   └── RoleFilters.tsx
│   │   ├── Budget/
│   │   │   ├── RequestForm.tsx
│   │   │   ├── StateTable.tsx
│   │   │   └── UsageChart.tsx
│   │   ├── DLQ/
│   │   │   ├── MessageList.tsx
│   │   │   ├── MessageDetails.tsx
│   │   │   └── ResolveModal.tsx
│   │   ├── Sandbox/
│   │   │   ├── CodeEditor.tsx
│   │   │   └── ExecutionResult.tsx
│   │   └── CircuitBreakers/
│   │       ├── BreakerCard.tsx
│   │       └── ResetButton.tsx
│   ├── pages/
│   │   ├── Login.tsx
│   │   ├── Dashboard.tsx
│   │   ├── Governance.tsx
│   │   ├── Budget.tsx
│   │   ├── DLQ.tsx
│   │   ├── CircuitBreakers.tsx
│   │   └── Sandbox.tsx
│   ├── stores/
│   │   └── authStore.ts       # Zustand для JWT + роль
│   ├── types/
│   │   └── api.ts             # TypeScript типи
│   └── App.tsx
├── package.json
└── vite.config.ts
```

### Роути
```typescript
const routes = [
  { path: "/login", element: <Login />, public: true },
  { path: "/", element: <Dashboard />, permissions: [] },
  { path: "/governance", element: <Governance />, permissions: ["read:governance"] },
  { path: "/budget", element: <Budget />, permissions: ["read:budget"] },
  { path: "/dlq", element: <DLQ />, permissions: ["read:dlq"] },
  { path: "/breakers", element: <CircuitBreakers />, permissions: ["read:system"] },
  { path: "/sandbox", element: <Sandbox />, permissions: ["execute:code"] },
]
```

### Ключові features

#### 1. Автентифікація
```typescript
// api/client.ts
const client = axios.create({ baseURL: "http://localhost:8000" })

client.interceptors.request.use(config => {
  const token = localStorage.getItem("jwt")
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

client.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem("jwt")
      window.location.href = "/login"
    }
    return Promise.reject(err)
  }
)
```

#### 2. Real-time Updates
```typescript
// Polling для /stats, /circuit-breakers, /governance/status
const { data } = useQuery({
  queryKey: ["stats"],
  queryFn: () => api.getStats(),
  refetchInterval: 10_000, // 10 сек
})
```

#### 3. RBAC у UI
```typescript
const { role, permissions } = useAuthStore()

<Button disabled={!permissions.includes("admin:reset_breakers")}>
  Reset All
</Button>
```

#### 4. DLQ Management
```typescript
const ResolveDLQModal = ({ messageId }) => {
  const mutation = useMutation({
    mutationFn: (data) => api.resolveDLQ(messageId, data),
    onSuccess: () => {
      queryClient.invalidateQueries(["dlq"])
      toast.success("Message resolved!")
    }
  })

  return (
    <Dialog>
      <TextField label="Note" {...} />
      <Checkbox label="Requeue message" {...} />
      <Button onClick={() => mutation.mutate({...})}>Resolve</Button>
    </Dialog>
  )
}
```

#### 5. Sandbox
```typescript
import Editor from "@monaco-editor/react"

const Sandbox = () => {
  const [code, setCode] = useState("print('hello')")
  const mutation = useMutation({
    mutationFn: (code) => sandboxApi.execute({ code, language: "python" })
  })

  return (
    <>
      <Editor value={code} onChange={setCode} language="python" />
      <Button onClick={() => mutation.mutate(code)}>Execute</Button>
      {mutation.data && (
        <Paper>
          <pre>{mutation.data.stdout}</pre>
          <pre style={{color: 'red'}}>{mutation.data.stderr}</pre>
        </Paper>
      )}
    </>
  )
}
```

---

## 📋 Поетапне впровадження

### Етап 1: Критичні фікси (1-2 дні)
- ✅ JWT/JSON імпорт
- ✅ Порт у test_api.sh
- ✅ ServiceMonitor apiVersion
- ⏳ Rate limiting (api/security.py)
- ⏳ DB pool (demo_server.py)
- ⏳ Міграційний скрипт
- ⏳ Sandbox security options
- ⏳ .env.example

### Етап 2: API для фронтенда (2-3 дні)
- Auth endpoints
- Budget endpoints
- DLQ endpoints
- Circuit breakers endpoints
- Документація OpenAPI

### Етап 3: Фронтенд MVP (5-7 днів)
- Setup (Vite + React + MUI)
- Автентифікація + routing
- Dashboard + Stats
- Governance view
- DLQ management

### Етап 4: Повний функціонал (5-7 днів)
- Budget management
- Circuit breakers monitoring
- Sandbox executor
- Real-time updates (polling → WebSocket)
- Audit log viewer

### Етап 5: Production Ready (3-5 днів)
- E2E тести (Playwright)
- Docker для фронтенда
- CORS налаштування
- Rate limiting на фронтенді
- Error boundaries
- Loading states
- Accessibility (a11y)

---

## 🔒 Безпека

### Backend
- ✅ JWT з HS256
- ✅ RBAC permissions
- ✅ Input sanitization (SQL/script injection)
- ✅ Rate limiting per role
- ⏳ CORS whitelist
- ⏳ CSP headers
- ⏳ HTTPOnly cookies (замість localStorage)

### Frontend
- HttpOnly cookies для JWT (безпечніше за localStorage)
- Content Security Policy
- XSS захист (React вбудований)
- Валідація форм (Zod або Yup)
- HTTPS у production

---

## 📊 Моніторинг

### Метрики для Prometheus
```python
from prometheus_client import Counter, Histogram, Gauge

request_count = Counter("api_requests_total", "Total requests", ["method", "endpoint"])
request_duration = Histogram("api_request_duration_seconds", "Request duration")
active_users = Gauge("active_users", "Currently logged in users")
```

### Dashboard у Grafana
- API latency (p50, p95, p99)
- Request rate (req/min)
- Error rate
- Circuit breaker states
- DLQ size
- Budget usage per tenant

---

## 🎯 Успіх критерії

### Backend
- ✅ Всі тести проходять
- ✅ Відсутні критичні security issues
- ⏳ API response time < 200ms (p95)
- ⏳ 99.9% uptime
- ⏳ Zero message loss (DLQ працює)

### Frontend
- Lighthouse score > 90
- Доступність AA (WCAG 2.1)
- Mobile responsive
- Cross-browser (Chrome, Firefox, Safari)
- Load time < 3s

---

## 📝 TODO List

### Високий пріоритет
- [ ] Виправити rate limiting integration
- [ ] Додати DB connection pool
- [ ] Створити scripts/migrate.py
- [ ] Додати .env.example
- [ ] Написати Auth endpoints
- [ ] Написати Budget endpoints
- [ ] Написати DLQ endpoints

### Середній пріоритет
- [ ] Setup фронтенд проєкт
- [ ] Імплементувати автентифікацію
- [ ] Dashboard з базовими метриками
- [ ] Governance таблиця
- [ ] DLQ management UI

### Низький пріоритет
- [ ] WebSocket для real-time
- [ ] Grafana дашборди
- [ ] E2E тести
- [ ] CI/CD pipeline
- [ ] Kubernetes deployment

---

## 🚀 Команди для запуску

### Backend
```bash
# Міграції
python3 scripts/migrate.py

# Запуск сервера
source .venv/bin/activate
uvicorn demo_server:app --host 0.0.0.0 --port 8000 --reload

# Тести
bash test_api.sh
```

### Frontend (коли буде готовий)
```bash
cd frontend
npm install
npm run dev  # http://localhost:5173

# Production build
npm run build
npm run preview
```

### Docker Compose (майбутнє)
```yaml
services:
  backend:
    build: .
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: postgresql://postgres:secret@db/golden_arch

  frontend:
    build: ./frontend
    ports: ["80:80"]

  db:
    image: postgres:14
    environment:
      POSTGRES_PASSWORD: secret

  redis:
    image: redis:7-alpine

  nats:
    image: nats:latest
    command: ["-js"]
```

---

**Створено**: 2025-01-08
**Версія**: 1.0
**Автор**: Golden Architecture Team
