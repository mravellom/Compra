"""
Production middleware: rate limiting, request logging, API key auth.
"""
import hashlib
import logging
import os
import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("api.access")

# ── API Key Authentication ────────────────────────────────────

API_KEY = os.getenv("API_KEY", "")  # Empty = no auth required


def _check_api_key(request: Request) -> bool:
    """Return True if request is authenticated or auth is disabled."""
    if not API_KEY:
        return True  # No auth configured
    # Skip auth for health endpoints and docs
    if request.url.path in ("/health", "/health/pipeline", "/docs", "/openapi.json"):
        return True
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if not key:
        return False
    return hashlib.sha256(key.encode()).hexdigest() == hashlib.sha256(API_KEY.encode()).hexdigest()


# ── Rate Limiter (in-memory, per-IP) ─────────────────────────

RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))  # per window
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))      # seconds

_rate_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if under rate limit."""
    now = time.monotonic()
    window_start = now - RATE_LIMIT_WINDOW
    # Clean old entries
    _rate_store[client_ip] = [t for t in _rate_store[client_ip] if t > window_start]
    if len(_rate_store[client_ip]) >= RATE_LIMIT_REQUESTS:
        return False
    _rate_store[client_ip].append(now)
    return True


# ── Request Logging + Auth + Rate Limit Middleware ────────────

class ProductionMiddleware(BaseHTTPMiddleware):
    """Combined middleware: structured access logs, API key auth, rate limiting."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        client_ip = request.client.host if request.client else "unknown"

        # Auth check
        if not _check_api_key(request):
            logger.warning("auth_fail ip=%s path=%s", client_ip, request.url.path)
            return Response(
                content='{"detail":"Invalid or missing API key"}',
                status_code=401,
                media_type="application/json",
            )

        # Rate limit check (skip health endpoints)
        if request.url.path.startswith("/api/"):
            if not _check_rate_limit(client_ip):
                logger.warning("rate_limit ip=%s path=%s", client_ip, request.url.path)
                return Response(
                    content='{"detail":"Rate limit exceeded"}',
                    status_code=429,
                    media_type="application/json",
                    headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
                )

        # Process request
        response = await call_next(request)

        # Structured access log
        duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            "method=%s path=%s status=%d duration=%.1fms ip=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            client_ip,
        )

        return response
