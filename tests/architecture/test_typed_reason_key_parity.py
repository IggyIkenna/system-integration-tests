"""Cross-repo parity: deployment-ui's EMPTY_REASON_KEYS vs deployment-api's.

deployment-ui/src/components/TypedReasonBadges.test.tsx already asserts
``EMPTY_REASON_KEYS`` against a MANUALLY-SYNCED pinned snapshot — it can only
catch accidental same-repo drift, because deployment-ui's own CI checks out
that repo alone and has no access to deployment-api's source. This test is
the only place both repos are checked out side by side, so it's the only
place a REAL cross-repo parity check can run.

Both sides are read as SOURCE TEXT, never imported as live packages:
importing ``deployment_api.services.data_status.coverage_metrics`` pulls in
``deployment_api/services/__init__.py``, which eagerly resolves cloud bucket
settings at import time (fails without ``GCP_PROJECT_ID`` etc.) — a pure
taxonomy comparison shouldn't depend on cloud credentials. This mirrors
``test_import_boundaries.py`` in this same directory, which AST-walks source
files rather than importing the audited packages.

Provenance: coverage-exclusions denominator study 2026-07-17 (follow-up from
the TypedReasonBadges taxonomy-drift fix, sports_manifest_canonicalisation_2026_06_01.md).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# system-integration-tests/tests/architecture/test_typed_reason_key_parity.py -> workspace root
_WORKSPACE_ROOT: Path = Path(__file__).resolve().parents[3]

_API_SOURCE: Path = (
    _WORKSPACE_ROOT / "deployment-api" / "deployment_api" / "services" / "data_status" / "coverage_metrics.py"
)
_UI_SOURCE: Path = _WORKSPACE_ROOT / "deployment-ui" / "src" / "components" / "TypedReasonBadges.tsx"

_UI_ARRAY_RE = re.compile(r"export const EMPTY_REASON_KEYS = \[(.*?)\] as const;", re.DOTALL)
_STRING_LITERAL_RE = re.compile(r'"([^"]+)"')


def _extract_api_empty_reason_keys(py_path: Path) -> list[str]:
    """AST-parse ``EMPTY_REASON_KEYS: tuple[str, ...] = (...)`` out of coverage_metrics.py."""
    tree = ast.parse(py_path.read_text(), filename=str(py_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        target = node.target
        if isinstance(target, ast.Name) and target.id == "EMPTY_REASON_KEYS" and node.value is not None:
            return list(ast.literal_eval(node.value))
    raise AssertionError(
        f"Could not find `EMPTY_REASON_KEYS: tuple[str, ...] = (...)` in {py_path} — "
        "the declaration shape changed; update this test's AST walk to match."
    )


def _extract_ui_empty_reason_keys(ts_path: Path) -> list[str]:
    """Regex-extract ``export const EMPTY_REASON_KEYS = [...] as const;`` out of the .tsx source.

    No TypeScript AST parser is available in this repo's Python toolchain, so this is a
    literal-array text extraction — deliberately mirrors the API-side approach of reading
    the SOURCE rather than executing/importing either side.
    """
    match = _UI_ARRAY_RE.search(ts_path.read_text())
    if match is None:
        raise AssertionError(
            f"Could not find `export const EMPTY_REASON_KEYS = [...] as const;` in {ts_path} — "
            "the declaration shape changed; update this test's regex to match."
        )
    return _STRING_LITERAL_RE.findall(match.group(1))


def test_ui_empty_reason_keys_is_superset_of_api_empty_reason_keys() -> None:
    """deployment-ui's closed-set empty-reason taxonomy must cover every reason
    deployment-api emits — a reason deployment-api adds without a UI follow-up
    would otherwise be silently invisible (honest-absence violation).
    """
    if not _API_SOURCE.is_file():
        pytest.skip(f"{_API_SOURCE} not found in this checkout")
    if not _UI_SOURCE.is_file():
        pytest.skip(f"{_UI_SOURCE} not found in this checkout")

    api_keys = set(_extract_api_empty_reason_keys(_API_SOURCE))
    ui_keys = set(_extract_ui_empty_reason_keys(_UI_SOURCE))

    missing_in_ui = api_keys - ui_keys
    assert not missing_in_ui, (
        "deployment-api EMPTY_REASON_KEYS has reasons deployment-ui does not render:\n"
        f"  {sorted(missing_in_ui)}\n"
        "Add these to deployment-ui/src/components/TypedReasonBadges.tsx's EMPTY_REASON_KEYS "
        "(+ EMPTY_REASON_META) — an empty reason invisible in the UI violates honest-absence "
        "discipline."
    )
