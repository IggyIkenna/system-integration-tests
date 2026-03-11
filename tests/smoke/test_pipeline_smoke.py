"""Layer 3a smoke tests — zero service imports, HTTP only."""

import httpx
import pytest

pytestmark = pytest.mark.deployment_test


@pytest.mark.smoke
def test_instruments_service_health(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Instruments service responds to health check."""
    response: httpx.Response = http_client.get(f"{base_urls['instruments']}/health")
    assert response.status_code == 200


@pytest.mark.smoke
def test_deployment_api_health(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """Deployment API responds to health check."""
    response: httpx.Response = http_client.get(f"{base_urls['deployment_api']}/health")
    assert response.status_code == 200
