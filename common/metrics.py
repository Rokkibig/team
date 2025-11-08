"""
Prometheus Metrics for Golden Architecture V5.1
Centralized metrics collection for HTTP requests and business operations
"""

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# HTTP Request Metrics
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["route", "method", "status"]
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["route", "method"]
)

# Business Metrics
AUTH_LOGINS = Counter(
    "auth_logins_total",
    "Total authentication attempts",
    ["result"]  # success | fail
)

BUDGET_REQUESTS = Counter(
    "budget_requests_total",
    "Total budget requests",
    ["status"]  # approved | insufficient
)

BUDGET_COMMITS = Counter(
    "budget_commits_total",
    "Total budget commits"
)

BUDGET_RELEASES = Counter(
    "budget_releases_total",
    "Total budget releases"
)

DLQ_RESOLVED = Counter(
    "dlq_resolved_total",
    "Total DLQ messages resolved"
)

BREAKER_RESETS = Counter(
    "breaker_resets_total",
    "Total circuit breaker resets"
)

__all__ = [
    "HTTP_REQUESTS",
    "HTTP_REQUEST_DURATION",
    "AUTH_LOGINS",
    "BUDGET_REQUESTS",
    "BUDGET_COMMITS",
    "BUDGET_RELEASES",
    "DLQ_RESOLVED",
    "BREAKER_RESETS",
    "generate_latest",
    "CONTENT_TYPE_LATEST"
]
