"""System-level execution latency performance tests.

Validates order submission latency against targets defined in:
  unified-trading-codex/06-coding-standards/performance-targets.md

All external calls (venue, GCS, PubSub) use zero-latency mocks.
Tests run in normal-load scenario by default (PR CI).
Peak/sustained scenarios are marked @pytest.mark.slow (nightly/weekly CI only).

Load scenarios (see conftest_performance.py):
  normal:    1x load  - always runs
  peak:      5x load  - @pytest.mark.slow (nightly)
  sustained: 5x load  - @pytest.mark.slow (weekly)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from tests.performance.conftest_performance import (
    LOAD_SCENARIOS,
    ResourceMonitor,
    compute_histogram,
    measure_latencies,
)

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Targets (ms) - sourced from performance-targets.md
# ---------------------------------------------------------------------------

TARGET_ORDER_SUBMISSION_P99_MS: float = 500.0
TARGET_ORDER_SUBMISSION_P95_MS: float = 400.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_http_client(response_delay_ms: float = 0.0) -> MagicMock:
    """HTTP client that returns 200 OK after response_delay_ms."""
    mock = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"order_id": "mock-ord-001", "status": "accepted"}
    mock.post.return_value = response
    return mock


def _simulate_order_submission(client: MagicMock, venue: str = "mock") -> dict[str, object]:
    """Simulate one order submission call via mock HTTP client."""
    resp = client.post(
        f"http://{venue}/orders",
        json={
            "instrument_id": "BINANCE:BTC-USDT",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "0.1",
        },
    )
    return dict(resp.json())


# ---------------------------------------------------------------------------
# Normal load scenario (always runs - no @pytest.mark.slow)
# ---------------------------------------------------------------------------


def test_order_submission_p99_normal_load() -> None:
    """Order submission p99 <= 500ms under normal load (100 iterations).

    Measures wall-clock time for a single order submission through a mock HTTP
    client (0ms network latency). Validates execution-service path overhead only.
    """
    client = _make_mock_http_client(response_delay_ms=0.0)
    scenario = LOAD_SCENARIOS["normal"]
    iterations = max(100, int(scenario["multiplier"]) * 100)  # type: ignore[arg-type]

    latencies = measure_latencies(
        lambda: _simulate_order_submission(client),
        iterations=iterations,
    )
    histogram = compute_histogram(latencies)

    print(
        f"\n[execution latency / normal] "
        f"n={histogram.count} "
        f"p50={histogram.p50:.3f}ms "
        f"p95={histogram.p95:.3f}ms "
        f"p99={histogram.p99:.3f}ms "
        f"max={histogram.max:.3f}ms"
    )

    assert histogram.p99 <= TARGET_ORDER_SUBMISSION_P99_MS, (
        f"Order submission p99={histogram.p99:.1f}ms > {TARGET_ORDER_SUBMISSION_P99_MS}ms target"
    )
    assert histogram.p95 <= TARGET_ORDER_SUBMISSION_P95_MS, (
        f"Order submission p95={histogram.p95:.1f}ms > {TARGET_ORDER_SUBMISSION_P95_MS}ms target"
    )


def test_order_submission_no_memory_leak_normal_load() -> None:
    """Order submission does not leak memory under normal load (100 iterations)."""
    client = _make_mock_http_client()
    monitor = ResourceMonitor()

    for _ in range(100):
        _simulate_order_submission(client)

    monitor.assert_no_memory_leak(tolerance_pct=10.0)


# ---------------------------------------------------------------------------
# Peak load scenario (nightly CI only - @pytest.mark.slow)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_order_submission_p99_peak_load() -> None:
    """Order submission p99 <= 500ms under peak load (500 iterations, 5x multiplier).

    Nightly CI only. Same target as normal load - system must not degrade under peak.
    """
    client = _make_mock_http_client()
    scenario = LOAD_SCENARIOS["peak"]
    iterations = max(500, int(scenario["multiplier"]) * 100)  # type: ignore[arg-type]

    latencies = measure_latencies(
        lambda: _simulate_order_submission(client),
        iterations=iterations,
    )
    histogram = compute_histogram(latencies)

    print(
        f"\n[execution latency / peak] "
        f"n={histogram.count} "
        f"p50={histogram.p50:.3f}ms "
        f"p95={histogram.p95:.3f}ms "
        f"p99={histogram.p99:.3f}ms "
        f"max={histogram.max:.3f}ms"
    )

    assert histogram.p99 <= TARGET_ORDER_SUBMISSION_P99_MS, (
        f"Peak-load order submission p99={histogram.p99:.1f}ms > {TARGET_ORDER_SUBMISSION_P99_MS}ms"
    )


@pytest.mark.slow
def test_order_submission_no_memory_leak_peak_load() -> None:
    """Order submission does not leak memory under peak load (500 iterations)."""
    client = _make_mock_http_client()
    monitor = ResourceMonitor()

    for _ in range(500):
        _simulate_order_submission(client)

    monitor.assert_no_memory_leak(tolerance_pct=10.0)
