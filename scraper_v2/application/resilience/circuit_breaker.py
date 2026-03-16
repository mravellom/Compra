"""Circuit Breaker — prevents cascading failures per marketplace.

States:
  CLOSED  → normal operation, failures counted
  OPEN    → all requests short-circuited, waiting for recovery
  HALF_OPEN → limited probe requests to test recovery
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from scraper_v2.domain.enums import CircuitState, Marketplace

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 2
    success_threshold: int = 3


@dataclass
class _CircuitMetrics:
    failures: int = 0
    successes: int = 0
    half_open_calls: int = 0
    state: CircuitState = CircuitState.CLOSED
    last_failure_time: float = 0.0
    last_state_change: float = field(default_factory=time.monotonic)


class CircuitBreaker:
    """Per-marketplace circuit breaker with configurable thresholds."""

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = config or CircuitBreakerConfig()
        self._circuits: dict[Marketplace, _CircuitMetrics] = {}
        self._lock = asyncio.Lock()

    def _get(self, marketplace: Marketplace) -> _CircuitMetrics:
        if marketplace not in self._circuits:
            self._circuits[marketplace] = _CircuitMetrics()
        return self._circuits[marketplace]

    async def allow_request(self, marketplace: Marketplace) -> bool:
        async with self._lock:
            m = self._get(marketplace)

            if m.state == CircuitState.CLOSED:
                return True

            if m.state == CircuitState.OPEN:
                elapsed = time.monotonic() - m.last_failure_time
                if elapsed >= self._config.recovery_timeout:
                    m.state = CircuitState.HALF_OPEN
                    m.half_open_calls = 0
                    m.successes = 0
                    m.last_state_change = time.monotonic()
                    logger.info(
                        "Circuit %s → HALF_OPEN after %.0fs recovery",
                        marketplace.value,
                        elapsed,
                    )
                    return True
                return False

            # HALF_OPEN
            if m.half_open_calls < self._config.half_open_max_calls:
                m.half_open_calls += 1
                return True
            return False

    async def record_success(self, marketplace: Marketplace) -> None:
        async with self._lock:
            m = self._get(marketplace)
            if m.state == CircuitState.HALF_OPEN:
                m.successes += 1
                if m.successes >= self._config.success_threshold:
                    m.state = CircuitState.CLOSED
                    m.failures = 0
                    m.successes = 0
                    m.last_state_change = time.monotonic()
                    logger.info("Circuit %s → CLOSED (recovered)", marketplace.value)
            elif m.state == CircuitState.CLOSED:
                m.failures = max(0, m.failures - 1)

    async def record_failure(self, marketplace: Marketplace) -> None:
        async with self._lock:
            m = self._get(marketplace)
            m.failures += 1
            m.last_failure_time = time.monotonic()

            if m.state == CircuitState.HALF_OPEN:
                m.state = CircuitState.OPEN
                m.last_state_change = time.monotonic()
                logger.warning("Circuit %s → OPEN (half-open probe failed)", marketplace.value)

            elif m.state == CircuitState.CLOSED:
                if m.failures >= self._config.failure_threshold:
                    m.state = CircuitState.OPEN
                    m.last_state_change = time.monotonic()
                    logger.warning(
                        "Circuit %s → OPEN after %d failures",
                        marketplace.value,
                        m.failures,
                    )

    def is_open(self, marketplace: Marketplace) -> bool:
        m = self._get(marketplace)
        return m.state == CircuitState.OPEN

    def open_count(self) -> int:
        return sum(1 for m in self._circuits.values() if m.state == CircuitState.OPEN)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            mp.value: {
                "state": m.state.value,
                "failures": m.failures,
                "successes": m.successes,
            }
            for mp, m in self._circuits.items()
        }
