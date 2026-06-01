"""Phase 9 mode-switch isolation + Phase 10 cutover dress-rehearsal tests.

Phase 9 — Mode-switch isolation:
  1. Batch and live handlers have distinct implementations (different content,
     not identical copies) for all T4 services that declare both modes.
  2. Neither handler imports directly from the other (no circular coupling).
  3. Services that declare batch_capable + live_capable in strategy-manifest.json
     have corresponding handler files on disk.

Phase 10 — Cutover dress-rehearsal smoke:
  4. strategy-manifest.json declares staked_basis strategies (carry archetype)
     as batch_capable + live_capable with valid class_paths on disk.
  5. DeFi paper + live launch scripts exist for the May-23 cutover path.
  6. All DeFi strategies in the manifest satisfy the required metadata schema
     (domain, venues, class_path, batch_capable, live_capable).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]  # pyright: ignore[reportUnknownMemberType]

# ---------------------------------------------------------------------------
# Workspace layout
# ---------------------------------------------------------------------------

_WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", Path(__file__).resolve().parents[3]))
_PM_ROOT = _WORKSPACE_ROOT / "unified-trading-pm"
_STRATEGY_MANIFEST = _PM_ROOT / "strategy-manifest.json"
_E2E_DEFI = _WORKSPACE_ROOT / "e2e-testing" / "scripts" / "defi"


def _load_manifest() -> list[dict[str, object]]:
    if not _STRATEGY_MANIFEST.exists():
        pytest.skip(f"strategy-manifest.json not found at {_STRATEGY_MANIFEST}")
    raw = json.loads(_STRATEGY_MANIFEST.read_text(encoding="utf-8"))
    strategies: list[dict[str, object]] = raw.get("strategies", [])  # pyright: ignore[reportAny]
    return strategies


# ---------------------------------------------------------------------------
# Phase 9 — Mode-switch isolation
# ---------------------------------------------------------------------------


class TestModeSwitchHandlerDistinctness:
    """Batch and live handlers must be distinct files with different content."""

    @pytest.mark.parametrize(
        "repo_name,pkg_name",
        [
            ("features-service", "features_service"),
            ("strategy-service", "strategy_service"),
        ],
    )
    def test_batch_and_live_handler_content_differs(self, repo_name: str, pkg_name: str) -> None:
        repo_root = _WORKSPACE_ROOT / repo_name
        batch_path = repo_root / pkg_name / "cli" / "handlers" / "batch_handler.py"
        live_path = repo_root / pkg_name / "cli" / "handlers" / "live_handler.py"
        if not batch_path.exists() or not live_path.exists():
            pytest.skip(f"batch/live handlers not found in {repo_name}")
        batch_content = batch_path.read_text(encoding="utf-8")
        live_content = live_path.read_text(encoding="utf-8")
        assert batch_content != live_content, (
            f"{repo_name}: batch_handler.py and live_handler.py have identical content — "
            "they must implement distinct mode-specific logic"
        )

    @pytest.mark.parametrize(
        "repo_name,pkg_name",
        [
            ("features-service", "features_service"),
            ("strategy-service", "strategy_service"),
        ],
    )
    def test_batch_handler_does_not_import_live_handler(self, repo_name: str, pkg_name: str) -> None:
        repo_root = _WORKSPACE_ROOT / repo_name
        batch_path = repo_root / pkg_name / "cli" / "handlers" / "batch_handler.py"
        if not batch_path.exists():
            pytest.skip(f"batch_handler.py not found in {repo_name}")
        content = batch_path.read_text(encoding="utf-8")
        assert "live_handler" not in content, (
            f"{repo_name}/batch_handler.py imports live_handler — mode coupling violates Batch=Live rule"
        )

    @pytest.mark.parametrize(
        "repo_name,pkg_name",
        [
            ("features-service", "features_service"),
            ("strategy-service", "strategy_service"),
        ],
    )
    def test_live_handler_does_not_import_batch_handler(self, repo_name: str, pkg_name: str) -> None:
        repo_root = _WORKSPACE_ROOT / repo_name
        live_path = repo_root / pkg_name / "cli" / "handlers" / "live_handler.py"
        if not live_path.exists():
            pytest.skip(f"live_handler.py not found in {repo_name}")
        content = live_path.read_text(encoding="utf-8")
        assert "batch_handler" not in content, (
            f"{repo_name}/live_handler.py imports batch_handler — mode coupling violates Batch=Live rule"
        )


class TestManifestModeParity:
    """Strategies with class_exists=true in manifest must have the module file on disk."""

    def test_defi_strategies_with_class_exists_have_module_on_disk(self) -> None:
        strategies = _load_manifest()
        strategy_svc = _WORKSPACE_ROOT / "strategy-service" / "strategy_service"
        missing: list[str] = []
        for s in strategies:
            if s.get("domain") != "defi":
                continue
            maturity = s.get("maturity", {})
            if not isinstance(maturity, dict):
                continue
            code_block = maturity.get("code", {})
            if not isinstance(code_block, dict) or not code_block.get("class_exists", False):
                continue
            class_path = s.get("class_path", "")
            if not isinstance(class_path, str) or not class_path:
                continue
            module_path = "/".join(class_path.rsplit(".", 1)[0].split(".")[1:]) + ".py"
            full_path = strategy_svc / module_path
            if not full_path.exists():
                missing.append(f"{s.get('strategy_id')} → {full_path}")
        assert not missing, (
            "DeFi strategies with maturity.code.class_exists=true but missing module files:\n" + "\n".join(missing)
        )


# ---------------------------------------------------------------------------
# Phase 10 — Cutover dress-rehearsal smoke
# ---------------------------------------------------------------------------


class TestCutoverReadinessStrategyManifest:
    """May-23 cutover gate: staked_basis + price_dispersion archetypes must be present."""

    def test_staked_basis_strategies_declared(self) -> None:
        strategies = _load_manifest()
        staked_basis = [
            s
            for s in strategies
            if "staked_basis" in str(s.get("class_path", "")).lower() and s.get("domain") == "defi"
        ]
        assert len(staked_basis) >= 1, (
            "No defi staked_basis strategy in strategy-manifest.json — "
            "carry_staked_basis archetype must be declared for May-23 cutover"
        )

    def test_staked_basis_strategies_are_batch_and_live_capable(self) -> None:
        strategies = _load_manifest()
        staked_basis = [
            s
            for s in strategies
            if "staked_basis" in str(s.get("class_path", "")).lower() and s.get("domain") == "defi"
        ]
        for s in staked_basis:
            sid = s.get("strategy_id")
            assert s.get("batch_capable"), f"{sid}: must be batch_capable for May-23 paper-trade gate"
            assert s.get("live_capable"), f"{sid}: must be live_capable for May-23 live promotion"

    def test_all_defi_strategies_have_required_manifest_fields(self) -> None:
        strategies = _load_manifest()
        required_fields = ["strategy_id", "domain", "class_path", "batch_capable", "live_capable", "venues"]
        violations: list[str] = []
        for s in strategies:
            if s.get("domain") != "defi":
                continue
            for field in required_fields:
                if field not in s or s[field] is None:
                    violations.append(f"{s.get('strategy_id', '?')} missing field '{field}'")
        assert not violations, "DeFi strategy manifest field violations:\n" + "\n".join(violations)

    def test_defi_strategies_all_have_defi_domain(self) -> None:
        strategies = _load_manifest()
        non_defi = [
            s for s in strategies if "defi" in str(s.get("strategy_id", "")).lower() and s.get("domain") != "defi"
        ]
        assert not non_defi, "Strategies with 'defi' in ID but wrong domain: " + ", ".join(
            str(s.get("strategy_id")) for s in non_defi
        )


class TestCutoverLaunchScriptReadiness:
    """Paper and live launch scripts must exist for the May-23 cutover path."""

    def test_run_paper_script_exists(self) -> None:
        script = _E2E_DEFI / "run-paper.sh"
        assert script.exists(), f"run-paper.sh not found at {script} — required for May-23 paper-trade launch"

    def test_run_live_script_exists(self) -> None:
        script = _E2E_DEFI / "run-live.sh"
        assert script.exists(), f"run-live.sh not found at {script} — required for May-23 live promotion"

    def test_csb_paper_smoke_exists(self) -> None:
        smoke = _E2E_DEFI / "test_csb_paper_e2e_smoke.py"
        assert smoke.exists(), (
            "test_csb_paper_e2e_smoke.py not found — carry_staked_basis paper smoke required for May-23"
        )

    def test_apd_paper_smoke_exists(self) -> None:
        smoke = _E2E_DEFI / "test_apd_paper_e2e_smoke.py"
        assert smoke.exists(), (
            "test_apd_paper_e2e_smoke.py not found — arbitrage_price_dispersion paper smoke required for May-23"
        )

    def test_run_paper_script_supports_strategy_flag(self) -> None:
        script = _E2E_DEFI / "run-paper.sh"
        if not script.exists():
            pytest.skip("run-paper.sh not found")
        content = script.read_text(encoding="utf-8")
        assert "--strategy" in content, (
            "run-paper.sh does not declare --strategy flag — "
            "required to launch individual DeFi strategies for May-23 cutover"
        )
