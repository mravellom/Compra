"""
Distributed Scraper Task Queue.

Architecture:
  Scheduler produces ScrapeTask → Redis Stream → Worker consumes + executes

  ┌───────────┐     ┌──────────────┐     ┌──────────────────┐
  │ Scheduler │────▶│  Redis Stream │────▶│  Scraper Workers  │
  │ (cron)    │     │  scrape_tasks │     │  (consumer group) │
  └───────────┘     └──────────────┘     └──────────────────┘

Task Types:
  - category_crawl: Crawl a marketplace category
  - keyword_search: Search for specific keywords
  - trending_scan:  Scan trending/bestseller pages
  - product_refresh: Re-scrape known product URLs

Patterns:
  - Producer/Consumer via Redis Streams
  - Queue-Based Load Leveling (workers pull at own pace)
  - Backoff Retry (failed tasks requeued with delay)
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TASK_STREAM = "scrape_tasks"
TASK_GROUP = "scraper_workers"
DEAD_LETTER_STREAM = "scrape_dead_letter"
MAX_TASK_RETRIES = 3


class TaskType(Enum):
    CATEGORY_CRAWL = "category_crawl"
    KEYWORD_SEARCH = "keyword_search"
    TRENDING_SCAN = "trending_scan"
    PRODUCT_REFRESH = "product_refresh"


class TaskPriority(Enum):
    HIGH = 1      # High-yield categories, trending
    NORMAL = 2    # Standard crawl cycle
    LOW = 3       # Backfill, re-scrape old data


@dataclass
class ScrapeTask:
    task_type: str
    marketplace: str
    target: str          # Category name, search term, or URL
    priority: int = 2
    max_pages: int = 7
    retry_count: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_redis(self) -> dict[str, str]:
        return {
            "task_type": self.task_type,
            "marketplace": self.marketplace,
            "target": self.target,
            "priority": str(self.priority),
            "max_pages": str(self.max_pages),
            "retry_count": str(self.retry_count),
            "created_at": str(self.created_at),
            "metadata": json.dumps(self.metadata),
        }

    @classmethod
    def from_redis(cls, data: dict[str, str]) -> "ScrapeTask":
        return cls(
            task_type=data["task_type"],
            marketplace=data["marketplace"],
            target=data["target"],
            priority=int(data.get("priority", "2")),
            max_pages=int(data.get("max_pages", "7")),
            retry_count=int(data.get("retry_count", "0")),
            created_at=float(data.get("created_at", "0")),
            metadata=json.loads(data.get("metadata", "{}")),
        )


class TaskScheduler:
    """
    Produces scrape tasks to the Redis stream.

    Responsibilities:
      - Generate tasks from category/keyword config
      - Prioritize high-yield categories
      - Avoid duplicate tasks within a cycle
    """

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._scheduled_keys: set[str] = set()

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    async def schedule(self, task: ScrapeTask) -> str | None:
        """Add task to the queue. Returns message ID or None if duplicate."""
        key = f"{task.task_type}:{task.marketplace}:{task.target}"
        if key in self._scheduled_keys:
            return None

        r = await self._get_redis()

        # Ensure consumer group exists
        try:
            await r.xgroup_create(TASK_STREAM, TASK_GROUP, id="0", mkstream=True)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        msg_id = await r.xadd(TASK_STREAM, task.to_redis())
        self._scheduled_keys.add(key)
        return msg_id

    async def schedule_batch(self, tasks: list[ScrapeTask]) -> int:
        """Schedule multiple tasks. Returns count of newly scheduled."""
        count = 0
        for task in tasks:
            if await self.schedule(task):
                count += 1
        return count

    def reset_cycle(self) -> None:
        """Clear dedup set for a new scheduling cycle."""
        self._scheduled_keys.clear()

    async def get_queue_depth(self) -> int:
        """Return number of pending tasks in the stream."""
        r = await self._get_redis()
        try:
            info = await r.xinfo_stream(TASK_STREAM)
            return info.get("length", 0)
        except aioredis.ResponseError:
            return 0

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None


class TaskWorker:
    """
    Consumes and executes scrape tasks from the Redis stream.

    Features:
      - Consumer group for distributed processing
      - Automatic retry with backoff
      - Dead letter queue for permanently failed tasks
    """

    def __init__(
        self,
        worker_id: str = "worker-1",
        handler: "Callable[[ScrapeTask], Awaitable[int]] | None" = None,
    ):
        self.worker_id = worker_id
        self.handler = handler
        self._redis: aioredis.Redis | None = None
        self._running = False
        self._tasks_completed = 0
        self._tasks_failed = 0

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        return self._redis

    async def start(self) -> None:
        """Start consuming tasks."""
        self._running = True
        r = await self._get_redis()

        # Ensure group exists
        try:
            await r.xgroup_create(TASK_STREAM, TASK_GROUP, id="0", mkstream=True)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        logger.info("[TaskWorker:%s] Started consuming from %s", self.worker_id, TASK_STREAM)

        while self._running:
            try:
                messages = await r.xreadgroup(
                    TASK_GROUP, self.worker_id,
                    {TASK_STREAM: ">"},
                    count=1, block=5000,
                )

                if not messages:
                    continue

                for stream_name, entries in messages:
                    for msg_id, data in entries:
                        await self._process_message(r, msg_id, data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[TaskWorker:%s] Error: %s", self.worker_id, e)
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def _process_message(
        self, r: aioredis.Redis, msg_id: str, data: dict
    ) -> None:
        task = ScrapeTask.from_redis(data)
        logger.info(
            "[TaskWorker:%s] Processing %s/%s/%s (attempt %d)",
            self.worker_id, task.task_type, task.marketplace, task.target, task.retry_count + 1,
        )

        try:
            if self.handler:
                listings_count = await self.handler(task)
                logger.info(
                    "[TaskWorker:%s] Completed %s/%s → %d listings",
                    self.worker_id, task.marketplace, task.target, listings_count,
                )

            await r.xack(TASK_STREAM, TASK_GROUP, msg_id)
            self._tasks_completed += 1

        except Exception as e:
            logger.error(
                "[TaskWorker:%s] Failed %s/%s: %s",
                self.worker_id, task.marketplace, task.target, e,
            )
            self._tasks_failed += 1

            if task.retry_count < MAX_TASK_RETRIES:
                # Requeue with incremented retry count
                task.retry_count += 1
                task.metadata["last_error"] = str(e)[:200]
                await r.xadd(TASK_STREAM, task.to_redis())
                logger.info("[TaskWorker:%s] Requeued (retry %d)", self.worker_id, task.retry_count)
            else:
                # Move to dead letter queue
                await r.xadd(DEAD_LETTER_STREAM, task.to_redis())
                logger.warning("[TaskWorker:%s] Dead-lettered after %d retries", self.worker_id, MAX_TASK_RETRIES)

            await r.xack(TASK_STREAM, TASK_GROUP, msg_id)

    @property
    def stats(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "completed": self._tasks_completed,
            "failed": self._tasks_failed,
        }
