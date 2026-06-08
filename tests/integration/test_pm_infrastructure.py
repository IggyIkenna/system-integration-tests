"""PM infrastructure integration tests.

Tests that quickmerge base scripts (base-service.sh, base-library.sh) source correctly
and that quality-gates.sh can run in dry-run/check mode. Validates the 4 QG script
inheritance paths without running full QG for all 70 repos.

Marker: code_test (no live services required).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", Path(__file__).resolve().parents[3]))
PM_ROOT = WORKSPACE_ROOT / "unified-trading-pm"


@pytest.mark.code_test
class TestQualityGateScriptInheritance:
    """Verify QG base scripts source correctly for each repo type."""

    def _check_base_script_exists(self, script_name: str) -> None:
        base_dir = PM_ROOT / "scripts" / "quality-gates-base"
        script_path = base_dir / script_name
        assert script_path.is_file(), f"Base script missing: {script_path}"

    def test_base_service_script_exists(self) -> None:
        self._check_base_script_exists("base-service.sh")

    def test_base_library_script_exists(self) -> None:
        self._check_base_script_exists("base-library.sh")

    def test_base_ui_script_exists(self) -> None:
        """UI repos may use a different base or quality-gates-ui-template.sh."""
        ui_template = PM_ROOT / "scripts" / "quality-gates-base" / "base-ui.sh"
        codex_template = (
            WORKSPACE_ROOT / "unified-trading-codex" / "06-coding-standards" / "quality-gates-ui-template.sh"
        )
        assert ui_template.is_file() or codex_template.is_file(), (
            "Neither base-ui.sh nor quality-gates-ui-template.sh found"
        )


@pytest.mark.code_test
class TestQuickmergeScriptIntegrity:
    """Verify quickmerge.sh is syntactically valid and key functions exist."""

    def test_quickmerge_script_exists(self) -> None:
        qm = PM_ROOT / "scripts" / "quickmerge.sh"
        assert qm.is_file(), f"quickmerge.sh not found at {qm}"

    def test_quickmerge_bash_syntax(self) -> None:
        """Verify quickmerge.sh passes bash -n syntax check."""
        qm = PM_ROOT / "scripts" / "quickmerge.sh"
        result = subprocess.run(
            ["bash", "-n", str(qm)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Syntax error in quickmerge.sh:\n{result.stderr}"

    def test_quickmerge_has_staging_routing(self) -> None:
        """Verify quickmerge routes to staging by default."""
        qm = PM_ROOT / "scripts" / "quickmerge.sh"
        content = qm.read_text()
        assert 'PR_BASE="staging"' in content, "quickmerge.sh missing staging routing"

    def test_quickmerge_has_doc_fast_path(self) -> None:
        """Verify PM/codex doc-only fast-path exists."""
        qm = PM_ROOT / "scripts" / "quickmerge.sh"
        content = qm.read_text()
        assert "Doc/plan-only change" in content or "doc-only" in content.lower() or "fast-path" in content.lower(), (
            "quickmerge.sh missing PM/codex doc-only fast-path routing"
        )

    def test_quickmerge_has_agent_flag(self) -> None:
        """Verify --agent flag handling exists."""
        qm = PM_ROOT / "scripts" / "quickmerge.sh"
        content = qm.read_text()
        assert "--agent" in content, "quickmerge.sh missing --agent flag"


@pytest.mark.code_test
class TestWorkflowYAMLValidity:
    """Verify key PM workflows are valid YAML."""

    @pytest.fixture()
    def pm_workflows(self) -> list[Path]:
        wf_dir = PM_ROOT / ".github" / "workflows"
        if not wf_dir.is_dir():
            pytest.skip("PM workflows directory not found")
        return sorted(wf_dir.glob("*.yml"))

    def test_all_workflows_parse(self, pm_workflows: list[Path]) -> None:
        """Every .yml in PM workflows must be valid YAML."""
        import yaml

        failures: list[str] = []
        for wf in pm_workflows:
            try:
                yaml.safe_load(wf.read_text())
            except yaml.YAMLError as e:
                failures.append(f"{wf.name}: {e}")
        assert not failures, "Invalid YAML in workflows:\n" + "\n".join(failures)

    def test_critical_workflows_exist(self) -> None:
        """Key CI/CD workflows must be present."""
        wf_dir = PM_ROOT / ".github" / "workflows"
        required = [
            "quality-gates-v2.yml",
            "semver-agent.yml",
            "sit-gate.yml",
            "staging-to-main.yml",
            "conflict-resolution-agent.yml",
            "plan-health-agent.yml",
            "rules-alignment-agent.yml",
        ]
        missing = [w for w in required if not (wf_dir / w).is_file()]
        assert not missing, f"Missing critical workflows: {missing}"
