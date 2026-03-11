"""Layer 1 smoke tests: verify service modules load correctly.

These tests verify that service main modules are importable (no missing deps, no broken
top-level imports). No GCS, PubSub, or cloud credentials are required.

Runs as part of quickmerge gate (Layer 1 — blocks schema robustness).

Note: SystemExit from main() is acceptable — it means the module loaded correctly and
main() ran to completion (or exited on missing args). ImportError is the failure signal.
"""

import importlib

import pytest

pytestmark = pytest.mark.code_test


@pytest.mark.smoke
@pytest.mark.parametrize(
    "module",
    [
        "instruments_service.cli.main",
        "market_data_processing_service.cli.main",
        "strategy_service.cli.main",
        "execution_service.cli.main",
        "features_delta_one_service.cli.main",
        "features_multi_timeframe_service.cli.main",
        "features_calendar_service.cli.main",
        "features_volatility_service.cli.main",
        "features_commodity_service.cli.main",
        "features_sports_service.cli.main",
        "features_onchain_service.cli.main",
        "features_cross_instrument_service.cli.main",
        "pnl_attribution_service.config",
        "market_tick_data_service.config",
        "alerting_service.config",
        "risk_and_exposure_service.config",
        "position_balance_monitor_service.config",
    ],
)
def test_service_module_importable(module: str) -> None:
    """Service main modules must be importable (no missing deps at import time)."""
    try:
        importlib.import_module(module)
    except SystemExit:
        pass  # OK — main() called sys.exit on missing args or completed normally


def test_instruments_service_config_importable() -> None:
    """instruments_service.config must be importable and expose instruments_config."""
    try:
        from instruments_service.config import instruments_config

        assert instruments_config is not None
    except SystemExit:
        pass


def test_strategy_service_config_importable() -> None:
    """strategy_service.config must be importable and expose get_config."""
    try:
        from strategy_service.config import get_config

        assert callable(get_config)
    except SystemExit:
        pass


def test_execution_service_config_importable() -> None:
    """execution_service.service_config must be importable and expose get_execution_config."""
    try:
        from execution_service.service_config import get_execution_config

        assert callable(get_execution_config)
    except SystemExit:
        pass


def test_market_data_processing_config_importable() -> None:
    """market_data_processing_service.config must be importable and expose get_service_config."""
    try:
        from market_data_processing_service.config import get_service_config

        assert callable(get_service_config)
    except SystemExit:
        pass
