"""Unit tests for AuditResolutionAgent, AuditResult, and AuditReport."""

from __future__ import annotations

from pathlib import Path

import pytest

from system_integration_tests.audit.agent import (
    AuditFinding,
    AuditReport,
    AuditResolutionAgent,
    AuditResult,
    AuditSeverity,
    AuditStatus,
)


class TestAuditStatus:
    def test_enum_values(self) -> None:
        assert AuditStatus.PASS.value == "PASS"
        assert AuditStatus.FAIL.value == "FAIL"
        assert AuditStatus.WARN.value == "WARN"
        assert AuditStatus.SKIP.value == "SKIP"


class TestAuditSeverity:
    def test_enum_values(self) -> None:
        assert AuditSeverity.CRITICAL.value == "CRITICAL"
        assert AuditSeverity.HIGH.value == "HIGH"
        assert AuditSeverity.MEDIUM.value == "MEDIUM"
        assert AuditSeverity.LOW.value == "LOW"
        assert AuditSeverity.INFO.value == "INFO"


class TestAuditFinding:
    def test_creation(self) -> None:
        finding = AuditFinding(
            message="test finding",
            severity=AuditSeverity.HIGH,
            file_path="foo.py",
            line_number=42,
            evidence="some evidence",
        )
        assert finding.message == "test finding"
        assert finding.severity == AuditSeverity.HIGH
        assert finding.file_path == "foo.py"
        assert finding.line_number == 42
        assert finding.evidence == "some evidence"

    def test_immutable(self) -> None:
        finding = AuditFinding(message="test", severity=AuditSeverity.LOW)
        with pytest.raises(AttributeError):
            finding.message = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        finding = AuditFinding(message="test", severity=AuditSeverity.LOW)
        assert finding.file_path is None
        assert finding.line_number is None
        assert finding.evidence is None


class TestAuditResult:
    def test_passed_property(self) -> None:
        result = AuditResult(repo_name="r", section="s", status=AuditStatus.PASS)
        assert result.passed is True
        assert result.failed is False

    def test_failed_property(self) -> None:
        result = AuditResult(repo_name="r", section="s", status=AuditStatus.FAIL)
        assert result.passed is False
        assert result.failed is True

    def test_warn_is_not_passed_or_failed(self) -> None:
        result = AuditResult(repo_name="r", section="s", status=AuditStatus.WARN)
        assert result.passed is False
        assert result.failed is False

    def test_findings_default_empty(self) -> None:
        result = AuditResult(repo_name="r", section="s", status=AuditStatus.PASS)
        assert result.findings == []


class TestAuditReport:
    def _make_result(self, status: AuditStatus, repo: str = "repo", section: str = "sec") -> AuditResult:
        return AuditResult(repo_name=repo, section=section, status=status)

    def test_grade_pass(self) -> None:
        report = AuditReport(results=[self._make_result(AuditStatus.PASS)])
        assert report.grade == AuditStatus.PASS

    def test_grade_fail(self) -> None:
        report = AuditReport(
            results=[
                self._make_result(AuditStatus.PASS),
                self._make_result(AuditStatus.FAIL),
            ]
        )
        assert report.grade == AuditStatus.FAIL

    def test_grade_warn(self) -> None:
        report = AuditReport(
            results=[
                self._make_result(AuditStatus.PASS),
                self._make_result(AuditStatus.WARN),
            ]
        )
        assert report.grade == AuditStatus.WARN

    def test_fail_overrides_warn(self) -> None:
        report = AuditReport(
            results=[
                self._make_result(AuditStatus.WARN),
                self._make_result(AuditStatus.FAIL),
            ]
        )
        assert report.grade == AuditStatus.FAIL

    def test_counts(self) -> None:
        report = AuditReport(
            results=[
                self._make_result(AuditStatus.PASS),
                self._make_result(AuditStatus.PASS),
                self._make_result(AuditStatus.FAIL),
                self._make_result(AuditStatus.WARN),
                self._make_result(AuditStatus.SKIP),
            ]
        )
        assert report.total_checks == 5
        assert report.pass_count == 2
        assert report.fail_count == 1
        assert report.warn_count == 1
        assert report.skip_count == 1

    def test_failures(self) -> None:
        report = AuditReport(
            results=[
                self._make_result(AuditStatus.PASS),
                self._make_result(AuditStatus.FAIL, repo="bad"),
            ]
        )
        failures = report.failures()
        assert len(failures) == 1
        assert failures[0].repo_name == "bad"

    def test_warnings(self) -> None:
        report = AuditReport(
            results=[
                self._make_result(AuditStatus.WARN, repo="risky"),
                self._make_result(AuditStatus.PASS),
            ]
        )
        warnings = report.warnings()
        assert len(warnings) == 1
        assert warnings[0].repo_name == "risky"

    def test_results_for_repo(self) -> None:
        report = AuditReport(
            results=[
                self._make_result(AuditStatus.PASS, repo="a", section="s1"),
                self._make_result(AuditStatus.FAIL, repo="a", section="s2"),
                self._make_result(AuditStatus.PASS, repo="b", section="s1"),
            ]
        )
        results_a = report.results_for_repo("a")
        assert len(results_a) == 2

    def test_results_for_section(self) -> None:
        report = AuditReport(
            results=[
                self._make_result(AuditStatus.PASS, repo="a", section="sec1"),
                self._make_result(AuditStatus.FAIL, repo="b", section="sec1"),
                self._make_result(AuditStatus.PASS, repo="a", section="sec2"),
            ]
        )
        results_s1 = report.results_for_section("sec1")
        assert len(results_s1) == 2

    def test_empty_report_passes(self) -> None:
        report = AuditReport()
        assert report.grade == AuditStatus.PASS
        assert report.total_checks == 0


