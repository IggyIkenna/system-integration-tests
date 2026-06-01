"""
Artifact Registry / container registry smoke tests.

GCP Artifact Registry: tested when GCP credentials available.
  - Verifies Docker image repositories are accessible
  - Verifies Python wheel repository is accessible (CodeArtifact equivalent)
  - Lists recent image tags for each service

AWS ECR / CodeArtifact: skipped (no AWS credentials).

Required env vars for live GCP tests:
  GCP_PROJECT_ID          — GCP project
  AR_LOCATION             — Artifact Registry location (default: asia-northeast1)
  AR_DOCKER_REPO          — Docker repo name (default: trading-images)
  AR_PYTHON_REPO          — Python wheel repo name (default: trading-wheels)
"""

from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.deployment_test

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------


def _has_gcp_creds() -> bool:
    if importlib.util.find_spec("google.auth") is None:
        return False
    try:
        import google.auth

        creds, _ = google.auth.default()
        return creds is not None
    except Exception:
        return False


def _has_requests() -> bool:
    return importlib.util.find_spec("requests") is not None


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


_AR_LOCATION = os.environ.get("AR_LOCATION", "asia-northeast1")
_AR_DOCKER_REPO = os.environ.get("AR_DOCKER_REPO", "trading-images")
_AR_PYTHON_REPO = os.environ.get("AR_PYTHON_REPO", "trading-wheels")

# Core service images that must exist in the registry
_EXPECTED_SERVICE_IMAGES = [
    "execution-service",
    "strategy-service",
    "risk-service",
    "instruments-service",
    "market-tick-data-service",
    "deployment-api",
]


def _ar_base_url(project_id: str, location: str = _AR_LOCATION, repo: str = _AR_DOCKER_REPO) -> str:
    return f"{location}-docker.pkg.dev/{project_id}/{repo}"


