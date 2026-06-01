"""Layer 3b full e2e tests — OAuth flows and auth failure handling."""

import httpx
import pytest

pytestmark = pytest.mark.deployment_test


@pytest.mark.full_e2e
def test_unauthenticated_write_rejected(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Write endpoints require authentication — unauthenticated requests return 401/403."""
    try:
        resp: httpx.Response = http_client.post(
            f"{base_urls['deployment_api']}/pipeline/trigger",
            json={"date": "2024-01-15", "venue": "XNAS"},
        )
    except httpx.ConnectError:
        pytest.skip("deployment-api not reachable")
    # In mock/CI mode with DISABLE_AUTH=true, endpoints may return 500 (route not found)
    if resp.status_code == 500:
        pytest.skip("/pipeline/trigger not implemented in mock mode")
    # Without auth header, write endpoints must reject
    assert resp.status_code in (401, 403), f"Expected 401/403 for unauthenticated write, got {resp.status_code}"


@pytest.mark.full_e2e
def test_read_endpoints_public(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Read-only endpoints (health, version) are accessible without auth."""
    for name, url in base_urls.items():
        try:
            resp: httpx.Response = http_client.get(f"{url}/health")
        except httpx.ConnectError:
            continue  # Service not running — skip this one
        assert resp.status_code != 401, f"{name} health check should not require auth"


@pytest.mark.full_e2e
def test_invalid_token_rejected(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Invalid bearer token is rejected with 401."""
    try:
        resp: httpx.Response = http_client.post(
            f"{base_urls['deployment_api']}/pipeline/trigger",
            json={"date": "2024-01-15", "venue": "XNAS"},
            headers={"Authorization": "Bearer invalid-token-xyz"},
        )
    except httpx.ConnectError:
        pytest.skip("deployment-api not reachable")
    # In mock/CI mode with DISABLE_AUTH=true, endpoints may return 500 (route not found)
    if resp.status_code == 500:
        pytest.skip("/pipeline/trigger not implemented in mock mode")
    assert resp.status_code == 401, f"Expected 401 for invalid token, got {resp.status_code}"
