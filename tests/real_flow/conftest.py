"""Real-flow test configuration — marker registration, base URLs, and triad assertion helpers.

Flow priority tiers:
    - critical: Run on every staging deploy (deployment, kill switch)
    - high: Run in nightly SIT (alert routing)
    - medium: Run in weekly SIT (log query)

Each real-flow test asserts the triad:
    1. Request sent (HTTP call succeeds)
    2. Response received (status code + body structure validated)
    3. State updated (subsequent GET confirms side-effect)
"""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import dataclass

import httpx
import pytest

# ---------------------------------------------------------------------------
# Marker registration
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """Register real-flow priority markers."""
    config.addinivalue_line("markers", "critical: Critical journey — runs on every staging deploy")
    config.addinivalue_line("markers", "high: High-priority journey — runs in nightly SIT")
    config.addinivalue_line("markers", "medium: Medium-priority journey — runs in weekly SIT")
    config.addinivalue_line("markers", "real_flow: Real UI/API flow validation test")


# ---------------------------------------------------------------------------
# Base URL fixture for real-flow services
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlowServiceURLs:
    """Service base URLs resolved from environment variables.

    Defaults match the canonical port registry:
    ``unified-trading-pm/scripts/dev/ui-api-mapping.json``
    """

    deployment_api: str
    execution_service: str
    alerting_service: str
    batch_audit_api: str
    logs_dashboard_api: str
    live_health_monitor_api: str

    @staticmethod
    def from_env() -> FlowServiceURLs:
        """Build from SIT_* env vars with sensible local-dev defaults."""
        return FlowServiceURLs(
            deployment_api=os.environ.get("SIT_DEPLOYMENT_API_URL", "http://localhost:8001"),
            execution_service=os.environ.get("SIT_EXECUTION_SERVICE_URL", "http://localhost:8005"),
            alerting_service=os.environ.get("SIT_ALERTING_SERVICE_URL", "http://localhost:8008"),
            batch_audit_api=os.environ.get("SIT_BATCH_AUDIT_API_URL", "http://localhost:8014"),
            logs_dashboard_api=os.environ.get("SIT_LOGS_DASHBOARD_API_URL", "http://localhost:8015"),
            live_health_monitor_api=os.environ.get("SIT_LIVE_HEALTH_MONITOR_URL", "http://localhost:8012"),
        )


@pytest.fixture(scope="session")
def flow_urls() -> FlowServiceURLs:
    """Session-scoped service URL configuration for real-flow tests."""
    return FlowServiceURLs.from_env()


@pytest.fixture(scope="session")
def flow_client() -> Generator[httpx.Client, None, None]:
    """Dedicated httpx client for real-flow tests with generous timeout."""
    with httpx.Client(timeout=30.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Triad assertion helper
# ---------------------------------------------------------------------------


@dataclass
class TriadResult:
    """Captures the three phases of a real-flow assertion.

    Attributes:
        request_sent: True if the HTTP request completed without transport error.
        response_valid: True if the response status and body passed validation.
        state_updated: True if a follow-up GET confirmed the expected side-effect.
    """

    request_sent: bool = False
    response_valid: bool = False
    state_updated: bool = False

    @property
    def passed(self) -> bool:
        return self.request_sent and self.response_valid and self.state_updated

    def assert_triad(self, journey_name: str) -> None:
        """Assert all three phases passed, with a descriptive message on failure."""
        failures: list[str] = []
        if not self.request_sent:
            failures.append("request NOT sent")
        if not self.response_valid:
            failures.append("response NOT valid")
        if not self.state_updated:
            failures.append("state NOT updated")
        assert not failures, f"[{journey_name}] Triad assertion failed: {', '.join(failures)}"


@pytest.fixture()
def triad() -> TriadResult:
    """Fresh triad tracker for a single test."""
    return TriadResult()
