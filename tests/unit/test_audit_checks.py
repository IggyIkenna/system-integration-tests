"""Unit tests for the audit section check functions."""

from __future__ import annotations

from pathlib import Path

from system_integration_tests.audit.agent import AuditSeverity, AuditStatus
from system_integration_tests.audit.checks.check_code_quality import check_code_quality
from system_integration_tests.audit.checks.check_observability import check_observability
from system_integration_tests.audit.checks.check_security import check_security
from system_integration_tests.audit.checks.check_testing import check_testing


def _make_repo(tmp_path: Path, name: str = "test-repo") -> Path:
    """Create a minimal valid repo directory."""
    repo = tmp_path / name
    repo.mkdir()
    return repo


def _make_full_repo(tmp_path: Path) -> Path:
    """Create a repo with all expected infrastructure."""
    repo = _make_repo(tmp_path, "full-repo")

    # quality-gates.sh (>50 lines)
    scripts = repo / "scripts"
    scripts.mkdir()
    qg = scripts / "quality-gates.sh"
    qg.write_text("\n".join([f"# line {i}" for i in range(80)]))

    # pyproject.toml with basedpyright and ruff
    pyproject = repo / "pyproject.toml"
    pyproject.write_text('[tool.basedpyright]\n[tool.ruff]\n[project]\nname = "test"\n')

    # QUALITY_GATE_BYPASS_AUDIT.md
    (repo / "QUALITY_GATE_BYPASS_AUDIT.md").write_text("# Bypass audit\n")

    # tests/ with conftest, unit, integration
    tests = repo / "tests"
    tests.mkdir()
    (tests / "conftest.py").write_text("# conftest\n")
    (tests / "__init__.py").write_text("")
    unit_dir = tests / "unit"
    unit_dir.mkdir()
    (unit_dir / "__init__.py").write_text("")
    (unit_dir / "test_something.py").write_text("def test_pass(): assert True\n")
    int_dir = tests / "integration"
    int_dir.mkdir()
    (int_dir / "__init__.py").write_text("")
    (int_dir / "test_int.py").write_text("def test_int(): assert True\n")

    # coverage.xml
    (repo / "coverage.xml").write_text("<coverage/>\n")

    # Source package with health, metrics, log_event
    pkg = repo / "full_repo"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "health.py").write_text("# health endpoint\n")
    (pkg / "metrics.py").write_text("# prometheus metrics\n")
    (pkg / "main.py").write_text(
        'from unified_trading_library.events_interface import setup_events, log_event\nlog_event("STARTED")\nsetup_events()\n'
    )

    return repo


class TestCheckCodeQuality:
    def test_pass_with_full_repo(self, tmp_path: Path) -> None:
        repo = _make_full_repo(tmp_path)
        result = check_code_quality(repo, tmp_path)
        assert result.status == AuditStatus.PASS

    def test_fail_no_qg_script(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / "pyproject.toml").write_text("[tool.basedpyright]\n[tool.ruff]\n")
        result = check_code_quality(repo, tmp_path)
        critical_findings = [f for f in result.findings if f.severity == AuditSeverity.CRITICAL]
        assert len(critical_findings) >= 1
        assert result.status == AuditStatus.FAIL

    def test_warn_short_qg_script(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        scripts = repo / "scripts"
        scripts.mkdir()
        (scripts / "quality-gates.sh").write_text("#!/bin/bash\necho hi\n")
        (repo / "pyproject.toml").write_text("[tool.basedpyright]\n[tool.ruff]\n")
        result = check_code_quality(repo, tmp_path)
        assert result.status == AuditStatus.WARN

    def test_no_pyproject(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        result = check_code_quality(repo, tmp_path)
        assert result.status == AuditStatus.FAIL


class TestCheckSecurity:
    def test_clean_repo(self, tmp_path: Path) -> None:
        repo = _make_full_repo(tmp_path)
        result = check_security(repo, tmp_path)
        # The full repo uses log_event but not os.getenv, so should be clean
        assert result.status in (AuditStatus.PASS, AuditStatus.WARN)

    def test_detects_os_getenv(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pkg = repo / "my_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "config.py").write_text('import os\nval = os.getenv("MY_VAR")\n')
        result = check_security(repo, tmp_path)
        getenv_findings = [f for f in result.findings if "os.getenv" in f.message]
        assert len(getenv_findings) == 1

    def test_allows_config_bootstrap_exception(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pkg = repo / "my_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "factory.py").write_text(
            'import os\nval = os.getenv("CLOUD_PROVIDER", "local")  # config-bootstrap: factory\n'
        )
        result = check_security(repo, tmp_path)
        getenv_findings = [f for f in result.findings if "os.getenv" in f.message]
        assert len(getenv_findings) == 0

    def test_detects_import_fallback(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pkg = repo / "my_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "bad.py").write_text("try:\n    import foo\nexcept ImportError:\n    foo = None\n")
        result = check_security(repo, tmp_path)
        fallback_findings = [f for f in result.findings if "ImportError" in f.message]
        assert len(fallback_findings) == 1

    def test_detects_env_file(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        (repo / ".env").write_text("SECRET=abc123\n")
        result = check_security(repo, tmp_path)
        env_findings = [f for f in result.findings if ".env file" in f.message]
        assert len(env_findings) == 1

    def test_empty_repo(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        result = check_security(repo, tmp_path)
        assert result.status == AuditStatus.PASS


class TestCheckTesting:
    def test_pass_with_full_repo(self, tmp_path: Path) -> None:
        repo = _make_full_repo(tmp_path)
        result = check_testing(repo, tmp_path)
        assert result.status == AuditStatus.PASS

    def test_fail_no_tests_dir(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        result = check_testing(repo, tmp_path)
        assert result.status == AuditStatus.FAIL

    def test_warn_no_test_files(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        tests = repo / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("")
        result = check_testing(repo, tmp_path)
        assert result.status == AuditStatus.WARN

    def test_missing_integration_dir(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        tests = repo / "tests"
        tests.mkdir()
        (tests / "conftest.py").write_text("")
        unit = tests / "unit"
        unit.mkdir()
        (unit / "test_foo.py").write_text("def test_foo(): pass\n")
        result = check_testing(repo, tmp_path)
        integration_findings = [f for f in result.findings if "integration" in f.message.lower()]
        assert len(integration_findings) >= 1


class TestCheckObservability:
    def test_pass_with_full_repo(self, tmp_path: Path) -> None:
        repo = _make_full_repo(tmp_path)
        result = check_observability(repo, tmp_path)
        assert result.status == AuditStatus.PASS

    def test_warn_no_health(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pkg = repo / "my_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "metrics.py").write_text("# metrics\n")
        (pkg / "main.py").write_text(
            'from unified_trading_library.events_interface import log_event, setup_events\nlog_event("X")\nsetup_events()\n'
        )
        result = check_observability(repo, tmp_path)
        assert result.status == AuditStatus.WARN

    def test_warn_no_source_package(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        result = check_observability(repo, tmp_path)
        assert result.status == AuditStatus.WARN

    def test_detects_missing_metrics(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        pkg = repo / "my_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "health.py").write_text("# health\n")
        (pkg / "main.py").write_text(
            'from unified_trading_library.events_interface import log_event, setup_events\nlog_event("X")\nsetup_events()\n'
        )
        result = check_observability(repo, tmp_path)
        metrics_findings = [f for f in result.findings if "metrics" in f.message.lower()]
        assert len(metrics_findings) >= 1
