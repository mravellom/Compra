"""
Scalable pipeline worker framework.

Each pipeline stage:
  1. Reads from an input Redis stream (consumer group)
  2. Processes messages in configurable batches
  3. Writes results to an output Redis stream
  4. ACKs processed messages
  5. Routes failures to dead_letters stream
  6. Emits metrics to metrics stream

Usage:
    class NormalizeStage(PipelineStage):
        async def process_batch(self, messages):
            results = []
            for msg_id, data in messages:
                normalized = normalize(data)
                results.append((msg_id, normalized))
            return results

    stage = NormalizeStage(
        input_stream="raw_listings",
        output_stream="normalized",
        group="normalizer_group",
        consumer_name="normalizer-1",
        batch_size=100,
    )
    await stage.run()
"""
import asyncio
import logging
import os
import signal
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import redis.asyncio as redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DLQ_STREAM = "dead_letters"
METRICS_STREAM = "metrics"
MAX_RETRIES = 3
CLAIM_IDLE_MS = 60_000  # Reclaim messages pending > 60s


@dataclass
class StageMetrics:
    """Accumulated metrics for a pipeline stage."""
    processed: int = 0
    failed: int = 0
    total_duration_ms: float = 0
    last_batch_size: int = 0
    last_batch_duration_ms: float = 0
    consumer_lag: int = 0


