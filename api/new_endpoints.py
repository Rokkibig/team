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
    reservation_id: str
    actual_tokens: int

class BudgetReleaseModel(BaseModel):
    reservation_id: str

class DLQMessage(BaseModel):
    id: str
    original_subject: str
    payload: dict
    error: str
    attempts: int
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

    Demo credentials:
    - admin/admin123
    - operator/operator123
    - developer/dev123
    - observer/obs123
    """
    user = DEMO_USERS.get(request.username)

    if not user or user["password"] != request.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

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

    # Get Redis from app state
    redis = req.app.state.redis

    # Simple budget check (in production: use IdempotentBudgetController)
    budget_key = f"budget:{request.tenant_id}:{request.project_id}"

    # Get current usage (default 100k tokens per project)
    current_usage = await redis.get(budget_key) or "0"
    total_limit = 100000
    available = total_limit - int(current_usage)

    if available >= request.estimated_tokens:
        # Approve and create reservation
        reservation_id = str(uuid.uuid4())
        reservation_key = f"reservation:{reservation_id}"

        await redis.setex(
            reservation_key,
            3600,  # 1 hour expiry
            f"{request.estimated_tokens}:{request.task_id}"
        )

        return BudgetResponse(
            approved=True,
            reservation_id=reservation_id,
            allocated=request.estimated_tokens
        )
    else:
        return BudgetResponse(
            approved=False,
            allocated=0,
            reason=f"Insufficient budget. Available: {available}, Requested: {request.estimated_tokens}"
        )

@router.post("/budget/commit")
@rbac.require_permission(Permission.BUDGET_VIEW)
async def budget_commit(
    request: BudgetCommitModel,
    req: Request,
    user = Depends(rbac.verify_token)
):
    """Commit actual token usage"""

    redis = req.app.state.redis
    reservation_key = f"reservation:{request.reservation_id}"

    # Get reservation
    reservation = await redis.get(reservation_key)
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found or expired")

    # Delete reservation
    await redis.delete(reservation_key)

    return {"status": "committed", "tokens": request.actual_tokens}

@router.post("/budget/release")
@rbac.require_permission(Permission.BUDGET_VIEW)
async def budget_release(
    request: BudgetReleaseModel,
    req: Request,
    user = Depends(rbac.verify_token)
):
    """Release unused reservation"""

    redis = req.app.state.redis
    reservation_key = f"reservation:{request.reservation_id}"

    await redis.delete(reservation_key)

    return {"status": "released"}

@router.get("/budget/state")
@rbac.require_permission(Permission.BUDGET_VIEW)
async def budget_state(
    tenant_id: str,
    project_id: str,
    req: Request,
    user = Depends(rbac.verify_token)
):
    """Get budget state for tenant/project"""

    redis = req.app.state.redis
    budget_key = f"budget:{tenant_id}:{project_id}"

    used = int(await redis.get(budget_key) or "0")
    total = 100000  # Default limit

    # Count active reservations
    cursor = 0
    reserved = 0
    while True:
        cursor, keys = await redis.scan(cursor, match="reservation:*", count=100)
        for key in keys:
            value = await redis.get(key)
            if value:
                tokens, _ = value.split(":")
                reserved += int(tokens)
        if cursor == 0:
            break

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
            SELECT id, original_subject, payload, error, attempts,
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
        messages.append(DLQMessage(
            id=str(row["id"]),
            original_subject=row["original_subject"],
            payload=row["payload"],
            error=row["error"],
            attempts=row["attempts"],
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
        # Mark as resolved
        await conn.execute(
            """
            UPDATE dlq_messages
            SET resolved = TRUE,
                resolved_at = NOW(),
                resolution_note = $2
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

    reset_count = 0
    for name, breaker in circuit_breaker_registry.items():
        breaker.reset()
        reset_count += 1

    return {
        "status": "success",
        "reset_count": reset_count,
        "breakers": list(circuit_breaker_registry.keys())
    }
