"""Layer 0 smoke tests: verify schema imports and basic contract validation.

These tests verify that all T0 interface libraries (UAC, UIC, UEI) are importable
and their key schemas are intact. No cloud credentials required.
Runs as part of quickmerge gate (Layer 0 — blocks contract alignment).
"""

import pytest

pytestmark = pytest.mark.code_test


def test_uac_schemas_importable() -> None:
    """UAC top-level schemas must all be importable without errors."""
    import unified_api_contracts

    assert unified_api_contracts is not None


def test_uac_canonical_mappings_importable() -> None:
    """UAC canonical mappings (VENUE_TO_DATA_SOURCE etc.) must be importable."""
    from unified_api_contracts import (
        DATA_SOURCE_TO_VENUES,
        VENUE_TO_DATA_SOURCE,
    )

    assert isinstance(VENUE_TO_DATA_SOURCE, dict)
    assert isinstance(DATA_SOURCE_TO_VENUES, dict)
    assert len(VENUE_TO_DATA_SOURCE) > 0


def test_uac_facade_modules_non_empty() -> None:
    """UAC facade modules (market, execution, errors) must be importable and non-empty."""
    from unified_api_contracts import errors, execution, market

    for mod_name, mod in [("market", market), ("execution", execution), ("errors", errors)]:
        assert mod is not None, f"unified_api_contracts.{mod_name} is None"
        exported = [name for name in dir(mod) if not name.startswith("_")]
        assert len(exported) > 0, f"unified_api_contracts.{mod_name} exports nothing"


def test_uic_schemas_importable() -> None:
    """UIC top-level package must be importable without errors."""
    import unified_api_contracts.internal

    assert unified_api_contracts.internal is not None


def test_uic_features_importable() -> None:
    """UIC features module must be importable."""
    from unified_api_contracts.internal import features

    assert features is not None


def test_uic_execution_importable() -> None:
    """UIC execution module must be importable."""
    from unified_api_contracts.internal import execution

    assert execution is not None


def test_uic_market_data_importable() -> None:
    """UIC market_data subpackage must be importable."""
    from unified_api_contracts.internal import market_data

    assert market_data is not None


def test_uic_events_importable() -> None:
    """UIC events module must be importable."""
    from unified_api_contracts.internal import events

    assert events is not None


def test_uic_domain_instruments_schema() -> None:
    """UIC instruments domain schema must be present and non-None."""
    from unified_api_contracts.internal.domain.instruments import INSTRUMENTS_SCHEMA

    assert INSTRUMENTS_SCHEMA is not None


def test_events_interface_importable() -> None:
    """UEI setup_events and log_event must be importable."""
    from unified_trading_library.events import log_event, setup_events

    assert setup_events is not None
    assert log_event is not None
    assert callable(setup_events)
    assert callable(log_event)


def test_uic_pubsub_importable() -> None:
    """UIC pubsub schema module must be importable."""
    from unified_api_contracts.internal import pubsub

    assert pubsub is not None


def test_uic_positions_importable() -> None:
    """UIC positions subpackage must be importable."""
    from unified_api_contracts.internal import positions

    assert positions is not None
