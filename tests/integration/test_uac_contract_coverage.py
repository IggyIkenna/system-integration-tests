"""SIT integration test: every UAC __all__ symbol must be importable.

Scope: structural only — verifies that nothing in UAC __all__ is broken,
missing, or accidentally removed. Does NOT check which services use each
contract (that requires source repos on disk; see the contract-adoption-check
GHA job in smoke-test-gate.yml).

Coverage: all symbols in unified_api_contracts.__all__ + key public submodules.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import cast

import pytest

pytestmark = pytest.mark.code_test


def _get_uac_all() -> list[str]:
    """Read __all__ from the installed UAC package source at collection time."""
    import unified_api_contracts

    pkg_init = Path(unified_api_contracts.__file__).parent / "__init__.py"
    src = pkg_init.read_text()
    all_match = re.search(r"__all__\s*=\s*\[(.*?)\]", src, re.DOTALL)
    if not all_match:
        raise RuntimeError("Could not parse __all__ from unified_api_contracts/__init__.py")
    return sorted(set(re.findall(r'"(\w+)"', all_match.group(1))))


# Resolved once at collection time — parametrize below uses this list.
_UAC_ALL: list[str] = _get_uac_all()

# Public submodules/subpackages accessible as top-level attributes
_UAC_SUBMODULES = [
    "schemas",
    "registry.venue_constants",
    "canonical.canonical_mappings",
    "registry.endpoint_registry",
    "registry",
]


# ---------------------------------------------------------------------------
# 1. Every __all__ symbol must be accessible as a top-level attribute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", _UAC_ALL, ids=_UAC_ALL)
@pytest.mark.integration
def test_uac_symbol_importable(symbol: str) -> None:
    """uac.<symbol> must be accessible — fails if __all__ contains a broken export."""
    import unified_api_contracts as uac

    obj = getattr(uac, symbol, None)
    assert obj is not None, (
        f"unified_api_contracts.{symbol} is listed in __all__ but is not "
        f"accessible as an attribute. Check for missing re-export in __init__.py."
    )


# ---------------------------------------------------------------------------
# 2. __all__ completeness regression guard
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_uac_all_minimum_size() -> None:
    """__all__ must have at least 150 entries — regression guard against accidental shrink."""
    assert len(_UAC_ALL) >= 150, (
        f"UAC __all__ has only {len(_UAC_ALL)} entries. "
        "Something may have been accidentally removed from unified_api_contracts/__init__.py"
    )


@pytest.mark.integration
def test_uac_all_no_duplicates() -> None:
    """__all__ must contain no duplicate names."""
    import unified_api_contracts

    pkg_init = Path(unified_api_contracts.__file__).parent / "__init__.py"
    src = pkg_init.read_text()
    all_match = re.search(r"__all__\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert all_match, "Could not parse __all__"
    raw = cast(list[str], re.findall(r'"(\w+)"', all_match.group(1)))
    dupes = [x for x in set(raw) if raw.count(x) > 1]
    assert not dupes, f"Duplicate entries in UAC __all__: {dupes}"


# ---------------------------------------------------------------------------
# 3. Every submodule must import cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("submodule", _UAC_SUBMODULES, ids=_UAC_SUBMODULES)
@pytest.mark.integration
def test_uac_submodule_importable(submodule: str) -> None:
    """unified_api_contracts.<submodule> must import without errors."""
    m = importlib.import_module(f"unified_api_contracts.{submodule}")
    assert m is not None
