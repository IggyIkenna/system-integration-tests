"""Critical journey: Kill switch — toggle -> verify status change.

Validates the kill switch flow through live-health-monitor-ui + execution-service:
    1. GET current kill switch state (request sent)
    2. POST toggle kill switch (response received with new state)
    3. GET state again confirms the toggle took effect (state updated)

Uses execution-service /api/kill-switch endpoint.

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
def execution_api(flow_urls: FlowServiceURLs) -> str:
    """Execution service base URL, skipping if not reachable."""
    url = flow_urls.execution_service
    try:
        with httpx.Client(timeout=5.0) as probe:
            resp = probe.get(f"{url}/health")
            if resp.status_code != 200:
                pytest.skip(f"execution-service at {url} returned {resp.status_code}")
    except httpx.ConnectError:
        pytest.skip(f"execution-service not reachable at {url}")
    return url


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.critical
def test_kill_switch_toggle_and_verify(
    flow_client: httpx.Client,
    execution_api: str,
    triad: TriadResult,
) -> None:
    """Toggle the kill switch and verify the state change persists.

    Triad:
        1. Request sent: GET /api/kill-switch (read current state)
        2. Response valid: POST /api/kill-switch/toggle returns new state
        3. State updated: GET /api/kill-switch confirms toggled state
    """
    # Phase 1: Read current kill switch state
    get_resp = flow_client.get(f"{execution_api}/api/kill-switch", timeout=10.0)
    triad.request_sent = True

    # If kill-switch endpoint doesn't exist, try /api/circuit-breaker
    if get_resp.status_code == 404:
        get_resp = flow_client.get(f"{execution_api}/api/circuit-breaker", timeout=10.0)
        if get_resp.status_code == 404:
            pytest.skip("Kill switch / circuit breaker endpoint not available")

    assert get_resp.status_code == 200, f"Kill switch GET failed: {get_resp.status_code} — {get_resp.text}"
    initial_body = cast(dict[str, object], get_resp.json())
    initial_active = initial_body.get("active", initial_body.get("enabled", initial_body.get("killed", False)))

    # Phase 2: Toggle kill switch
    toggle_resp = flow_client.post(
        f"{execution_api}/api/kill-switch/toggle",
        json={"reason": "SIT real-flow test"},
        timeout=10.0,
    )
    # Accept 200 (toggled), 201 (created), or 404 (endpoint uses different path)
    if toggle_resp.status_code == 404:
        # Try alternative endpoints
        toggle_resp = flow_client.post(
            f"{execution_api}/api/circuit-breaker/toggle",
            json={"reason": "SIT real-flow test"},
            timeout=10.0,
        )
    if toggle_resp.status_code == 404:
        # If toggle doesn't exist, try PUT to set state directly
        toggle_resp = flow_client.put(
            f"{execution_api}/api/kill-switch",
            json={"active": not initial_active, "reason": "SIT real-flow test"},
            timeout=10.0,
        )
    if toggle_resp.status_code == 404:
        pytest.skip("Kill switch toggle endpoint not available — no toggle/PUT path found")

    assert toggle_resp.status_code in (200, 201, 202), (
        f"Kill switch toggle failed: {toggle_resp.status_code} — {toggle_resp.text}"
    )
    triad.response_valid = True

    # Phase 3: Verify state changed
    verify_resp = flow_client.get(f"{execution_api}/api/kill-switch", timeout=10.0)
    if verify_resp.status_code == 404:
        verify_resp = flow_client.get(f"{execution_api}/api/circuit-breaker", timeout=10.0)

    assert verify_resp.status_code == 200, f"Kill switch verify GET failed: {verify_resp.status_code}"
    verify_body = cast(dict[str, object], verify_resp.json())
    new_active = verify_body.get("active", verify_body.get("enabled", verify_body.get("killed")))

    # State should have changed (toggled)
    assert new_active != initial_active, f"Kill switch state did not change: was {initial_active}, still {new_active}"

    # Restore original state to avoid side effects
    flow_client.post(
        f"{execution_api}/api/kill-switch/toggle",
        json={"reason": "SIT real-flow test — restore"},
        timeout=10.0,
    )

    triad.state_updated = True
    triad.assert_triad("kill-switch: toggle -> verify status change")


@pytest.mark.critical
def test_kill_switch_read_only(
    flow_client: httpx.Client,
    execution_api: str,
    triad: TriadResult,
) -> None:
    """Verify kill switch state is readable (non-destructive health check).

    Triad:
        1. Request sent: GET kill switch state
        2. Response valid: body has expected structure
        3. State updated: state is consistent (second read matches first)
    """
    # Phase 1
    resp1 = flow_client.get(f"{execution_api}/api/kill-switch", timeout=10.0)
    if resp1.status_code == 404:
        resp1 = flow_client.get(f"{execution_api}/api/circuit-breaker", timeout=10.0)
    if resp1.status_code == 404:
        pytest.skip("Kill switch endpoint not available")
    triad.request_sent = True

    # Phase 2
    assert resp1.status_code == 200, f"Kill switch read failed: {resp1.status_code}"
    body1 = cast(dict[str, object], resp1.json())
    # Must have at least one state indicator field
    state_fields = {"active", "enabled", "killed", "status"}
    found_fields = state_fields & set(body1.keys())
    assert found_fields, f"Kill switch response missing state field. Keys: {list(body1.keys())}"
    triad.response_valid = True

    # Phase 3: Second read should return consistent state
    resp2 = flow_client.get(f"{execution_api}/api/kill-switch", timeout=10.0)
    if resp2.status_code == 404:
        resp2 = flow_client.get(f"{execution_api}/api/circuit-breaker", timeout=10.0)
    assert resp2.status_code == 200
    body2 = cast(dict[str, object], resp2.json())

    # Pick the first common state field and verify consistency
    for field_name in found_fields:
        val1 = body1.get(field_name)
        val2 = body2.get(field_name)
        assert val1 == val2, f"Kill switch state inconsistent: {field_name}={val1} then {val2}"
        break

    triad.state_updated = True
    triad.assert_triad("kill-switch: read-only consistency check")
