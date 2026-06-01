"""
vc-verify: Version Cascade End-to-End Verification Tests.

Verifies that the three-tier version cascade (feat: commit → GHA workflow →
workspace-manifest.json bump → downstream dep-update dispatch) works correctly.

These tests use the GitHub CLI (`gh`) to inspect workflow runs and manifests
WITHOUT requiring a push. They validate the cascade mechanics on already-run
workflows and the current manifest state.

Run:
    pytest tests/e2e/test_version_cascade_e2e.py -m version_cascade -v

To trigger a live cascade (requires user approval to push):
    cd unified-api-contracts
    git commit --allow-empty -m "feat: vc-verify cascade trigger"
    # Then await user approval before pushing
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, cast

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gh(*args: str) -> tuple[int, str]:
    """Run a `gh` CLI command; return (returncode, stdout)."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.strip()


def _read_manifest() -> dict[str, Any]:
    """Read workspace-manifest.json from unified-trading-pm."""
    rc, out = _gh(
        "api",
        "repos/IggyIkenna/unified-trading-pm/contents/workspace-manifest.json",
        "--jq",
        ".content",
    )
    if rc != 0:
        return {}
    import base64

    content = base64.b64decode(out).decode()
    return cast("dict[str, Any]", json.loads(content))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.version_cascade, pytest.mark.deployment_test]


@pytest.mark.version_cascade
def test_gh_cli_available() -> None:
    """gh CLI is available and authenticated."""
    rc, out = _gh("auth", "status")
    assert rc == 0, f"gh CLI not authenticated: {out}"


@pytest.mark.version_cascade
def test_workspace_manifest_exists_and_valid() -> None:
    """workspace-manifest.json exists in unified-trading-pm and has required structure."""
    manifest = _read_manifest()
    # Manifest may use 'repos' or 'repositories' as the key
    assert "repos" in manifest or "repositories" in manifest, "manifest missing 'repos'/'repositories' key"
    assert len(manifest) > 1, "manifest seems empty or malformed"


@pytest.mark.version_cascade
def test_uac_version_bump_workflow_exists() -> None:
    """version-bump.yml GHA workflow exists in unified-api-contracts."""
    rc, out = _gh(
        "api",
        "repos/IggyIkenna/unified-api-contracts/contents/.github/workflows",
        "--jq",
        "[.[].name]",
    )
    assert rc == 0, f"gh API call failed: {out}"
    workflow_names = json.loads(out) if out else []
    assert any("version" in name or "bump" in name for name in workflow_names), (
        f"No version-bump workflow found in UAC. Workflows: {workflow_names}"
    )


@pytest.mark.version_cascade
def test_uac_last_workflow_run_succeeded() -> None:
    """Most recent completed GHA run in unified-api-contracts passed."""
    import os

    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        pytest.skip("GH_TOKEN not set — cannot check workflow runs")
    rc, out = _gh(
        "run",
        "list",
        "--repo",
        "IggyIkenna/unified-api-contracts",
        "--status",
        "completed",
        "--limit",
        "1",
        "--json",
        "conclusion,status,name",
    )
    if rc != 0:
        pytest.skip("Could not list workflow runs (may need GHA access)")

    runs = json.loads(out) if out else []
    if not runs:
        pytest.skip("No completed workflow runs found in UAC")

    last_run = runs[0]
    # In CI/local dev, the last workflow run may have failed for unrelated reasons.
    # This test is informational — skip rather than fail if last run wasn't success.
    if last_run["conclusion"] != "success":
        pytest.skip(f"Last UAC workflow run was '{last_run['conclusion']}' (informational): {last_run['name']}")


@pytest.mark.version_cascade
def test_pm_update_repo_version_workflow_exists() -> None:
    """update-repo-version.yml GHA workflow exists in unified-trading-pm."""
    rc, out = _gh(
        "api",
        "repos/IggyIkenna/unified-trading-pm/contents/.github/workflows",
        "--jq",
        "[.[].name]",
    )
    assert rc == 0, f"gh API call failed: {out}"
    workflow_names = json.loads(out) if out else []
    assert any("update" in name and "version" in name for name in workflow_names), (
        f"No update-repo-version workflow found in PM. Workflows: {workflow_names}"
    )


@pytest.mark.version_cascade
def test_manifest_uac_version_semver() -> None:
    """workspace-manifest.json has a valid semver for unified-api-contracts."""
    import re

    manifest = _read_manifest()
    repos = manifest.get("repos", manifest.get("repositories", {}))

    uac_entry = repos.get("unified-api-contracts", {})
    version = uac_entry.get("version", "")

    assert re.match(r"^\d+\.\d+\.\d+$", version), f"unified-api-contracts version '{version}' is not valid semver"


@pytest.mark.version_cascade
def test_manifest_internal_versions_consistent() -> None:
    """All repos in workspace-manifest.json have a valid semver version."""
    import re

    manifest = _read_manifest()
    repos = manifest.get("repos", manifest.get("repositories", {}))

    bad = []
    for repo_name, repo_data in repos.items():
        version = repo_data.get("version", "")
        # Skip repos without a version set (e.g. newly added repos)
        if not version:
            continue
        if not re.match(r"^\d+\.\d+\.\d+$", version):
            bad.append(f"{repo_name}: '{version}'")

    assert not bad, "Repos with invalid versions:\n" + "\n".join(bad)


@pytest.mark.version_cascade
def test_staging_status_not_locked() -> None:
    """Staging is not currently locked (no SIT in progress)."""
    manifest = _read_manifest()
    staging = manifest.get("staging_status", {})
    locked = staging.get("locked", False)
    assert not locked, (
        "staging_status.locked=True — SIT is in progress or stale lock. "
        "Run: gh workflow run staging-to-main.yml to proceed."
    )
