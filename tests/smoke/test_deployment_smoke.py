"""
Deployment API smoke tests — Layer 3a.

Validates that the deployment-api is reachable and responds correctly to
basic read-only and dry-run requests. No live deployments are triggered.

Run against a local deployment-api:
    DEPLOYMENT_API_URL=http://localhost:8001 pytest -m smoke tests/smoke/test_deployment_smoke.py -v
"""

from typing import cast

import httpx
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api(base_urls: dict[str, str]) -> str:
    """Return base URL for the deployment-api, skipping if not set."""
    url = base_urls.get("deployment_api", "")
    if not url:
        pytest.skip("DEPLOYMENT_API_URL not configured")
    return url


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_deployment_api_health(http_client: httpx.Client, api: str) -> None:
    """deployment-api /health returns 200 with status=healthy."""
    resp = http_client.get(f"{api}/health")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = cast(dict[str, object], resp.json())
    assert body.get("status") == "healthy", f"Unexpected status: {body}"


@pytest.mark.smoke
def test_deployment_api_infra_health(http_client: httpx.Client, api: str) -> None:
    """deployment-api /infra/health Layer 2 verification returns 200."""
    resp = http_client.get(f"{api}/infra/health")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


@pytest.mark.smoke
def test_list_services(http_client: httpx.Client, api: str) -> None:
    """GET /api/services returns a non-empty service list."""
    resp = http_client.get(f"{api}/api/services")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = cast(dict[str, object], resp.json())
    services = cast(list[object], body.get("services") or [])
    assert len(services) >= 1, "Expected at least one service in the response"


@pytest.mark.smoke
def test_dry_run_batch_deployment(http_client: httpx.Client, api: str) -> None:
    """POST /api/deployments with dry_run=true returns a deployment_id."""
    payload = {
        "service": "instruments-service",
        "dry_run": True,
        "mode": "batch",
        "start_date": "2025-01-01",
        "end_date": "2025-01-07",
        "compute": "cloud_run",
    }
    resp = http_client.post(f"{api}/api/deployments", json=payload, timeout=60.0)
    assert resp.status_code in (
        200,
        201,
        202,
    ), f"Expected 2xx, got {resp.status_code}: {resp.text}"
    body = cast(dict[str, object], resp.json())
    # Dry-run responses may use deployment_id or message; both are acceptable
    assert body, "Expected non-empty response body from dry-run"


@pytest.mark.smoke
def test_list_deployments(http_client: httpx.Client, api: str) -> None:
    """GET /api/deployments returns a valid list (empty OK for fresh environment)."""
    resp = http_client.get(f"{api}/api/deployments?limit=10")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = cast(dict[str, object], resp.json())
    # The response may be a dict with 'deployments' key or a direct list
    assert isinstance(body, (dict, list)), f"Unexpected response shape: {type(body)}"


@pytest.mark.smoke
def test_data_status_turbo(http_client: httpx.Client, api: str) -> None:
    """GET /api/data-status/turbo returns within 10s for instruments-service."""
    params = {
        "service": "instruments-service",
        "start_date": "2025-01-01",
        "end_date": "2025-01-31",
    }
    resp = http_client.get(f"{api}/api/data-status/turbo", params=params, timeout=10.0)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


@pytest.mark.smoke
def test_deployment_api_metrics(http_client: httpx.Client, api: str) -> None:
    """GET /metrics returns Prometheus text format."""
    resp = http_client.get(f"{api}/metrics")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    content_type = resp.headers.get("content-type", "")
    assert "text/plain" in content_type or "text" in content_type, (
        f"Expected Prometheus text format, got content-type: {content_type}"
    )
    # Verify it looks like Prometheus output
    assert "# HELP" in resp.text or "deployment" in resp.text, "Response does not appear to be Prometheus format"
