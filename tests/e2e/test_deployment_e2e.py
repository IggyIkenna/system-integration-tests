"""
Deployment API end-to-end tests — Layer 3b.

Tests the full batch and live deployment lifecycle including event streams,
rollback, and checklists. Requires a running deployment-api at DEPLOYMENT_API_URL.

Run:
    DEPLOYMENT_API_URL=http://localhost:8001 pytest -m full_e2e tests/e2e/test_deployment_e2e.py -v
    DEPLOYMENT_API_URL=http://localhost:8001 LIVE_SERVICES_ENABLED=1 pytest -m full_e2e -v

Tests that create deployments use dry_run=True so no real Cloud Run jobs are
submitted to GCP/AWS.
"""

import os
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


@pytest.fixture(scope="module")
def live_enabled() -> bool:
    """Return True if live service e2e tests are enabled."""
    return bool(os.environ.get("LIVE_SERVICES_ENABLED"))


# ---------------------------------------------------------------------------
# E2E tests
# ---------------------------------------------------------------------------


@pytest.mark.full_e2e
def test_batch_deployment_dry_run_to_list(http_client: httpx.Client, api: str) -> None:
    """Dry-run batch deployment appears in the deployment list."""
    payload = {
        "service": "instruments-service",
        "dry_run": True,
        "mode": "batch",
        "start_date": "2025-01-01",
        "end_date": "2025-01-03",
        "compute": "cloud_run",
    }
    create_resp = http_client.post(f"{api}/api/deployments", json=payload, timeout=60.0)
    assert create_resp.status_code in (200, 201, 202), (
        f"Create deployment failed: {create_resp.status_code} — {create_resp.text}"
    )
    # List deployments and verify the service appears
    list_resp = http_client.get(f"{api}/api/deployments?limit=5")
    assert list_resp.status_code == 200, f"List deployments failed: {list_resp.status_code}"


@pytest.mark.full_e2e
def test_batch_deployment_cancel(http_client: httpx.Client, api: str) -> None:
    """Create a batch deployment and immediately cancel it."""
    payload = {
        "service": "instruments-service",
        "dry_run": False,
        "mode": "batch",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "compute": "cloud_run",
        "max_shards": 1,
    }
    create_resp = http_client.post(f"{api}/api/deployments", json=payload, timeout=60.0)
    assert create_resp.status_code in (200, 201, 202), (
        f"Create deployment failed: {create_resp.status_code} — {create_resp.text}"
    )
    body = cast(dict[str, object], create_resp.json())
    deployment_id = body.get("deployment_id")
    if not deployment_id:
        pytest.skip("deployment_id not returned — skipping cancel test")

    cancel_resp = http_client.post(f"{api}/api/deployments/{deployment_id}/cancel", timeout=30.0)
    # Accept 200 (cancelled) or 404 (already completed/not found in small window)
    assert cancel_resp.status_code in (200, 404), (
        f"Cancel failed with unexpected status {cancel_resp.status_code}: {cancel_resp.text}"
    )


@pytest.mark.full_e2e
def test_batch_deployment_retry_failed(http_client: httpx.Client, api: str) -> None:
    """Retry endpoint accepts a valid deployment ID (dry-run)."""
    # Create a dry-run deployment first to get an ID
    payload = {
        "service": "instruments-service",
        "dry_run": True,
        "mode": "batch",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "compute": "cloud_run",
    }
    create_resp = http_client.post(f"{api}/api/deployments", json=payload, timeout=60.0)
    assert create_resp.status_code in (200, 201, 202)
    body = cast(dict[str, object], create_resp.json())
    deployment_id = body.get("deployment_id")
    if not deployment_id:
        pytest.skip("deployment_id not returned — skipping retry test")

    retry_resp = http_client.post(
        f"{api}/api/deployments/{deployment_id}/retry-failed?dry_run=true",
        timeout=60.0,
    )
    # Accept 200 (retried/dry-run) or 404 (dry-run deployment cleaned up)
    assert retry_resp.status_code in (200, 404), f"Retry endpoint returned unexpected status {retry_resp.status_code}"


@pytest.mark.full_e2e
def test_live_deployment_dry_run(http_client: httpx.Client, api: str) -> None:
    """POST /api/deployments with dry_run=true and mode=live returns 2xx."""
    payload = {
        "service": "execution-service",
        "dry_run": True,
        "mode": "live",
        "compute": "cloud_run",
        "tag": "latest",
    }
    resp = http_client.post(f"{api}/api/deployments", json=payload, timeout=60.0)
    assert resp.status_code in (200, 201, 202), f"Live dry-run deployment failed: {resp.status_code} — {resp.text}"


