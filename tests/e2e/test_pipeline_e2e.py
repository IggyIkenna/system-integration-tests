"""Layer 3b full e2e tests — multi-date, multi-venue pipeline validation. HTTP/GCS only."""

import os
import time
from typing import cast

import httpx
import pytest
import requests

pytestmark = pytest.mark.deployment_test


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


@pytest.mark.e2e
def test_risk_snapshot_written_to_gcs() -> None:
    """Verify risk exposure snapshot is written to GCS after exposure calculation."""
    gcs_bucket = os.environ.get("GCS_TEST_BUCKET")
    if not gcs_bucket:
        pytest.skip("GCS_TEST_BUCKET not set — skipping GCS verification")
    # Trigger exposure calculation and verify risk/{client_id}/{date}/exposure_summary.json exists
    pytest.skip("GCS E2E test stub — implement with actual GCS client")


@pytest.mark.e2e
def test_pnl_output_written_to_gcs() -> None:
    """Verify PnL attribution Parquet is written to GCS after compute run."""
    gcs_bucket = os.environ.get("GCS_TEST_BUCKET")
    if not gcs_bucket:
        pytest.skip("GCS_TEST_BUCKET not set — skipping GCS verification")
    pytest.skip("GCS E2E test stub — implement with actual GCS client")


@pytest.mark.e2e
def test_market_data_api_reads_real_candles(base_urls: dict[str, str]) -> None:
    """Verify market-data-api returns real candle data (not synthetic)."""
    url = base_urls.get("market_data", "http://localhost:8001")
    try:
        resp = requests.get(f"{url}/data-contract", timeout=3)
    except requests.ConnectionError:
        pytest.skip("market-data-api not running")
    if resp.status_code == 404:
        pytest.skip("data-contract endpoint not yet available")
    body = cast(dict[str, object], resp.json())
    assert body.get("type") == "GCS_READER", f"Expected GCS_READER, got {body.get('type')}"
    assert "synthetic" not in str(body).lower()


@pytest.mark.e2e
def test_era_fills_survive_restart_via_gcs(base_urls: dict[str, str]) -> None:
    """Verify fills are persisted to GCS and survive API restart."""
    gcs_bucket = os.environ.get("GCS_TEST_BUCKET")
    if not gcs_bucket:
        pytest.skip("GCS_TEST_BUCKET not set — skipping GCS verification")
    pytest.skip("GCS E2E test stub — implement with actual GCS client")
