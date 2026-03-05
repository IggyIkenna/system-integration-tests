"""Shared fixtures for integration tests. Zero service imports — HTTP/GCS/PubSub only."""

import os

import httpx
import pytest


@pytest.fixture(scope="session")
def base_urls() -> dict[str, str]:
    return {
        "instruments": os.environ.get("INSTRUMENTS_SERVICE_URL", "http://localhost:8080"),
        "era": os.environ.get("ERA_URL", "http://localhost:8002"),
        "mda": os.environ.get("MDA_URL", "http://localhost:8004"),
        "cra": os.environ.get("CRA_URL", "http://localhost:8003"),
        "deployment_api": os.environ.get("DEPLOYMENT_API_URL", "http://localhost:8001"),
    }


@pytest.fixture(scope="session")
def http_client():
    with httpx.Client(timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def gcs_bucket() -> str:
    bucket = os.environ.get("GCS_TEST_BUCKET")
    if not bucket:
        raise ValueError("GCS_TEST_BUCKET env var required for integration tests")
    return bucket


@pytest.fixture(scope="session")
def gcp_project_id() -> str:
    project_id = os.environ.get("GCP_PROJECT_ID", "test-project")
    return project_id


@pytest.fixture(scope="session")
def s3_bucket() -> str | None:
    """S3_TEST_BUCKET env var — parallel to GCS_TEST_BUCKET. None if not set (AWS tests skip)."""
    return os.environ.get("S3_TEST_BUCKET")
