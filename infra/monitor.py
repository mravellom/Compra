"""
Pipeline monitor — tracks health of all stages.

Reads from the metrics stream and exposes a status summary.
Can be queried by the API health endpoint or run standalone.

Usage:
    # As standalone dashboard:
    python -m infra.monitor

    # Programmatic:
    monitor = PipelineMonitor()
    await monitor.collect_snapshot()
    print(monitor.status)
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

import redis.asyncio as redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Streams to monitor
STREAMS = [
    "scrape_jobs",
    "raw_listings",
    "normalized",
    "resolved",
    "enriched",
    "dead_letters",
]

# Consumer groups to check lag
GROUPS = {
    "raw_listings": "normalizer_group",
    "normalized": "embedder_group",
    "resolved": "enricher_group",
    "scrape_jobs": "scraper_fleet",
}

# Alert thresholds
ALERT_THRESHOLDS = {
    "consumer_lag_warning": 10_000,
    "consumer_lag_critical": 100_000,
    "dlq_warning": 50,
    "dlq_critical": 500,
    "stale_minutes": 10,  # No new messages in this long = stale
}


@dataclass
class StreamHealth:
    name: str
    length: int = 0
    consumer_lag: int = 0
    groups: int = 0
    consumers: int = 0
    last_entry_age_sec: float = 0
    status: str = "ok"  # "ok", "warning", "critical"


@dataclass
class PipelineSnapshot:
    timestamp: float = 0
    streams: dict[str, StreamHealth] = field(default_factory=dict)
    dlq_length: int = 0
    overall_status: str = "ok"
    alerts: list[str] = field(default_factory=list)


class PipelineMonitor:
    """Collects and evaluates pipeline health metrics."""

    def __init__(self, redis_url: str | None = None):
        self._redis_url = redis_url or REDIS_URL
        self._last_snapshot: PipelineSnapshot | None = None

    async def collect_snapshot(self) -> PipelineSnapshot:
        """Collect current state of all pipeline streams."""
        r = redis.from_url(self._redis_url, decode_responses=True)
        snapshot = PipelineSnapshot(timestamp=time.time())

        try:
            for stream_name in STREAMS:
                health = StreamHealth(name=stream_name)

                try:
                    health.length = await r.xlen(stream_name)
                except Exception:
                    health.length = 0
                    health.status = "unknown"
                    snapshot.streams[stream_name] = health
                    continue

                # Check consumer group info
                group_name = GROUPS.get(stream_name)
                if group_name:
                    try:
                        groups = await r.xinfo_groups(stream_name)
                        health.groups = len(groups)
                        for g in groups:
                            if g["name"] == group_name:
                                health.consumer_lag = g.get("lag", g.get("pending", 0))
                                health.consumers = g.get("consumers", 0)
                                break
                    except Exception:
                        pass

                # Check last entry age
                try:
                    info = await r.xinfo_stream(stream_name)
                    last_entry = info.get("last-generated-id", "0-0")
                    if "-" in last_entry:
                        ts_ms = int(last_entry.split("-")[0])
                        health.last_entry_age_sec = (time.time() * 1000 - ts_ms) / 1000
                except Exception:
                    pass

                # Evaluate status
                if health.consumer_lag > ALERT_THRESHOLDS["consumer_lag_critical"]:
                    health.status = "critical"
                    snapshot.alerts.append(
                        f"CRITICAL: {stream_name} consumer lag = {health.consumer_lag}"
                    )
                elif health.consumer_lag > ALERT_THRESHOLDS["consumer_lag_warning"]:
                    health.status = "warning"
                    snapshot.alerts.append(
                        f"WARNING: {stream_name} consumer lag = {health.consumer_lag}"
                    )
                elif health.last_entry_age_sec > ALERT_THRESHOLDS["stale_minutes"] * 60:
                    health.status = "warning"
                    snapshot.alerts.append(
                        f"WARNING: {stream_name} stale ({health.last_entry_age_sec:.0f}s since last message)"
                    )

                snapshot.streams[stream_name] = health

            # DLQ check
            dlq = snapshot.streams.get("dead_letters")
            if dlq:
                snapshot.dlq_length = dlq.length
                if dlq.length > ALERT_THRESHOLDS["dlq_critical"]:
                    snapshot.alerts.append(
                        f"CRITICAL: Dead letter queue has {dlq.length} messages"
                    )
                elif dlq.length > ALERT_THRESHOLDS["dlq_warning"]:
                    snapshot.alerts.append(
                        f"WARNING: Dead letter queue has {dlq.length} messages"
                    )

            # Overall status
            statuses = [s.status for s in snapshot.streams.values()]
            if "critical" in statuses:
                snapshot.overall_status = "critical"
            elif "warning" in statuses:
                snapshot.overall_status = "warning"
            else:
                snapshot.overall_status = "ok"

        finally:
            await r.aclose()

        self._last_snapshot = snapshot
        return snapshot

    @property
    def last_snapshot(self) -> PipelineSnapshot | None:
        return self._last_snapshot

    def format_status(self, snapshot: PipelineSnapshot | None = None) -> str:
        """Format snapshot as human-readable text."""
        s = snapshot or self._last_snapshot
        if not s:
            return "No data collected yet"

        lines = [
            f"Pipeline Status: {s.overall_status.upper()}",
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s.timestamp))}",
            "",
            f"{'Stream':<20} {'Length':>10} {'Lag':>10} {'Consumers':>10} {'Status':>10}",
            "-" * 65,
        ]

        for name, h in s.streams.items():
            lines.append(
                f"{name:<20} {h.length:>10,} {h.consumer_lag:>10,} "
                f"{h.consumers:>10} {h.status:>10}"
            )

        if s.alerts:
            lines.append("")
            lines.append("ALERTS:")
            for alert in s.alerts:
                lines.append(f"  {alert}")

        return "\n".join(lines)


async def main():
    """Run monitor as standalone dashboard, refreshing every 10 seconds."""
    logging.basicConfig(level=logging.INFO)
    monitor = PipelineMonitor()

    while True:
        snapshot = await monitor.collect_snapshot()
        # Clear screen
        print("\033[2J\033[H", end="")
        print(monitor.format_status(snapshot))
        print(f"\n(refreshing every 10s, Ctrl+C to exit)")
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
