"""
Metrics Collector — lightweight in-process metrics aggregation.

Collects counters, gauges, and histograms for system observability.
Exposes metrics via /metrics API endpoint (Prometheus-compatible text format).

No external dependencies — pure Python implementation.
For production scale, can be replaced with prometheus_client or statsd.
"""
import logging
import statistics
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class HistogramBucket:
    """Tracks distribution of values."""
    values: list[float] = field(default_factory=list)
    max_size: int = 1000

    def observe(self, value: float) -> None:
        self.values.append(value)
        if len(self.values) > self.max_size:
            self.values = self.values[-self.max_size:]

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def summary(self) -> dict:
        if not self.values:
            return {"count": 0, "min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}
        sorted_vals = sorted(self.values)
        n = len(sorted_vals)
        return {
            "count": n,
            "min": round(sorted_vals[0], 2),
            "max": round(sorted_vals[-1], 2),
            "avg": round(statistics.mean(sorted_vals), 2),
            "p50": round(sorted_vals[int(n * 0.5)], 2),
            "p95": round(sorted_vals[int(n * 0.95)], 2),
            "p99": round(sorted_vals[int(n * 0.99)], 2),
        }


class MetricsCollector:
    """
    Thread-safe metrics collection.

    Usage:
        metrics = get_metrics()
        metrics.inc("scraper.listings_scraped", 60)
        metrics.set("processor.queue_depth", 1500)
        metrics.observe("processor.batch_latency_ms", 42.5)

        # Get all metrics
        snapshot = metrics.snapshot()
    """

    def __init__(self) -> None:
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, HistogramBucket] = defaultdict(HistogramBucket)
        self._lock = threading.Lock()
        self._started_at = time.time()

    def inc(self, name: str, value: float = 1.0) -> None:
        """Increment a counter (monotonically increasing)."""
        with self._lock:
            self._counters[name] += value

    def set(self, name: str, value: float) -> None:
        """Set a gauge (point-in-time value)."""
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        """Record a histogram observation (for latency, sizes, etc.)."""
        with self._lock:
            self._histograms[name].observe(value)

    def get_counter(self, name: str) -> float:
        return self._counters.get(name, 0.0)

    def get_gauge(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    def snapshot(self) -> dict:
        """Return a complete metrics snapshot."""
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    name: bucket.summary
                    for name, bucket in self._histograms.items()
                },
            }

    def prometheus_text(self) -> str:
        """Export metrics in Prometheus text exposition format."""
        lines = []
        with self._lock:
            for name, value in self._counters.items():
                pname = name.replace(".", "_")
                lines.append(f"# TYPE {pname} counter")
                lines.append(f"{pname} {value}")

            for name, value in self._gauges.items():
                pname = name.replace(".", "_")
                lines.append(f"# TYPE {pname} gauge")
                lines.append(f"{pname} {value}")

            for name, bucket in self._histograms.items():
                pname = name.replace(".", "_")
                s = bucket.summary
                lines.append(f"# TYPE {pname} summary")
                lines.append(f'{pname}{{quantile="0.5"}} {s["p50"]}')
                lines.append(f'{pname}{{quantile="0.95"}} {s["p95"]}')
                lines.append(f'{pname}{{quantile="0.99"}} {s["p99"]}')
                lines.append(f"{pname}_count {s['count']}")

        return "\n".join(lines) + "\n"


# ── Singleton ─────────────────────────────────────────────────

_metrics: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics
