"""Security check -- dependency vulnerabilities, secret scanning.

Checks for common security anti-patterns in Python repos:
- Hardcoded secrets / API keys in source
- os.getenv() usage (should use UnifiedCloudConfig)
- try/except ImportError fallbacks (fail loud required)
- Presence of .env files in tracked source
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from system_integration_tests.audit.agent import (
    AuditFinding,
    AuditResult,
    AuditSeverity,
    AuditStatus,
)

logger = logging.getLogger(__name__)

# Patterns that indicate hardcoded secrets
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"""(?:api_key|apikey|secret|password|token)\s*=\s*['"][A-Za-z0-9+/=]{20,}['"]""", re.IGNORECASE),
    re.compile(r"""AKIA[0-9A-Z]{16}"""),  # AWS access key
    re.compile(r"""-----BEGIN (?:RSA )?PRIVATE KEY-----"""),
]

# os.getenv() is banned -- services must use UnifiedCloudConfig
_OS_GETENV_PATTERN = re.compile(r"""os\.getenv\s*\(""")

# try/except ImportError is banned
_IMPORT_FALLBACK_PATTERN = re.compile(r"""except\s+ImportError""")


def check_security(repo_path: Path, workspace_root: Path) -> AuditResult:
    """Check security patterns in a repo.

    Scans Python source files for:
    1. Hardcoded secrets / API keys
    2. os.getenv() usage (should use UnifiedCloudConfig)
    3. try/except ImportError fallbacks
    4. .env files in the repo root

    Args:
        repo_path: Absolute path to the repo.
        workspace_root: Absolute path to workspace root.

    Returns:
        AuditResult with status and findings.
    """
    findings: list[AuditFinding] = []

    # Find Python source files (exclude .venv, tests, build)
    py_files = _find_source_files(repo_path)

    for py_file in py_files:
        rel_path = str(py_file.relative_to(repo_path))
        file_text = py_file.read_text(encoding="utf-8", errors="replace")
        lines = file_text.splitlines()

        for line_num, line in enumerate(lines, start=1):
            # Check hardcoded secrets
            for pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        AuditFinding(
                            message="Potential hardcoded secret detected",
                            severity=AuditSeverity.CRITICAL,
                            file_path=rel_path,
                            line_number=line_num,
                            evidence=line.strip()[:80],
                        )
                    )

            # Check os.getenv() (allow config-bootstrap exceptions)
            if _OS_GETENV_PATTERN.search(line) and "# config-bootstrap:" not in line:
                findings.append(
                    AuditFinding(
                        message="os.getenv() usage -- use UnifiedCloudConfig instead",
                        severity=AuditSeverity.HIGH,
                        file_path=rel_path,
                        line_number=line_num,
                        evidence=line.strip()[:80],
                    )
                )

            # Check try/except ImportError
            if _IMPORT_FALLBACK_PATTERN.search(line):
                findings.append(
                    AuditFinding(
                        message="try/except ImportError -- imports must fail loud",
                        severity=AuditSeverity.MEDIUM,
                        file_path=rel_path,
                        line_number=line_num,
                        evidence=line.strip()[:80],
                    )
                )

    # Check for .env file in repo root
    env_file = repo_path / ".env"
    if env_file.is_file():
        findings.append(
            AuditFinding(
                message=".env file found in repo root -- should be in .gitignore",
                severity=AuditSeverity.MEDIUM,
                file_path=".env",
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
        section="security",
        status=status,
        findings=findings,
        summary=f"Scanned {len(py_files)} files, {len(findings)} finding(s)",
    )


def _find_source_files(repo_path: Path) -> list[Path]:
    """Find Python source files, excluding venvs, tests, and build dirs."""
    exclude_dirs = {".venv", ".venv-workspace", "venv", "node_modules", "build", "dist", ".git", "__pycache__"}
    result: list[Path] = []

    for py_file in repo_path.rglob("*.py"):
        parts = py_file.relative_to(repo_path).parts
        if any(part in exclude_dirs for part in parts):
            continue
        # Skip test files for security scan
        if any(part.startswith("test") for part in parts):
            continue
        result.append(py_file)

    return result
