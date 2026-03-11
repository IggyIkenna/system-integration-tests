#!/usr/bin/env python3
"""check-sit-readiness.py — Advisory readiness prerequisite check before SIT runs.

Reads per-repo YAML checklists from unified-trading-codex/10-audit/repos/ for
repos in the SIT scope. Flags repos where dr3_feature_env.status != "pass".
Outputs a markdown table to GitHub Step Summary.

This is ADVISORY only — SIT may still run even if some repos haven't reached DR3.
Exit code is always 0 (advisory, not blocking).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# yaml may not be installed in all environments — use a lightweight fallback
try:
    import yaml  # type: ignore[import-untyped]

    def _load_yaml(path: Path) -> dict[str, object]:
        with open(path) as f:
            result = yaml.safe_load(f)
        if not isinstance(result, dict):
            return {}
        return result

except ImportError:

    def _process_yaml_line(
        line: str,
        data: dict[str, object],
        current_section: dict[str, object] | None,
        current_key: str | None,
    ) -> tuple[dict[str, object] | None, str | None]:
        """Process one line from the minimal YAML parser. Returns (section, key)."""
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        kv = re.match(r"^([a-zA-Z0-9_]+)\s*:\s*(.*)", stripped)
        if not kv:
            return current_section, current_key
        key, value = kv.group(1), kv.group(2).strip()
        if indent == 0:
            if value:
                data[key] = value
                return None, None
            else:
                section: dict[str, object] = {}
                data[key] = section
                return section, key
        elif indent > 0 and current_section is not None and value:
            current_section[key] = value
        return current_section, current_key

    def _load_yaml(path: Path) -> dict[str, object]:  # type: ignore[misc]
        """Minimal YAML parser — reads only top-level key: value lines."""
        data: dict[str, object] = {}
        current_section: dict[str, object] | None = None
        current_key: str | None = None
        with open(path) as f:
            for raw_line in f:
                line = raw_line.rstrip()
                if not line or line.startswith("#"):
                    continue
                current_section, current_key = _process_yaml_line(line, data, current_section, current_key)
        return data


# SIT-scope repos: services and APIs that SIT exercises.
# This list mirrors the repos checked by smoke-test-gate.yml.
SIT_SCOPE_REPOS = [
    "execution-service",
    "strategy-service",
    "market-data-processing-service",
    "market-tick-data-service",
    "market-data-api",
    "instruments-service",
    "alerting-service",
    "risk-and-exposure-service",
    "position-balance-monitor-service",
    "pnl-attribution-service",
    "ml-inference-service",
    "ml-training-service",
    "features-delta-one-service",
    "features-volatility-service",
    "features-cross-instrument-service",
    "features-onchain-service",
    "features-sports-service",
    "features-calendar-service",
    "deployment-service",
    "system-integration-tests",
]


def _get_dr3_status(checklist: dict[str, object]) -> str:
    """Extract dr3_feature_env.status from a checklist dict."""
    dr3 = checklist.get("dr3_feature_env")
    if isinstance(dr3, dict):
        status = dr3.get("status")
        if isinstance(status, str):
            return status
    # Also handle flat key pattern
    status = checklist.get("dr3_feature_env_status")
    if isinstance(status, str):
        return status
    return "unknown"


def _get_cr_status(checklist: dict[str, object]) -> str:
    """Extract highest declared code-readiness stage."""
    for stage in ("cr5", "cr4", "cr3", "cr2", "cr1", "cr0"):
        val = checklist.get(stage)
        if isinstance(val, dict):
            s = val.get("status")
            if isinstance(s, str) and s == "pass":
                return stage.upper()
    return "CR0"


def _check_repo(repo: str, codex_path: Path) -> tuple[str, str, str, str]:
    """Return (repo, dr3_status, cr_stage, notes) for one repo."""
    checklist_file = codex_path / f"{repo}.yaml"
    if not checklist_file.exists():
        return (repo, "missing", "—", "No checklist file in codex")
    try:
        checklist = _load_yaml(checklist_file)
    except Exception as exc:
        return (repo, "error", "—", f"Parse error: {exc}")
    dr3 = _get_dr3_status(checklist)
    cr = _get_cr_status(checklist)
    notes = "" if dr3 == "pass" else "DR3 not yet confirmed — repo may not be feature-env deployed"
    return (repo, dr3, cr, notes)


def _build_table(rows: list[tuple[str, str, str, str]], warn_count: int) -> str:
    """Build the markdown summary table string."""
    lines: list[str] = [
        "## SIT Readiness Prerequisites (Advisory)",
        "",
        f"> **{warn_count} repo(s)** have not confirmed DR3 (feature env deployed). "
        "SIT proceeds regardless — this is informational only.",
        "",
        "| Repo | DR3 Status | Highest CR | Notes |",
        "| ---- | ---------- | ---------- | ----- |",
    ]
    for repo, dr3, cr, notes in rows:
        icon = "✅" if dr3 == "pass" else ("⚠️" if dr3 != "missing" else "❓")
        lines.append(f"| `{repo}` | {icon} {dr3} | {cr} | {notes} |")
    return "\n".join(lines)


def _write_step_summary(table: str) -> None:
    """Write table to GITHUB_STEP_SUMMARY if available."""
    step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not step_summary_path:
        return
    try:
        with open(step_summary_path, "a") as f:
            f.write(table + "\n")
    except OSError as exc:
        print(f"Warning: could not write to GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Advisory SIT readiness check")
    parser.add_argument(
        "--codex-path",
        default="../unified-trading-codex/10-audit/repos",
        help="Path to codex 10-audit/repos/ directory",
    )
    parser.add_argument(
        "--repos",
        nargs="*",
        default=None,
        help="Specific repos to check (default: SIT scope list)",
    )
    args = parser.parse_args()

    codex_path = Path(args.codex_path)
    repos_to_check: list[str] = args.repos if args.repos else SIT_SCOPE_REPOS

    rows = [_check_repo(repo, codex_path) for repo in repos_to_check]
    warn_count = sum(1 for _, dr3, _, _ in rows if dr3 != "pass")

    table = _build_table(rows, warn_count)
    print(table)
    _write_step_summary(table)

    if warn_count > 0:
        print(
            f"\n::warning::{warn_count} SIT-scope repo(s) have not confirmed DR3. "
            "SIT is ADVISORY — runs proceed regardless.",
            file=sys.stderr,
        )

    # Always exit 0 — this is advisory only
    return 0


if __name__ == "__main__":
    sys.exit(main())
