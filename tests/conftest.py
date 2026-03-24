"""Shared fixtures for integration tests. Zero service imports — HTTP/GCS/PubSub only.

GCP Pub/Sub Emulator
--------------------
When ``PUBSUB_EMULATOR_HOST`` is set (or the emulator is reachable at
``localhost:8085``), the ``with_pubsub_emulator`` fixture activates it for
individual tests.  Tests that use this fixture are **skipped automatically**
when the emulator is not running.

To run the emulator locally::

    gcloud beta emulators pubsub start --host-port=localhost:8085
    # or
    docker run --rm -p 8085:8085 gcr.io/google.com/cloudsdktool/google-cloud-cli:latest \\
        gcloud beta emulators pubsub start --host-port=0.0.0.0:8085

CI (GitHub Actions) — add to the workflow job that runs SIT::

    services:
      pubsub-emulator:
        image: gcr.io/google.com/cloudsdktool/google-cloud-cli:latest
        options: >-
          --entrypoint gcloud
        args: beta emulators pubsub start --host-port=0.0.0.0:8085
        ports:
          - 8085:8085
    env:
      PUBSUB_EMULATOR_HOST: localhost:8085

GCS Emulator (fake-gcs-server)
-------------------------------
When ``STORAGE_EMULATOR_HOST`` is set and the emulator is reachable, the
``gcs_emulator`` fixture activates it.  Tests that use this fixture are
**skipped automatically** when the env var is not set or the emulator is down.

# GCS emulator: docker run -d -p 4443:4443 fsouza/fake-gcs-server:latest
# Set STORAGE_EMULATOR_HOST=http://localhost:4443 before running integration tests

CI (GitHub Actions) — add to the workflow job that runs SIT::

    services:
      gcs-emulator:
        image: fsouza/fake-gcs-server:latest
        ports:
          - 4443:4443
    env:
      STORAGE_EMULATOR_HOST: http://localhost:4443
"""

from __future__ import annotations

import os

# Set credential-free environment BEFORE any imports that might trigger GCP client init
os.environ.setdefault("CLOUD_PROVIDER", "local")
os.environ.setdefault("CLOUD_MOCK_MODE", "true")
os.environ.setdefault("STORAGE_EMULATOR_HOST", "http://localhost:4443")
os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

import socket
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
        import yaml

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


_original_connect = socket.socket.connect


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--block-network",
        action="store_true",
        default=False,
        help="Block all socket connections to enforce credential-free CI runs.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "allow_network: mark test as allowed to make network calls (opt-out of --block-network)",
    )


def _blocked_connect(self: socket.socket, address: object) -> None:
    raise OSError(f"Network access blocked by --block-network: attempted connection to {address}")


