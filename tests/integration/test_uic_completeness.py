"""SIT integration test: every public UIC class defined in source must be in __all__.

Scope: structural completeness — verifies that no public class defined in UIC source
files is accidentally missing from __all__. Mirrors the logic in
unified-internal-contracts/scripts/check_uic_completeness.py but runs against the
installed package (uses importlib to find source path).

Does NOT check which services use each contract — see test_contract_coverage.py
and the contract-adoption-check GHA job in smoke-test-gate.yml for that.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.code_test

# ---------------------------------------------------------------------------
# Classes intentionally NOT in UIC top-level __all__ (mirrors EXEMPT_MISSING
# in check_uic_completeness.py — keep in sync if source exemptions change)
# ---------------------------------------------------------------------------
_UIC_EXEMPT: frozenset[str] = frozenset(
    [
        # domain-internal: feature record types used only by their owning feature service
        "BookDepthFeature1m",
        "CompositeSRFeature1m",
        "FlowInteractionFeature1m",
        "LiquidationClusterFeature1m",
        "LiquidityWallEvent",
        # domain-internal: sports feature records
        "HalfTimeFeatureRecord",
        "RefereeFeatureRecord",
        "SeasonContextFeatureRecord",
        "VenueContextFeatureRecord",
        "SportsMLPredictionRecord",
        # domain-internal: private TypedDict base types in schema_definition.py
        "_RawColumn",
        "_RawSchema",
    ]
)

_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        ".venv-workspace",
        "venv",
        "build",
        "dist",
        "node_modules",
        ".git",
        ".ruff_cache",
        ".pytest_cache",
        "__pycache__",
        "tests",
    }
)


def _get_uic_package_root() -> Path:
    import unified_api_contracts.internal

    return Path(unified_api_contracts.internal.__file__).parent


def _get_uic_all_set() -> set[str]:
    pkg_init = _get_uic_package_root() / "__init__.py"
    src = pkg_init.read_text()
    all_match = re.search(r"__all__\s*=\s*\[(.*?)\]", src, re.DOTALL)
    if not all_match:
        raise RuntimeError("Could not parse __all__ from unified_api_contracts.internal/__init__.py")
    return set(re.findall(r'"(\w+)"', all_match.group(1)))


def _collect_source_classes(pkg_root: Path) -> dict[str, str]:
    """AST-scan all source .py files and return {class_name: relative_path}."""
    defined: dict[str, str] = {}
    for py_file in pkg_root.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        if any(part in _EXCLUDE_DIRS for part in py_file.parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue
        rel = str(py_file.relative_to(pkg_root.parent))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_") and node.name not in defined:
                defined[node.name] = rel
    return defined


# Resolved once at collection time
_UIC_PKG_ROOT: Path = _get_uic_package_root()
_UIC_EXPORTED: set[str] = _get_uic_all_set()
_UIC_SOURCE_CLASSES: dict[str, str] = _collect_source_classes(_UIC_PKG_ROOT)

# Classes in source, not exempt, that must be in __all__
_UIC_REQUIRED: list[str] = sorted(
    name for name in _UIC_SOURCE_CLASSES if name not in _UIC_EXEMPT and not name.startswith("_")
)


# ---------------------------------------------------------------------------
# 1. Every non-exempt public source class must appear in __all__
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", _UIC_REQUIRED, ids=_UIC_REQUIRED)
def test_uic_source_class_in_all(cls: str) -> None:
    """Public UIC class defined in source must be exported in __all__."""
    assert cls in _UIC_EXPORTED, (
        f"unified_api_contracts.internal.{cls} is defined in "
        f"{_UIC_SOURCE_CLASSES[cls]} but is not in __all__. "
        "Add it to unified_api_contracts.internal/__init__.py or mark it exempt."
    )


# ---------------------------------------------------------------------------
# 2. Completeness regression guards
# ---------------------------------------------------------------------------


def test_uic_completeness_all_size() -> None:
    """__all__ must have at least 150 entries — regression guard against accidental shrink."""
    assert len(_UIC_EXPORTED) >= 150, (
        f"UIC __all__ has only {len(_UIC_EXPORTED)} entries. "
        "Something may have been accidentally removed from unified_api_contracts.internal/__init__.py"
    )


def test_uic_completeness_source_class_count() -> None:
    """Source must define at least 100 public classes — sanity guard."""
    assert len(_UIC_SOURCE_CLASSES) >= 100, (
        f"UIC source scan found only {len(_UIC_SOURCE_CLASSES)} public classes. "
        "Source scan may have failed or package layout changed."
    )


def test_uic_completeness_no_gaps() -> None:
    """No non-exempt public source class should be missing from __all__ (summary check)."""
    missing = [name for name in _UIC_REQUIRED if name not in _UIC_EXPORTED]
    assert not missing, f"{len(missing)} non-exempt public UIC class(es) are missing from __all__:\n" + "\n".join(
        f"  - {name}  [{_UIC_SOURCE_CLASSES[name]}]" for name in sorted(missing)
    )