class PipelineStage(ABC):
    """
    Base class for a pipeline processing stage.

    Subclasses implement process_batch() which receives a list of
    (message_id, data_dict) tuples and returns a list of
    (message_id, result_dict) tuples for successfully processed messages.

    Failed messages are automatically retried up to MAX_RETRIES times,
    then routed to the dead letter queue.
    """

    def __init__(
        self,
        input_stream: str,
        output_stream: str | None,
        group: str,
        consumer_name: str | None = None,
        batch_size: int = 50,
        block_ms: int = 2000,
        redis_url: str | None = None,
    ):
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.group = group
        self.consumer_name = consumer_name or f"{group}-{os.getpid()}"
        self.batch_size = batch_size
        self.block_ms = block_ms
        self._redis_url = redis_url or REDIS_URL
        self._redis: redis.Redis | None = None
        self._stop = asyncio.Event()
        self._metrics = StageMetrics()

    @abstractmethod
    async def process_batch(
        self, messages: list[tuple[str, dict[str, str]]]
    ) -> list[tuple[str, dict[str, str]]]:
        """
        Process a batch of messages.

        Args:
            messages: List of (message_id, data_dict) tuples.

        Returns:
            List of (message_id, result_dict) for successful processing.
            Omit message_ids that failed — they will be retried.
        """
        ...

    async def setup(self) -> None:
        """Optional setup hook. Called once before processing starts."""
        pass

    async def teardown(self) -> None:
        """Optional teardown hook. Called on graceful shutdown."""
        pass

    async def run(self) -> None:
        """Main loop: read → process → write → ack → repeat."""
        self._redis = redis.from_url(self._redis_url, decode_responses=True)

        try:
            await self._redis.ping()
        except redis.ConnectionError:
            logger.error("Cannot connect to Redis at %s", self._redis_url)
            return

        # Ensure consumer group exists
        try:
            await self._redis.xgroup_create(
                self.input_stream, self.group, id="0", mkstream=True
            )
            logger.info("Created consumer group '%s' on '%s'", self.group, self.input_stream)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        # Graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop.set)

        await self.setup()
        logger.info(
            "Pipeline stage '%s' started (input=%s, output=%s, batch=%d)",
            self.consumer_name, self.input_stream,
            self.output_stream or "none", self.batch_size,
        )

        try:
            # Phase 1: Reclaim any pending messages from crashed workers
            await self._reclaim_pending()

            # Phase 2: Process new messages
            while not self._stop.is_set():
                await self._process_cycle()
        finally:
            await self.teardown()
            await self._redis.aclose()
            logger.info(
                "Stage '%s' shutdown. Processed=%d Failed=%d",
                self.consumer_name, self._metrics.processed, self._metrics.failed,
            )

    async def _process_cycle(self) -> None:
        """Single read-process-write-ack cycle."""
        # Read batch from stream
        try:
            results = await self._redis.xreadgroup(
                groupname=self.group,
                consumername=self.consumer_name,
                streams={self.input_stream: ">"},
                count=self.batch_size,
                block=self.block_ms,
            )
        except redis.ConnectionError:
            logger.warning("Redis connection lost, reconnecting in 5s")
            await asyncio.sleep(5)
            return

        if not results:
            return

        messages = []
        for _stream, entries in results:
            for msg_id, data in entries:
                messages.append((msg_id, data))

        if not messages:
            return

        # Process batch
        start = time.monotonic()
        try:
            processed = await self.process_batch(messages)
        except Exception:
            logger.error(
                "Batch processing failed for %d messages", len(messages),
                exc_info=True,
            )
            # Send all to DLQ
            for msg_id, data in messages:
                await self._send_to_dlq(msg_id, data, "batch_exception")
            processed = []

        duration_ms = (time.monotonic() - start) * 1000

        # Write results to output stream
        processed_ids = set()
        if self.output_stream and processed:
            for msg_id, result_data in processed:
                try:
                    await self._redis.xadd(self.output_stream, result_data)
                    processed_ids.add(msg_id)
                except Exception:
                    logger.error("Failed to write to %s", self.output_stream, exc_info=True)
        elif processed:
            # No output stream — just track processed IDs
            processed_ids = {msg_id for msg_id, _ in processed}

        # ACK processed messages
        if processed_ids:
            await self._redis.xack(
                self.input_stream, self.group, *processed_ids
            )

        # Handle unprocessed messages (not in processed_ids)
        all_ids = {msg_id for msg_id, _ in messages}
        failed_ids = all_ids - processed_ids
        if failed_ids:
            for msg_id, data in messages:
                if msg_id in failed_ids:
                    await self._handle_failure(msg_id, data)

        # Update metrics
        self._metrics.processed += len(processed_ids)
        self._metrics.failed += len(failed_ids)
        self._metrics.last_batch_size = len(messages)
        self._metrics.last_batch_duration_ms = duration_ms
        self._metrics.total_duration_ms += duration_ms

        if len(messages) > 0:
            logger.info(
                "[%s] Batch: %d/%d processed in %.0fms (total: %d)",
                self.consumer_name, len(processed_ids), len(messages),
                duration_ms, self._metrics.processed,
            )

        # Emit metrics periodically
        if self._metrics.processed % 500 == 0 and self._metrics.processed > 0:
            await self._emit_metrics()

    async def _reclaim_pending(self) -> None:
        """Reclaim messages that were pending from crashed workers."""
        try:
            claimed = await self._redis.xautoclaim(
                self.input_stream, self.group, self.consumer_name,
                min_idle_time=CLAIM_IDLE_MS, start_id="0-0", count=self.batch_size,
            )
            if claimed and len(claimed) > 1 and claimed[1]:
                count = len(claimed[1])
                logger.info(
                    "[%s] Reclaimed %d pending messages", self.consumer_name, count,
                )
        except Exception:
            logger.debug("No pending messages to reclaim", exc_info=True)

    async def _handle_failure(self, msg_id: str, data: dict) -> None:
        """Check retry count and either re-queue or send to DLQ."""
        retry_count = int(data.get("_retry_count", "0"))
        if retry_count < MAX_RETRIES:
            # Re-add with incremented retry count
            data["_retry_count"] = str(retry_count + 1)
            await self._redis.xadd(self.input_stream, data)
            await self._redis.xack(self.input_stream, self.group, msg_id)
            logger.debug("Retry %d/%d for %s", retry_count + 1, MAX_RETRIES, msg_id)
        else:
            await self._send_to_dlq(msg_id, data, "max_retries_exceeded")

    async def _send_to_dlq(self, msg_id: str, data: dict, error: str) -> None:
        """Send a failed message to the dead letter queue."""
        try:
            await self._redis.xadd(DLQ_STREAM, {
                "original_stream": self.input_stream,
                "original_id": msg_id,
                "error": error,
                "data_preview": str(data)[:500],
                "timestamp": str(time.time()),
            })
            await self._redis.xack(self.input_stream, self.group, msg_id)
            self._metrics.failed += 1
            logger.warning("Message %s sent to DLQ: %s", msg_id, error)
        except Exception:
            logger.error("Failed to send to DLQ", exc_info=True)

    async def _emit_metrics(self) -> None:
        """Publish stage metrics to the metrics stream."""
        try:
            # Get consumer lag
            info = await self._redis.xinfo_groups(self.input_stream)
            for g in info:
                if g["name"] == self.group:
                    self._metrics.consumer_lag = g.get("lag", 0)
                    break

            await self._redis.xadd(METRICS_STREAM, {
                "stage": self.consumer_name,
                "processed": str(self._metrics.processed),
                "failed": str(self._metrics.failed),
                "consumer_lag": str(self._metrics.consumer_lag),
                "avg_batch_ms": str(
                    round(self._metrics.total_duration_ms / max(1, self._metrics.processed) * self.batch_size, 1)
                ),
                "timestamp": str(time.time()),
            })
        except Exception:
            logger.debug("Failed to emit metrics", exc_info=True)

    @property
    def metrics(self) -> StageMetrics:
        return self._metrics
