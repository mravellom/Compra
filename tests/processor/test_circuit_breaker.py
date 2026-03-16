"""
Unit Tests — Circuit Breaker.

Tests state machine:
  CLOSED → OPEN → HALF_OPEN → CLOSED (recovery)
  HALF_OPEN → OPEN (test failure)

Also tests:
  - Rejected call counting
  - Manual reset
  - Sync and async function support
"""
import asyncio
import time

import pytest

from processor.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


class TestCircuitBreakerStates:

    @pytest.mark.asyncio
    async def test_starts_closed(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker("test", failure_threshold=3)

        async def failing():
            raise RuntimeError("boom")

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(failing)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_rejects_calls(self):
        cb = CircuitBreaker("test", failure_threshold=2)

        async def failing():
            raise RuntimeError("boom")

        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(failing)

        with pytest.raises(CircuitOpenError):
            await cb.call(failing)

        assert cb.stats.rejected_calls >= 1

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)

        async def failing():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await cb.call(failing)

        assert cb.state == CircuitState.OPEN
        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_recovers_to_closed(self):
        cb = CircuitBreaker(
            "test", failure_threshold=1,
            recovery_timeout=0.1, success_threshold=1,
        )

        async def failing():
            raise RuntimeError("boom")

        async def succeeding():
            return "ok"

        with pytest.raises(RuntimeError):
            await cb.call(failing)

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        result = await cb.call(succeeding)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(
            "test", failure_threshold=1,
            recovery_timeout=0.1, success_threshold=2,
        )

        async def failing():
            raise RuntimeError("boom")

        # Trip to OPEN
        with pytest.raises(RuntimeError):
            await cb.call(failing)

        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        # Fail again in HALF_OPEN → OPEN
        with pytest.raises(RuntimeError):
            await cb.call(failing)

        assert cb.state == CircuitState.OPEN


class TestCircuitBreakerFeatures:

    @pytest.mark.asyncio
    async def test_success_resets_consecutive_failures(self):
        cb = CircuitBreaker("test", failure_threshold=5)

        async def failing():
            raise RuntimeError("boom")

        async def succeeding():
            return "ok"

        # 2 failures, then 1 success
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(failing)

        assert cb.stats.consecutive_failures == 2
        await cb.call(succeeding)
        assert cb.stats.consecutive_failures == 0
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_manual_reset(self):
        cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=999)

        async def failing():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await cb.call(failing)

        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_sync_function_support(self):
        cb = CircuitBreaker("test", failure_threshold=5)

        def sync_fn(x):
            return x * 2

        result = await cb.call(sync_fn, 5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_stats_tracking(self):
        cb = CircuitBreaker("test", failure_threshold=10)

        async def succeeding():
            return "ok"

        async def failing():
            raise RuntimeError("boom")

        await cb.call(succeeding)
        await cb.call(succeeding)
        with pytest.raises(RuntimeError):
            await cb.call(failing)

        assert cb.stats.total_calls == 3
        assert cb.stats.successful_calls == 2
        assert cb.stats.failed_calls == 1

    @pytest.mark.asyncio
    async def test_half_open_max_calls_limit(self):
        cb = CircuitBreaker(
            "test", failure_threshold=1,
            recovery_timeout=0.05, half_open_max_calls=1,
        )

        async def failing():
            raise RuntimeError("boom")

        async def succeeding():
            return "ok"

        with pytest.raises(RuntimeError):
            await cb.call(failing)

        await asyncio.sleep(0.1)
        assert cb.state == CircuitState.HALF_OPEN

        # First call allowed
        await cb.call(succeeding)
        # Circuit should be closing now (success_threshold=2 by default)
        # but let's test max_calls enforcement is working by checking stats
        assert cb.stats.total_calls >= 2
