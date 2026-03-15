"""Strategy readiness tests -- validate every manifest strategy is real and tested.

Loads strategy-manifest.json from unified-trading-pm and verifies:
1. Each strategy's class file exists in strategy-service.
2. A test file referencing the strategy exists.
3. Config YAML (if declared) exists on disk.

Reports a per-strategy readiness score and fails if any production-declared
strategy lacks tests.

This module is credential-free and requires no live services.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.code_test, pytest.mark.unit]  # pyright: ignore[reportUnknownMemberType]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PM_ROOT = _WORKSPACE_ROOT / "unified-trading-pm"
_STRATEGY_MANIFEST_PATH = _PM_ROOT / "strategy-manifest.json"
_STRATEGY_SERVICE_ROOT = _WORKSPACE_ROOT / "strategy-service"
_STRATEGY_TESTS_DIR = _STRATEGY_SERVICE_ROOT / "tests"

# Type alias for a strategy dict entry from the manifest.
StrategyEntry = dict[str, str | bool | list[str] | None]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_manifest() -> list[StrategyEntry]:
    """Load strategy-manifest.json and return the strategies list."""
    if not _STRATEGY_MANIFEST_PATH.exists():
        pytest.skip(  # pyright: ignore[reportUnknownMemberType]
            f"strategy-manifest.json not found at {_STRATEGY_MANIFEST_PATH}"
        )
    raw = _STRATEGY_MANIFEST_PATH.read_text(encoding="utf-8")
    parsed: dict[str, list[StrategyEntry]] = json.loads(raw)  # pyright: ignore[reportAny]
    strategies: list[StrategyEntry] = parsed.get("strategies", [])
    return strategies


def _class_path_to_module_file(class_path: str) -> Path:
    """Convert dotted class_path to the .py module file path.

    Example:
        strategy_service.engine.core.strategies.btc_basis_strategy.BTCBasisStrategyManager
        -> strategy-service/strategy_service/engine/core/strategies/btc_basis_strategy.py
    """
    parts = class_path.rsplit(".", 1)[0]  # drop class name
    segments = parts.split(".")
    file_path = _STRATEGY_SERVICE_ROOT / "/".join(segments[:-1]) / f"{segments[-1]}.py"
    return file_path


def _find_test_for_strategy(class_name: str, class_path: str) -> Path | None:
    """Search strategy-service/tests/ for a test file referencing the strategy."""
    if not _STRATEGY_TESTS_DIR.exists():
        return None

    module_dotted = class_path.rsplit(".", 1)[0]
    module_name = module_dotted.rsplit(".", 1)[-1]
    search_terms = [class_name, module_dotted, f"from {module_dotted}"]

    if "." in module_dotted:
        parent = module_dotted.rsplit(".", 1)[0]
        search_terms.append(f"from {parent}.{module_name} import")

    for test_file in _STRATEGY_TESTS_DIR.rglob("test_*.py"):
        try:
            content = test_file.read_text(encoding="utf-8")
            for term in search_terms:
                if term in content:
                    return test_file
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")  # pyright: ignore[reportUnknownMemberType, reportUntypedFunctionDecorator]
def strategy_manifest() -> list[StrategyEntry]:
    """Load the strategy manifest once per module."""
    return _load_manifest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_manifest_not_empty(strategy_manifest: list[StrategyEntry]) -> None:
    """strategy-manifest.json must contain at least one strategy."""
    assert len(strategy_manifest) > 0, "strategy-manifest.json has zero strategies"


def test_all_strategy_classes_exist(strategy_manifest: list[StrategyEntry]) -> None:
    """Every declared strategy class file must exist on disk."""
    missing: list[str] = []
    for strat in strategy_manifest:
        class_path = str(strat["class_path"])
        module_file = _class_path_to_module_file(class_path)
        if not module_file.exists():
            missing.append(f"{strat['name']}: {module_file}")

    assert not missing, "Strategy class files not found:\n" + "\n".join(missing)


def test_all_strategies_have_tests(strategy_manifest: list[StrategyEntry]) -> None:
    """Every strategy must have at least one test file referencing it."""
    untested: list[str] = []
    for strat in strategy_manifest:
        class_name = str(strat["class_name"])
        class_path = str(strat["class_path"])
        test_file = _find_test_for_strategy(class_name, class_path)
        if test_file is None:
            untested.append(f"{strat['name']} ({class_name})")

    if untested:
        pytest.fail(  # pyright: ignore[reportUnknownMemberType]
            f"{len(untested)} strategies lack test coverage:\n" + "\n".join(f"  - {s}" for s in untested)
        )


def test_production_strategies_must_have_tests(
    strategy_manifest: list[StrategyEntry],
) -> None:
    """Production-declared strategies MUST have test files (hard fail)."""
    production_untested: list[str] = []
    for strat in strategy_manifest:
        maturity = str(strat.get("maturity", ""))
        if maturity != "production":
            continue
        class_name = str(strat["class_name"])
        class_path = str(strat["class_path"])
        if _find_test_for_strategy(class_name, class_path) is None:
            production_untested.append(f"{strat['name']} ({class_name})")

    assert not production_untested, "Production strategies without tests:\n" + "\n".join(
        f"  - {s}" for s in production_untested
    )


def test_config_yaml_exists_when_declared(
    strategy_manifest: list[StrategyEntry],
) -> None:
    """If has_config_yaml is True, the config file must exist."""
    missing_configs: list[str] = []
    for strat in strategy_manifest:
        if not strat.get("has_config_yaml"):
            continue
        config_path_str = strat.get("config_yaml_path")
        if config_path_str is None:
            missing_configs.append(f"{strat['name']}: has_config_yaml=True but config_yaml_path is null")
            continue
        full_path = _STRATEGY_SERVICE_ROOT / str(config_path_str)
        if not full_path.exists():
            missing_configs.append(f"{strat['name']}: {full_path}")

    assert not missing_configs, "Declared config YAMLs not found:\n" + "\n".join(f"  - {c}" for c in missing_configs)


def _score_strategy(strat: StrategyEntry) -> tuple[int, int, bool, bool, bool]:
    """Score a single strategy. Returns (score, max_score, class_exists, has_tests, config_ok)."""
    class_path = str(strat["class_path"])
    class_name = str(strat["class_name"])

    score = 0
    max_score = 2  # class + tests

    class_exists = _class_path_to_module_file(class_path).exists()
    if class_exists:
        score += 1

    has_tests = _find_test_for_strategy(class_name, class_path) is not None
    if has_tests:
        score += 1

    config_ok = True
    if strat.get("has_config_yaml"):
        max_score = 3
        config_path_str = strat.get("config_yaml_path")
        config_ok = bool(config_path_str) and (_STRATEGY_SERVICE_ROOT / str(config_path_str)).exists()
        if config_ok:
            score += 1

    return score, max_score, class_exists, has_tests, config_ok


def test_strategy_readiness_score(strategy_manifest: list[StrategyEntry]) -> None:
    """Compute and report readiness score per strategy.

    This test always passes but prints the readiness breakdown for visibility.
    A strategy scores:
      - +1 for class file existing
      - +1 for having tests
      - +1 for having config YAML (when declared)
    Max score = 3 (or 2 if no config declared).
    """
    results: list[str] = []
    total_score = 0
    total_max = 0

    for strat in strategy_manifest:
        name = str(strat["name"])
        score, max_score, class_exists, has_tests, config_ok = _score_strategy(strat)
        total_score += score
        total_max += max_score

        maturity = str(strat.get("maturity", "unknown"))
        results.append(
            f"  {name:<25s} {score}/{max_score}  "
            f"class={'yes' if class_exists else 'NO':>3s}  "
            f"tests={'yes' if has_tests else 'NO':>3s}  "
            f"config={'yes' if config_ok else 'NO':>3s}  "
            f"maturity={maturity}"
        )

    pct = (total_score / total_max * 100) if total_max > 0 else 0.0
    header = f"Strategy Readiness: {total_score}/{total_max} ({pct:.0f}%)"
    print(f"\n{'=' * 70}")
    print(header)
    print("=" * 70)
    for line in results:
        print(line)
    print("=" * 70)


def test_strategy_module_importable(strategy_manifest: list[StrategyEntry]) -> None:
    """Strategy modules must be importable (no missing deps at import time).

    Skips if strategy-service is not installed in the current venv.
    """
    not_importable: list[str] = []
    for strat in strategy_manifest:
        class_path = str(strat["class_path"])
        module_dotted = class_path.rsplit(".", 1)[0]
        try:
            importlib.import_module(module_dotted)
        except ImportError as exc:
            # If the root package isn't installed, skip all
            if "strategy_service" in str(exc) and "No module named 'strategy_service'" in str(exc):
                pytest.skip(  # pyright: ignore[reportUnknownMemberType]
                    "strategy-service not installed in this venv"
                )
            not_importable.append(f"{strat['name']}: {exc}")
        except SystemExit:
            pass  # Acceptable: main() calls sys.exit

    if not_importable:
        pytest.fail(  # pyright: ignore[reportUnknownMemberType]
            f"{len(not_importable)} strategy modules failed to import:\n"
            + "\n".join(f"  - {s}" for s in not_importable)
        )
