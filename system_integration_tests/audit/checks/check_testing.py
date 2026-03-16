"""Testing check -- coverage, test count, integration test presence.

Verifies that a repo has adequate test infrastructure:
- tests/ directory exists with unit and integration subdirectories
- conftest.py exists
- Minimum test file count
- coverage.xml exists (from previous QG run)
"""

from __future__ import annotations

import logging
from pathlib import Path

from system_integration_tests.audit.agent import (
    AuditFinding,
    AuditResult,
    AuditSeverity,
    AuditStatus,
)

logger = logging.getLogger(__name__)

_MIN_TEST_FILES = 1


def check_testing(repo_path: Path, workspace_root: Path) -> AuditResult:
    """Check testing infrastructure for a repo.

    Verifies:
    1. tests/ directory exists
    2. At least one test file exists
    3. conftest.py exists
    4. Integration test directory exists
    5. coverage.xml from previous QG run

    Args:
        repo_path: Absolute path to the repo.
        workspace_root: Absolute path to workspace root.

    Returns:
        AuditResult with status and findings.
    """
    findings: list[AuditFinding] = []

    tests_dir = repo_path / "tests"
    if not tests_dir.is_dir():
        findings.append(
            AuditFinding(
                message="tests/ directory not found",
                severity=AuditSeverity.CRITICAL,
                file_path=str(tests_dir),
            )
        )
        return AuditResult(
            repo_name=repo_path.name,
            section="testing",
            status=AuditStatus.FAIL,
            findings=findings,
            summary="No tests/ directory",
        )

    # Count test files
    test_files = list(tests_dir.rglob("test_*.py"))
    if len(test_files) < _MIN_TEST_FILES:
        findings.append(
            AuditFinding(
                message=f"Only {len(test_files)} test file(s) found (minimum {_MIN_TEST_FILES})",
                severity=AuditSeverity.HIGH,
                file_path=str(tests_dir),
                evidence=f"test_file_count={len(test_files)}",
            )
        )

    # Check conftest.py
    conftest = tests_dir / "conftest.py"
    if not conftest.is_file():
        findings.append(
            AuditFinding(
                message="tests/conftest.py not found",
                severity=AuditSeverity.LOW,
                file_path=str(conftest),
            )
        )

    # Check for unit test subdirectory
    unit_dir = tests_dir / "unit"
    if not unit_dir.is_dir():
        # Some repos use flat test layout -- that is acceptable
        findings.append(
            AuditFinding(
                message="tests/unit/ subdirectory not found (flat layout detected)",
                severity=AuditSeverity.INFO,
                file_path=str(unit_dir),
            )
        )

    # Check for integration test subdirectory
    integration_dir = tests_dir / "integration"
    if not integration_dir.is_dir():
        findings.append(
            AuditFinding(
                message="tests/integration/ subdirectory not found",
                severity=AuditSeverity.MEDIUM,
                file_path=str(integration_dir),
            )
        )

    # Check coverage.xml
    coverage_xml = repo_path / "coverage.xml"
    if not coverage_xml.is_file():
        findings.append(
            AuditFinding(
                message="coverage.xml not found (run quality-gates.sh to generate)",
                severity=AuditSeverity.MEDIUM,
                file_path=str(coverage_xml),
            )
        )

    # Determine status
    critical_count = sum(1 for f in findings if f.severity == AuditSeverity.CRITICAL)
    high_count = sum(1 for f in findings if f.severity == AuditSeverity.HIGH)

    if critical_count > 0:
        status = AuditStatus.FAIL
    elif high_count > 0:
        status = AuditStatus.WARN
    else:
        status = AuditStatus.PASS

    return AuditResult(
        repo_name=repo_path.name,
        section="testing",
        status=status,
        findings=findings,
        summary=f"{len(test_files)} test files, {len(findings)} finding(s)",
    )
