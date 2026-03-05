"""Layer 3b AWS S3 smoke tests — placeholder, skips when no AWS creds or S3_TEST_BUCKET."""

import importlib.util

import pytest


def _boto3_available() -> bool:
    """Return True if boto3 is installed."""
    return importlib.util.find_spec("boto3") is not None


def _has_aws_creds() -> bool:
    """Return True if boto3 can obtain credentials."""
    if not _boto3_available():
        return False
    import boto3  # noqa: PLC0415 — conditional import after availability check
    from botocore.exceptions import BotoCoreError, NoCredentialsError  # noqa: PLC0415

    try:
        creds = boto3.Session().get_credentials()
        return creds is not None
    except (NoCredentialsError, BotoCoreError):
        return False


@pytest.fixture(autouse=True)
def _skip_aws_without_creds(request, s3_bucket):
    """Skip AWS integration tests when no creds or S3_TEST_BUCKET."""
    if "integration" in request.keywords and (not _boto3_available() or s3_bucket is None or not _has_aws_creds()):
        pytest.skip(
            "S3_TEST_BUCKET and AWS credentials required — install boto3, set S3_TEST_BUCKET, configure credentials"
        )


@pytest.mark.integration
def test_aws_s3_smoke_placeholder(s3_bucket):
    """Placeholder: verify S3 bucket is reachable. Skips when no AWS creds."""
    import boto3  # noqa: PLC0415 — available because fixture above skips if not present

    client = boto3.client("s3")
    client.head_bucket(Bucket=s3_bucket)
