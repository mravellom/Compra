"""Metrics Collector — logging-based implementation of MetricsPort.

For production, swap with Prometheus/StatsD/Datadog adapter.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from scraper_v2.domain.ports import MetricsPort

logger = logging.getLogger(__name__)


class LoggingMetrics(MetricsPort):
    """Simple in-memory metrics with periodic log output."""

    def __init__(self, log_interval: float = 10.0) -> None:
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._timings: dict[str, list[float]] = defaultdict(list)
        self._last_log = time.monotonic()
        self._log_interval = log_interval

    def increment(
        self, metric: str, value: float = 1.0, tags: dict[str, str] | None = None
    ) -> None:
        key = self._key(metric, tags)
        self._counters[key] += value
        self._maybe_log()

    def gauge(
        self, metric: str, value: float, tags: dict[str, str] | None = None
    ) -> None:
        key = self._key(metric, tags)
        self._gauges[key] = value

    def timing(
        self, metric: str, ms: float, tags: dict[str, str] | None = None
    ) -> None:
        key = self._key(metric, tags)
        self._timings[key].append(ms)
        # Keep last 1000 timings
        if len(self._timings[key]) > 1000:
            self._timings[key] = self._timings[key][-500:]

    def _key(self, metric: str, tags: dict[str, str] | None) -> str:
        if not tags:
            return metric
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{metric}[{tag_str}]"

    def _maybe_log(self) -> None:
        now = time.monotonic()
        if now - self._last_log < self._log_interval:
            return
        self._last_log = now

        parts: list[str] = []
        for k, v in sorted(self._counters.items()):
            parts.append(f"{k}={v:.0f}")
        for k, v in sorted(self._gauges.items()):
            parts.append(f"{k}={v:.1f}")
        for k, vals in sorted(self._timings.items()):
            if vals:
                avg = sum(vals) / len(vals)
                parts.append(f"{k}_avg={avg:.1f}ms")

        if parts:
            logger.info("METRICS | %s", " | ".join(parts))

    def snapshot(self) -> dict[str, object]:
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "timing_avgs": {
                k: sum(v) / len(v) if v else 0 for k, v in self._timings.items()
            },
        }
