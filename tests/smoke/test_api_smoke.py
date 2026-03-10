"""Layer 3a smoke tests — ERA/MDA/CRA health checks, HTTP only."""

from typing import cast

import httpx
import pytest
import requests


@pytest.mark.smoke
def test_api_services_health(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """All T5 API services respond to health checks."""
    api_services: list[tuple[str, str]] = [
        ("era", base_urls["era"]),
        ("mda", base_urls["mda"]),
        ("cra", base_urls["cra"]),
    ]
    for name, url in api_services:
        resp: httpx.Response = http_client.get(f"{url}/health")
        assert resp.status_code == 200, f"{name} health check failed with {resp.status_code}"


@pytest.mark.smoke
def test_api_services_version(http_client: httpx.Client, base_urls: dict[str, str]) -> None:
    """All T5 API services expose a version endpoint."""
    api_services: list[tuple[str, str]] = [
        ("era", base_urls["era"]),
        ("mda", base_urls["mda"]),
        ("cra", base_urls["cra"]),
    ]
    for name, url in api_services:
        resp: httpx.Response = http_client.get(f"{url}/version")
        assert resp.status_code == 200, f"{name} version endpoint failed with {resp.status_code}"
        body = cast(dict[str, object], resp.json())
        assert "version" in body, f"{name} version response missing 'version' field"


# --- New service health checks (P3.2) ---

_NEW_SERVICES: list[tuple[str, str, int]] = [
    ("execution", "execution", 8005),
    ("risk", "risk", 8006),
    ("position_monitor", "position_monitor", 8007),
    ("alerting", "alerting", 8008),
    ("pnl", "pnl", 8009),
    ("market_tick", "market_tick", 8010),
]


@pytest.mark.smoke
@pytest.mark.parametrize("label,key,port", _NEW_SERVICES)
def test_new_service_health_schema(label: str, key: str, port: int, base_urls: dict[str, str]) -> None:
    """GET /health returns rich schema: status, service, checks."""
    url = base_urls.get(key, f"http://localhost:{port}")
    try:
        resp = requests.get(f"{url}/health", timeout=3)
    except requests.ConnectionError:
        pytest.skip(f"{label} not running")
    body = cast(dict[str, object], resp.json())
    assert body["status"] in {"ok", "degraded", "unhealthy"}, f"{label}: Invalid status: {body['status']}"
    assert "service" in body, f"{label}: Missing 'service' field"
    assert isinstance(body.get("checks"), dict), f"{label}: Missing or invalid 'checks' field"


@pytest.mark.smoke
@pytest.mark.parametrize("label,key,port", _NEW_SERVICES)
def test_new_service_readiness(label: str, key: str, port: int, base_urls: dict[str, str]) -> None:
    """GET /readiness returns 200 or 503."""
    url = base_urls.get(key, f"http://localhost:{port}")
    try:
        resp = requests.get(f"{url}/readiness", timeout=3)
    except requests.ConnectionError:
        pytest.skip(f"{label} not running")
    assert resp.status_code in {200, 503}, f"{label}: Unexpected readiness status {resp.status_code}"
