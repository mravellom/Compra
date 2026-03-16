"""Domain events — decoupled event signaling."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .enums import BlockType, Marketplace


@dataclass(frozen=True, slots=True)
class DomainEvent:
    timestamp: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class BatchScrapedEvent(DomainEvent):
    marketplace: Marketplace = Marketplace.AMAZON_US
    listings_count: int = 0
    category: str = ""
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class BlockDetectedEvent(DomainEvent):
    marketplace: Marketplace = Marketplace.AMAZON_US
    block_type: BlockType = BlockType.NONE
    proxy_url: str = ""
    url: str = ""


@dataclass(frozen=True, slots=True)
class ProxyRotatedEvent(DomainEvent):
    old_proxy: str = ""
    new_proxy: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CircuitOpenedEvent(DomainEvent):
    marketplace: Marketplace = Marketplace.AMAZON_US
    failure_count: int = 0
    recovery_time: float = 0.0


@dataclass(frozen=True, slots=True)
class WorkerCrashedEvent(DomainEvent):
    worker_id: str = ""
    error: str = ""
    task_id: str = ""


@dataclass(frozen=True, slots=True)
class BackpressureEvent(DomainEvent):
    queue_depth: int = 0
    action: str = "throttle"  # "throttle" | "resume"


@dataclass(frozen=True, slots=True)
class CycleCompletedEvent(DomainEvent):
    total_listings: int = 0
    duration_seconds: float = 0.0
    workers_used: int = 0
    blocks_encountered: int = 0
