"""SIT integration test: historically deep-imported UAC classes must be top-level accessible.

Verifies that classes previously only reachable via deep submodule paths are now
accessible directly from unified_api_contracts (i.e., properly in __all__).
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_canonical_options_chain_entry_top_level() -> None:
    """CanonicalOptionsChainEntry must be accessible at top-level UAC (was deep import)."""
    from unified_api_contracts import CanonicalOptionsChainEntry

    assert CanonicalOptionsChainEntry is not None


@pytest.mark.integration
def test_team_mapping_top_level() -> None:
    """TeamMapping must be accessible at top-level UAC (was deep import in instruments-service)."""
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
