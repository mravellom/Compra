"""
Structured JSON logging for production observability.

Replaces unstructured log lines with machine-parseable JSON events.
Compatible with ELK Stack, Datadog, CloudWatch, Grafana Loki.

Features:
  - JSON-formatted log output
  - Contextual fields (component, operation, duration_ms)
  - Correlation IDs for request tracing
  - Log level filtering
  - Performance-safe (lazy serialization)
"""
import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

# ── Correlation ID for request tracing ────────────────────────
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def set_correlation_id(cid: str | None = None) -> str:
    """Set correlation ID for current async context. Returns the ID."""
    cid = cid or uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str:
    return _correlation_id.get("")


# ── JSON Formatter ────────────────────────────────────────────

class StructuredFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects.

    Output:
      {"ts":"2026-03-16T00:00:00Z","level":"INFO","component":"processor",
       "msg":"Batch processed","items":256,"duration_ms":42.5,"cid":"abc123"}
    """

    def __init__(self, component: str = "app"):
        super().__init__()
        self.component = component

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "component": getattr(record, "component", self.component),
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Add correlation ID if set
        cid = get_correlation_id()
        if cid:
            entry["cid"] = cid

        # Add extra fields from record
        for key in ("duration_ms", "operation", "items", "throughput",
                     "error_type", "marketplace", "product_id", "batch_size"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val

        # Add exception info
        if record.exc_info and record.exc_info[1]:
            entry["error"] = str(record.exc_info[1])
            entry["error_type"] = type(record.exc_info[1]).__name__

        return json.dumps(entry, default=str, ensure_ascii=False)


# ── Setup Function ────────────────────────────────────────────

def setup_structured_logging(
    component: str = "app",
    level: str | None = None,
) -> None:
    """
    Configure structured JSON logging for the entire application.

    Call once at startup:
        setup_structured_logging("processor")
    """
    log_level = level or os.getenv("LOG_LEVEL", "INFO")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(component))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Reduce noise from third-party libraries
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Structured Logger Helper ──────────────────────────────────

class StructuredLogger:
    """
    Convenience wrapper for structured logging with extra fields.

    Usage:
        log = StructuredLogger("processor.pipeline")
        log.info("Batch processed", items=256, duration_ms=42.5)
        log.error("Embedding failed", error_type="CUDA", batch_size=64)
    """

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def _log(self, level: int, msg: str, **kwargs: object) -> None:
        extra = {k: v for k, v in kwargs.items() if v is not None}
        self._logger.log(level, msg, extra=extra)

    def debug(self, msg: str, **kwargs: object) -> None:
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: object) -> None:
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: object) -> None:
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: object) -> None:
        self._log(logging.ERROR, msg, **kwargs)

    def timed(self, operation: str) -> "TimedContext":
        """Context manager for timing operations."""
        return TimedContext(self, operation)


class TimedContext:
    """Context manager that logs duration on exit."""

    def __init__(self, logger: StructuredLogger, operation: str):
        self._logger = logger
        self._operation = operation
        self._start = 0.0

    def __enter__(self) -> "TimedContext":
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc: object) -> None:
        duration_ms = (time.monotonic() - self._start) * 1000
        if exc[0]:
            self._logger.error(
                f"{self._operation} failed",
                operation=self._operation,
                duration_ms=round(duration_ms, 1),
            )
        else:
            self._logger.info(
                f"{self._operation} completed",
                operation=self._operation,
                duration_ms=round(duration_ms, 1),
            )
