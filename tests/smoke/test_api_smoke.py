"""Layer 3a smoke tests — ERA/MDA/CRA health checks, HTTP only."""

import pytest


@pytest.mark.smoke
def test_api_services_health(http_client, base_urls):
    """All T5 API services respond to health checks."""
    api_services = [
        ("era", base_urls["era"]),
        ("mda", base_urls["mda"]),
        ("cra", base_urls["cra"]),
    ]
    for name, url in api_services:
        resp = http_client.get(f"{url}/health")
        assert resp.status_code == 200, f"{name} health check failed with {resp.status_code}"


@pytest.mark.smoke
def test_api_services_version(http_client, base_urls):
    """All T5 API services expose a version endpoint."""
    api_services = [
        ("era", base_urls["era"]),
        ("mda", base_urls["mda"]),
        ("cra", base_urls["cra"]),
    ]
    for name, url in api_services:
        resp = http_client.get(f"{url}/version")
        assert resp.status_code == 200, f"{name} version endpoint failed with {resp.status_code}"
        body = resp.json()
        assert "version" in body, f"{name} version response missing 'version' field"
