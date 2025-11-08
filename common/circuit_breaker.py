"""
Circuit Breaker Pattern Implementation
Protects services from cascading failures
"""

import time
import asyncio
import logging
from enum import Enum
from typing import Optional, Callable, Any, Type
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# =============================================================================
# CIRCUIT BREAKER STATES
# =============================================================================

class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, reject requests
    HALF_OPEN = "half_open" # Testing if service recovered

# =============================================================================
# CIRCUIT BREAKER
# =============================================================================

@dataclass
class CircuitBreakerStats:
    """Statistics for monitoring"""
    state: str
    failure_count: int
    success_count: int
    last_failure_time: Optional[float]
    opened_at: Optional[float]
    total_calls: int
    total_failures: int
    total_successes: int

class CircuitBreaker:
    """
    Circuit Breaker for fault tolerance

    States:
    - CLOSED: Normal operation, all requests pass through
    - OPEN: Too many failures, reject all requests immediately
    - HALF_OPEN: Testing recovery, allow limited requests

    Example:
        breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30
        )

        result = await breaker.call(risky_function, arg1, arg2)
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        half_open_max_calls: int = 3,
        expected_exception: Type[Exception] = Exception,
        name: str = "default"
    ):
        """
        Initialize circuit breaker

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            half_open_max_calls: Max calls allowed in HALF_OPEN state
            expected_exception: Exception type to catch
            name: Breaker name for logging
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.expected_exception = expected_exception
        self.name = name

        # State
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.opened_at: Optional[float] = None
        self.half_open_calls = 0

        # Statistics
        self.total_calls = 0
        self.total_failures = 0
        self.total_successes = 0

        # Lock for thread safety
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection

        Args:
            func: Async function to call
            *args, **kwargs: Arguments for function

        Returns:
            Result of function call

        Raises:
            Exception: If circuit is OPEN or function fails
        """

        async with self._lock:
            self.total_calls += 1

            # Check circuit state
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    logger.info(f"Circuit {self.name}: OPEN → HALF_OPEN (testing recovery)")
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                else:
                    logger.warning(f"Circuit {self.name}: OPEN - rejecting request")
                    raise CircuitOpenException(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Retry after {self._time_until_retry():.1f}s"
                    )

            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_calls >= self.half_open_max_calls:
                    logger.warning(
                        f"Circuit {self.name}: HALF_OPEN max calls reached - "
                        "rejecting request"
                    )
                    raise CircuitOpenException(
                        f"Circuit breaker '{self.name}' is HALF_OPEN "
                        f"(testing recovery)"
                    )
                self.half_open_calls += 1

        # Execute function
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result

        except self.expected_exception as e:
            await self._on_failure()
            raise

    async def _on_success(self):
        """Handle successful call"""
        async with self._lock:
            self.total_successes += 1
            self.success_count += 1

            if self.state == CircuitState.HALF_OPEN:
                # Successful test in HALF_OPEN
                logger.info(
                    f"Circuit {self.name}: HALF_OPEN → CLOSED "
                    f"(recovery successful)"
                )
                self._reset()

            elif self.state == CircuitState.CLOSED:
                # Reset failure count on success
                if self.failure_count > 0:
                    self.failure_count = 0

    async def _on_failure(self):
        """Handle failed call"""
        async with self._lock:
            self.total_failures += 1
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.state == CircuitState.HALF_OPEN:
                # Failed test in HALF_OPEN, back to OPEN
                logger.warning(
                    f"Circuit {self.name}: HALF_OPEN → OPEN "
                    f"(recovery failed)"
                )
                self.state = CircuitState.OPEN
                self.opened_at = time.time()

            elif self.state == CircuitState.CLOSED:
                # Check if threshold exceeded
                if self.failure_count >= self.failure_threshold:
                    logger.error(
                        f"Circuit {self.name}: CLOSED → OPEN "
                        f"({self.failure_count} failures)"
                    )
                    self.state = CircuitState.OPEN
                    self.opened_at = time.time()

    def _should_attempt_reset(self) -> bool:
        """Check if enough time passed to attempt recovery"""
        if not self.last_failure_time:
            return True

        time_since_failure = time.time() - self.last_failure_time
        return time_since_failure >= self.recovery_timeout

    def _time_until_retry(self) -> float:
        """Calculate time until retry is allowed"""
        if not self.last_failure_time:
            return 0.0

        time_since_failure = time.time() - self.last_failure_time
        return max(0.0, self.recovery_timeout - time_since_failure)

    def _reset(self):
        """Reset circuit to CLOSED state"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.opened_at = None
        self.half_open_calls = 0

    def get_stats(self) -> CircuitBreakerStats:
        """Get current statistics"""
        return CircuitBreakerStats(
            state=self.state.value,
            failure_count=self.failure_count,
            success_count=self.success_count,
            last_failure_time=self.last_failure_time,
            opened_at=self.opened_at,
            total_calls=self.total_calls,
            total_failures=self.total_failures,
            total_successes=self.total_successes
        )

    async def reset_manually(self):
        """Manually reset circuit (admin operation)"""
        async with self._lock:
            logger.info(f"Circuit {self.name}: Manual reset")
            self._reset()

# =============================================================================
# EXCEPTIONS
# =============================================================================

class CircuitOpenException(Exception):
    """Raised when circuit breaker is open"""
    pass

# =============================================================================
# CIRCUIT BREAKER DECORATOR
# =============================================================================

def with_circuit_breaker(
    breaker: CircuitBreaker,
    fallback: Optional[Callable] = None
):
    """
    Decorator to apply circuit breaker to function

    Args:
        breaker: CircuitBreaker instance
        fallback: Optional fallback function if circuit is open

    Example:
        breaker = CircuitBreaker(name="api")

        @with_circuit_breaker(breaker)
        async def call_external_api():
            ...
    """

    def decorator(func: Callable):
        async def wrapper(*args, **kwargs):
            try:
                return await breaker.call(func, *args, **kwargs)
            except CircuitOpenException as e:
                if fallback:
                    logger.info(f"Using fallback for {func.__name__}")
                    return await fallback(*args, **kwargs)
                raise

        return wrapper

    return decorator

# =============================================================================
# CIRCUIT BREAKER REGISTRY
# =============================================================================

class CircuitBreakerRegistry:
    """
    Global registry for circuit breakers

    Allows monitoring and management of all breakers
    """

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}

    def register(self, name: str, breaker: CircuitBreaker):
        """Register a circuit breaker"""
        self._breakers[name] = breaker
        logger.info(f"Registered circuit breaker: {name}")

    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name"""
        return self._breakers.get(name)

    def get_all_stats(self) -> dict[str, CircuitBreakerStats]:
        """Get statistics for all breakers"""
        return {
            name: breaker.get_stats()
            for name, breaker in self._breakers.items()
        }

    async def reset_all(self):
        """Reset all circuit breakers (emergency operation)"""
        for name, breaker in self._breakers.items():
            await breaker.reset_manually()
        logger.warning("All circuit breakers reset")

# Global registry
circuit_breaker_registry = CircuitBreakerRegistry()
