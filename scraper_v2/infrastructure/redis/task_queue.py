"""Priority Task Queue — in-memory async priority queue for CrawlTasks.

For single-process deployment. For multi-process, swap with Redis-based implementation.
"""

from __future__ import annotations

import asyncio

from scraper_v2.domain.models import CrawlTask
from scraper_v2.domain.ports import TaskQueuePort


class InMemoryTaskQueue(TaskQueuePort):
    """Async priority queue backed by asyncio.PriorityQueue."""

    def __init__(self, maxsize: int = 10_000) -> None:
        self._queue: asyncio.PriorityQueue[tuple[int, float, CrawlTask]] = (
            asyncio.PriorityQueue(maxsize=maxsize)
        )

    async def enqueue(self, task: CrawlTask) -> None:
        await self._queue.put((task.priority.value, task.created_at, task))

    async def dequeue(self, timeout: float = 5.0) -> CrawlTask | None:
        try:
            _, _, task = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return task
        except asyncio.TimeoutError:
            return None

    async def size(self) -> int:
        return self._queue.qsize()

    async def drain(self) -> list[CrawlTask]:
        tasks: list[CrawlTask] = []
        while not self._queue.empty():
            try:
                _, _, task = self._queue.get_nowait()
                tasks.append(task)
            except asyncio.QueueEmpty:
                break
        return tasks
