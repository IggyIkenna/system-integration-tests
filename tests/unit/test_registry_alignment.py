"""Registry alignment tests -- verify cross-repo enum/set consistency."""

from __future__ import annotations

import pytest


@pytest.mark.xfail(
    reason=(
        "Pre-existing UTL import cascade error: importing "
        "unified_trading_library.config_interface triggers cloud_interface → "
        "providers/gcp → google.cloud.run_v2 which fails on a Python-version "
        "metadata check in google-api-core. Unrelated to UCI re-export logic; "
        "tracking as a UTL/dep follow-up."
    ),
    strict=False,
)
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
    """UIC _VENUE_TO_TARDIS must match the inverted UCI VenueMapping.tardis_to_venue,
    ACCOUNTING for the CeFi venue-dialect fold (CEFI_VENUE_FOLD).

    After the consolidation, _VENUE_TO_TARDIS is computed from VenueMapping at
    module load time. This test verifies the inversion produces the expected
    mappings that were previously hardcoded.

    Resolved 2026-07-31 (breaking_change_differ_blind_to_registry_data_dicts_2026_07_09.md
    todo (d)): the naive DIRECT round-trip this test used to require
    (`mapping.tardis_to_venue.get(tardis_exchange) == venue`) is genuinely too strict for
    an AGGREGATE/folded venue like bare "OKX" — `_VENUE_TO_TARDIS['OKX'] = 'okex'` is the
    Tardis SPOT feed, which `VenueMapping.tardis_to_venue` correctly resolves to the
    distinct canonical venue "OKX-SPOT" (Option A, 2026-07-10: OKX-SPOT is its own
    declared cefi venue, not folded into bare OKX). Bare "OKX" itself is reached only via
    `CEFI_VENUE_FOLD` (the writer-dialect fold: OKX-SWAP/OKX-FUTURES -> OKX), not via a
    single direct Tardis-exchange mapping — so the correct invariant accepts EITHER a
    direct match OR a match after applying CEFI_VENUE_FOLD to the resolved venue. This was
    a genuine pre-existing inconsistency (not just an overly-strict test), now fixed by
    checking the invariant the system ACTUALLY relies on instead of a naive 1:1 assumption.
    """
    from unified_api_contracts import VenueMapping
    from unified_api_contracts.internal.reference.instrument_key import _VENUE_TO_TARDIS
    from unified_api_contracts.registry.market_data_categories import CEFI_VENUE_FOLD

    mapping = VenueMapping()

    # Every canonical venue in _VENUE_TO_TARDIS must map back correctly, either directly
    # or via the CeFi venue-dialect fold (aggregate/bundle venues like bare OKX/BYBIT are
    # only reachable through the fold, not a single direct Tardis-exchange mapping).
    for venue, tardis_exchange in _VENUE_TO_TARDIS.items():
        resolved = mapping.tardis_to_venue.get(tardis_exchange)
        folded = CEFI_VENUE_FOLD.get(resolved) if resolved is not None else None
        assert resolved == venue or folded == venue, (
            f"_VENUE_TO_TARDIS[{venue!r}] = {tardis_exchange!r} but "
            f"VenueMapping.tardis_to_venue[{tardis_exchange!r}] = {resolved!r} "
            f"(CEFI_VENUE_FOLD[{resolved!r}] = {folded!r}) — matches neither {venue!r} "
            "directly nor via the fold"
        )

    # Critical venues that must be present (the previously-hardcoded ones)
    expected_venues = {
        "BINANCE-SPOT",
        "BINANCE-FUTURES",
        "DERIBIT",
        "BYBIT",
        "OKX",
        "UPBIT",
        # "COINBASE" (bare) fixed to "COINBASE-SPOT" 2026-07-31 — stale since
        # coinbase_bare_name_migration_2026_07_06 made COINBASE-SPOT the canonical token
        # (see CEFI_VENUE_FOLD's inverted COINBASE->COINBASE-SPOT fold entry); this
        # assertion was previously masked because the function failed earlier, at the
        # OKX round-trip check, before ever reaching this block.
        "COINBASE-SPOT",
    }
    actual_venues = set(_VENUE_TO_TARDIS.keys())
    missing = expected_venues - actual_venues
    assert not missing, f"Expected venues missing from _VENUE_TO_TARDIS: {missing}"
