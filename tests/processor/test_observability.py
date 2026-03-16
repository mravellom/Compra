"""
Unit Tests — Observability (Metrics & Health Monitor).

Tests:
  - MetricsCollector counters, gauges, histograms
  - Prometheus text export
  - Snapshot format
"""
import pytest

from observability.metrics import MetricsCollector, HistogramBucket


class TestHistogramBucket:

    def test_observe_and_count(self):
        h = HistogramBucket()
        h.observe(1.0)
        h.observe(2.0)
        h.observe(3.0)
        assert h.count == 3

    def test_summary_stats(self):
        h = HistogramBucket()
        for i in range(1, 101):
            h.observe(float(i))
        s = h.summary
        assert s["min"] == 1.0
        assert s["max"] == 100.0
        assert 49 < s["avg"] < 51
        assert s["p50"] is not None
        assert s["p95"] is not None
        assert s["p99"] is not None

    def test_empty_summary(self):
        h = HistogramBucket()
        s = h.summary
        assert s["count"] == 0


class TestMetricsCollector:

    def test_counter_increment(self):
        m = MetricsCollector()
        m.inc("requests_total")
        m.inc("requests_total")
        m.inc("requests_total", 5.0)
        assert m.get_counter("requests_total") == 7.0

    def test_gauge_set(self):
        m = MetricsCollector()
        m.set("active_connections", 42.0)
        assert m.get_gauge("active_connections") == 42.0
        m.set("active_connections", 10.0)
        assert m.get_gauge("active_connections") == 10.0

    def test_histogram_observe(self):
        m = MetricsCollector()
        m.observe("request_duration_ms", 15.0)
        m.observe("request_duration_ms", 25.0)
        snap = m.snapshot()
        assert "histograms" in snap
        assert "request_duration_ms" in snap["histograms"]

    def test_snapshot_structure(self):
        m = MetricsCollector()
        m.inc("c1")
        m.set("g1", 5.0)
        m.observe("h1", 10.0)
        snap = m.snapshot()
        assert "counters" in snap
        assert "gauges" in snap
        assert "histograms" in snap

    def test_prometheus_text_format(self):
        m = MetricsCollector()
        m.inc("http_requests_total", 10.0)
        m.set("active_workers", 3.0)
        text = m.prometheus_text()
        assert "http_requests_total 10" in text
        assert "active_workers 3" in text

    def test_missing_counter_returns_zero(self):
        m = MetricsCollector()
        assert m.get_counter("nonexistent") == 0.0

    def test_missing_gauge_returns_zero(self):
        m = MetricsCollector()
        assert m.get_gauge("nonexistent") == 0.0
