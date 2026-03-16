"""Registry alignment tests -- verify cross-repo enum/set consistency."""

from __future__ import annotations


def test_instrument_type_not_reexported_from_uci() -> None:
    """UCI must NOT re-export InstrumentType.

    InstrumentType lives in UAC (unified-api-contracts). Consumers import
    directly from the source. UCI should not create a false ownership
    illusion by re-exporting it.
    """
    import unified_config_interface as uci

    assert not hasattr(uci, "InstrumentType"), (
        "UCI should not export InstrumentType. "
        "Consumers must import from unified_api_contracts directly."
    )


def test_uac_instrument_type_is_canonical_source() -> None:
    """UAC InstrumentType is the canonical source for all instrument types."""
    from unified_api_contracts.reference import InstrumentType

    assert len(InstrumentType) >= 20, (
        f"UAC InstrumentType has only {len(InstrumentType)} members, expected >= 20"
    )


def test_uac_instrument_type_values_are_valid_strings() -> None:
    """All UAC InstrumentType members must have non-empty string values."""
    from unified_api_contracts.reference import InstrumentType

    for member in InstrumentType:
        assert isinstance(member.value, str), f"{member.name} value is not a string"
        assert len(member.value) > 0, f"{member.name} has empty value"


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
