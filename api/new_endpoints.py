"""
New API Endpoints for Frontend Integration
Auth, Budget, DLQ, Circuit Breakers
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import uuid
import bcrypt
import os

from api.security import rbac, Permission, AuditLogger
from common.metrics import (
    AUTH_LOGINS,
    BUDGET_REQUESTS,
    BUDGET_COMMITS,
    BUDGET_RELEASES,
    DLQ_RESOLVED,
    BREAKER_RESETS
)

# Audit logger dependency
def get_audit_logger(req: Request) -> AuditLogger:
    """Factory for AuditLogger dependency"""
    return AuditLogger(req.app.state.db_pool)

# Create router
router = APIRouter()

# Login lockout settings
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_TTL = int(os.getenv("LOGIN_LOCKOUT_TTL_SECONDS", "900"))

# ==============================================================================
# MODELS
# ==============================================================================

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    token: str
    role: str
    permissions: List[str]

class BudgetRequestModel(BaseModel):
    tenant_id: str
    project_id: str
    task_id: str
    model: str
    estimated_tokens: int

class BudgetResponse(BaseModel):
    approved: bool
    reservation_id: Optional[str]
    allocated: int
    reason: Optional[str]

class BudgetCommitModel(BaseModel):
    tenant_id: str
    project_id: str
    reservation_id: str
    actual_tokens: int

class BudgetReleaseModel(BaseModel):
    tenant_id: str
    project_id: str
    reservation_id: str

class DLQMessage(BaseModel):
    id: str
    original_subject: str
    data_preview: str  # First 200 chars of data
    error: Optional[str]  # From headers.error if exists
    attempts: int  # Mapped from error_count
    created_at: datetime
    resolved: bool

class DLQResolveModel(BaseModel):
    note: str
    requeue: bool = False

# ==============================================================================
# AUTH ENDPOINTS
# ==============================================================================

# Demo users with bcrypt hashed passwords (in production: use real user database)
DEMO_USERS = {
    "admin": {"password_hash": "$2b$12$9VvRP7Egf4CQWu.cK2nDfeVwzUw/hze7CFAmBvR0qFoLe6XJ0DqYS", "role": "admin"},
    "operator": {"password_hash": "$2b$12$rEo4hjoK0A8uf7ibBKaNmuAzEjJr.W4O0qKVQ4U3Fq1y3fyA9y4Oe", "role": "operator"},
    "developer": {"password_hash": "$2b$12$ZL1VFIrPyket8NL7Yp7dlu/Ss8JIJCmSnKGgj/VnWsqad7paVF3TW", "role": "developer"},
    "observer": {"password_hash": "$2b$12$g2n/iLfEenJctKL25GJYsOjcrq9Ilaa4/1ppVJgROlwf2WlcdA5iG", "role": "observer"},
}

@router.post("/auth/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    req: Request,
    audit: AuditLogger = Depends(get_audit_logger)
):
    """
    Login endpoint with bcrypt password hashing and Redis-based lockout

    - Bcrypt password verification
    - Lockout: 5 failed attempts → 429 for 15 minutes
    - JWT token with 24h expiration

    Demo credentials:
    - admin/admin123
    - operator/operator123
    - developer/dev123
    - observer/obs123
    """
    redis = req.app.state.redis

    # Normalize username and create lockout key
    username_lower = request.username.strip().lower()
    client_ip = req.client.host if req.client else "unknown"
    lock_key = f"login:attempts:{username_lower}:{client_ip}"

    # Check/increment lockout counter
    attempts = await redis.incr(lock_key)
    if attempts == 1:
        # First attempt - set TTL
        await redis.expire(lock_key, LOGIN_LOCKOUT_TTL)

    if attempts > LOGIN_MAX_ATTEMPTS:
        # Lockout active
        raise HTTPException(
            status_code=429,
            detail=f"rate_limit.exceeded: Too many login attempts. Try again in {LOGIN_LOCKOUT_TTL // 60} minutes"
        )

    # Get user and verify password
    user = DEMO_USERS.get(username_lower)
    if not user:
        # User not found
        AUTH_LOGINS.labels(result="fail").inc()
        await audit.log_action(
            user_id=username_lower,
            role="anonymous",
            action="auth.login.fail",
            resource_type="auth",
            resource_id=username_lower,
            details={"reason": "user_not_found"}
        )
        raise HTTPException(
            status_code=401,
            detail="auth.invalid_credentials: Invalid credentials"
        )

    # Verify bcrypt password
    password_bytes = request.password.encode('utf-8')
    hash_bytes = user["password_hash"].encode('utf-8')

    if not bcrypt.checkpw(password_bytes, hash_bytes):
        # Wrong password
        AUTH_LOGINS.labels(result="fail").inc()
        await audit.log_action(
            user_id=username_lower,
            role="anonymous",
            action="auth.login.fail",
            resource_type="auth",
            resource_id=username_lower,
            details={"reason": "invalid_password"}
        )
        raise HTTPException(
            status_code=401,
            detail="auth.invalid_credentials: Invalid credentials"
        )

    # Success - reset lockout counter
    await redis.delete(lock_key)

    # Generate JWT token
    token = rbac.generate_token(
        user_id=username_lower,
        role=user["role"],
        expires_in_hours=24
    )

    # Get permissions for role
    from api.security import ROLE_PERMISSIONS
    permissions = list(ROLE_PERMISSIONS.get(user["role"], []))

    # Metrics: successful login
    AUTH_LOGINS.labels(result="success").inc()

    # Audit: successful login
    await audit.log_action(
        user_id=username_lower,
        role=user["role"],
        action="auth.login.success",
        resource_type="auth",
        resource_id=username_lower,
        details=None
    )

    return LoginResponse(
        token=token,
        role=user["role"],
        permissions=permissions
    )

# ==============================================================================
# BUDGET ENDPOINTS
# ==============================================================================

@router.post("/budget/request", response_model=BudgetResponse)
@rbac.require_permission(Permission.BUDGET_VIEW)
async def budget_request(
    request: BudgetRequestModel,
    req: Request,
    user = Depends(rbac.verify_token)
):
    """Request budget allocation for tokens"""

    redis = req.app.state.redis
    db_pool = req.app.state.db_pool

    # Namespaced keys for tenant/project isolation
    budget_key = f"budget:{request.tenant_id}:{request.project_id}"
    reservations_set = f"reservations:{request.tenant_id}:{request.project_id}"

    async with db_pool.acquire() as conn:
        # Get limit from DB (or use default 100k)
        limit_row = await conn.fetchrow(
            "SELECT total_limit FROM budget_limits WHERE tenant_id = $1 AND project_id = $2",
            request.tenant_id, request.project_id
        )
        total_limit = limit_row["total_limit"] if limit_row else 100000

    # Get current usage
    used = int(await redis.get(budget_key) or "0")

    # Count reserved tokens from Set
    reservation_ids = await redis.smembers(reservations_set)
    reserved = 0
    for res_id in reservation_ids:
        res_key = f"reservation:{request.tenant_id}:{request.project_id}:{res_id}"
        res_data = await redis.get(res_key)
        if res_data:
            tokens, _ = res_data.split(":", 1)
            reserved += int(tokens)

    available = total_limit - used - reserved

    if available >= request.estimated_tokens:
        # Approve and create namespaced reservation
        reservation_id = str(uuid.uuid4())
        reservation_key = f"reservation:{request.tenant_id}:{request.project_id}:{reservation_id}"

        await redis.setex(
            reservation_key,
            3600,  # 1 hour expiry
            f"{request.estimated_tokens}:{request.task_id}"
        )
        # Add to reservations set
        await redis.sadd(reservations_set, reservation_id)
        await redis.expire(reservations_set, 3600)

        # Metrics: budget approved
        BUDGET_REQUESTS.labels(status="approved").inc()

        return BudgetResponse(
            approved=True,
            reservation_id=reservation_id,
            allocated=request.estimated_tokens
        )
    else:
        # Metrics: budget insufficient
        BUDGET_REQUESTS.labels(status="insufficient").inc()

        raise HTTPException(
            status_code=409,
            detail=f"budget.insufficient: Available {available}, Requested {request.estimated_tokens}"
        )

@router.post("/budget/commit")
@rbac.require_permission(Permission.BUDGET_VIEW)
async def budget_commit(
    request: BudgetCommitModel,
    req: Request,
    user = Depends(rbac.verify_token),
    audit: AuditLogger = Depends(get_audit_logger)
):
    """Commit actual token usage and increment used counter"""

    redis = req.app.state.redis

    # Namespaced keys
    reservation_key = f"reservation:{request.tenant_id}:{request.project_id}:{request.reservation_id}"
    budget_key = f"budget:{request.tenant_id}:{request.project_id}"
    reservations_set = f"reservations:{request.tenant_id}:{request.project_id}"

    # Get reservation
    reservation = await redis.get(reservation_key)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found or expired")

    # Increment used counter
    await redis.incrby(budget_key, request.actual_tokens)

    # Delete reservation and remove from set
    await redis.delete(reservation_key)
    await redis.srem(reservations_set, request.reservation_id)

    # Metrics: budget commit
    BUDGET_COMMITS.inc()

    # Audit: budget commit
    await audit.log_action(
        user_id=user["user_id"],
        role=user["role"],
        action="budget.commit",
        resource_type="budget",
        resource_id=request.reservation_id,
        details={"actual_tokens": request.actual_tokens, "tenant_id": request.tenant_id, "project_id": request.project_id}
    )

    return {"status": "committed", "tokens": request.actual_tokens}

@router.post("/budget/release")
@rbac.require_permission(Permission.BUDGET_VIEW)
async def budget_release(
    request: BudgetReleaseModel,
    req: Request,
    user = Depends(rbac.verify_token),
    audit: AuditLogger = Depends(get_audit_logger)
):
    """Release unused reservation (cancel without incrementing used)"""

    redis = req.app.state.redis

    # Namespaced keys
    reservation_key = f"reservation:{request.tenant_id}:{request.project_id}:{request.reservation_id}"
    reservations_set = f"reservations:{request.tenant_id}:{request.project_id}"

    # Delete reservation without incrementing used
    await redis.delete(reservation_key)
    await redis.srem(reservations_set, request.reservation_id)

    # Metrics: budget release
    BUDGET_RELEASES.inc()

    # Audit: budget release
    await audit.log_action(
        user_id=user["user_id"],
        role=user["role"],
        action="budget.release",
        resource_type="budget",
        resource_id=request.reservation_id,
        details=None
    )

    return {"status": "released"}

@router.get("/budget/state")
@rbac.require_permission(Permission.BUDGET_VIEW)
async def budget_state(
    tenant_id: str,
    project_id: str,
    req: Request,
    user = Depends(rbac.verify_token)
):
    """Get budget state for tenant/project (no SCAN, uses namespaced Set)"""

    redis = req.app.state.redis
    db_pool = req.app.state.db_pool

    # Namespaced keys
    budget_key = f"budget:{tenant_id}:{project_id}"
    reservations_set = f"reservations:{tenant_id}:{project_id}"

    # Get limit from DB
    async with db_pool.acquire() as conn:
        limit_row = await conn.fetchrow(
            "SELECT total_limit FROM budget_limits WHERE tenant_id = $1 AND project_id = $2",
            tenant_id, project_id
        )
        total = limit_row["total_limit"] if limit_row else 100000

    # Get used from Redis
    used = int(await redis.get(budget_key) or "0")

    # Count reservations from Set (no global SCAN)
    reservation_ids = await redis.smembers(reservations_set)
    reserved = 0
    for res_id in reservation_ids:
        res_key = f"reservation:{tenant_id}:{project_id}:{res_id}"
        res_data = await redis.get(res_key)
        if res_data:
            tokens, _ = res_data.split(":", 1)
            reserved += int(tokens)

    return {
        "total": total,
        "used": used,
        "reserved": reserved,
        "available": total - used - reserved
    }

# ==============================================================================
# DLQ ENDPOINTS
# ==============================================================================

@router.get("/dlq", response_model=List[DLQMessage])
@rbac.require_permission(Permission.READ_DLQ)
async def get_dlq_messages(
    resolved: bool = False,
    limit: int = 50,
    offset: int = 0,
    req: Request = None,
    user = Depends(rbac.verify_token)
):
    """Get DLQ messages"""

    db_pool = req.app.state.db_pool

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, original_subject, data, headers, error_count,
                   created_at, resolved
            FROM dlq_messages
            WHERE resolved = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            resolved, limit, offset
        )

    messages = []
    for row in rows:
        # Extract error from headers.error if exists
        error_msg = None
        if row["headers"] and isinstance(row["headers"], dict):
            error_msg = row["headers"].get("error", "Unknown error")

        # Preview first 200 chars of data
        data_preview = row["data"][:200] if row["data"] else ""

        messages.append(DLQMessage(
            id=str(row["id"]),
            original_subject=row["original_subject"],
            data_preview=data_preview,
            error=error_msg,
            attempts=row["error_count"],
            created_at=row["created_at"],
            resolved=row["resolved"]
        ))

    return messages

@router.get("/dlq/{message_id}")
@rbac.require_permission(Permission.READ_DLQ)
async def get_dlq_message(
    message_id: str,
    req: Request,
    user = Depends(rbac.verify_token)
):
    """Get single DLQ message details"""

    db_pool = req.app.state.db_pool

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM dlq_messages WHERE id = $1",
            message_id
        )

    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    return dict(row)

@router.post("/dlq/{message_id}/resolve")
@rbac.require_permission(Permission.SYSTEM_ADMIN)
async def resolve_dlq_message(
    message_id: str,
    resolve_req: DLQResolveModel,
    req: Request,
    user = Depends(rbac.verify_token),
    audit: AuditLogger = Depends(get_audit_logger)
):
    """Resolve DLQ message"""

    db_pool = req.app.state.db_pool

    async with db_pool.acquire() as conn:
        # Mark as resolved (correct column: resolution_notes, not resolution_note)
        await conn.execute(
            """
            UPDATE dlq_messages
            SET resolved = TRUE,
                resolved_at = NOW(),
                resolution_notes = $2
            WHERE id = $1
            """,
            message_id,
            resolve_req.note
        )

    # Metrics: DLQ resolved
    DLQ_RESOLVED.inc()

    # Audit: DLQ resolved
    await audit.log_action(
        user_id=user["user_id"],
        role=user["role"],
        action="dlq.resolve",
        resource_type="dlq_message",
        resource_id=message_id,
        details={"note": resolve_req.note, "requeue": resolve_req.requeue}
    )

    return {
        "status": "resolved",
        "message_id": message_id,
        "requeued": resolve_req.requeue
    }

# ==============================================================================
# CIRCUIT BREAKER ENDPOINTS
# ==============================================================================

@router.post("/circuit-breakers/reset_all")
@rbac.require_permission(Permission.SYSTEM_ADMIN)
async def reset_all_circuit_breakers(
    user = Depends(rbac.verify_token),
    audit: AuditLogger = Depends(get_audit_logger)
):
    """Reset all circuit breakers (admin only)"""

    from common.circuit_breaker import circuit_breaker_registry

    # Use the correct async reset_all() method
    await circuit_breaker_registry.reset_all()

    # Get all breaker names from stats
    all_stats = circuit_breaker_registry.get_all_stats()
    breaker_names = list(all_stats.keys())
    reset_count = len(breaker_names)

    # Metrics: circuit breaker resets
    BREAKER_RESETS.inc(reset_count)

    # Audit: circuit breaker resets
    await audit.log_action(
        user_id=user["user_id"],
        role=user["role"],
        action="breakers.reset_all",
        resource_type="circuit_breakers",
        resource_id="all",
        details={"reset_count": reset_count}
    )

    return {
        "status": "success",
        "reset_count": reset_count,
        "breakers": breaker_names
    }
