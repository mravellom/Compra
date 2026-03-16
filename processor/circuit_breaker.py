"""
Circuit Breaker pattern for fault-tolerant external service calls.

States:
  CLOSED  → normal operation, calls pass through
  OPEN    → calls fail fast without executing (service assumed down)
  HALF_OPEN → limited calls allowed to test if service recovered

Transitions:
  CLOSED → OPEN:      when failure_count >= failure_threshold
  OPEN → HALF_OPEN:   after recovery_timeout seconds
  HALF_OPEN → CLOSED: when a test call succeeds
  HALF_OPEN → OPEN:   when a test call fails
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, TypeVar, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStats:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0


class CircuitBreaker:
    """
    Async circuit breaker with configurable thresholds.

    Usage:
        cb = CircuitBreaker("embeddings", failure_threshold=5, recovery_timeout=30)

        result = await cb.call(generate_embeddings_batch, texts)
        # or
        async with cb.protect():
            result = await some_async_call()
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
        half_open_max_calls: int = 3,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._lock = asyncio.Lock()
        self._opened_at: float = 0.0
        self._half_open_calls: int = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info("[CircuitBreaker:%s] OPEN → HALF_OPEN (testing recovery)", self.name)
        return self._state

    @property
    def stats(self) -> CircuitStats:
        return self._stats

    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute function through the circuit breaker."""
        async with self._lock:
            current = self.state

            if current == CircuitState.OPEN:
                self._stats.rejected_calls += 1
                raise CircuitOpenError(
                    f"Circuit '{self.name}' is OPEN — failing fast "
                    f"(recovery in {self.recovery_timeout - (time.monotonic() - self._opened_at):.0f}s)"
                )

            if current == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    self._stats.rejected_calls += 1
                    raise CircuitOpenError(
                        f"Circuit '{self.name}' HALF_OPEN limit reached"
                    )
                self._half_open_calls += 1

        self._stats.total_calls += 1

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: func(*args, **kwargs)
                )
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure(e)
            raise

    async def _on_success(self) -> None:
        async with self._lock:
            self._stats.successful_calls += 1
            self._stats.consecutive_successes += 1
            self._stats.consecutive_failures = 0
            self._stats.last_success_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                if self._stats.consecutive_successes >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    logger.info("[CircuitBreaker:%s] HALF_OPEN → CLOSED (recovered)", self.name)

    async def _on_failure(self, error: Exception) -> None:
        async with self._lock:
            self._stats.failed_calls += 1
            self._stats.consecutive_failures += 1
            self._stats.consecutive_successes = 0
            self._stats.last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "[CircuitBreaker:%s] HALF_OPEN → OPEN (test failed: %s)", self.name, error
                )
            elif self._stats.consecutive_failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "[CircuitBreaker:%s] CLOSED → OPEN after %d failures: %s",
                    self.name, self._stats.consecutive_failures, error,
                )

    def reset(self) -> None:
        """Manual reset to CLOSED."""
        self._state = CircuitState.CLOSED
        self._stats.consecutive_failures = 0
        self._stats.consecutive_successes = 0
        logger.info("[CircuitBreaker:%s] Manually reset to CLOSED", self.name)


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""
    pass


# ── Pre-configured circuit breakers ──────────────────────────

embedding_circuit = CircuitBreaker(
    name="embeddings",
    failure_threshold=3,
    recovery_timeout=60.0,
    success_threshold=2,
)

database_circuit = CircuitBreaker(
    name="database",
    failure_threshold=5,
    recovery_timeout=30.0,
    success_threshold=3,
)

redis_circuit = CircuitBreaker(
    name="redis",
    failure_threshold=5,
    recovery_timeout=15.0,
    success_threshold=2,
)