class TestAuditResolutionAgent:
    def test_init_with_valid_dir(self, tmp_path: Path) -> None:
        agent = AuditResolutionAgent(workspace_root=str(tmp_path))
        assert agent.workspace_root == tmp_path

    def test_init_with_invalid_dir(self) -> None:
        with pytest.raises(ValueError, match="does not exist"):
            AuditResolutionAgent(workspace_root="/nonexistent/path")

    def test_register_check(self, tmp_path: Path) -> None:
        agent = AuditResolutionAgent(workspace_root=str(tmp_path))
        assert agent.registered_checks == []

        def dummy_check(repo_path: Path, ws_root: Path) -> AuditResult:
            return AuditResult(repo_name="", section="", status=AuditStatus.PASS)

        agent.register_check("test_section", dummy_check)
        assert agent.registered_checks == ["test_section"]

    def test_run_audit(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo = workspace / "my-repo"
        repo.mkdir()

        agent = AuditResolutionAgent(workspace_root=str(workspace))

        def passing_check(repo_path: Path, ws_root: Path) -> AuditResult:
            return AuditResult(repo_name="", section="", status=AuditStatus.PASS, summary="ok")

        agent.register_check("check_a", passing_check)
        results = agent.run_audit(str(repo))

        assert len(results) == 1
        assert results[0].repo_name == "my-repo"
        assert results[0].section == "check_a"
        assert results[0].passed

    def test_run_audit_invalid_path(self, tmp_path: Path) -> None:
        agent = AuditResolutionAgent(workspace_root=str(tmp_path))
        with pytest.raises(ValueError, match="does not exist"):
            agent.run_audit("/nonexistent/repo")

    def test_generate_report(self, tmp_path: Path) -> None:
        agent = AuditResolutionAgent(workspace_root=str(tmp_path))
        results = [
            AuditResult(repo_name="a", section="s", status=AuditStatus.PASS),
            AuditResult(repo_name="b", section="s", status=AuditStatus.FAIL),
        ]
        report = agent.generate_report(results)
        assert report.grade == AuditStatus.FAIL
        assert report.workspace_root == str(tmp_path)
        assert report.total_checks == 2

    def test_multiple_checks_ordered(self, tmp_path: Path) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo = workspace / "repo"
        repo.mkdir()

        agent = AuditResolutionAgent(workspace_root=str(workspace))

        sections_called: list[str] = []

        def make_check(name: str):  # noqa: ANN202
            def check_fn(repo_path: Path, ws_root: Path) -> AuditResult:
                sections_called.append(name)
                return AuditResult(repo_name="", section="", status=AuditStatus.PASS)

            return check_fn

        agent.register_check("z_last", make_check("z_last"))
        agent.register_check("a_first", make_check("a_first"))

        agent.run_audit(str(repo))
        # Checks run in sorted order
        assert sections_called == ["a_first", "z_last"]
