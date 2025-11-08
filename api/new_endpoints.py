"""
New API Endpoints for Frontend Integration
Auth, Budget, DLQ, Circuit Breakers
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import uuid

from api.security import rbac, Permission

# Create router
router = APIRouter()

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

# Mock users for demo (in production: use real user database)
DEMO_USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "operator": {"password": "operator123", "role": "operator"},
    "developer": {"password": "dev123", "role": "developer"},
    "observer": {"password": "obs123", "role": "observer"},
}

@router.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Login endpoint - returns JWT token
    Rate limited: 10 requests per minute per IP (via decorator)

    Demo credentials:
    - admin/admin123
    - operator/operator123
    - developer/dev123
    - observer/obs123
    """
    user = DEMO_USERS.get(request.username)

    if not user or user["password"] != request.password:
        # Use error_code marker for unified error handler
        raise HTTPException(status_code=401, detail="auth.invalid_credentials: Invalid credentials")

    # Generate JWT token
    token = rbac.generate_token(
        user_id=request.username,
        role=user["role"],
        expires_in_hours=24
    )

    # Get permissions for role
    from api.security import ROLE_PERMISSIONS
    permissions = list(ROLE_PERMISSIONS.get(user["role"], []))

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

        return BudgetResponse(
            approved=True,
            reservation_id=reservation_id,
            allocated=request.estimated_tokens
        )
    else:
        raise HTTPException(
            status_code=409,
            detail=f"budget.insufficient: Available {available}, Requested {request.estimated_tokens}"
        )

@router.post("/budget/commit")
@rbac.require_permission(Permission.BUDGET_VIEW)
async def budget_commit(
    request: BudgetCommitModel,
    req: Request,
    user = Depends(rbac.verify_token)
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

    return {"status": "committed", "tokens": request.actual_tokens}

@router.post("/budget/release")
@rbac.require_permission(Permission.BUDGET_VIEW)
async def budget_release(
    request: BudgetReleaseModel,
    req: Request,
    user = Depends(rbac.verify_token)
):
    """Release unused reservation (cancel without incrementing used)"""

    redis = req.app.state.redis

    # Namespaced keys
    reservation_key = f"reservation:{request.tenant_id}:{request.project_id}:{request.reservation_id}"
    reservations_set = f"reservations:{request.tenant_id}:{request.project_id}"

    # Delete reservation without incrementing used
    await redis.delete(reservation_key)
    await redis.srem(reservations_set, request.reservation_id)

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
    user = Depends(rbac.verify_token)
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
async def reset_all_circuit_breakers(user = Depends(rbac.verify_token)):
    """Reset all circuit breakers (admin only)"""

    from common.circuit_breaker import circuit_breaker_registry

    # Use the correct async reset_all() method
    await circuit_breaker_registry.reset_all()

    # Get all breaker names from stats
    all_stats = circuit_breaker_registry.get_all_stats()
    breaker_names = list(all_stats.keys())

    return {
        "status": "success",
        "reset_count": len(breaker_names),
        "breakers": breaker_names
    }
