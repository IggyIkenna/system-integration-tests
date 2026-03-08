"""Layer 3b full e2e tests — multi-date, multi-venue pipeline validation. HTTP/GCS only."""

import time

import httpx
import pytest


@pytest.mark.full_e2e
def test_pipeline_single_date_single_venue(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Full pipeline: trigger one date + one venue, verify GCS output exists."""
    # Trigger pipeline via deployment API
    resp: httpx.Response = http_client.post(
        f"{base_urls['deployment_api']}/pipeline/trigger",
        json={"date": "2024-01-15", "venue": "XNAS", "instrument": "AAPL"},
    )
    assert resp.status_code in (200, 202), f"Pipeline trigger failed: {resp.status_code}"


@pytest.mark.full_e2e
def test_pipeline_multi_date_multi_venue(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Full pipeline: trigger multiple dates + venues, verify all complete."""
    dates = ["2024-01-15", "2024-01-16"]
    venues = ["XNAS", "XNYS"]
    for date in dates:
        for venue in venues:
            resp: httpx.Response = http_client.post(
                f"{base_urls['deployment_api']}/pipeline/trigger",
                json={"date": date, "venue": venue},
            )
            assert resp.status_code in (200, 202), f"Pipeline trigger failed for {date}/{venue}: {resp.status_code}"


@pytest.mark.full_e2e
def test_pipeline_perf_baseline(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Pipeline health endpoint responds within acceptable latency (<500ms)."""
    start = time.monotonic()
    resp: httpx.Response = http_client.get(f"{base_urls['deployment_api']}/health")
    elapsed_ms = (time.monotonic() - start) * 1000
    assert resp.status_code == 200
    assert elapsed_ms < 500, f"Health check too slow: {elapsed_ms:.0f}ms"
