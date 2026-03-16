"""Retry with exponential backoff and jitter."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryConfig:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)


def _compute_delay(attempt: int, config: RetryConfig) -> float:
    delay = config.base_delay * (config.exponential_base ** attempt)
    delay = min(delay, config.max_delay)
    if config.jitter:
        delay = delay * (0.5 + random.random())
    return delay


async def retry_async(
    fn: Callable[..., Awaitable[T]],
    *args: object,
    config: RetryConfig | None = None,
    operation_name: str = "operation",
    **kwargs: object,
) -> T:
    """Execute async function with retry and exponential backoff."""
    cfg = config or RetryConfig()
    last_exception: Exception | None = None

    for attempt in range(cfg.max_attempts):
        try:
            return await fn(*args, **kwargs)
        except cfg.retryable_exceptions as exc:
            last_exception = exc
            if attempt == cfg.max_attempts - 1:
                break
            delay = _compute_delay(attempt, cfg)
            logger.warning(
                "Retry %s attempt %d/%d after %.1fs: %s",
                operation_name,
                attempt + 1,
                cfg.max_attempts,
                delay,
                str(exc)[:120],
            )
            await asyncio.sleep(delay)

    raise last_exception  # type: ignore[misc]


class RetryPolicy:
    """Reusable retry policy with named configuration."""

    def __init__(self, config: RetryConfig | None = None) -> None:
        self._config = config or RetryConfig()

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: object,
        operation_name: str = "operation",
        **kwargs: object,
    ) -> T:
        return await retry_async(
            fn, *args, config=self._config, operation_name=operation_name, **kwargs
        )
