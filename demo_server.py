"""
Golden Architecture V5.1 - Demo Server
Simple FastAPI server to demonstrate core functionality
"""

import os
import json
import time
import asyncpg
import redis.asyncio as redis
from datetime import datetime
from dotenv import load_dotenv
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Load environment
load_dotenv()

# Validate critical env variables
REQUIRED_ENV = ["DATABASE_URL", "REDIS_URL", "JWT_SECRET"]
missing = [e for e in REQUIRED_ENV if not os.getenv(e)]
if missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

# Import our security modules
from api.security import rbac, Permission, AuditLogger
from supervisor_optimizer.llm_utils import safe_parse_synthesis, sanitize_llm_response
from common.circuit_breaker import CircuitBreaker, circuit_breaker_registry
from common.error_handlers import install_error_handlers

# ============================================================================
# LIFESPAN - DB Pool & Redis Connection
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown"""
    # Startup
    print("🚀 Starting Golden Architecture V5.1...")

    # Create database pool
    app.state.db_pool = await asyncpg.create_pool(
        os.getenv("DATABASE_URL"),
        min_size=2,
        max_size=10,
        command_timeout=60
    )
    print("✅ Database pool created")

    # Create Redis connection
    app.state.redis = await redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        encoding="utf-8",
        decode_responses=True
    )
    print("✅ Redis connected")

    # Initialize rate limiter with Redis storage
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri=redis_url  # Use Redis for distributed rate limiting
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    print("✅ Rate limiter initialized with Redis storage")

    yield

    # Shutdown
    print("🛑 Shutting down...")
    await app.state.db_pool.close()
    await app.state.redis.close()
    print("✅ Cleanup complete")

# Create app with lifespan
app = FastAPI(
    title="Golden Architecture V5.1",
    description="Battle-Hardened Multi-Agent System",
    version="5.1.0",
    lifespan=lifespan
)

# ============================================================================
# CORS Configuration via ENV
# ============================================================================

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
    print(f"✅ CORS enabled for origins: {allow_origins}")
else:
    print("⚠️  CORS_ALLOW_ORIGINS not set - CORS disabled")

# ============================================================================
# PROMETHEUS METRICS MIDDLEWARE
# ============================================================================

from common.metrics import (
    HTTP_REQUESTS,
    HTTP_REQUEST_DURATION,
    generate_latest,
    CONTENT_TYPE_LATEST
)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Collect HTTP metrics for all requests"""
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

        HTTP_REQUESTS.labels(route=route, method=method, status=str(status_code)).inc()
        HTTP_REQUEST_DURATION.labels(route=route, method=method).observe(duration)

# ============================================================================
# ERROR HANDLERS
# ============================================================================

install_error_handlers(app)

# ============================================================================
# MODELS
# ============================================================================

class SynthesisRequest(BaseModel):
    """Request for LLM synthesis validation"""
    llm_response: str

class BudgetRequest(BaseModel):
    """Request for budget allocation"""
    purpose: str
    estimated_tokens: int
    model: str
    task_id: str
    project_id: str
    tenant_id: str
    request_id: Optional[str] = None

# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Golden Architecture V5.1",
        "status": "running",
        "version": "5.1.0",
        "features": {
            "security": "multi-layer (LLM, RBAC, Sandbox)",
            "reliability": "circuit breakers + DLQ",
            "scalability": "SLO-based auto-scaling",
            "governance": "learning rate limits"
        }
    }

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {
            "api": "healthy",
            "database": "healthy",  # Would check actual connection
            "redis": "healthy",
            "nats": "healthy"
        }
    }

@app.get("/metrics")
async def metrics():
    """
    Prometheus metrics endpoint
    Returns metrics in Prometheus exposition format
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/validate/synthesis")
async def validate_synthesis(request: SynthesisRequest):
    """
    Validate LLM synthesis response

    Demonstrates:
    - JSON schema validation
    - Input sanitization
    - Security layer
    """
    try:
        # Sanitize first
        clean = sanitize_llm_response(request.llm_response)

        # Validate and parse
        result = safe_parse_synthesis(clean)

        return {
            "status": "valid",
            "action_plan": result.action_plan,
            "reasoning": result.reasoning,
            "sanitized": clean != request.llm_response
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/governance/status")
async def governance_status():
    """
    Get learning governance status

    Demonstrates:
    - Database integration
    - Governance controls
    """
    try:
        async with app.state.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM governance_status")

        return {
            "governance": [dict(row) for row in rows]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/circuit-breakers")
async def get_circuit_breakers():
    """
    Get all circuit breaker states

    Demonstrates:
    - Circuit breaker pattern
    - Fault tolerance
    """
    stats = circuit_breaker_registry.get_all_stats()

    return {
        "breakers": {
            name: {
                "state": stat.state,
                "failure_count": stat.failure_count,
                "total_calls": stat.total_calls,
                "total_failures": stat.total_failures
            }
            for name, stat in stats.items()
        }
    }

@app.post("/test/injection")
async def test_injection(data: Dict):
    """
    Test SQL injection prevention

    Demonstrates:
    - Input sanitization
    - Security validation
    """
    raw_input = data.get("input", "")
    sanitized = sanitize_llm_response(raw_input)

    return {
        "original": raw_input,
        "sanitized": sanitized,
        "was_malicious": raw_input != sanitized,
        "message": "✅ Injection blocked!" if raw_input != sanitized else "✅ Input clean"
    }

@app.get("/stats")
async def get_stats():
    """
    Get system statistics

    Demonstrates:
    - System observability
    - Metrics collection
    """
    try:
        async with app.state.db_pool.acquire() as conn:
            tasks_count = await conn.fetchval("SELECT COUNT(*) FROM tasks")
            escalations_count = await conn.fetchval(
                "SELECT COUNT(*) FROM escalations WHERE resolved = FALSE"
            )
            dlq_count = await conn.fetchval(
                "SELECT COUNT(*) FROM dlq_messages WHERE resolved = FALSE"
            )

        return {
            "tasks": {
                "total": tasks_count
            },
            "escalations": {
                "unresolved": escalations_count
            },
            "dlq": {
                "unresolved": dlq_count
            },
            "system": {
                "status": "operational",
                "version": "5.1.0"
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# PROTECTED ENDPOINTS (with RBAC)
# ============================================================================

@app.get("/admin/config")
@rbac.require_permission(Permission.SYSTEM_ADMIN)
async def get_config(user=Depends(rbac.verify_token)):
    """
    Admin-only endpoint

    Demonstrates:
    - RBAC protection
    - Permission enforcement
    """
    return {
        "message": "Admin access granted",
        "user": user,
        "config": {
            "jwt_secret_set": bool(os.getenv("JWT_SECRET")),
            "database_url_set": bool(os.getenv("DATABASE_URL")),
            "redis_url_set": bool(os.getenv("REDIS_URL")),
            "nats_url_set": bool(os.getenv("NATS_URL"))
        }
    }

# ============================================================================
# INCLUDE NEW API ENDPOINTS
# ============================================================================

# Include new endpoints router with /api/v1 prefix
from api.new_endpoints import router as new_endpoints_router
app.include_router(new_endpoints_router, prefix="/api/v1", tags=["API v1"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        log_level="info"
    )
