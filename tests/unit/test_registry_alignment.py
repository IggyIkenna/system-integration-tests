"""Registry alignment tests -- verify cross-repo enum/set consistency."""

from __future__ import annotations


def test_instrument_type_not_reexported_from_uci() -> None:
    """UCI must NOT re-export InstrumentType.

    InstrumentType lives in UAC (unified-api-contracts). Consumers import
    directly from the source. UCI should not create a false ownership
    illusion by re-exporting it.
    """
    import unified_trading_library.config_interface as uci

    assert not hasattr(uci, "InstrumentType"), (
        "UCI should not export InstrumentType. Consumers must import from unified_api_contracts directly."
    )


def test_uac_instrument_type_is_canonical_source() -> None:
    """UAC InstrumentType must contain every type the system trades or references.

    These are the types actively used across execution-service (CLOB/DEX/zero-alpha),
    instruments-service (venue→type mapping), and strategy configs. If a type is
    removed from UAC, downstream services break.
    """
    from unified_api_contracts.reference import InstrumentType

    # Types required by at least one service or venue mapping
    required_types = {
        # CeFi / TradFi execution
        "SPOT_PAIR",
        "PERPETUAL",
        "FUTURE",
        "OPTION",
        "INDEX",
        # TradFi assets
        "BOND",
        "EQUITY",
        "ETF",
        "COMMODITY",
        "CURRENCY",
        # DeFi protocols
        "POOL",
        "LENDING",
        "STAKING",
        "YIELD_BEARING",
        "DEBT_TOKEN",
        "LST",
        "A_TOKEN",
        "SPOT_ASSET",
        # Sports / prediction markets
        "PREDICTION_MARKET",
        "EXCHANGE_ODDS",
        "FIXED_ODDS",
        "PROP",
    }
    actual_names = {m.name for m in InstrumentType}
    missing = required_types - actual_names
    assert not missing, (
        f"UAC InstrumentType missing required types: {missing}. "
        "These are used by execution-service, instruments-service, or strategy configs."
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
    from unified_api_contracts import VenueMapping
    from unified_api_contracts.internal.reference.instrument_key import _VENUE_TO_TARDIS

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
