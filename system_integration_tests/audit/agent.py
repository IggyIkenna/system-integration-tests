"""Audit resolution agent -- runs section checks and produces typed reports.

Usage::

    from system_integration_tests.audit.agent import AuditResolutionAgent

    agent = AuditResolutionAgent(workspace_root="/path/to/workspace")
    result = agent.run_audit("/path/to/repo")
    report = agent.generate_report([result])
    print(report.grade)  # PASS | FAIL | WARN
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditStatus(Enum):
    """Outcome status for a single check."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


class AuditSeverity(Enum):
    """Severity level for audit findings."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """A single finding within an audit check."""

    message: str
    severity: AuditSeverity
    file_path: str | None = None
    line_number: int | None = None
    evidence: str | None = None


CheckFunction = Callable[[Path, Path], "AuditResult"]


@dataclass(slots=True)
class AuditResult:
    """Result of a single section check against a single repo."""

    repo_name: str
    section: str
    status: AuditStatus
    findings: list[AuditFinding] = field(default_factory=list)
    summary: str = ""
    duration_seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == AuditStatus.PASS

    @property
    def failed(self) -> bool:
        return self.status == AuditStatus.FAIL


@dataclass(slots=True)
class AuditReport:
    """Aggregated audit report across all repos and sections."""

    results: list[AuditResult] = field(default_factory=list)
    workspace_root: str = ""

    @property
    def grade(self) -> AuditStatus:
        """Overall grade: FAIL if any FAIL, WARN if any WARN, else PASS."""
        statuses = {r.status for r in self.results}
        if AuditStatus.FAIL in statuses:
            return AuditStatus.FAIL
        if AuditStatus.WARN in statuses:
            return AuditStatus.WARN
        return AuditStatus.PASS

    @property
    def total_checks(self) -> int:
        return len(self.results)

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.status == AuditStatus.PASS)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if r.status == AuditStatus.FAIL)

    @property
    def warn_count(self) -> int:
        return sum(1 for r in self.results if r.status == AuditStatus.WARN)

    @property
    def skip_count(self) -> int:
        return sum(1 for r in self.results if r.status == AuditStatus.SKIP)

    def failures(self) -> list[AuditResult]:
        """Return only FAIL results."""
        return [r for r in self.results if r.status == AuditStatus.FAIL]

    def warnings(self) -> list[AuditResult]:
        """Return only WARN results."""
        return [r for r in self.results if r.status == AuditStatus.WARN]

    def results_for_repo(self, repo_name: str) -> list[AuditResult]:
        """Return all results for a specific repo."""
        return [r for r in self.results if r.repo_name == repo_name]

    def results_for_section(self, section: str) -> list[AuditResult]:
        """Return all results for a specific section."""
        return [r for r in self.results if r.section == section]


class AuditResolutionAgent:
    """Runs structured audit checks against repos and generates reports.

    Args:
        workspace_root: Absolute path to the workspace root directory.
    """

    def __init__(self, workspace_root: str) -> None:
        self._workspace_root = Path(workspace_root)
        if not self._workspace_root.is_dir():
            raise ValueError(f"Workspace root does not exist: {workspace_root}")
        self._check_registry: dict[str, CheckFunction] = {}

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def registered_checks(self) -> list[str]:
        return sorted(self._check_registry)

    def register_check(self, section: str, check_fn: CheckFunction) -> None:
        """Register a check function for a named section.

        Args:
            section: Section identifier (e.g. "code_quality", "security").
            check_fn: Function that accepts (repo_path: Path, workspace_root: Path)
                      and returns an AuditResult.
        """
        self._check_registry[section] = check_fn
        logger.debug("Registered audit check: %s", section)

    def run_audit(self, repo_path: str) -> list[AuditResult]:
        """Run all registered checks against a single repo.

        Args:
            repo_path: Absolute path to the repo directory.

        Returns:
            List of AuditResult, one per registered section check.
        """
        path = Path(repo_path)
        if not path.is_dir():
            raise ValueError(f"Repo path does not exist: {repo_path}")

        repo_name = path.name
        results: list[AuditResult] = []

        for section, check_fn in sorted(self._check_registry.items()):
            logger.info("Running check %s on %s", section, repo_name)
            result = check_fn(path, self._workspace_root)
            result.repo_name = repo_name
            result.section = section
            results.append(result)

        return results

    def generate_report(self, results: list[AuditResult]) -> AuditReport:
        """Generate an aggregated report from individual results.

        Args:
            results: Flat list of AuditResult from one or more repos.

        Returns:
            AuditReport with aggregate grade and filtering helpers.
        """
        return AuditReport(
            results=list(results),
            workspace_root=str(self._workspace_root),
        )
