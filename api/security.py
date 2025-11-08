"""
RBAC (Role-Based Access Control) + Rate Limiting
Security middleware for API endpoints with JWT authentication
"""

import os
import json
import logging
from typing import Dict, List, Optional, Callable
from datetime import datetime, timedelta
from functools import wraps

from jose import jwt
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

# =============================================================================
# ROLE-BASED RATE LIMITS
# =============================================================================

RATE_LIMITS = {
    "admin": "100/minute",
    "operator": "50/minute",
    "developer": "30/minute",
    "observer": "20/minute",
    "anonymous": "5/minute"
}

# =============================================================================
# PERMISSION DEFINITIONS
# =============================================================================

class Permission:
    """Available permissions in the system"""

    # Escalation management
    ESCALATION_VIEW = "escalation.view"
    ESCALATION_RESOLVE = "escalation.resolve"
    ESCALATION_CREATE = "escalation.create"

    # Task management
    TASK_CREATE = "task.create"
    TASK_UPDATE = "task.update"
    TASK_DELETE = "task.delete"
    TASK_VIEW = "task.view"

    # Agent management
    AGENT_CONFIGURE = "agent.configure"
    AGENT_VIEW = "agent.view"

    # Budget management
    BUDGET_VIEW = "budget.view"
    BUDGET_CONFIGURE = "budget.configure"

    # Learning system
    LEARNING_APPROVE = "learning.approve"
    LEARNING_VIEW = "learning.view"

    # System administration
    SYSTEM_ADMIN = "system.admin"
    METRICS_VIEW = "metrics.view"

# Role → Permissions mapping
ROLE_PERMISSIONS = {
    "admin": [
        Permission.SYSTEM_ADMIN,
        Permission.ESCALATION_VIEW,
        Permission.ESCALATION_RESOLVE,
        Permission.ESCALATION_CREATE,
        Permission.TASK_CREATE,
        Permission.TASK_UPDATE,
        Permission.TASK_DELETE,
        Permission.TASK_VIEW,
        Permission.AGENT_CONFIGURE,
        Permission.AGENT_VIEW,
        Permission.BUDGET_VIEW,
        Permission.BUDGET_CONFIGURE,
        Permission.LEARNING_APPROVE,
        Permission.LEARNING_VIEW,
        Permission.METRICS_VIEW,
    ],
    "operator": [
        Permission.ESCALATION_VIEW,
        Permission.ESCALATION_RESOLVE,
        Permission.TASK_CREATE,
        Permission.TASK_UPDATE,
        Permission.TASK_VIEW,
        Permission.AGENT_VIEW,
        Permission.BUDGET_VIEW,
        Permission.LEARNING_VIEW,
        Permission.METRICS_VIEW,
    ],
    "developer": [
        Permission.TASK_CREATE,
        Permission.TASK_UPDATE,
        Permission.TASK_VIEW,
        Permission.AGENT_VIEW,
        Permission.METRICS_VIEW,
    ],
    "observer": [
        Permission.TASK_VIEW,
        Permission.AGENT_VIEW,
        Permission.METRICS_VIEW,
    ],
}

# =============================================================================
# JWT SECURITY
# =============================================================================

security = HTTPBearer()

class RBACMiddleware:
    """
    Role-Based Access Control Middleware

    Features:
    - JWT token validation
    - Role-based permissions
    - Audit logging
    - Token expiration
    """

    def __init__(self, secret_key: Optional[str] = None):
        self.secret_key = secret_key or os.getenv(
            "JWT_SECRET",
            "CHANGE_ME_IN_PRODUCTION"
        )

        if self.secret_key == "CHANGE_ME_IN_PRODUCTION":
            logger.warning("Using default JWT secret - CHANGE IN PRODUCTION!")

    def generate_token(
        self,
        user_id: str,
        role: str,
        expires_in_hours: int = 24
    ) -> str:
        """Generate JWT token for user"""

        # Get permissions for role
        permissions = ROLE_PERMISSIONS.get(role, [])

        payload = {
            "sub": user_id,
            "role": role,
            "permissions": permissions,
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(hours=expires_in_hours)
        }

        token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        return token

    async def verify_token(
        self,
        credentials: HTTPAuthorizationCredentials = Depends(security)
    ) -> Dict:
        """
        Verify JWT token and extract claims

        Returns:
            Dict with user_id, role, permissions

        Raises:
            HTTPException: If token is invalid or expired
        """

        token = credentials.credentials

        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=["HS256"]
            )

            user_id = payload.get("sub")
            role = payload.get("role", "observer")
            permissions = payload.get("permissions", [])

            # Validate required fields
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token: missing user_id"
                )

            return {
                "user_id": user_id,
                "role": role,
                "permissions": permissions
            }

        except jwt.ExpiredSignatureError:
            logger.warning(f"Expired token attempt")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired"
            )

        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token attempt: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )

    def require_permission(self, *required_permissions: str):
        """
        Decorator to require specific permissions

        Usage:
            @app.post("/escalations/{id}/resolve")
            @rbac.require_permission(Permission.ESCALATION_RESOLVE)
            async def resolve_escalation(user=Depends(rbac.verify_token)):
                ...
        """

        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(*args, user: Dict = Depends(self.verify_token), **kwargs):

                user_permissions = set(user.get("permissions", []))
                required = set(required_permissions)

                # Check if user has all required permissions
                if not required.issubset(user_permissions):
                    missing = required - user_permissions

                    logger.warning(
                        f"Access denied for user {user['user_id']} "
                        f"(role: {user['role']}) - missing permissions: {missing}"
                    )

                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Missing permissions: {', '.join(missing)}"
                    )

                # Call original function with user context
                return await func(*args, user=user, **kwargs)

            return wrapper

        return decorator

    def require_role(self, *required_roles: str):
        """
        Decorator to require specific roles

        Usage:
            @app.post("/admin/config")
            @rbac.require_role("admin")
            async def update_config(user=Depends(rbac.verify_token)):
                ...
        """

        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(*args, user: Dict = Depends(self.verify_token), **kwargs):

                user_role = user.get("role")

                if user_role not in required_roles:
                    logger.warning(
                        f"Access denied for user {user['user_id']} "
                        f"(role: {user_role}) - requires one of: {required_roles}"
                    )

                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Role '{user_role}' not authorized. "
                               f"Requires one of: {', '.join(required_roles)}"
                    )

                return await func(*args, user=user, **kwargs)

            return wrapper

        return decorator

