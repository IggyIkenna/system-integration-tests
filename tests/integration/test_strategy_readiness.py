"""Strategy readiness integration tests — validate manifest entries are real.

Reads strategy-manifest.json from unified-trading-pm/ and verifies:
1. Each strategy's class_path points to a module file that exists on disk.
2. Each strategy's config_file (if not null) exists on disk.
3. Each strategy's factory_function name is a valid Python identifier.
4. Each strategy has required manifest fields.

Parametrized: one test case per strategy entry for clear per-strategy reporting.

This module is credential-free and requires no live services.
"""

from __future__ import annotations

import json
import keyword
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

# Type alias for a strategy dict entry.
_ManifestValue = str | bool | int | float | dict[str, object] | list[object] | None
_StrategyDict = dict[str, _ManifestValue]

# Required top-level keys in every strategy entry.
_REQUIRED_KEYS: list[str] = [
    "strategy_id",
    "display_name",
    "domain",
    "class_path",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_strategies() -> list[_StrategyDict]:
    """Load strategy-manifest.json and return the strategies list.

    Raises FileNotFoundError if the manifest is missing — callers that want
    to assert existence should catch or let it propagate.
    """
    if not _STRATEGY_MANIFEST_PATH.exists():
        msg = f"strategy-manifest.json not found at {_STRATEGY_MANIFEST_PATH}"
        raise FileNotFoundError(msg)
    raw = _STRATEGY_MANIFEST_PATH.read_text(encoding="utf-8")
    parsed: dict[str, list[_StrategyDict]] = json.loads(raw)  # pyright: ignore[reportAny]
    strategies: list[_StrategyDict] = parsed.get("strategies", [])
    return strategies


def _class_path_to_module_file(class_path: str) -> Path:
    """Convert dotted class_path to the .py module file path.

    Example:
        strategy_service.engine.strategies.cefi_momentum.CeFiMomentumStrategy
        -> strategy-service/strategy_service/engine/strategies/cefi_momentum.py
    """
    module_dotted = class_path.rsplit(".", 1)[0]  # drop class name
    segments = module_dotted.split(".")
    # Build path from strategy-service root: strategy_service/engine/strategies/cefi_momentum.py
    file_path = _STRATEGY_SERVICE_ROOT.joinpath(*segments[:-1]) / f"{segments[-1]}.py"
    return file_path


def _strategy_ids() -> list[str]:
    """Return strategy IDs for parametrize, or empty if manifest is missing."""
    if not _STRATEGY_MANIFEST_PATH.exists():
        return []
    raw = _STRATEGY_MANIFEST_PATH.read_text(encoding="utf-8")
    parsed: dict[str, list[_StrategyDict]] = json.loads(raw)  # pyright: ignore[reportAny]
    strategies = parsed.get("strategies", [])
    return [str(s.get("strategy_id", f"unknown_{i}")) for i, s in enumerate(strategies)]


def _require_strategies() -> list[_StrategyDict]:
    """Load strategies, skipping the test if the manifest is absent.

    Use for data-dependent tests where existence is already asserted by
    ``test_manifest_exists_and_not_empty``.
    """
    try:
        return _load_strategies()
    except FileNotFoundError:
        pytest.skip("strategy-manifest.json not present — see test_manifest_exists_and_not_empty")  # pyright: ignore[reportUnknownMemberType]


def _strategy_by_id(strategy_id: str) -> _StrategyDict:
    """Look up a strategy entry by ID."""
    strategies = _require_strategies()
    for s in strategies:
        if str(s.get("strategy_id")) == strategy_id:
            return s
    msg = f"Strategy {strategy_id} not found in manifest"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_manifest_exists_and_not_empty() -> None:
    """strategy-manifest.json must exist and contain at least one strategy."""
    assert _STRATEGY_MANIFEST_PATH.exists(), (
        f"strategy-manifest.json not found at {_STRATEGY_MANIFEST_PATH} — "
        "ensure unified-trading-pm is cloned as a sibling repo"
    )
    strategies = _load_strategies()
    assert len(strategies) > 0, "strategy-manifest.json has zero strategies"


@pytest.mark.parametrize("strategy_id", _strategy_ids())  # pyright: ignore[reportUnknownMemberType]
def test_class_path_file_exists(strategy_id: str) -> None:
    """The .py module file referenced by class_path must exist on disk."""
    strat = _strategy_by_id(strategy_id)
    class_path = str(strat["class_path"])
    module_file = _class_path_to_module_file(class_path)
    assert module_file.exists(), f"Strategy {strategy_id}: class module file not found at {module_file}"


@pytest.mark.parametrize("strategy_id", _strategy_ids())  # pyright: ignore[reportUnknownMemberType]
def test_config_file_exists_if_declared(strategy_id: str) -> None:
    """If config_file is not null, the file must exist on disk."""
    strat = _strategy_by_id(strategy_id)
    config_file = strat.get("config_file")
    if config_file is None:
        pytest.skip(f"Strategy {strategy_id} has no config_file declared")  # pyright: ignore[reportUnknownMemberType]
    full_path = _WORKSPACE_ROOT / str(config_file)
    assert full_path.exists(), f"Strategy {strategy_id}: config file not found at {full_path}"


@pytest.mark.parametrize("strategy_id", _strategy_ids())  # pyright: ignore[reportUnknownMemberType]
def test_factory_function_is_valid_identifier(strategy_id: str) -> None:
    """If factory_function is declared, it must be a valid Python identifier."""
    strat = _strategy_by_id(strategy_id)
    factory_fn = strat.get("factory_function")
    if factory_fn is None:
        pytest.skip(f"Strategy {strategy_id} has no factory_function")  # pyright: ignore[reportUnknownMemberType]
    fn_name = str(factory_fn)
    assert fn_name.isidentifier(), (
        f"Strategy {strategy_id}: factory_function '{fn_name}' is not a valid Python identifier"
    )
    assert not keyword.iskeyword(fn_name), f"Strategy {strategy_id}: factory_function '{fn_name}' is a Python keyword"


@pytest.mark.parametrize("strategy_id", _strategy_ids())  # pyright: ignore[reportUnknownMemberType]
def test_required_manifest_fields_present(strategy_id: str) -> None:
    """Every strategy entry must have the required manifest keys."""
    strat = _strategy_by_id(strategy_id)
    missing = [k for k in _REQUIRED_KEYS if k not in strat]
    assert not missing, f"Strategy {strategy_id} is missing required fields: {missing}"


def test_no_duplicate_strategy_ids() -> None:
    """Strategy IDs must be unique across the manifest."""
    strategies = _require_strategies()
    ids = [str(s.get("strategy_id", "")) for s in strategies]
    seen: set[str] = set()
    duplicates: list[str] = []
    for sid in ids:
        if sid in seen:
            duplicates.append(sid)
        seen.add(sid)
    assert not duplicates, f"Duplicate strategy IDs: {duplicates}"


def test_all_domains_represented() -> None:
    """The manifest should contain strategies from multiple domains."""
    strategies = _require_strategies()
    domains = {str(s.get("domain", "")) for s in strategies}
    # We expect at least cefi, tradfi, defi, and sports in the manifest
    assert len(domains) >= 3, f"Expected at least 3 domains represented, got {len(domains)}: {domains}"
