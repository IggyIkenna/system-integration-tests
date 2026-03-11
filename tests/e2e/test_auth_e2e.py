"""Layer 3b full e2e tests — OAuth flows and auth failure handling."""

import httpx
import pytest

pytestmark = pytest.mark.deployment_test


@pytest.mark.full_e2e
def test_unauthenticated_write_rejected(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Write endpoints require authentication — unauthenticated requests return 401/403."""
    resp: httpx.Response = http_client.post(
        f"{base_urls['deployment_api']}/pipeline/trigger",
        json={"date": "2024-01-15", "venue": "XNAS"},
    )
    # Without auth header, write endpoints must reject
    assert resp.status_code in (401, 403), f"Expected 401/403 for unauthenticated write, got {resp.status_code}"


@pytest.mark.full_e2e
def test_read_endpoints_public(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Read-only endpoints (health, version) are accessible without auth."""
    for name, url in base_urls.items():
        resp: httpx.Response = http_client.get(f"{url}/health")
        assert resp.status_code != 401, f"{name} health check should not require auth"


@pytest.mark.full_e2e
def test_invalid_token_rejected(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Invalid bearer token is rejected with 401."""
    resp: httpx.Response = http_client.post(
        f"{base_urls['deployment_api']}/pipeline/trigger",
        json={"date": "2024-01-15", "venue": "XNAS"},
        headers={"Authorization": "Bearer invalid-token-xyz"},
    )
    assert resp.status_code == 401, f"Expected 401 for invalid token, got {resp.status_code}"
