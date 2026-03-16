"""Registry alignment tests -- verify cross-repo enum/set consistency."""

from __future__ import annotations


def test_uci_instrument_type_is_uac_reexport() -> None:
    """UCI InstrumentType must be the exact same class object as UAC InstrumentType.

    After consolidation, UCI no longer defines its own InstrumentType --
    it re-exports from unified_api_contracts.reference.
    """
    from unified_api_contracts.reference import InstrumentType as UAC_IT
    from unified_config_interface import InstrumentType as UCI_IT

    assert UCI_IT is UAC_IT, (
        "UCI InstrumentType is not the same object as UAC InstrumentType. "
        "UCI should re-export from unified_api_contracts.reference, not define its own."
    )


def test_uac_instrument_type_values_are_valid_strings() -> None:
    """All UAC InstrumentType members must have non-empty string values."""
    from unified_api_contracts.reference import InstrumentType

    for member in InstrumentType:
        assert isinstance(member.value, str), f"{member.name} value is not a string"
        assert len(member.value) > 0, f"{member.name} has empty value"


def test_uac_instrument_type_superset_of_uci() -> None:
    """UAC InstrumentType must be a superset of UCI InstrumentType.

    Since UCI now re-exports from UAC, this is trivially true, but this test
    guards against future regression if someone accidentally re-introduces
    a local definition.
    """
    from unified_api_contracts.reference import InstrumentType as UAC_IT
    from unified_config_interface import InstrumentType as UCI_IT

    uac_values = {m.value for m in UAC_IT}
    uci_values = {m.value for m in UCI_IT}
    missing = uci_values - uac_values
    assert not missing, f"UCI has InstrumentType values not in UAC: {missing}"


def test_venue_to_tardis_matches_inverted_venue_mapping() -> None:
    """UIC _VENUE_TO_TARDIS must match the inverted UCI VenueMapping.tardis_to_venue.

    After the consolidation, _VENUE_TO_TARDIS is computed from VenueMapping at
    module load time. This test verifies the inversion produces the expected
    mappings that were previously hardcoded.
    """
    from unified_config_interface.venue_config import VenueMapping
    from unified_internal_contracts.reference.instrument_key import _VENUE_TO_TARDIS

    mapping = VenueMapping()

    # Every canonical venue in _VENUE_TO_TARDIS must map back correctly
    for venue, tardis_exchange in _VENUE_TO_TARDIS.items():
        assert mapping.tardis_to_venue.get(tardis_exchange) == venue, (
            f"_VENUE_TO_TARDIS[{venue!r}] = {tardis_exchange!r} but "
            f"VenueMapping.tardis_to_venue[{tardis_exchange!r}] = "
            f"{mapping.tardis_to_venue.get(tardis_exchange)!r}"
        )

    # Critical venues that must be present (the previously-hardcoded ones)
    expected_venues = {
        "BINANCE-SPOT",
        "BINANCE-FUTURES",
        "DERIBIT",
        "BYBIT",
        "OKX",
        "UPBIT",
        "COINBASE",
    }
    actual_venues = set(_VENUE_TO_TARDIS.keys())
    missing = expected_venues - actual_venues
    assert not missing, f"Expected venues missing from _VENUE_TO_TARDIS: {missing}"
