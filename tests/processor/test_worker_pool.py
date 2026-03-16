"""
Unit Tests — Worker Pool.

Tests:
  - Start/stop lifecycle
  - Item processing
  - Failure handling
  - Backpressure
  - Graceful shutdown
  - Metrics
"""
import asyncio

import pytest

from processor.worker_pool import WorkerPool, WorkerMetrics, PoolMetrics


class TestWorkerPoolLifecycle:

    @pytest.mark.asyncio
    async def test_start_creates_workers(self):
        pool = WorkerPool(name="test", worker_count=3)
        await pool.start()
        assert len(pool._workers) == 3
        assert pool._running is True
        await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self):
        pool = WorkerPool(name="test", worker_count=2)
        await pool.start()
        await pool.stop()
        assert pool._running is False
        assert len(pool._workers) == 0

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        pool = WorkerPool(name="test", worker_count=2)
        await pool.start()
        await pool.start()  # Should not create more workers
        assert len(pool._workers) == 2
        await pool.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        pool = WorkerPool(name="test", worker_count=2)
        await pool.stop()  # Should not raise


class TestWorkerPoolProcessing:

    @pytest.mark.asyncio
    async def test_processes_items(self):
        processed = []

        async def handler(item):
            processed.append(item)
            return item * 2

        pool = WorkerPool(name="test", worker_count=2, process_fn=handler)
        await pool.start()

        for i in range(5):
            await pool.submit(i)

        # Give workers time to process
        await asyncio.sleep(0.3)
        await pool.stop()

        assert sorted(processed) == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_output_queue_receives_results(self):
        async def handler(item):
            return item * 10

        pool = WorkerPool(name="test", worker_count=1, process_fn=handler)
        await pool.start()

        await pool.submit(5)
        await asyncio.sleep(0.2)
        await pool.stop()

        result = pool.output_queue.get_nowait()
        assert result == 50

    @pytest.mark.asyncio
    async def test_handles_processing_errors(self):
        call_count = 0

        async def failing_handler(item):
            nonlocal call_count
            call_count += 1
            if item == "bad":
                raise ValueError("bad item")
            return item

        pool = WorkerPool(name="test", worker_count=1, process_fn=failing_handler)
        await pool.start()

        await pool.submit("good")
        await pool.submit("bad")
        await pool.submit("also good")

        await asyncio.sleep(0.3)
        await pool.stop()

        assert call_count == 3
        assert pool.metrics.total_failed >= 1
        assert pool.metrics.total_processed >= 2

    @pytest.mark.asyncio
    async def test_none_result_not_queued(self):
        async def handler(item):
            return None  # Explicitly return None

        pool = WorkerPool(name="test", worker_count=1, process_fn=handler)
        await pool.start()

        await pool.submit("item")
        await asyncio.sleep(0.2)
        await pool.stop()

        assert pool.output_queue.empty()


class TestWorkerMetrics:

    def test_avg_process_time_zero_when_empty(self):
        wm = WorkerMetrics(worker_id=0)
        assert wm.avg_process_time_ms == 0.0

    def test_avg_process_time_calculation(self):
        wm = WorkerMetrics(worker_id=0, items_processed=4, total_process_time_ms=100.0)
        assert wm.avg_process_time_ms == 25.0


class TestPoolMetrics:

    def test_total_processed(self):
        pm = PoolMetrics(
            workers=[
                WorkerMetrics(worker_id=0, items_processed=10),
                WorkerMetrics(worker_id=1, items_processed=15),
            ]
        )
        assert pm.total_processed == 25

    def test_total_failed(self):
        pm = PoolMetrics(
            workers=[
                WorkerMetrics(worker_id=0, items_failed=2),
                WorkerMetrics(worker_id=1, items_failed=3),
            ]
        )
        assert pm.total_failed == 5

    def test_summary_format(self):
        pm = PoolMetrics(workers=[WorkerMetrics(worker_id=0)])
        pm.started_at = 1.0
        s = pm.summary()
        assert "workers=" in s
        assert "processed=" in s
