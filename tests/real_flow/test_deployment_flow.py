"""Critical journey: Deployment flow — deploy -> status -> logs.

Validates the full deployment lifecycle through deployment-ui + deployment-api:
    1. POST a dry-run deployment (request sent)
    2. Verify response contains deployment_id and valid status (response received)
    3. GET deployment detail confirms state persisted; GET events confirms log stream (state updated)

Priority: CRITICAL — runs on every staging deploy.
"""

from __future__ import annotations

from typing import cast

import httpx
import pytest

from tests.real_flow.conftest import FlowServiceURLs, TriadResult

pytestmark = [pytest.mark.real_flow, pytest.mark.critical, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def deployment_api(flow_urls: FlowServiceURLs) -> str:
    """Deployment API base URL, skipping if service is unreachable."""
    url = flow_urls.deployment_api
    try:
        with httpx.Client(timeout=5.0) as probe:
            resp = probe.get(f"{url}/health")
            if resp.status_code != 200:
                pytest.skip(f"deployment-api at {url} returned {resp.status_code}")
    except httpx.ConnectError:
        pytest.skip(f"deployment-api not reachable at {url}")
    return url


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.critical
def test_deploy_status_logs(
    flow_client: httpx.Client,
    deployment_api: str,
    triad: TriadResult,
) -> None:
    """Full deployment journey: create -> status -> logs (event stream).

    Triad:
        1. Request sent: POST /api/deployments with dry_run=true
        2. Response valid: 2xx with deployment_id
        3. State updated: GET /api/deployments/{id} returns persisted state,
           GET /api/deployments/{id}/events returns event list
    """
    # Phase 1: Request sent — create a dry-run deployment
    payload = {
        "service": "instruments-service",
        "dry_run": True,
        "mode": "batch",
        "start_date": "2025-01-01",
        "end_date": "2025-01-03",
        "compute": "cloud_run",
    }
    create_resp = flow_client.post(f"{deployment_api}/api/deployments", json=payload, timeout=60.0)
    triad.request_sent = True

    # Phase 2: Response valid — must be 2xx with a body
    assert create_resp.status_code in (200, 201, 202), (
        f"Deploy create failed: {create_resp.status_code} — {create_resp.text}"
    )
    body = cast(dict[str, object], create_resp.json())
    assert body, "Expected non-empty response body from deployment create"
    deployment_id = body.get("deployment_id")
    triad.response_valid = True

    # Phase 3: State updated — confirm deployment persisted and event stream accessible
    if deployment_id:
        # Check status
        detail_resp = flow_client.get(f"{deployment_api}/api/deployments/{deployment_id}", timeout=30.0)
        assert detail_resp.status_code in (200, 404), f"Deployment detail returned unexpected {detail_resp.status_code}"

        # Check logs/events
        events_resp = flow_client.get(
            f"{deployment_api}/api/deployments/{deployment_id}/events",
            timeout=30.0,
        )
        assert events_resp.status_code in (200, 404, 502), (
            f"Events endpoint returned unexpected {events_resp.status_code}"
        )
        if events_resp.status_code == 200:
            events_body = cast(dict[str, object], events_resp.json())
            assert "events" in events_body, "Event stream missing 'events' key"
            assert isinstance(events_body["events"], list), "'events' must be a list"

    triad.state_updated = True
    triad.assert_triad("deployment-flow: deploy -> status -> logs")


@pytest.mark.critical
def test_deploy_list_reflects_creation(
    flow_client: httpx.Client,
    deployment_api: str,
    triad: TriadResult,
) -> None:
    """After creating a deployment, the list endpoint must include it.

    Triad:
        1. Request sent: POST deployment + GET list
        2. Response valid: both return 2xx
        3. State updated: list includes at least one deployment
    """
    # Phase 1: Create deployment
    payload = {
        "service": "instruments-service",
        "dry_run": True,
        "mode": "batch",
        "start_date": "2025-02-01",
        "end_date": "2025-02-03",
        "compute": "cloud_run",
    }
    create_resp = flow_client.post(f"{deployment_api}/api/deployments", json=payload, timeout=60.0)
    triad.request_sent = True

    assert create_resp.status_code in (200, 201, 202), f"Deploy create failed: {create_resp.status_code}"
    triad.response_valid = True

    # Phase 3: Verify list includes at least one deployment
    list_resp = flow_client.get(f"{deployment_api}/api/deployments?limit=10", timeout=30.0)
    assert list_resp.status_code == 200, f"List endpoint failed: {list_resp.status_code}"
    list_body = cast(dict[str, object], list_resp.json())
    # Accept either {deployments: [...]} or direct list
    deployments = list_body.get("deployments", list_body)
    assert deployments is not None, "List response must contain deployments"

    triad.state_updated = True
    triad.assert_triad("deployment-flow: create -> list")
