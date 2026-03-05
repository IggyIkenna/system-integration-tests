"""Layer 3a smoke tests — zero service imports, HTTP only."""

import pytest


@pytest.mark.smoke
def test_instruments_service_health(http_client, base_urls):
    """Instruments service responds to health check."""
    resp = http_client.get(f"{base_urls['instruments']}/health")
    assert resp.status_code == 200


@pytest.mark.smoke
def test_deployment_api_health(http_client, base_urls):
    """Deployment API responds to health check."""
    resp = http_client.get(f"{base_urls['deployment_api']}/health")
    assert resp.status_code == 200
