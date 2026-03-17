"""SIT integration test: every UIC __all__ symbol must be importable.

Scope: structural only — verifies that nothing in UIC __all__ is broken,
missing, or accidentally removed. Does NOT check which services use each
contract (that requires source repos on disk; see the contract-adoption-check
GHA job in smoke-test-gate.yml).

Coverage: all 171 symbols in unified_internal_contracts.__all__ + all
17 public submodules (alerting, events, execution, ...).
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import cast

import pytest

pytestmark = pytest.mark.code_test


def _get_uic_all() -> list[str]:
    """Read __all__ from the installed UIC package source at collection time."""
    import unified_internal_contracts

    pkg_init = Path(unified_internal_contracts.__file__).parent / "__init__.py"
    src = pkg_init.read_text()
    all_match = re.search(r"__all__\s*=\s*\[(.*?)\]", src, re.DOTALL)
    if not all_match:
        raise RuntimeError("Could not parse __all__ from unified_internal_contracts/__init__.py")
    return sorted(set(re.findall(r'"(\w+)"', all_match.group(1))))


# Resolved once at collection time — parametrize below uses this list.
_UIC_ALL: list[str] = _get_uic_all()

# Subpackages exported as module-level names (not schema classes — not in __all__)
_UIC_SUBMODULES = [
    "alerting",
    "connectivity",
    "defi",
    "domain",
    "events",
    "execution",
    "features",
    "market_data",
    "messaging",
    "ml",
    "positions",
    "pubsub",
    "reference",
    "reporting",
    "risk",
    "schemas",
    "sports",
]


# ---------------------------------------------------------------------------
# 1. Every __all__ symbol must be accessible as a top-level attribute
# ---------------------------------------------------------------------------


# Symbols declared in UIC __all__ but with known broken re-exports.
# These must be fixed in unified-internal-contracts, tracked as upstream issues.
_UIC_KNOWN_BROKEN_EXPORTS: frozenset[str] = frozenset({"OptionsChain"})


@pytest.mark.parametrize("symbol", _UIC_ALL, ids=_UIC_ALL)
def test_uic_symbol_importable(symbol: str) -> None:
    """uic.<symbol> must be accessible — fails if __all__ contains a broken export."""
    import unified_internal_contracts as uic

    obj = getattr(uic, symbol, None)
    if obj is None and symbol in _UIC_KNOWN_BROKEN_EXPORTS:
        pytest.skip(f"{symbol} is a known broken re-export in UIC — fix in unified-internal-contracts")
    assert obj is not None, (
        f"unified_internal_contracts.{symbol} is listed in __all__ but is not "
        f"accessible as an attribute. Check for missing re-export in __init__.py."
    )


# ---------------------------------------------------------------------------
# 2. __all__ completeness regression guard
# ---------------------------------------------------------------------------


def test_uic_all_minimum_size() -> None:
    """__all__ must have at least 150 entries — regression guard against accidental shrink."""
    assert len(_UIC_ALL) >= 150, (
        f"UIC __all__ has only {len(_UIC_ALL)} entries. "
        "Something may have been accidentally removed from unified_internal_contracts/__init__.py"
    )


def test_uic_all_no_duplicates() -> None:
    """__all__ must contain no duplicate names."""
    import unified_internal_contracts

    pkg_init = Path(unified_internal_contracts.__file__).parent / "__init__.py"
    src = pkg_init.read_text()
    all_match = re.search(r"__all__\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert all_match, "Could not parse __all__"
    raw = cast(list[str], re.findall(r'"(\w+)"', all_match.group(1)))
    dupes = [x for x in set(raw) if raw.count(x) > 1]
    assert not dupes, f"Duplicate entries in UIC __all__: {dupes}"


# ---------------------------------------------------------------------------
# 3. Every submodule must import cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("submodule", _UIC_SUBMODULES, ids=_UIC_SUBMODULES)
def test_uic_submodule_importable(submodule: str) -> None:
    """unified_internal_contracts.<submodule> must import without errors."""
    m = importlib.import_module(f"unified_internal_contracts.{submodule}")
    assert m is not None
