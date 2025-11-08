"""
Idempotent Budget Controller with Redis
Prevents duplicate token requests and ensures exactly-once semantics
"""

import uuid
import json
import logging
from typing import Optional, Dict
from dataclasses import dataclass, asdict
from datetime import datetime

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class BudgetDecision:
    """Result of budget request"""
    approved: bool
    reason: str
    allocated_tokens: int = 0
    request_id: Optional[str] = None
    timestamp: Optional[str] = None

@dataclass
class BudgetLimit:
    """Budget limits for a tenant/project"""
    tenant_id: str
    project_id: str
    total_limit: int
    current_usage: int
    reserved: int
    available: int

# =============================================================================
# IDEMPOTENT BUDGET CONTROLLER
# =============================================================================

class IdempotentBudgetController:
    """
    Budget controller with idempotency guarantees

    Features:
    - Exactly-once token allocation
    - Duplicate request detection
    - Automatic cleanup of old requests
    - Multi-tenant budget isolation
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        db_pool,
        default_tenant_limit: int = 1_000_000
    ):
        self.redis = redis_client
        self.db = db_pool
        self.default_tenant_limit = default_tenant_limit

    async def request_tokens(
        self,
        purpose: str,
        estimated_tokens: int,
        model: str,
        task_id: str,
        project_id: str,
        tenant_id: str,
        request_id: Optional[str] = None
    ) -> BudgetDecision:
        """
        Request budget with idempotency

        Args:
            purpose: Why tokens are needed (e.g., "code_generation")
            estimated_tokens: Estimated token count
            model: Model name (affects cost)
            task_id: Task identifier
            project_id: Project identifier
            tenant_id: Tenant identifier
            request_id: Optional idempotency key

        Returns:
            BudgetDecision with approval status

        Idempotency:
            Same request_id will return cached result
        """

        # Generate request_id if not provided
        request_id = request_id or str(uuid.uuid4())

        # Create idempotency key
        req_key = f"budget:req:{tenant_id}:{task_id}:{request_id}"

        # Try to acquire lock (only if not exists)
        was_new = await self.redis.set(
            req_key,
            "processing",
            nx=True,  # Only set if not exists
            ex=300    # 5 minute TTL
        )

        if not was_new:
            # Request already processed or in progress
            logger.info(f"Duplicate request detected: {request_id}")

            # Try to get cached result
            cached_result = await self.redis.get(f"{req_key}:result")

            if cached_result:
                result_data = json.loads(cached_result)
                return BudgetDecision(**result_data)
            else:
                # Request in progress
                return BudgetDecision(
                    approved=False,
                    reason="duplicate_request_in_progress",
                    request_id=request_id
                )

        try:
            # Process budget request
            decision = await self._process_budget_request(
                purpose=purpose,
                estimated_tokens=estimated_tokens,
                model=model,
                task_id=task_id,
                project_id=project_id,
                tenant_id=tenant_id,
                request_id=request_id
            )

            # Cache result for idempotency
            await self.redis.setex(
                f"{req_key}:result",
                300,  # 5 minutes
                json.dumps(asdict(decision))
            )

            return decision

        except Exception as e:
            # Clear lock on error
            await self.redis.delete(req_key)
            logger.error(f"Budget request failed: {e}")
            raise

    async def _process_budget_request(
        self,
        purpose: str,
        estimated_tokens: int,
        model: str,
        task_id: str,
        project_id: str,
        tenant_id: str,
        request_id: str
    ) -> BudgetDecision:
        """Internal processing of budget request"""

        # Get current budget for tenant/project
        budget = await self._get_budget(tenant_id, project_id)

        # Check if request can be approved
        if budget.available < estimated_tokens:
            logger.warning(
                f"Budget insufficient for {tenant_id}/{project_id}: "
                f"requested={estimated_tokens}, available={budget.available}"
            )

            return BudgetDecision(
                approved=False,
                reason=f"insufficient_budget: {budget.available} < {estimated_tokens}",
                request_id=request_id,
                timestamp=datetime.utcnow().isoformat()
            )

        # Reserve tokens
        success = await self._reserve_tokens(
            tenant_id=tenant_id,
            project_id=project_id,
            amount=estimated_tokens,
            task_id=task_id,
            purpose=purpose,
            request_id=request_id
        )

        if not success:
            return BudgetDecision(
                approved=False,
                reason="reservation_failed",
                request_id=request_id,
                timestamp=datetime.utcnow().isoformat()
            )

        # Approved
        logger.info(
            f"Budget approved: {tenant_id}/{project_id} - "
            f"{estimated_tokens} tokens for {purpose}"
        )

        return BudgetDecision(
            approved=True,
            reason="approved",
            allocated_tokens=estimated_tokens,
            request_id=request_id,
            timestamp=datetime.utcnow().isoformat()
        )

    async def _get_budget(
        self,
        tenant_id: str,
        project_id: str
    ) -> BudgetLimit:
        """Get current budget state"""

        # Try Redis cache first
        cache_key = f"budget:state:{tenant_id}:{project_id}"
        cached = await self.redis.get(cache_key)

        if cached:
            data = json.loads(cached)
            return BudgetLimit(**data)

        # Get from database
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    tenant_id,
                    project_id,
                    total_limit,
                    current_usage,
                    reserved,
                    (total_limit - current_usage - reserved) as available
                FROM budget_limits
                WHERE tenant_id = $1 AND project_id = $2
            """, tenant_id, project_id)

            if row:
                budget = BudgetLimit(**dict(row))
            else:
                # Create default budget
                await conn.execute("""
                    INSERT INTO budget_limits
                    (tenant_id, project_id, total_limit, current_usage, reserved)
                    VALUES ($1, $2, $3, 0, 0)
                """, tenant_id, project_id, self.default_tenant_limit)

                budget = BudgetLimit(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    total_limit=self.default_tenant_limit,
                    current_usage=0,
                    reserved=0,
                    available=self.default_tenant_limit
                )

        # Cache for 10 seconds
        await self.redis.setex(
            cache_key,
            10,
            json.dumps(asdict(budget))
        )

        return budget

    async def _reserve_tokens(
        self,
        tenant_id: str,
        project_id: str,
        amount: int,
        task_id: str,
        purpose: str,
        request_id: str
    ) -> bool:
        """
        Reserve tokens atomically

        Returns:
            True if reservation succeeded
        """

        async with self.db.acquire() as conn:
            # Atomic update with constraint check
            result = await conn.fetchrow("""
                UPDATE budget_limits
                SET reserved = reserved + $3
                WHERE tenant_id = $1
                  AND project_id = $2
                  AND (total_limit - current_usage - reserved) >= $3
                RETURNING reserved
            """, tenant_id, project_id, amount)

            if result is None:
                # Reservation failed (insufficient budget)
                return False

            # Log reservation
            await conn.execute("""
                INSERT INTO budget_transactions
                (tenant_id, project_id, task_id, request_id, amount, type, purpose, timestamp)
                VALUES ($1, $2, $3, $4, $5, 'reserve', $6, NOW())
            """, tenant_id, project_id, task_id, request_id, amount, purpose)

            # Invalidate cache
            cache_key = f"budget:state:{tenant_id}:{project_id}"
            await self.redis.delete(cache_key)

            return True

    async def commit_usage(
        self,
        tenant_id: str,
        project_id: str,
        task_id: str,
        actual_tokens: int,
        request_id: str
    ):
        """
        Commit actual token usage

        Moves tokens from reserved to current_usage
        """

        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE budget_limits
                SET
                    current_usage = current_usage + $3,
                    reserved = reserved - $3
                WHERE tenant_id = $1 AND project_id = $2
            """, tenant_id, project_id, actual_tokens)

            # Log commit
            await conn.execute("""
                INSERT INTO budget_transactions
                (tenant_id, project_id, task_id, request_id, amount, type, purpose, timestamp)
                VALUES ($1, $2, $3, $4, $5, 'commit', 'actual_usage', NOW())
            """, tenant_id, project_id, task_id, request_id, actual_tokens)

            # Invalidate cache
            cache_key = f"budget:state:{tenant_id}:{project_id}"
            await self.redis.delete(cache_key)

        logger.info(
            f"Budget committed: {tenant_id}/{project_id} - "
            f"{actual_tokens} tokens for task {task_id}"
        )

    async def release_reservation(
        self,
        tenant_id: str,
        project_id: str,
        task_id: str,
        amount: int,
        request_id: str
    ):
        """Release reserved tokens (on task failure/cancellation)"""

        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE budget_limits
                SET reserved = reserved - $3
                WHERE tenant_id = $1 AND project_id = $2
            """, tenant_id, project_id, amount)

            # Log release
            await conn.execute("""
                INSERT INTO budget_transactions
                (tenant_id, project_id, task_id, request_id, amount, type, purpose, timestamp)
                VALUES ($1, $2, $3, $4, $5, 'release', 'cancelled', NOW())
            """, tenant_id, project_id, task_id, request_id, amount)

            # Invalidate cache
            cache_key = f"budget:state:{tenant_id}:{project_id}"
            await self.redis.delete(cache_key)

        logger.info(f"Budget released: {tenant_id}/{project_id} - {amount} tokens")

# =============================================================================
# DATABASE SCHEMA
# =============================================================================

BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_limits (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    total_limit BIGINT NOT NULL,
    current_usage BIGINT DEFAULT 0,
    reserved BIGINT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tenant_id, project_id)
);

CREATE TABLE IF NOT EXISTS budget_transactions (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    amount BIGINT NOT NULL,
    type TEXT NOT NULL,  -- reserve/commit/release
    purpose TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_budget_tx_tenant
    ON budget_transactions(tenant_id, project_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_budget_tx_request
    ON budget_transactions(request_id);
"""
