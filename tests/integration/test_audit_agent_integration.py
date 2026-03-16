"""Integration tests for the audit agent running against a realistic repo structure."""

from __future__ import annotations

import json
from pathlib import Path

from system_integration_tests.audit.agent import (
    AuditResolutionAgent,
    AuditStatus,
)
from system_integration_tests.audit.checks.check_code_quality import check_code_quality
from system_integration_tests.audit.checks.check_observability import check_observability
from system_integration_tests.audit.checks.check_security import check_security
from system_integration_tests.audit.checks.check_testing import check_testing
from system_integration_tests.audit.repo_manager import discover_repos, get_repo_context


def _build_workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Build a realistic workspace with manifest and a sample service repo."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create manifest
    manifest = {
        "repos": {
            "good-service": {
                "tier": "T3",
                "ci_status": "VALIDATED",
                "dependencies": ["unified-trading-library"],
            },
            "bad-service": {
                "tier": "T3",
                "ci_status": "FAILING",
                "dependencies": [],
            },
        }
    }
    manifest_path = workspace / "workspace-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # Build good-service
    good = workspace / "good-service"
    good.mkdir()
    scripts = good / "scripts"
    scripts.mkdir()
    (scripts / "quality-gates.sh").write_text("\n".join([f"# line {i}" for i in range(80)]))
    (good / "pyproject.toml").write_text('[tool.basedpyright]\n[tool.ruff]\n[project]\nname = "good-service"\n')
    (good / "QUALITY_GATE_BYPASS_AUDIT.md").write_text("# Bypass\n")
    (good / "coverage.xml").write_text("<coverage/>\n")

    # Tests
    tests = good / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "conftest.py").write_text("# conftest\n")
    unit = tests / "unit"
    unit.mkdir()
    (unit / "__init__.py").write_text("")
    (unit / "test_main.py").write_text("def test_main(): assert True\n")
    integration = tests / "integration"
    integration.mkdir()
    (integration / "__init__.py").write_text("")
    (integration / "test_int.py").write_text("def test_int(): assert True\n")

    # Source package
    pkg = good / "good_service"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "health.py").write_text("# health\n")
    (pkg / "metrics.py").write_text("# metrics\n")
    (pkg / "main.py").write_text(
        'from unified_events_interface import setup_events, log_event\nlog_event("SERVICE_STARTED")\nsetup_events()\n'
    )

    # Build bad-service (minimal, missing most things)
    bad = workspace / "bad-service"
    bad.mkdir()

    return workspace, manifest_path


class TestAuditAgentEndToEnd:
    """End-to-end test: discover repos, run all checks, generate report."""

    def test_full_audit_flow(self, tmp_path: Path) -> None:
        workspace, manifest_path = _build_workspace(tmp_path)

        # Discover repos
        repos = discover_repos(str(manifest_path))
        assert len(repos) == 2

        good_ctx = get_repo_context("good-service", repos)
        assert good_ctx["tier"] == "T3"

        # Create agent and register all checks
        agent = AuditResolutionAgent(workspace_root=str(workspace))
        agent.register_check("code_quality", check_code_quality)
        agent.register_check("security", check_security)
        agent.register_check("testing", check_testing)
        agent.register_check("observability", check_observability)

        # Run audit on good-service
        good_results = agent.run_audit(good_ctx["path"])
        assert len(good_results) == 4
        for r in good_results:
            assert r.repo_name == "good-service"
            assert r.status in (AuditStatus.PASS, AuditStatus.WARN)

        # Run audit on bad-service
        bad_ctx = get_repo_context("bad-service", repos)
        bad_results = agent.run_audit(bad_ctx["path"])
        assert len(bad_results) == 4
        # bad-service should fail at least code_quality and testing
        statuses = {r.section: r.status for r in bad_results}
        assert statuses["code_quality"] == AuditStatus.FAIL
        assert statuses["testing"] == AuditStatus.FAIL

        # Generate combined report
        all_results = good_results + bad_results
        report = agent.generate_report(all_results)
        assert report.grade == AuditStatus.FAIL  # bad-service brings it down
        assert report.total_checks == 8
        assert report.fail_count >= 2
        assert len(report.results_for_repo("good-service")) == 4
        assert len(report.results_for_repo("bad-service")) == 4

    def test_repo_context_has_path(self, tmp_path: Path) -> None:
        workspace, manifest_path = _build_workspace(tmp_path)
        repos = discover_repos(str(manifest_path))
        good_ctx = get_repo_context("good-service", repos)
        assert Path(good_ctx["path"]).is_dir()
