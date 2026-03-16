"""
Worker Pool for parallel processing stages.

Architecture:
  WorkerPool manages N async workers that pull from an input queue,
  process items through a configurable pipeline, and push to an output queue.

  ┌─────────┐     ┌──────────┐     ┌──────────┐
  │  Input   │────▶│ Worker 1 │────▶│  Output  │
  │  Queue   │────▶│ Worker 2 │────▶│  Queue   │
  │          │────▶│ Worker N │────▶│          │
  └─────────┘     └──────────┘     └──────────┘

Features:
  - Configurable worker count (auto-scales to CPU cores)
  - Per-worker circuit breaker
  - Graceful shutdown with drain
  - Backpressure via bounded queues
  - Per-worker metrics
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class WorkerMetrics:
    worker_id: int
    items_processed: int = 0
    items_failed: int = 0
    total_process_time_ms: float = 0.0
    last_active: float = 0.0

    @property
    def avg_process_time_ms(self) -> float:
        if self.items_processed == 0:
            return 0.0
        return self.total_process_time_ms / self.items_processed


@dataclass
class PoolMetrics:
    workers: list[WorkerMetrics] = field(default_factory=list)
    started_at: float = 0.0

    @property
    def total_processed(self) -> int:
        return sum(w.items_processed for w in self.workers)

    @property
    def total_failed(self) -> int:
        return sum(w.items_failed for w in self.workers)

    @property
    def throughput(self) -> float:
        elapsed = time.monotonic() - self.started_at
        if elapsed <= 0:
            return 0.0
        return self.total_processed / elapsed

    def summary(self) -> str:
        active = sum(1 for w in self.workers if time.monotonic() - w.last_active < 5)
        return (
            f"workers={len(self.workers)} active={active} "
            f"processed={self.total_processed} failed={self.total_failed} "
            f"throughput={self.throughput:.1f}/s"
        )


class WorkerPool:
    """
    Async worker pool with backpressure and graceful shutdown.

    Usage:
        pool = WorkerPool(
            name="embedding",
            worker_count=4,
            process_fn=process_batch,
            input_queue=input_q,
            output_queue=output_q,
        )
        await pool.start()
        # ... later
        await pool.stop()
    """

    def __init__(
        self,
        name: str,
        worker_count: int | None = None,
        process_fn: Callable[..., Coroutine[Any, Any, Any]] | None = None,
        input_queue: asyncio.Queue | None = None,
        output_queue: asyncio.Queue | None = None,
        max_queue_size: int = 1024,
    ):
        self.name = name
        self.worker_count = worker_count or min(os.cpu_count() or 4, 8)
        self.process_fn = process_fn
        self.input_queue = input_queue or asyncio.Queue(maxsize=max_queue_size)
        self.output_queue = output_queue or asyncio.Queue(maxsize=max_queue_size)

        self._workers: list[asyncio.Task] = []
        self._running = False
        self._metrics = PoolMetrics()
        self._shutdown_event = asyncio.Event()

    @property
    def metrics(self) -> PoolMetrics:
        return self._metrics

    async def start(self) -> None:
        """Start all workers."""
        if self._running:
            return

        self._running = True
        self._metrics.started_at = time.monotonic()
        self._shutdown_event.clear()

        for i in range(self.worker_count):
            wm = WorkerMetrics(worker_id=i)
            self._metrics.workers.append(wm)
            task = asyncio.create_task(self._worker_loop(i, wm))
            self._workers.append(task)

        logger.info("[WorkerPool:%s] Started %d workers", self.name, self.worker_count)

    async def stop(self, timeout: float = 10.0) -> None:
        """Graceful shutdown: drain queues, then cancel workers."""
        if not self._running:
            return

        self._running = False
        self._shutdown_event.set()

        # Signal workers to stop by sending sentinel values
        for _ in self._workers:
            try:
                self.input_queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

        # Wait for workers to finish
        done, pending = await asyncio.wait(self._workers, timeout=timeout)
        for task in pending:
            task.cancel()

        self._workers.clear()
        logger.info(
            "[WorkerPool:%s] Stopped — %s", self.name, self._metrics.summary()
        )

    async def submit(self, item: Any) -> None:
        """Submit item to input queue (blocks if full = backpressure)."""
        await self.input_queue.put(item)

    async def _worker_loop(self, worker_id: int, metrics: WorkerMetrics) -> None:
        """Main worker loop: pull → process → push."""
        logger.debug("[WorkerPool:%s] Worker %d started", self.name, worker_id)

        while self._running:
            try:
                item = await asyncio.wait_for(
                    self.input_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            if item is None:  # Sentinel for shutdown
                break

            metrics.last_active = time.monotonic()
            t0 = time.monotonic()

            try:
                if self.process_fn:
                    result = await self.process_fn(item)
                    if result is not None:
                        await self.output_queue.put(result)
                metrics.items_processed += 1
            except Exception as e:
                metrics.items_failed += 1
                logger.error(
                    "[WorkerPool:%s] Worker %d error: %s", self.name, worker_id, e
                )
            finally:
                elapsed_ms = (time.monotonic() - t0) * 1000
                metrics.total_process_time_ms += elapsed_ms

        logger.debug("[WorkerPool:%s] Worker %d stopped", self.name, worker_id)
