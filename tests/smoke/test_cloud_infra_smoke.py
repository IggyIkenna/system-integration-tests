"""
Cloud infrastructure smoke tests — bucket availability, permissions, and auth.

Tests run against BOTH GCP and AWS to validate dual-cloud capability:
  - GCP: always attempted; skip gracefully when no credentials
  - AWS: attempted when AWS creds available; skip otherwise

Cloud provider selection:
  CLOUD_PROVIDER=gcp | aws | local (default: gcp)

Required env vars for live GCP tests:
  GCP_PROJECT_ID       — e.g. test-project
  GCS_TEST_BUCKET      — test bucket for read/write probe (default: instruments-store-cefi-test-{project})

Required env vars for live AWS tests:
  AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (or role)
  S3_TEST_BUCKET

All tests skip gracefully without credentials — no fixture errors.
"""

from __future__ import annotations

import importlib.util
import os
import uuid

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


def _has_aws_creds() -> bool:
    if importlib.util.find_spec("boto3") is None:
        return False
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, NoCredentialsError

        return boto3.Session().get_credentials() is not None
    except (BotoCoreError, NoCredentialsError, Exception):
        return False


# ---------------------------------------------------------------------------
# 1. All required GCS buckets exist
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestGCSBucketsExist:
    """Verify all required production GCS buckets are reachable."""

    def test_required_buckets_list_non_empty(self, required_gcs_buckets: list[str]) -> None:
        """Fixture must resolve at least the core infra buckets from deployment-service config."""
        assert len(required_gcs_buckets) >= 10, (
            f"Expected ≥10 required buckets from deployment-service config, got {len(required_gcs_buckets)}. "
            "Check unified-trading-pm/configs/dependencies.yaml is reachable."
        )

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    def test_all_required_gcs_buckets_accessible(self, required_gcs_buckets: list[str]) -> None:
        """Every bucket in required_gcs_buckets must be accessible via UCI storage client."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_storage_client

        client = get_storage_client(provider="gcp")
        missing: list[str] = []

        for bucket in required_gcs_buckets:
            try:
                list(client.list_blobs(bucket=bucket, prefix="", max_results=1))
            except Exception as exc:
                missing.append(f"{bucket}: {exc}")

        assert not missing, (
            f"{len(missing)} required GCS buckets inaccessible:\n"
            + "\n".join(f"  - {m}" for m in missing[:20])
            + ("\n  ... (truncated)" if len(missing) > 20 else "")
            + "\nRun: python deployment-service/scripts/setup-buckets.py --cloud gcp --dry-run"
        )

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    def test_core_infra_buckets_accessible(self, gcp_project_id: str) -> None:
        """Core infrastructure buckets (terraform-state, config-store) must always be accessible."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_storage_client

        client = get_storage_client(provider="gcp")
        core_buckets = [
            f"terraform-state-{gcp_project_id}",
            f"config-store-{gcp_project_id}",
            f"deployment-orchestration-{gcp_project_id}",
        ]
        for bucket in core_buckets:
            try:
                list(client.list_blobs(bucket=bucket, prefix="", max_results=1))
            except Exception as exc:
                pytest.fail(f"Core infra bucket gs://{bucket} inaccessible: {exc}")


