"""
Monetization & Observability API routes.

Endpoints:
  GET  /monetization/channels      — List registered channels and stats
  GET  /monetization/events        — Recent published events
  GET  /monetization/feed/rss      — RSS feed of opportunities
  GET  /metrics                    — Prometheus-compatible metrics
  GET  /health/detailed            — Detailed component health
"""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from monetization.publisher import OpportunityPublisher
from monetization.channels.rss_channel import RSSChannel
from observability.metrics import get_metrics
from observability.health_monitor import HealthMonitor

router = APIRouter(tags=["monetization"])

# ── Singletons (initialized at app startup) ──────────────────
_publisher: OpportunityPublisher | None = None
_rss_channel: RSSChannel | None = None
_health_monitor: HealthMonitor | None = None


def init_monetization() -> OpportunityPublisher:
    """Initialize the publisher and channels. Called once at app startup."""
    global _publisher, _rss_channel, _health_monitor

    from monetization.channels.rss_channel import RSSChannel
    from monetization.publisher import ChannelFilter

    _publisher = OpportunityPublisher()

    # RSS channel — always active, low bar for inclusion
    _rss_channel = RSSChannel(
        title="CompraVenta Opportunities",
        max_items=200,
        filter_rules=ChannelFilter(min_opportunity_score=20),
    )
    _publisher.register_channel(_rss_channel)

    # Health monitor
    _health_monitor = HealthMonitor()

    return _publisher


def get_publisher() -> OpportunityPublisher | None:
    return _publisher


def get_health_monitor() -> HealthMonitor | None:
    return _health_monitor


# ── Routes ────────────────────────────────────────────────────

@router.get("/monetization/channels")
async def list_channels():
    if not _publisher:
        return {"channels": [], "message": "Publisher not initialized"}
    return {"channels": _publisher.channels}


@router.get("/monetization/events")
async def recent_events():
    if not _publisher:
        return {"events": []}
    return {"events": _publisher.recent_events}


@router.get("/monetization/feed/rss")
async def rss_feed():
    if not _rss_channel:
        return Response(
            content="<rss><channel><title>Not initialized</title></channel></rss>",
            media_type="application/rss+xml",
        )
    return Response(
        content=_rss_channel.generate_xml(),
        media_type="application/rss+xml",
    )


@router.get("/metrics")
async def metrics_endpoint():
    """Prometheus-compatible metrics endpoint."""
    collector = get_metrics()
    return PlainTextResponse(
        content=collector.prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/metrics/json")
async def metrics_json():
    """JSON metrics snapshot."""
    return get_metrics().snapshot()


@router.get("/health/detailed")
async def detailed_health():
    """Detailed component-level health check."""
    if not _health_monitor:
        return {"status": "unknown", "message": "Health monitor not initialized"}
    return _health_monitor.current_health.to_dict()