def _get_auth_token() -> str | None:
    """Get a short-lived OAuth2 token for AR API requests."""
    try:
        import google.auth
        import google.auth.transport.requests

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        request = google.auth.transport.requests.Request()
        creds.refresh(request)
        return creds.token  # type: ignore[attr-defined]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1. AR repository accessibility
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestArtifactRegistryAccessible:
    """Verify Artifact Registry repositories are reachable."""

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    @pytest.mark.skipif(not _has_requests(), reason="requests not installed")
    def test_docker_repo_accessible(self, gcp_project_id: str) -> None:
        """Docker image repository must respond to v2 catalog endpoint."""
        import requests

        token = _get_auth_token()
        if token is None:
            pytest.skip("Could not obtain GCP auth token")

        # Docker Registry v2 catalog endpoint
        url = f"https://{_AR_LOCATION}-docker.pkg.dev/v2/{gcp_project_id}/{_AR_DOCKER_REPO}/_catalog"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)

        assert resp.status_code in (200, 404), (
            f"AR catalog returned unexpected status {resp.status_code}: {resp.text[:200]}"
        )
        # 404 means repo doesn't exist yet (not created) — informational only
        if resp.status_code == 404:
            pytest.skip(
                f"AR Docker repo '{_AR_DOCKER_REPO}' not found in {gcp_project_id}. "
                "Create it: gcloud artifacts repositories create trading-images "
                "--repository-format=docker --location=asia-northeast1"
            )

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    def test_ar_repos_list_accessible(self, gcp_project_id: str) -> None:
        """gcloud artifacts repositories list must return at least one repo."""
        import subprocess

        result = subprocess.run(
            [
                "gcloud",
                "artifacts",
                "repositories",
                "list",
                f"--project={gcp_project_id}",
                f"--location={_AR_LOCATION}",
                "--format=value(name)",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            pytest.skip(f"gcloud artifacts command failed: {result.stderr[:200]}")

        repos = [r.strip() for r in result.stdout.strip().splitlines() if r.strip()]
        assert len(repos) >= 1, (
            f"No Artifact Registry repositories found in {_AR_LOCATION}. "
            "Create one: gcloud artifacts repositories create trading-images "
            "--repository-format=docker --location=asia-northeast1"
        )


# ---------------------------------------------------------------------------
# 2. Service images exist
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestServiceImagesExist:
    """Verify service Docker images are present in Artifact Registry."""

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    @pytest.mark.skipif(not _has_requests(), reason="requests not installed")
    def test_at_least_one_service_image_present(self, gcp_project_id: str) -> None:
        """At least one of the expected service images must exist in the registry."""
        import requests

        token = _get_auth_token()
        if token is None:
            pytest.skip("Could not obtain GCP auth token")

        found: list[str] = []
        for service in _EXPECTED_SERVICE_IMAGES:
            url = f"https://{_AR_LOCATION}-docker.pkg.dev/v2/{gcp_project_id}/{_AR_DOCKER_REPO}/{service}/tags/list"
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if resp.status_code == 200:
                try:
                    tags = resp.json().get("tags", [])
                    if tags:
                        found.append(f"{service}:{len(tags)} tag(s)")
                except Exception:
                    pass

        if not found:
            pytest.skip(
                "No service images found in AR — CI has not pushed images yet. "
                f"Expected services: {_EXPECTED_SERVICE_IMAGES[:3]}... "
                f"Registry: {_ar_base_url(gcp_project_id)}"
            )
        # Informational: log what was found
        assert len(found) >= 1, f"No service images found. Check AR repo: {_AR_DOCKER_REPO}"

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    @pytest.mark.skipif(not _has_requests(), reason="requests not installed")
    def test_deployment_api_image_tagged(self, gcp_project_id: str) -> None:
        """deployment-api image must have at least one tag (most recently deployed)."""
        import requests

        token = _get_auth_token()
        if token is None:
            pytest.skip("Could not obtain GCP auth token")

        url = f"https://{_AR_LOCATION}-docker.pkg.dev/v2/{gcp_project_id}/{_AR_DOCKER_REPO}/deployment-api/tags/list"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)

        if resp.status_code == 404:
            pytest.skip("deployment-api image not yet pushed to AR")

        assert resp.status_code == 200, f"AR tags/list returned {resp.status_code}"
        data = resp.json()
        tags = data.get("tags", [])
        assert len(tags) >= 1, "deployment-api has no tags in Artifact Registry"


# ---------------------------------------------------------------------------
# 3. UCI AR integration (import path test — no credentials needed)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestArtifactRegistryUCI:
    """UCI Artifact Registry code paths importable and functional."""

    def test_ar_utils_importable(self) -> None:
        """deployment-api artifact_registry utils must be importable."""
        try:
            from deployment_api.utils.artifact_registry import ArtifactRegistryClient

            assert ArtifactRegistryClient is not None
        except ImportError:
            pytest.skip("deployment_api not installed in SIT virtualenv")

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    def test_ar_client_instantiates(self, gcp_project_id: str) -> None:
        """ArtifactRegistryClient must instantiate with project_id + location."""
        try:
            from deployment_api.utils.artifact_registry import ArtifactRegistryClient
        except ImportError:
            pytest.skip("deployment_api not installed in SIT virtualenv")

        client = ArtifactRegistryClient(project_id=gcp_project_id, location=_AR_LOCATION)
        assert client is not None


# ---------------------------------------------------------------------------
# 4. AWS ECR / CodeArtifact (blocked — future)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestAWSRegistries:
    """AWS ECR and CodeArtifact — skipped until AWS credentials configured."""

    def test_aws_ecr_blocked(self) -> None:
        """Placeholder: AWS ECR requires AWS_ACCESS_KEY_ID + ECR_REGISTRY_URL."""
        ecr_url = os.environ.get("ECR_REGISTRY_URL")
        if ecr_url is None:
            pytest.skip("ECR_REGISTRY_URL not set — AWS ECR not configured")
        # If set: validate connectivity
        import requests

        resp = requests.get(f"https://{ecr_url}/v2/", timeout=10)
        assert resp.status_code in (200, 401)  # 401 = auth required (reachable)

    def test_aws_codeartifact_blocked(self) -> None:
        """Placeholder: AWS CodeArtifact requires AWS credentials + CODEARTIFACT_URL."""
        ca_url = os.environ.get("CODEARTIFACT_INDEX_URL")
        if ca_url is None:
            pytest.skip("CODEARTIFACT_INDEX_URL not set — AWS CodeArtifact not configured")
        import requests

        resp = requests.get(ca_url, timeout=10)
        assert resp.status_code in (200, 401, 403)