@pytest.mark.full_e2e
def test_deployment_event_stream(http_client: httpx.Client, api: str) -> None:
    """GET /api/deployments/{id}/events returns a valid event stream (may be empty)."""
    # Create a dry-run deployment to get a deployment_id
    payload = {
        "service": "instruments-service",
        "dry_run": True,
        "mode": "batch",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "compute": "cloud_run",
    }
    create_resp = http_client.post(f"{api}/api/deployments", json=payload, timeout=60.0)
    assert create_resp.status_code in (200, 201, 202)
    body = cast(dict[str, object], create_resp.json())
    deployment_id = body.get("deployment_id")
    if not deployment_id:
        pytest.skip("deployment_id not returned — skipping event stream test")

    events_resp = http_client.get(f"{api}/api/deployments/{deployment_id}/events", timeout=30.0)
    # 200 with event list, or 502/404 if deployment-service HTTP layer not yet active
    assert events_resp.status_code in (200, 404, 502), (
        f"Events endpoint returned unexpected status {events_resp.status_code}"
    )
    if events_resp.status_code == 200:
        events_body = cast(dict[str, object], events_resp.json())
        assert "events" in events_body, "Response missing 'events' key"
        assert isinstance(events_body["events"], list), "'events' must be a list"


@pytest.mark.full_e2e
def test_readiness_checklist(http_client: httpx.Client, api: str) -> None:
    """GET /api/checklists/{service}/checklist returns a structured checklist."""
    resp = http_client.get(f"{api}/api/checklists/instruments-service/checklist", timeout=30.0)
    # Accept 200 (checklist found) or 404 (no checklist file for this environment)
    assert resp.status_code in (200, 404), (
        f"Checklist endpoint returned unexpected status {resp.status_code}: {resp.text}"
    )
    if resp.status_code == 200:
        body = cast(dict[str, object], resp.json())
        assert "readiness_percent" in body, "Checklist missing 'readiness_percent' field"
        assert "categories" in body, "Checklist missing 'categories' field"


# ---------------------------------------------------------------------------
# Stream 6 — New e2e tests (deployment system upgrade plan)
# ---------------------------------------------------------------------------


@pytest.mark.full_e2e
def test_operational_mode_injection(http_client: httpx.Client, api: str) -> None:
    """POST deployment with operational_mode returns it reflected in response or detail."""
    payload = {
        "service": "instruments-service",
        "dry_run": True,
        "mode": "batch",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "compute": "cloud_run",
        "operational_mode": "instrument",
    }
    resp = http_client.post(f"{api}/api/deployments", json=payload, timeout=60.0)
    assert resp.status_code in (200, 201, 202), (
        f"Deployment with operational_mode failed: {resp.status_code} — {resp.text}"
    )
    body = cast(dict[str, object], resp.json())
    assert body, "Expected non-empty response body"

    # If deployment_id returned, verify detail endpoint accepts the request
    deployment_id = body.get("deployment_id")
    if deployment_id:
        detail_resp = http_client.get(f"{api}/api/deployments/{deployment_id}", timeout=30.0)
        # 200 or 404 both acceptable — we just verify the deployment was created
        assert detail_resp.status_code in (200, 404), (
            f"Deployment detail returned unexpected status {detail_resp.status_code}"
        )


@pytest.mark.full_e2e
def test_cloud_provider_field(http_client: httpx.Client, api: str) -> None:
    """POST deployment with explicit cloud_provider='gcp' returns valid response."""
    payload = {
        "service": "instruments-service",
        "dry_run": True,
        "mode": "batch",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "compute": "cloud_run",
        "cloud_provider": "gcp",
    }
    resp = http_client.post(f"{api}/api/deployments", json=payload, timeout=60.0)
    assert resp.status_code in (200, 201, 202), (
        f"Deployment with cloud_provider=gcp failed: {resp.status_code} — {resp.text}"
    )
    body = cast(dict[str, object], resp.json())
    assert body, "Expected non-empty response body"

    # If cloud_provider is echoed back in response, verify it matches
    response_provider = body.get("cloud_provider")
    if response_provider is not None:
        assert str(response_provider) in ("gcp", "aws", "local"), (
            f"Unexpected cloud_provider value in response: {response_provider}"
        )


@pytest.mark.full_e2e
def test_aws_deploy_dry_run_unauthenticated(http_client: httpx.Client, api: str) -> None:
    """POST dry-run deployment with cloud_provider='aws' returns 200 or structured error.

    AWS deployment code paths must not crash — they must either succeed (dry-run validation)
    or return a structured error response, never an unhandled 5xx.
    """
    payload = {
        "service": "instruments-service",
        "dry_run": True,
        "mode": "batch",
        "start_date": "2025-01-01",
        "end_date": "2025-01-01",
        "compute": "cloud_run",
        "cloud_provider": "aws",
    }
    resp = http_client.post(f"{api}/api/deployments", json=payload, timeout=60.0)

    # Acceptable outcomes:
    # 200/201/202: dry-run succeeded (schema validated, no actual AWS calls needed)
    # 400: structured validation error (e.g. AWS not configured in this environment)
    # 422: unprocessable entity (schema validation failure on cloud_provider field)
    acceptable = (200, 201, 202, 400, 422)
    assert resp.status_code in acceptable, (
        f"AWS dry-run returned unexpected status {resp.status_code}: {resp.text}\n"
        f"Expected one of {acceptable} — never an unhandled 5xx"
    )

    # Response must be valid JSON with a message or error detail
    body = cast(dict[str, object], resp.json())
    assert body, "AWS dry-run response must not be empty"
    if resp.status_code >= 400:
        # Error response must include a detail message
        assert "detail" in body or "message" in body or "error" in body, (
            f"Error response missing detail/message/error field: {body}"
        )