# =============================================================================
# RATE LIMITING
# =============================================================================

class RoleBasedLimiter:
    """
    Rate limiter with different limits per role

    Usage:
        limiter = RoleBasedLimiter()

        @app.post("/task")
        @limiter.limit_by_role()
        async def create_task(user=Depends(rbac.verify_token)):
            ...
    """

    def __init__(self):
        self.limiter = Limiter(key_func=get_remote_address)

    def limit_by_role(self):
        """Apply rate limit based on user role"""

        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(
                request: Request,
                *args,
                user: Dict = Depends(rbac.verify_token),
                **kwargs
            ):

                # Get rate limit for role
                role = user.get("role", "observer")
                rate_limit = RATE_LIMITS.get(role, RATE_LIMITS["observer"])

                # Apply rate limit
                try:
                    await self.limiter.limit(rate_limit)(func)(
                        request, *args, user=user, **kwargs
                    )
                except Exception as e:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=f"Rate limit exceeded for role '{role}'"
                    )

                return await func(request, *args, user=user, **kwargs)

            return wrapper

        return decorator

# =============================================================================
# AUDIT LOGGING
# =============================================================================

class AuditLogger:
    """
    Audit logger for sensitive operations

    Logs all actions with user context for compliance
    """

    def __init__(self, db_pool):
        self.db = db_pool

    async def log_action(
        self,
        user_id: str,
        role: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: Optional[Dict] = None
    ):
        """Log security-relevant action"""

        try:
            async with self.db.acquire() as conn:
                await conn.execute("""
                    INSERT INTO audit_log
                    (user_id, role, action, resource_type, resource_id, details, timestamp)
                    VALUES ($1, $2, $3, $4, $5, $6, NOW())
                """,
                    user_id,
                    role,
                    action,
                    resource_type,
                    resource_id,
                    json.dumps(details) if details else None
                )

            logger.info(
                f"AUDIT: {user_id} ({role}) performed {action} "
                f"on {resource_type}/{resource_id}"
            )

        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

# =============================================================================
# DATABASE SCHEMA
# =============================================================================

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    details JSONB,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp DESC);
"""

# =============================================================================
# GLOBAL INSTANCES
# =============================================================================

# Create global RBAC instance
rbac = RBACMiddleware()
role_limiter = RoleBasedLimiter()

# =============================================================================
# EXAMPLE USAGE
# =============================================================================

"""
from fastapi import FastAPI
from api.security import rbac, Permission, AuditLogger

app = FastAPI()

@app.post("/escalations/{id}/resolve")
@rbac.require_permission(Permission.ESCALATION_RESOLVE)
async def resolve_escalation(
    id: str,
    resolution: Dict,
    user: Dict = Depends(rbac.verify_token),
    audit: AuditLogger = Depends(get_audit_logger)
):
    # User is guaranteed to have ESCALATION_RESOLVE permission
    # user = {"user_id": "...", "role": "operator", "permissions": [...]}

    # Perform action
    await resolve_escalation_in_db(id, resolution)

    # Log action
    await audit.log_action(
        user_id=user["user_id"],
        role=user["role"],
        action="escalation.resolve",
        resource_type="escalation",
        resource_id=id,
        details={"resolution": resolution}
    )

    return {"status": "resolved"}
"""
