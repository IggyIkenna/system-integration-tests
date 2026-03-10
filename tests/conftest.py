"""Shared fixtures for integration tests. Zero service imports — HTTP/GCS/PubSub only."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Bucket config helpers (module-level to keep fixture complexity low)
# ---------------------------------------------------------------------------


def _load_deployment_configs(config_dir: Path) -> tuple[dict, dict] | None:
    """Load dependencies.yaml and bucket_config.yaml. Returns None on error."""
    if not config_dir.exists():
        return None
    try:
        import yaml  # noqa: PLC0415

        deps = yaml.safe_load((config_dir / "dependencies.yaml").read_text())
        bucket_cfg = yaml.safe_load((config_dir / "bucket_config.yaml").read_text())
        return deps, bucket_cfg
    except Exception:
        return None


def _get_service_categories(
    svc: str,
    shared: set[str],
    restricted: dict[str, list[str]],
    default_cats: list[str],
) -> list[str]:
    if svc in shared:
        return [""]
    for pat, cats in restricted.items():
        if pat in svc:
            return cats
    return default_cats


def _resolve_bucket(template: str, cat: str, project_id: str) -> str:
    return (
        template.replace("{category_lower}", cat.lower())
        .replace("{project_id}", project_id)
        .replace("{domain}", cat.lower())
    )


def _enumerate_service_buckets(
    deps: dict,
    bucket_cfg: dict,
    project_id: str,
) -> set[str]:
    shared = set(bucket_cfg.get("shared_bucket_services", []))
    restricted = bucket_cfg.get("service_categories", {}).get("restricted_categories", {})
    default_cats: list[str] = bucket_cfg.get("service_categories", {}).get(
        "default_categories", ["cefi", "tradfi", "defi"]
    )
    buckets: set[str] = set()
    for svc, cfg in deps.get("services", {}).items():
        cats = _get_service_categories(svc, shared, restricted, default_cats)
        for out in cfg.get("outputs", []):
            tmpl = out.get("bucket_template", "")
            if not tmpl:
                continue
            for cat in cats:
                b = _resolve_bucket(tmpl, cat, project_id)
                if b:
                    buckets.add(b)
    return buckets


def _enumerate_infra_buckets(bucket_cfg: dict, project_id: str) -> set[str]:
    buckets: set[str] = set()
    for b in bucket_cfg.get("infrastructure_buckets", {}).get("gcp", []):
        buckets.add(b["name_template"].replace("{project_id}", project_id))
    return buckets


@pytest.fixture(scope="session")
def base_urls() -> dict[str, str]:
    return {
        "instruments": os.environ.get("INSTRUMENTS_SERVICE_URL", "http://localhost:8080"),
        "era": os.environ.get("ERA_URL", "http://localhost:8002"),
        "mda": os.environ.get("MDA_URL", "http://localhost:8004"),
        "cra": os.environ.get("CRA_URL", "http://localhost:8003"),
        "deployment_api": os.environ.get("DEPLOYMENT_API_URL", "http://localhost:8001"),
        "market_data": os.environ.get("MARKET_DATA_API_URL", "http://localhost:8001"),
        "client_reporting": os.environ.get("CLIENT_REPORTING_API_URL", "http://localhost:8003"),
        "execution": os.environ.get("EXECUTION_SERVICE_URL", "http://localhost:8005"),
        "risk": os.environ.get("RISK_SERVICE_URL", "http://localhost:8006"),
        "position_monitor": os.environ.get("POSITION_MONITOR_URL", "http://localhost:8007"),
        "alerting": os.environ.get("ALERTING_SERVICE_URL", "http://localhost:8008"),
        "pnl": os.environ.get("PNL_SERVICE_URL", "http://localhost:8009"),
        "market_tick": os.environ.get("MARKET_TICK_SERVICE_URL", "http://localhost:8010"),
    }


@pytest.fixture(scope="session")
def http_client() -> Generator[httpx.Client, None, None]:
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


@pytest.fixture(scope="session")
def has_gcp_creds() -> bool:
    """True if GCP_SA_KEY or ADC is available for real cloud calls."""
    import importlib.util

    if importlib.util.find_spec("google.auth") is None:
        return False
    try:
        import google.auth  # noqa: PLC0415

        credentials, _ = google.auth.default()
        return credentials is not None
    except Exception:
        return False


@pytest.fixture(scope="session")
def gcs_test_bucket(gcp_project_id: str) -> str:
    """Test bucket name derived from project ID (matches setup-buckets.py test naming convention)."""
    return os.environ.get("GCS_TEST_BUCKET", f"instruments-store-cefi-test-{gcp_project_id}")


@pytest.fixture(scope="session")
def required_gcs_buckets(gcp_project_id: str) -> list[str]:
    """Full list of required GCS production bucket names, loaded from deployment-service config."""
    config_dir = Path(__file__).parent.parent.parent.parent / "deployment-service" / "configs"
    result = _load_deployment_configs(config_dir)
    if result is None:
        return []
    deps, bucket_cfg = result
    buckets = _enumerate_service_buckets(deps, bucket_cfg, gcp_project_id)
    buckets |= _enumerate_infra_buckets(bucket_cfg, gcp_project_id)
    return sorted(buckets)