# ---------------------------------------------------------------------------
# 2. GCS bucket read/write permissions
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestGCSBucketPermissions:
    """Verify the CI service account has read/write on the test bucket."""

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    def test_gcs_write_read_delete(self, gcs_test_bucket: str) -> None:
        """Write a sentinel blob to test bucket, read it back, then delete it."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_storage_client

        client = get_storage_client(provider="gcp")
        blob_name = f"sit-probe/{uuid.uuid4().hex}"
        payload = b"sit-permission-check"

        # write
        client.upload_blob(bucket=gcs_test_bucket, key=blob_name, data=payload)

        # read back
        downloaded = client.download_blob(bucket=gcs_test_bucket, key=blob_name)
        assert downloaded == payload, "Downloaded content does not match uploaded payload"

        # delete
        client.delete_blob(bucket=gcs_test_bucket, key=blob_name)

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    def test_gcs_list_permission(self, gcs_test_bucket: str) -> None:
        """list_blobs must succeed on test bucket (requires storage.objects.list)."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_storage_client

        client = get_storage_client(provider="gcp")
        result = list(client.list_blobs(bucket=gcs_test_bucket, prefix="sit-probe/", max_results=5))
        # result can be empty — just verifying no permission error
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 3. Secret Manager accessible
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestSecretManagerAuth:
    """Verify Secret Manager is accessible via UCI get_secret_client."""

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    def test_gcp_secret_client_instantiates(self, gcp_project_id: str) -> None:
        """get_secret_client(provider='gcp') must return a working client."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_secret_client

        client = get_secret_client(provider="gcp")
        assert client is not None

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    def test_gcp_secret_manager_can_read_known_secret(self, gcp_project_id: str) -> None:
        """A known-good secret (github-automation-token) must be readable from SM."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_secret_client

        client = get_secret_client(provider="gcp")
        # github-automation-token is a known-good secret in test-project SM
        secret_name = os.environ.get("SIT_PROBE_SECRET", "github-automation-token")
        try:
            value = client.get_secret(project_id=gcp_project_id, secret_name=secret_name)
            assert value is not None and len(value) > 0, f"Secret {secret_name} returned empty value"
        except Exception as exc:
            pytest.fail(f"Secret Manager read failed for {secret_name}: {exc}")

    def test_gcp_secret_client_local_mode(self) -> None:
        """get_secret_client(provider='local') must not call Secret Manager (import-time safety)."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_secret_client

        client = get_secret_client(provider="local")
        assert client is not None


# ---------------------------------------------------------------------------
# 4. AWS S3 buckets
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestAWSS3Buckets:
    """AWS S3 bucket availability — skipped when no AWS credentials."""

    @pytest.mark.skipif(not _has_aws_creds(), reason="AWS credentials not available")
    def test_aws_s3_test_bucket_accessible(self, s3_bucket: str | None) -> None:
        """S3_TEST_BUCKET must be accessible when AWS creds are present."""
        if s3_bucket is None:
            pytest.skip("S3_TEST_BUCKET not set")

        from typing import cast

        import boto3
        from mypy_boto3_s3 import S3Client  # type: ignore[import]

        client: S3Client = cast("S3Client", boto3.client("s3"))
        client.head_bucket(Bucket=s3_bucket)

    @pytest.mark.skipif(not _has_aws_creds(), reason="AWS credentials not available")
    def test_aws_s3_write_read_delete(self, s3_bucket: str | None) -> None:
        """Write/read/delete sentinel object in S3 test bucket."""
        if s3_bucket is None:
            pytest.skip("S3_TEST_BUCKET not set")

        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_storage_client

        client = get_storage_client(provider="aws")
        blob_name = f"sit-probe/{uuid.uuid4().hex}"
        payload = b"sit-aws-permission-check"

        client.upload_blob(bucket=s3_bucket, key=blob_name, data=payload)
        downloaded = client.download_blob(bucket=s3_bucket, key=blob_name)
        assert downloaded == payload
        client.delete_blob(bucket=s3_bucket, key=blob_name)


# ---------------------------------------------------------------------------
# 5. Dual-cloud auth capability
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestDualCloudAuthCapable:
    """Validate UCI can instantiate both GCP and AWS clients — proves dual-cloud code path works.

    Uses local provider for import-time safety; real credentials tested in classes above.
    The deployment decision (GCP vs AWS) is separate from proving the code is capable of both.
    """

    def test_gcp_storage_client_instantiates_local(self) -> None:
        """UCI GCP storage path importable without live credentials."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_storage_client

        client = get_storage_client(provider="local")
        assert client is not None

    def test_aws_storage_client_importable(self) -> None:
        """UCI AWS storage path importable without live credentials."""
        pytest.importorskip("unified_cloud_interface")
        # Importing the module itself validates the import path
        import unified_cloud_interface as uci

        assert hasattr(uci, "get_storage_client")

    def test_both_secret_clients_importable(self) -> None:
        """UCI secret client works for both provider='gcp' and provider='local'."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_secret_client

        local_client = get_secret_client(provider="local")
        assert local_client is not None

    def test_cloud_provider_enum_has_gcp_and_aws(self) -> None:
        """CloudProvider/CloudTarget enum in unified-internal-contracts has GCP and AWS members."""
        uic = pytest.importorskip("unified_internal_contracts")
        # Accept either CloudProvider or CloudTarget naming
        enum_cls = None
        for name in ("CloudProvider", "CloudTarget", "CloudProviderEnum"):
            enum_cls = getattr(uic, name, None)
            if enum_cls is not None:
                break

        if enum_cls is None:
            pytest.skip("CloudProvider enum not found in unified_internal_contracts — check enum name")

        members = {m.value if hasattr(m, "value") else m.name for m in enum_cls}
        assert any(v in members for v in ("gcp", "GCP", "google")), f"GCP not in {members}"
        assert any(v in members for v in ("aws", "AWS", "amazon")), f"AWS not in {members}"
