"""Observability check -- health endpoints, metrics, logging.

Verifies that a repo has the required observability infrastructure:
- Health/readiness endpoint or health module
- Metrics module (Prometheus)
- Structured logging with log_event usage
- correlation_id support
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


def check_observability(repo_path: Path, workspace_root: Path) -> AuditResult:  # noqa: C901
    """Check observability infrastructure for a repo.

    Verifies:
    1. Health endpoint exists (health.py or routes/health.py)
    2. Metrics module exists (metrics.py)
    3. log_event usage from unified_trading_library.events
    4. setup_events call in main/startup

    Args:
        repo_path: Absolute path to the repo.
        workspace_root: Absolute path to workspace root.

    Returns:
        AuditResult with status and findings.
    """
    findings: list[AuditFinding] = []

    # Find the source package directory
    source_dirs = _find_source_packages(repo_path)
    if not source_dirs:
        findings.append(
            AuditFinding(
                message="No Python source package found",
                severity=AuditSeverity.HIGH,
                file_path=str(repo_path),
            )
        )
        return AuditResult(
            repo_name=repo_path.name,
            section="observability",
            status=AuditStatus.WARN,
            findings=findings,
            summary="No source package found",
        )

    has_health = False
    has_metrics = False
    has_log_event = False
    has_setup_events = False

    for src_dir in source_dirs:
        # Check for health endpoint
        health_paths = [
            src_dir / "health.py",
            src_dir / "api" / "routes" / "health.py",
            src_dir / "api" / "health.py",
            src_dir / "routes" / "health.py",
        ]
        if any(p.is_file() for p in health_paths):
            has_health = True

        # Check for metrics module
        metrics_paths = [
            src_dir / "metrics.py",
            src_dir / "observability.py",
        ]
        if any(p.is_file() for p in metrics_paths):
            has_metrics = True

        # Scan source files for log_event and setup_events usage
        for py_file in src_dir.rglob("*.py"):
            parts = py_file.relative_to(repo_path).parts
            if any(p in {".venv", "__pycache__", "node_modules"} for p in parts):
                continue
            file_text = py_file.read_text(encoding="utf-8", errors="replace")
            if "log_event" in file_text:
                has_log_event = True
            if "setup_events" in file_text:
                has_setup_events = True

    if not has_health:
        findings.append(
            AuditFinding(
                message="No health endpoint found (health.py or routes/health.py)",
                severity=AuditSeverity.HIGH,
                file_path=str(repo_path),
            )
        )

    if not has_metrics:
        findings.append(
            AuditFinding(
                message="No metrics module found (metrics.py)",
                severity=AuditSeverity.MEDIUM,
                file_path=str(repo_path),
            )
        )

    if not has_log_event:
        findings.append(
            AuditFinding(
                message="No log_event usage from unified_trading_library.events",
                severity=AuditSeverity.MEDIUM,
                file_path=str(repo_path),
            )
        )

    if not has_setup_events:
        findings.append(
            AuditFinding(
                message="No setup_events call found in service startup",
                severity=AuditSeverity.LOW,
                file_path=str(repo_path),
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
        section="observability",
        status=status,
        findings=findings,
        summary=f"health={has_health}, metrics={has_metrics}, log_event={has_log_event}",
    )


def _find_source_packages(repo_path: Path) -> list[Path]:
    """Find Python source package directories (dirs with __init__.py at top level)."""
    exclude = {".venv", ".venv-workspace", "venv", "tests", "node_modules", "build", "dist", ".git"}
    result: list[Path] = []
    for child in repo_path.iterdir():
        if child.is_dir() and child.name not in exclude and (child / "__init__.py").is_file():
            result.append(child)
    return result
