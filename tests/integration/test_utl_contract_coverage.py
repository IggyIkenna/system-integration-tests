"""SIT integration test: every UTL __all__ symbol must be importable.

Scope: structural only — verifies that nothing in UTL __all__ is broken,
missing, or accidentally removed. Does NOT check which services use each
class (that requires source repos on disk; see check_utl_adoption.py).

Coverage: all symbols in unified_trading_library.__all__ + public subpackages.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import cast

import pytest


def _get_utl_all() -> list[str]:
    """Read __all__ from the installed UTL package source at collection time."""
    import unified_trading_library

    pkg_init = Path(unified_trading_library.__file__).parent / "__init__.py"
    src = pkg_init.read_text()
    all_match = re.search(r"__all__\s*=\s*\[(.*?)\]", src, re.DOTALL)
    if not all_match:
        raise RuntimeError("Could not parse __all__ from unified_trading_library/__init__.py")
    return sorted(set(re.findall(r'"(\w+)"', all_match.group(1))))


# Resolved once at collection time — parametrize below uses this list.
_UTL_ALL: list[str] = _get_utl_all()

# Public subpackages exposed by UTL
_UTL_SUBMODULES = [
    "core",
    "domain",
    "io",
    "ml",
    "models",
    "utils",
]


# ---------------------------------------------------------------------------
# 1. Every __all__ symbol must be accessible as a top-level attribute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", _UTL_ALL, ids=_UTL_ALL)
@pytest.mark.integration
def test_utl_symbol_importable(symbol: str) -> None:
    """utl.<symbol> must be accessible — fails if __all__ contains a broken export."""
    import unified_trading_library as utl

    obj = getattr(utl, symbol, None)
    assert obj is not None, (
        f"unified_trading_library.{symbol} is listed in __all__ but is not "
        f"accessible as an attribute. Check for missing re-export in __init__.py."
    )


# ---------------------------------------------------------------------------
# 2. __all__ completeness regression guard
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_utl_all_minimum_size() -> None:
    """__all__ must have at least 180 entries — regression guard against accidental shrink."""
    assert len(_UTL_ALL) >= 180, (
        f"UTL __all__ has only {len(_UTL_ALL)} entries. "
        "Something may have been accidentally removed from unified_trading_library/__init__.py"
    )


@pytest.mark.integration
def test_utl_all_no_duplicates() -> None:
    """__all__ must contain no duplicate names."""
    import unified_trading_library

    pkg_init = Path(unified_trading_library.__file__).parent / "__init__.py"
    src = pkg_init.read_text()
    all_match = re.search(r"__all__\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert all_match, "Could not parse __all__"
    raw = cast(list[str], re.findall(r'"(\w+)"', all_match.group(1)))
    dupes = [x for x in set(raw) if raw.count(x) > 1]
    assert not dupes, f"Duplicate entries in UTL __all__: {dupes}"


# ---------------------------------------------------------------------------
# 3. Every subpackage must import cleanly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("submodule", _UTL_SUBMODULES, ids=_UTL_SUBMODULES)
@pytest.mark.integration
def test_utl_submodule_importable(submodule: str) -> None:
    """unified_trading_library.<submodule> must import without errors."""
    m = importlib.import_module(f"unified_trading_library.{submodule}")
    assert m is not None
