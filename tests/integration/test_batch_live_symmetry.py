"""Batch-live symmetry integration tests.

Verifies that each T4 pipeline service implements the expected batch/live handler
structure as documented in the Service Audit Matrix:
unified-trading-codex/04-architecture/batch-live-symmetry.md (updated 2026-03-11)

Tests are filesystem-level: they verify handler files exist in the expected
locations. No service imports required — runs from workspace root.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# ── Workspace root resolution ─────────────────────────────────────────────────
# Prefer WORKSPACE_ROOT env var (set in CI); fall back to relative path from this file.
# File path: <workspace>/system-integration-tests/tests/integration/test_*.py
#             parents[0]=integration, [1]=tests, [2]=system-integration-tests, [3]=workspace
_WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", Path(__file__).parents[3]))


# ── Service audit matrix ──────────────────────────────────────────────────────
# Derived from codex batch-live-symmetry.md Service Audit Matrix (2026-03-11).
# Each entry: (repo_name, python_pkg, batch_glob, live_glob, note)
#   batch_glob / live_glob: glob pattern relative to repo root; None = not applicable.
_SERVICE_MATRIX: list[tuple[str, str, str | None, str | None, str]] = [
    (
        "features-commodity-service",
        "features_commodity_service",
        "features_commodity_service/cli/handlers/batch_handler.py",
        "features_commodity_service/cli/handlers/live_handler.py",
        "batch / live — standard pattern",
    ),
    (
        "features-volatility-service",
        "features_volatility_service",
        "features_volatility_service/cli/handlers/batch_handler.py",
        "features_volatility_service/cli/handlers/live_handler.py",
        "batch / live — pre-existing handlers",
    ),
    (
        "features-onchain-service",
        "features_onchain_service",
        "features_onchain_service/cli/handlers/batch_handler.py",
        "features_onchain_service/cli/handlers/live_handler.py",
        "batch / live — pre-existing handlers",
    ),
    (
        "features-sports-service",
        "features_sports_service",
        "features_sports_service/cli/handlers/batch_handler.py",
        "features_sports_service/cli/handlers/live_handler.py",
        "batch-first; live handler present (future activation via p1-todo-10)",
    ),
    (
        "market-tick-data-service",
        "market_tick_data_service",
        "market_tick_data_service/cli/handlers/download_handler.py",
        None,  # download-only; no live streaming mode
        "download-only — DownloadBatchHandler; live mode N/A",
    ),
    (
        "market-data-processing-service",
        "market_data_processing_service",
        "market_data_processing_service/cli/handlers/process_handler.py",
        "market_data_processing_service/cli/handlers/live_mode_handler.py",
        "batch / live — lazy-imported via _mode_dispatch",
    ),
    (
        "instruments-service",
        "instruments_service",
        "instruments_service/cli/handlers/instrument_handler.py",
        None,  # catalogue-only; no live streaming mode
        "batch catalogue-only — InstrumentsBatchMode; live mode N/A",
    ),
    (
        "strategy-service",
        "strategy_service",
        "strategy_service/cli/handlers/batch_handler.py",
        "strategy_service/cli/handlers/live_handler.py",
        "batch / live — LiveHandler facade added (p1-todo-13)",
    ),
    (
        "execution-service",
        "execution_service",
        None,  # live-only; no batch mode
        "execution_service/cli/handlers/live_execution_handler.py",
        "live-only — execution is always live; batch mode N/A",
    ),
    (
        "alerting-service",
        "alerting_service",
        None,  # live-only event-driven API
        "alerting_service/main.py",
        "live-only event-driven FastAPI; no batch mode",
    ),
]

_SERVICE_IDS = [entry[0] for entry in _SERVICE_MATRIX]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _repo_path(repo_name: str) -> Path:
    return _WORKSPACE_ROOT / repo_name


def _handler_path(repo_name: str, rel_path: str) -> Path:
    return _repo_path(repo_name) / rel_path


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("entry", _SERVICE_MATRIX, ids=_SERVICE_IDS)
def test_service_repo_exists(
    entry: tuple[str, str, str | None, str | None, str],
) -> None:
    """Each service repo directory must exist at workspace root."""
    repo_name, _pkg, _batch, _live, note = entry
    repo = _repo_path(repo_name)
    assert repo.is_dir(), (
        f"{repo_name}: repo directory not found at {repo}. Expected at WORKSPACE_ROOT={_WORKSPACE_ROOT}. Note: {note}"
    )


@pytest.mark.parametrize(
    "entry",
    [e for e in _SERVICE_MATRIX if e[2] is not None],
    ids=[e[0] for e in _SERVICE_MATRIX if e[2] is not None],
)
def test_batch_handler_exists(
    entry: tuple[str, str, str | None, str | None, str],
) -> None:
    """Services that support batch mode must have their batch handler file."""
    repo_name, _pkg, batch_rel, _live, note = entry
    assert batch_rel is not None
    handler = _handler_path(repo_name, batch_rel)
    assert handler.is_file(), (
        f"{repo_name}: batch handler not found at {handler}. Expected per audit matrix. Note: {note}"
    )


@pytest.mark.parametrize(
    "entry",
    [e for e in _SERVICE_MATRIX if e[3] is not None],
    ids=[e[0] for e in _SERVICE_MATRIX if e[3] is not None],
)
def test_live_handler_exists(
    entry: tuple[str, str, str | None, str | None, str],
) -> None:
    """Services that support live mode must have their live handler file."""
    repo_name, _pkg, _batch, live_rel, note = entry
    assert live_rel is not None
    handler = _handler_path(repo_name, live_rel)
    assert handler.is_file(), (
        f"{repo_name}: live handler not found at {handler}. Expected per audit matrix. Note: {note}"
    )


@pytest.mark.parametrize(
    "entry",
    [e for e in _SERVICE_MATRIX if e[2] is not None and e[3] is not None],
    ids=[e[0] for e in _SERVICE_MATRIX if e[2] is not None and e[3] is not None],
)
def test_both_modes_present(
    entry: tuple[str, str, str | None, str | None, str],
) -> None:
    """Services supporting both modes must have both handler files simultaneously."""
    repo_name, _pkg, batch_rel, live_rel, note = entry
    assert batch_rel is not None
    assert live_rel is not None
    batch_path = _handler_path(repo_name, batch_rel)
    live_path = _handler_path(repo_name, live_rel)
    missing = [p for p in (batch_path, live_path) if not p.is_file()]
    assert not missing, f"{repo_name}: symmetry gap — missing: {[str(m) for m in missing]}. Note: {note}"


def test_audit_matrix_covers_all_expected_services() -> None:
    """Audit matrix must cover exactly the 13 T4 pipeline services."""
    assert len(_SERVICE_MATRIX) == 13, (
        f"Audit matrix has {len(_SERVICE_MATRIX)} entries; expected 13. "
        "Update matrix when adding/removing T4 pipeline services."
    )


def test_no_service_has_neither_batch_nor_live() -> None:
    """Every service must implement at least one mode (batch or live)."""
    neither = [repo_name for repo_name, _pkg, batch, live, _note in _SERVICE_MATRIX if batch is None and live is None]
    assert not neither, (
        f"Services with neither batch nor live handler: {neither}. All T4 services must implement at least one mode."
    )
