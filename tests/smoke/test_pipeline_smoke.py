"""Layer 3a smoke tests — zero service imports, HTTP only."""

import httpx
import pytest

pytestmark = pytest.mark.deployment_test


@pytest.mark.smoke
def test_instruments_service_health(http_client: httpx.Client) -> None:
    """Instruments service responds to health check.

    instruments-service is a CLI worker, not in the dev API stack.
    Uses a fixed URL — not in base_urls (which only tracks API services).
    """
    instruments_url = "http://localhost:8080"
    try:
        response: httpx.Response = http_client.get(f"{instruments_url}/health")
    except httpx.ConnectError:
        pytest.skip(f"instruments-service not running at {instruments_url}")
    assert response.status_code == 200


@pytest.mark.smoke
def test_deployment_api_health(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Deployment API responds to health check."""
    response: httpx.Response = http_client.get(f"{base_urls['deployment_api']}/health")
    assert response.status_code == 200
