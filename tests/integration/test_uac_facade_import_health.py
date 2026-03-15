"""SIT integration test: UAC facade imports are healthy.

Verifies that all key UAC types are accessible via top-level facade imports
(not deep submodule paths). Transition facades (canonical/execution.py,
canonical/options.py, canonical/odds.py, canonical/spread.py) were deleted
in Phase 5; all symbols must be reachable from `unified_api_contracts` directly.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.code_test


@pytest.mark.integration
def test_canonical_options_chain_entry_top_level() -> None:
    """CanonicalOptionsChainEntry must be accessible at top-level UAC."""
    from unified_api_contracts import CanonicalOptionsChainEntry

    assert CanonicalOptionsChainEntry is not None


@pytest.mark.integration
def test_team_mapping_top_level() -> None:
    """TeamMapping must be accessible at top-level UAC."""
    from unified_api_contracts import TeamMapping

    assert TeamMapping is not None


@pytest.mark.integration
def test_normalized_strike_coordinate_top_level() -> None:
    """NormalizedStrikeCoordinate must be accessible at top-level UAC."""
    from unified_api_contracts import NormalizedStrikeCoordinate

    assert NormalizedStrikeCoordinate is not None


@pytest.mark.integration
def test_option_chain_snapshot_top_level() -> None:
    """OptionChainSnapshot must be accessible at top-level UAC."""
    from unified_api_contracts import OptionChainSnapshot

    assert OptionChainSnapshot is not None


@pytest.mark.integration
def test_sports_classes_top_level() -> None:
    """BetExecution, BetStatus, SignalSource must be accessible at top-level UAC."""
    from unified_api_contracts import BetExecution, BetStatus, SignalSource

    assert BetExecution is not None
    assert BetStatus is not None
    assert SignalSource is not None


@pytest.mark.integration
def test_execution_classes_top_level() -> None:
    """Execution types accessible from top-level after transition facade removal."""
    from unified_api_contracts import (
        CanonicalFill,
        CanonicalOrder,
        ExecutionResult,
        OrderSide,
        OrderStatus,
        OrderType,
    )

    assert CanonicalOrder is not None
    assert CanonicalFill is not None
    assert OrderSide is not None
    assert OrderType is not None
    assert OrderStatus is not None
    assert ExecutionResult is not None


@pytest.mark.integration
def test_spread_classes_top_level() -> None:
    """Spread types accessible from top-level after transition facade removal."""
    from unified_api_contracts import CanonicalSpread, SpreadLeg

    assert CanonicalSpread is not None
    assert SpreadLeg is not None


@pytest.mark.integration
def test_odds_conversion_top_level() -> None:
    """Odds conversion utilities accessible from top-level after transition facade removal."""
    from unified_api_contracts import american_to_decimal, decimal_to_american

    assert callable(american_to_decimal)
    assert callable(decimal_to_american)
