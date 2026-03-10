"""CLI worker import smoke tests.

Verifies all CLI worker entrypoints can be imported without error.
Also includes a regression guard for UEI event constant names.
"""

from __future__ import annotations

import importlib

import pytest

CLI_WORKER_MODULES = [
    "execution_service.cli.main",
    "strategy_service.cli.main",
    "ml_inference_service.cli.main",
    "ml_training_service.cli.main",
    "risk_and_exposure_service.cli.main",
    "position_balance_monitor_service.cli.main",
    "alerting_service.cli.main",
    "pnl_attribution_service.cli.main",
    "market_tick_data_service.cli.main",
    "market_data_processing_service.cli.main",
]


@pytest.mark.smoke
@pytest.mark.parametrize("module_path", CLI_WORKER_MODULES)
def test_cli_worker_importable(module_path: str) -> None:
    """Each CLI worker module must be importable without side-effects."""
    importlib.import_module(module_path)


@pytest.mark.smoke
def test_uei_event_constants() -> None:
    """Regression guard: verify canonical event names are correct in UEI."""
    from unified_events_interface import STANDARD_LIFECYCLE_EVENTS

    # Must be present (canonical names)
    assert "MEMORY_THRESHOLD_REACHED" in STANDARD_LIFECYCLE_EVENTS, (
        "MEMORY_THRESHOLD_REACHED must be in STANDARD_LIFECYCLE_EVENTS"
    )
    assert "CPU_THRESHOLD_REACHED" in STANDARD_LIFECYCLE_EVENTS, (
        "CPU_THRESHOLD_REACHED must be in STANDARD_LIFECYCLE_EVENTS"
    )
    assert "DISK_THRESHOLD_REACHED" in STANDARD_LIFECYCLE_EVENTS, (
        "DISK_THRESHOLD_REACHED must be in STANDARD_LIFECYCLE_EVENTS"
    )

    # Must NOT be present (deprecated name)
    assert "SERVICE_MEMORY_CRITICAL" not in STANDARD_LIFECYCLE_EVENTS, (
        "SERVICE_MEMORY_CRITICAL has been renamed to MEMORY_THRESHOLD_REACHED — "
        "update any references in your service code"
    )
