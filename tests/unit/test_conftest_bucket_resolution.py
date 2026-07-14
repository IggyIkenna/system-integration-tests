"""Unit tests for tests/conftest.py's dependencies.yaml-driven bucket enumeration.

Regression coverage for Finding #12 (bucket_estate_consolidation_to_sub100_2026_07_13.md,
Deferred #12): `_resolve_bucket()` only ever substituted `{asset_group_lower}` /
`{project_id}` / `{domain}`, never `{category_lower}` — but dependencies.yaml's real
`bucket_template` fields use `{category_lower}` almost everywhere. Any such template
came back with the literal, un-substituted `{category_lower}` placeholder still in the
"bucket name" — not a valid GCS bucket name and not something
`test_all_required_gcs_buckets_accessible` could ever pass against real GCP creds.
"""

import pytest

from tests.conftest import _enumerate_service_buckets, _resolve_bucket

pytestmark = pytest.mark.code_test


def test_resolve_bucket_substitutes_category_lower() -> None:
    """{category_lower} must resolve to the lowered category, not survive as a literal."""
    resolved = _resolve_bucket(
        "instruments-store-{category_lower}-{project_id}",
        "CEFI",
        "test-project-123",
    )

    assert resolved == "instruments-store-cefi-test-project-123"
    assert "{category_lower}" not in resolved


def test_resolve_bucket_still_substitutes_asset_group_lower() -> None:
    """Pre-existing {asset_group_lower} templates must keep resolving (no regression)."""
    resolved = _resolve_bucket(
        "features-onchain-{asset_group_lower}-{project_id}",
        "DEFI",
        "test-project-123",
    )

    assert resolved == "features-onchain-defi-test-project-123"


def test_enumerate_service_buckets_no_literal_placeholders() -> None:
    """A dependencies.yaml service whose bucket_template uses {category_lower} must
    enumerate to real bucket names — never a name containing an un-substituted brace.
    """
    deps = {
        "services": {
            "instruments-service": {
                "outputs": [
                    {"bucket_template": "instruments-store-{category_lower}-{project_id}"},
                ],
            },
        },
    }
    bucket_cfg = {
        "shared_bucket_services": [],
        "service_categories": {
            "restricted_categories": {},
            "default_categories": ["cefi", "tradfi", "defi"],
        },
    }

    buckets = _enumerate_service_buckets(deps, bucket_cfg, "test-project-123")

    assert buckets == {
        "instruments-store-cefi-test-project-123",
        "instruments-store-tradfi-test-project-123",
        "instruments-store-defi-test-project-123",
    }
    assert not any("{" in b or "}" in b for b in buckets)