@pytest.fixture(autouse=True)
def _enforce_block_network(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    if not request.config.getoption("--block-network", default=False):
        yield
        return
    if request.node.get_closest_marker("allow_network"):
        yield
        return
    socket.socket.connect = _blocked_connect  # type: ignore[method-assign]
    yield
    socket.socket.connect = _original_connect  # type: ignore[method-assign]


@pytest.fixture(scope="session")
def base_urls() -> dict[str, str]:
    """Load API URLs from ui-api-mapping.json SSOT, with env var overrides.

    Only includes API services that serve HTTP (started by dev-start.sh).
    Worker services (execution-service, risk-service, instruments-service, etc.)
    are CLI workers — they don't serve HTTP and are tested via their own QG.
    """
    workspace_root = Path(__file__).resolve().parent.parent.parent
    mapping_file = workspace_root / "unified-trading-pm" / "scripts" / "dev" / "ui-api-mapping.json"

    # Build from SSOT
    urls: dict[str, str] = {}
    if mapping_file.exists():
        import json

        with open(mapping_file) as f:
            mapping = json.load(f)
        for stack_name, stack in mapping.get("stacks", {}).items():
            api_port = stack.get("api_port")
            api_name = stack.get("api")
            if api_port and api_name:
                # Normalize key: "deployment-api" -> "deployment_api"
                key = api_name.replace("-", "_")
                urls[key] = f"http://localhost:{api_port}"

    # Env var overrides (any SIT_<KEY>_URL takes precedence)
    for key in list(urls.keys()):
        env_key = f"{key.upper()}_URL"
        env_val = os.environ.get(env_key)
        if env_val:
            urls[key] = env_val

    # Legacy smoke-test aliases (execution-results-api consolidated into unified-trading-api)
    urls.setdefault("era", urls.get("unified_trading_api", "http://localhost:8030"))
    urls.setdefault("mda", urls.get("market_data_api", "http://localhost:8016"))
    urls.setdefault("cra", urls.get("client_reporting_api", "http://localhost:8014"))
    urls.setdefault("client_reporting", urls.get("client_reporting_api", "http://localhost:8014"))

    return urls


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
        import google.auth

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


# ---------------------------------------------------------------------------
# GCP Pub/Sub emulator fixtures
# ---------------------------------------------------------------------------


def _is_pubsub_emulator_running(host: str, port: int) -> bool:
    """Return True if something is listening on *host*:*port*."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def pubsub_emulator_host() -> str | None:
    """Return ``PUBSUB_EMULATOR_HOST`` when the emulator is reachable, else ``None``.

    The fixture probes the configured host:port via a raw TCP connect so that
    tests skip gracefully when the emulator is not running rather than
    hard-failing with a GCP API error.

    To run the emulator before your test session::

        gcloud beta emulators pubsub start --host-port=localhost:8085
        # or
        docker run --rm -p 8085:8085 gcr.io/google.com/cloudsdktool/google-cloud-cli:latest \\
            gcloud beta emulators pubsub start --host-port=0.0.0.0:8085

    CI (GitHub Actions) — add to the workflow job::

        services:
          pubsub-emulator:
            image: gcr.io/google.com/cloudsdktool/google-cloud-cli:latest
            options: >-
              --entrypoint gcloud
            args: beta emulators pubsub start --host-port=0.0.0.0:8085
            ports:
              - 8085:8085
        env:
          PUBSUB_EMULATOR_HOST: localhost:8085
    """
    raw = os.environ.get("PUBSUB_EMULATOR_HOST", "localhost:8085")
    hostname, _, port_str = raw.partition(":")
    port = int(port_str) if port_str else 8085
    if _is_pubsub_emulator_running(hostname, port):
        return raw
    return None


@pytest.fixture()
def with_pubsub_emulator(
    pubsub_emulator_host: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Activate the Pub/Sub emulator for a single SIT test.

    Sets ``PUBSUB_EMULATOR_HOST`` via *monkeypatch* so the ``google-cloud-pubsub``
    SDK routes all calls to the local emulator.  The test is **skipped**
    automatically when the emulator is not running.

    Usage::

        def test_something(with_pubsub_emulator: None) -> None:
            ...
    """
    if pubsub_emulator_host is None:
        pytest.skip(
            "Pub/Sub emulator not running — start it with "
            "'gcloud beta emulators pubsub start --host-port=localhost:8085' "
            "or set PUBSUB_EMULATOR_HOST=<host>:<port>"
        )
    monkeypatch.setenv("PUBSUB_EMULATOR_HOST", pubsub_emulator_host)
    yield


# ---------------------------------------------------------------------------
# GCS emulator fixtures (fake-gcs-server)
# ---------------------------------------------------------------------------

# GCS emulator: docker run -d -p 4443:4443 fsouza/fake-gcs-server:latest
# Set STORAGE_EMULATOR_HOST=http://localhost:4443 before running integration tests


def _is_gcs_emulator_reachable(url: str) -> bool:
    """Return True if the GCS emulator endpoint is reachable via TCP."""
    stripped = url.removeprefix("http://").removeprefix("https://")
    host, _, port_str = stripped.partition(":")
    port = int(port_str) if port_str else 4443
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def gcs_emulator() -> Generator[str, None, None]:
    """Yield the GCS emulator URL when ``STORAGE_EMULATOR_HOST`` is set and reachable.

    Tests that request this fixture are **skipped automatically** when the env var
    is not set or the emulator is not running — no manual ``pytest.mark.skipif``
    decoration needed.

    To start the emulator locally::

        docker run -d -p 4443:4443 fsouza/fake-gcs-server:latest
        export STORAGE_EMULATOR_HOST=http://localhost:4443

    CI (GitHub Actions) — add to the workflow job::

        services:
          gcs-emulator:
            image: fsouza/fake-gcs-server:latest
            ports:
              - 4443:4443
        env:
          STORAGE_EMULATOR_HOST: http://localhost:4443

    Usage::

        def test_something(gcs_emulator: str) -> None:
            # gcs_emulator is the emulator URL, e.g. "http://localhost:4443"
            ...
    """
    url = os.environ.get("STORAGE_EMULATOR_HOST", "")
    if not url:
        pytest.skip(
            "GCS emulator not configured — set STORAGE_EMULATOR_HOST=http://localhost:4443 "
            "and start: docker run -d -p 4443:4443 fsouza/fake-gcs-server:latest"
        )
    if not _is_gcs_emulator_reachable(url):
        pytest.skip(
            f"GCS emulator not reachable at {url} — start: docker run -d -p 4443:4443 fsouza/fake-gcs-server:latest"
        )
    yield url


@pytest.fixture()
def with_gcs_emulator(
    gcs_emulator: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Activate the GCS emulator for a single SIT test via ``STORAGE_EMULATOR_HOST``.

    Sets ``STORAGE_EMULATOR_HOST`` via *monkeypatch* so the ``google-cloud-storage``
    SDK routes all calls to the local fake-gcs-server.  The test is **skipped**
    automatically when the emulator is not running (inherited from ``gcs_emulator``).

    Usage::

        def test_something(with_gcs_emulator: None) -> None:
            # STORAGE_EMULATOR_HOST is set for this test's duration
            ...
    """
    monkeypatch.setenv("STORAGE_EMULATOR_HOST", gcs_emulator)
    yield
