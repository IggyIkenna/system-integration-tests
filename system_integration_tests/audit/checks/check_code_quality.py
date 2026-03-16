"""Code quality check -- lint, type check, codex compliance.

Verifies that a repo has quality-gates.sh, pyproject.toml with basedpyright
config, and ruff configuration. Does NOT run the actual tools (that is done
by quality-gates.sh); this check validates the infrastructure is in place.
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

_REQUIRED_QG_MIN_LINES = 50


def check_code_quality(repo_path: Path, workspace_root: Path) -> AuditResult:
    """Check code quality infrastructure for a repo.

    Verifies:
    1. scripts/quality-gates.sh exists and is non-trivial (>50 lines)
    2. pyproject.toml exists with [tool.basedpyright] section
    3. ruff config present (in pyproject.toml or ruff.toml)

    Args:
        repo_path: Absolute path to the repo.
        workspace_root: Absolute path to workspace root.

    Returns:
        AuditResult with status and findings.
    """
    findings: list[AuditFinding] = []

    # 1. Check quality-gates.sh
    qg_path = repo_path / "scripts" / "quality-gates.sh"
    if not qg_path.is_file():
        findings.append(
            AuditFinding(
                message="scripts/quality-gates.sh not found",
                severity=AuditSeverity.CRITICAL,
                file_path=str(qg_path),
            )
        )
    else:
        line_count = len(qg_path.read_text(encoding="utf-8").splitlines())
        if line_count < _REQUIRED_QG_MIN_LINES:
            findings.append(
                AuditFinding(
                    message=f"quality-gates.sh is only {line_count} lines (minimum {_REQUIRED_QG_MIN_LINES})",
                    severity=AuditSeverity.HIGH,
                    file_path=str(qg_path),
                    evidence=f"line_count={line_count}",
                )
            )

    # 2. Check pyproject.toml for basedpyright
    pyproject_path = repo_path / "pyproject.toml"
    if not pyproject_path.is_file():
        findings.append(
            AuditFinding(
                message="pyproject.toml not found",
                severity=AuditSeverity.CRITICAL,
                file_path=str(pyproject_path),
            )
        )
    else:
        pyproject_text = pyproject_path.read_text(encoding="utf-8")
        if "[tool.basedpyright]" not in pyproject_text:
            findings.append(
                AuditFinding(
                    message="[tool.basedpyright] section missing from pyproject.toml",
                    severity=AuditSeverity.HIGH,
                    file_path=str(pyproject_path),
                )
            )

        if "[tool.ruff" not in pyproject_text:
            # Check for standalone ruff.toml
            ruff_toml = repo_path / "ruff.toml"
            if not ruff_toml.is_file():
                findings.append(
                    AuditFinding(
                        message="No ruff config found (not in pyproject.toml or ruff.toml)",
                        severity=AuditSeverity.MEDIUM,
                        file_path=str(repo_path),
                    )
                )

    # 3. Check for QUALITY_GATE_BYPASS_AUDIT.md
    bypass_audit = repo_path / "QUALITY_GATE_BYPASS_AUDIT.md"
    if not bypass_audit.is_file():
        findings.append(
            AuditFinding(
                message="QUALITY_GATE_BYPASS_AUDIT.md not found",
                severity=AuditSeverity.LOW,
                file_path=str(bypass_audit),
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
        section="code_quality",
        status=status,
        findings=findings,
        summary=f"{len(findings)} finding(s): {critical_count} critical, {high_count} high",
    )
