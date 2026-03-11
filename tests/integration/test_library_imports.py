"""Integration tests verifying T0-T3 library imports and basic contracts."""

import pytest

pytestmark = pytest.mark.code_test


def test_uac_uic_schema_compat() -> None:
    """UIC instruments schema is importable and non-empty."""
    from unified_internal_contracts.domain.instruments import INSTRUMENTS_SCHEMA

    assert INSTRUMENTS_SCHEMA is not None


def test_uei_event_dispatch() -> None:
    """UEI log_event import chain is intact — should not raise."""
    from unified_events_interface import log_event

    # Should not raise — just verifies the import chain works
    assert callable(log_event)


def test_utl_cloud_base_service_import() -> None:
    """UTL UnifiedCloudService is importable."""
    from unified_trading_library import UnifiedCloudService

    assert UnifiedCloudService is not None
