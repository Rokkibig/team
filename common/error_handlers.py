"""
Unified Error Handlers for FastAPI
Standard error response format across all endpoints
"""

import logging
import uuid
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ============================================================================
# ERROR RESPONSE MODEL
# ============================================================================

class ErrorResponse(BaseModel):
    """Standard error response format"""
    error_code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None

# ============================================================================
# ERROR CODE MAPPING
# ============================================================================

ERROR_CODES = {
    400: "validation.invalid_request",
    401: "auth.unauthorized",
    403: "auth.forbidden",
    404: "resource.not_found",
    409: "state.conflict",
    422: "validation.unprocessable_entity",
    429: "rate_limit.exceeded",
    500: "internal.error",
    503: "service.unavailable",
}

# Specialized error codes
SPECIALIZED_CODES = {
    "budget.insufficient": 409,
    "idempotency.conflict": 409,
    "auth.invalid_credentials": 401,
    "dlq.already_resolved": 409,
}

# ============================================================================
# ERROR HANDLERS
# ============================================================================

async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle HTTPException with standard format"""

    from fastapi import HTTPException

    if not isinstance(exc, HTTPException):
        return await generic_exception_handler(request, exc)

    # Get or generate request ID
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    # Map status code to error code
    error_code = ERROR_CODES.get(exc.status_code, "unknown.error")

    # Check if detail contains specialized error code
    detail_str = str(exc.detail) if exc.detail else ""
    for specialized_code in SPECIALIZED_CODES:
        if specialized_code in detail_str:
            error_code = specialized_code
            break

    # Build error response
    error_response = ErrorResponse(
        error_code=error_code,
        message=exc.detail or "An error occurred",
        request_id=request_id
    )

    # Log error (except 404)
    if exc.status_code >= 500:
        logger.error(
            f"HTTP {exc.status_code}: {error_code} - {exc.detail}",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method
            }
        )
    elif exc.status_code != 404:
        logger.warning(
            f"HTTP {exc.status_code}: {error_code} - {exc.detail}",
            extra={"request_id": request_id}
        )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.dict(exclude_none=True)
    )

async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError
) -> JSONResponse:
    """Handle validation errors (422) with details"""

    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    # Extract validation errors
    errors = []
    for error in exc.errors():
        errors.append({
            "field": ".".join(str(loc) for loc in error["loc"]),
            "message": error["msg"],
            "type": error["type"]
        })

    error_response = ErrorResponse(
        error_code="validation.unprocessable_entity",
        message="Validation error",
        details={"errors": errors},
        request_id=request_id
    )

    logger.warning(
        f"Validation error: {len(errors)} field(s)",
        extra={"request_id": request_id, "errors": errors}
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_response.dict(exclude_none=True)
    )

async def rate_limit_exception_handler(
    request: Request,
    exc: RateLimitExceeded
) -> JSONResponse:
    """Handle rate limit exceeded (429)"""

    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    error_response = ErrorResponse(
        error_code="rate_limit.exceeded",
        message="Too many requests. Please try again later.",
        details={
            "retry_after": "60s"
        },
        request_id=request_id
    )

    logger.warning(
        f"Rate limit exceeded: {request.client.host}",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "client": request.client.host
        }
    )

    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=error_response.dict(exclude_none=True),
        headers={"Retry-After": "60"}
    )

async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions (500)"""

    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

    error_response = ErrorResponse(
        error_code="internal.error",
        message="An internal error occurred. Please try again later.",
        request_id=request_id
    )

    # Log full exception details
    logger.exception(
        f"Unhandled exception: {type(exc).__name__}",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "exception": str(exc)
        }
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response.dict(exclude_none=True)
    )

# ============================================================================
# INSTALLATION
# ============================================================================

def install_error_handlers(app: FastAPI) -> None:
    """Install all error handlers on FastAPI app"""

    from fastapi import HTTPException

    # HTTP exceptions
    app.add_exception_handler(HTTPException, http_exception_handler)

    # Validation errors
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # Rate limiting
    app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)

    # Generic exceptions (fallback)
    app.add_exception_handler(Exception, generic_exception_handler)

    logger.info("✅ Error handlers installed")

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def create_error_response(
    error_code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None
) -> Dict[str, Any]:
    """Helper to create standardized error response"""

    response = ErrorResponse(
        error_code=error_code,
        message=message,
        details=details,
        request_id=request_id
    )

    return response.dict(exclude_none=True)
