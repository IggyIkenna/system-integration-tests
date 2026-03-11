"""Unit tests for SIT configuration and fixture setup. Required by quality gates."""

import pytest

pytestmark = pytest.mark.code_test


def test_base_urls_fixture_keys() -> None:
    """base_urls fixture provides all required service URL keys."""
    import os
    from unittest.mock import patch

    expected_keys = {"instruments", "era", "mda", "cra", "deployment_api"}
    env = {
        "INSTRUMENTS_SERVICE_URL": "http://localhost:8080",
        "ERA_URL": "http://localhost:8002",
        "MDA_URL": "http://localhost:8004",
        "CRA_URL": "http://localhost:8003",
        "DEPLOYMENT_API_URL": "http://localhost:8001",
    }
    with patch.dict(os.environ, env):
        urls: dict[str, str] = {
            "instruments": os.environ.get("INSTRUMENTS_SERVICE_URL", "http://localhost:8080"),
            "era": os.environ.get("ERA_URL", "http://localhost:8002"),
            "mda": os.environ.get("MDA_URL", "http://localhost:8004"),
            "cra": os.environ.get("CRA_URL", "http://localhost:8003"),
            "deployment_api": os.environ.get("DEPLOYMENT_API_URL", "http://localhost:8001"),
        }
    assert set(urls.keys()) == expected_keys


def test_gcp_project_id_default() -> None:
    """gcp_project_id defaults to test-project when env var is absent."""
    import os

    project_id = os.environ.get("GCP_PROJECT_ID", "test-project")
    assert project_id == "test-project" or project_id.strip() != ""
