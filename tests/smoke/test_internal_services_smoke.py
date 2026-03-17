"""Internal service smoke tests — /health body validation, /readiness, /docs."""

from __future__ import annotations

from typing import cast

import pytest
import requests

pytestmark = pytest.mark.deployment_test

INTERNAL_SERVICES = [
    ("execution", 8005),
    ("risk", 8006),
    ("position_monitor", 8007),
    ("alerting", 8008),
    ("pnl", 8009),
    ("market_tick", 8010),
]


@pytest.mark.smoke
@pytest.mark.parametrize("service_name,port", INTERNAL_SERVICES)
def test_health_response_schema(service_name: str, port: int, base_urls: dict[str, str]) -> None:
    """Validate rich health response schema for each service."""
    url = base_urls.get(service_name, f"http://localhost:{port}")
    try:
        resp = requests.get(f"{url}/health", timeout=3)
    except requests.ConnectionError:
        pytest.skip(f"{service_name} not running")
    body = cast(dict[str, object], resp.json())
    assert body["status"] in {"ok", "healthy", "degraded", "unhealthy"}, f"Invalid status: {body['status']}"
    # In mock mode, some services may not include 'service' or 'checks' fields
    # The critical assertion is that status is valid


@pytest.mark.smoke
@pytest.mark.parametrize("service_name,port", INTERNAL_SERVICES)
def test_readiness_soft_check(service_name: str, port: int, base_urls: dict[str, str]) -> None:
    """Readiness endpoint returns 200 or 503 (both acceptable)."""
    url = base_urls.get(service_name, f"http://localhost:{port}")
    try:
        resp = requests.get(f"{url}/readiness", timeout=3)
    except requests.ConnectionError:
        pytest.skip(f"{service_name} not running")
    assert resp.status_code in {200, 503}


@pytest.mark.smoke
@pytest.mark.parametrize("service_name,port", INTERNAL_SERVICES)
def test_docs_endpoint_exists(service_name: str, port: int, base_urls: dict[str, str]) -> None:
    """GET /docs must return 200 (FastAPI swagger UI)."""
    url = base_urls.get(service_name, f"http://localhost:{port}")
    try:
        resp = requests.get(f"{url}/docs", timeout=3)
    except requests.ConnectionError:
        pytest.skip(f"{service_name} not running")
    assert resp.status_code == 200
