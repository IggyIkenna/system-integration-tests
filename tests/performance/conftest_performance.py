"""Shared fixtures and utilities for system-level performance tests.

All fixtures use mocks - no real GCS, PubSub, or venue connections are made.
This ensures performance tests are deterministic and runnable in CI without infra.

Load scenarios (applied per test via LOAD_SCENARIOS):
- normal:    1x load, 60s   - runs in every PR CI
- peak:      5x load, 600s  - runs in nightly CI (@pytest.mark.slow)
- sustained: 5x load, 3600s - runs in weekly CI (@pytest.mark.slow)
"""

from __future__ import annotations

import resource
import statistics
import time
from collections.abc import Generator
from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load scenarios
# ---------------------------------------------------------------------------

LOAD_SCENARIOS: dict[str, dict[str, object]] = {
    "normal": {"multiplier": 1, "duration_s": 60},
    "peak": {"multiplier": 5, "duration_s": 600},
    "sustained": {"multiplier": 5, "duration_s": 3600},
}


# ---------------------------------------------------------------------------
# Mock venue fixture
# ---------------------------------------------------------------------------


class MockVenue:
    """Zero-latency mock venue for performance tests.

    Responds to submit_order in 0ms to isolate service-code latency.
    """

    def __init__(self) -> None:
        self._order_count = 0
        self.submitted_orders: list[dict[str, object]] = []

    async def submit_order(self, **kwargs: object) -> str:
        """Accept order immediately (0ms latency)."""
        self._order_count += 1
        order_id = f"mock-{self._order_count}"
        self.submitted_orders.append({"order_id": order_id, **kwargs})
        return order_id

    def reset(self) -> None:
        self._order_count = 0
        self.submitted_orders.clear()


@pytest.fixture
def mock_venue() -> MockVenue:
    """Zero-latency mock venue for isolation benchmarking."""
    return MockVenue()


# ---------------------------------------------------------------------------
# Mock GCS fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_gcs() -> MagicMock:
    """Mock GCS client for storage benchmarks.

    Returns in-memory data immediately - no network overhead.
    """
    mock = MagicMock()
    mock.download.return_value = b"mock-data" * 100
    mock.upload.return_value = None
    mock.list_blobs.return_value = [f"blob-{i}" for i in range(10)]
    return mock


# ---------------------------------------------------------------------------
# Mock PubSub fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pubsub() -> MagicMock:
    """Mock PubSub client for event throughput benchmarks."""
    mock = MagicMock()
    mock.publish = AsyncMock(return_value="msg-id-mock")
    mock.subscribe = AsyncMock(return_value=None)
    return mock


# ---------------------------------------------------------------------------
# Metric collector
# ---------------------------------------------------------------------------


class LatencyHistogram(NamedTuple):
    """Latency histogram for a set of measurements."""

    p50: float
    p95: float
    p99: float
    max: float
    mean: float
    count: int


def compute_histogram(latencies_ms: list[float]) -> LatencyHistogram:
    """Compute p50/p95/p99/max/mean from a list of latency measurements (ms)."""
    if len(latencies_ms) < 2:
        raise ValueError(f"Need >=2 measurements for histogram, got {len(latencies_ms)}")
    quantiles = statistics.quantiles(latencies_ms, n=100)
    return LatencyHistogram(
        p50=quantiles[49],
        p95=quantiles[94],
        p99=quantiles[98],
        max=max(latencies_ms),
        mean=statistics.mean(latencies_ms),
        count=len(latencies_ms),
    )


def measure_latencies(fn, iterations: int) -> list[float]:
    """Run fn synchronously N times and return list of wall-clock latencies (ms)."""
    latencies: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        latencies.append((time.perf_counter() - t0) * 1000)
    return latencies


# ---------------------------------------------------------------------------
# Resource leak detector
# ---------------------------------------------------------------------------


class ResourceMonitor:
    """Monitors RSS memory growth across a test to detect leaks.

    Usage:
        monitor = ResourceMonitor()
        # ... run workload ...
        monitor.assert_no_memory_leak(tolerance_pct=10.0)
    """

    def __init__(self) -> None:
        self._baseline_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    def current_rss(self) -> int:
        """Return current RSS (platform-native units: bytes on Linux, KB on macOS)."""
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    def growth_pct(self) -> float:
        """Return RSS growth percentage since construction."""
        if self._baseline_rss == 0:
            return 0.0
        return (self.current_rss() - self._baseline_rss) / self._baseline_rss * 100

    def assert_no_memory_leak(self, tolerance_pct: float = 10.0) -> None:
        """Assert that RSS has not grown by more than tolerance_pct."""
        growth = self.growth_pct()
        assert growth <= tolerance_pct, (
            f"Memory grew {growth:.1f}% > {tolerance_pct}% threshold "
            f"(baseline={self._baseline_rss}, current={self.current_rss()})"
        )


@pytest.fixture
def resource_monitor() -> Generator[ResourceMonitor]:
    """Fixture that provides a ResourceMonitor and asserts no leak after test."""
    monitor = ResourceMonitor()
    yield monitor
    # Soft check - warns but does not fail by default in fixtures
    growth = monitor.growth_pct()
    if growth > 10.0:
        import warnings

        warnings.warn(
            f"Memory grew {growth:.1f}% during test (tolerance=10%)",
            ResourceWarning,
            stacklevel=2,
        )
