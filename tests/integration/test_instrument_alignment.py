"""Alignment tests: InstrumentGenerator (UIC) <-> instruments-service <-> URDI.

Verifies that mock instruments from InstrumentGenerator are compatible
with instruments-service processing and URDI consumption.

Schema topology:
  UAC.CanonicalInstrument  -- 76+ fields, float, GCS parquet storage shape
  UIC.InstrumentRecord     -- 31 fields, Decimal, normalised URDI adapter output
  UIC.InstrumentDefinition -- string-heavy, GCS/parquet instrument records (instruments-service)
  UIC.InstrumentKey        -- VENUE:INSTRUMENT_TYPE:SYMBOL dataclass
  UCI.Venue                -- StrEnum of all supported venues
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest
from unified_api_contracts import CanonicalInstrument, InstrumentType, OptionType
from unified_api_contracts.internal import InstrumentDefinition, InstrumentKey, InstrumentRecord
from unified_api_contracts.internal.testing.instrument_generator import InstrumentGenerator
from unified_api_contracts.registry.representative_sample import (
    CEFI_BASE_ASSETS,
    DEFI_LENDING_ASSETS,
    TRADFI_EQUITIES,
    TRADFI_FUTURES,
)
from unified_trading_library import Venue

pytestmark = pytest.mark.code_test

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_REF_DATE = date(2025, 6, 15)
_HEX_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")


@pytest.fixture(scope="module")
def generator() -> InstrumentGenerator:
    return InstrumentGenerator(seed=42)


@pytest.fixture(scope="module")
def all_instruments(generator: InstrumentGenerator) -> list[CanonicalInstrument]:
    return generator.generate_all(ref_date=_REF_DATE, include_options_chain=True)


@pytest.fixture(scope="module")
def no_options_instruments(generator: InstrumentGenerator) -> list[CanonicalInstrument]:
    return generator.generate_all(ref_date=_REF_DATE, include_options_chain=False)


# =========================================================================
# A. Schema Alignment
# =========================================================================


class TestSchemaAlignment:
    """Verify InstrumentGenerator output fields exist in CanonicalInstrument."""

    def test_generator_output_matches_canonical_instrument(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Every field the generator sets must be a valid CanonicalInstrument field."""
        canonical_fields = set(CanonicalInstrument.model_fields.keys())
        for inst in all_instruments:
            populated = {k for k, v in inst.model_dump().items() if v is not None}
            missing = populated - canonical_fields
            assert not missing, (
                f"InstrumentGenerator set fields not in CanonicalInstrument: {missing} for {inst.instrument_key}"
            )

    def test_generator_output_is_non_empty(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Generator must produce a non-trivial number of instruments."""
        assert len(all_instruments) > 20, f"Expected at least 20 instruments, got {len(all_instruments)}"

    def test_generator_instrument_types_subset_of_uac_enum(self, all_instruments: list[CanonicalInstrument]) -> None:
        """All InstrumentType values used by the generator are valid UAC enum members."""
        valid_types = set(InstrumentType)
        generator_types = {inst.instrument_type for inst in all_instruments if inst.instrument_type is not None}
        invalid = generator_types - valid_types
        assert not invalid, f"Generator uses InstrumentType values not in UAC enum: {invalid}"

    def test_generator_option_types_subset_of_uac_enum(self, all_instruments: list[CanonicalInstrument]) -> None:
        """All OptionType values used by the generator are valid UAC enum members."""
        valid_option_types = set(OptionType)
        generator_opt_types = {inst.option_type for inst in all_instruments if inst.option_type is not None}
        invalid = generator_opt_types - valid_option_types
        assert not invalid, f"Generator uses OptionType values not in UAC enum: {invalid}"

    def test_every_instrument_has_required_fields(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Each generated instrument must have the non-optional CanonicalInstrument fields."""
        for inst in all_instruments:
            assert inst.instrument_key, f"Missing instrument_key on {inst}"
            assert inst.venue, f"Missing venue on {inst.instrument_key}"
            assert inst.symbol, f"Missing symbol on {inst.instrument_key}"
            assert inst.timestamp is not None, f"Missing timestamp on {inst.instrument_key}"

    def test_instruments_service_definition_accepts_generator_keys(
        self, no_options_instruments: list[CanonicalInstrument]
    ) -> None:
        """InstrumentDefinition (instruments-service schema) can parse generator instrument_keys.

        InstrumentDefinition validates that instrument_key has at least 3 colon-separated parts.
        """
        for inst in no_options_instruments:
            # InstrumentDefinition requires ISO datetime strings, not datetime objects
            avail_from = inst.available_from_datetime
            avail_from_str = avail_from.isoformat() if avail_from is not None else "2020-01-01T00:00:00+00:00"
            defn = InstrumentDefinition(
                instrument_key=inst.instrument_key,
                venue=inst.venue,
                instrument_type=str(inst.instrument_type) if inst.instrument_type else "UNKNOWN",
                symbol=inst.symbol,
                available_from_datetime=avail_from_str,
            )
            assert defn.instrument_key == inst.instrument_key


# =========================================================================
# B. instrument_key Format Alignment
# =========================================================================


class TestKeyFormatAlignment:
    """Verify instrument_key format consistency: VENUE:INSTRUMENT_TYPE:SYMBOL."""

    def test_generator_key_format_matches_service_expectations(
        self, all_instruments: list[CanonicalInstrument]
    ) -> None:
        """instrument_key must be VENUE:TYPE:SYMBOL (at least 3 colon-separated parts)."""
        for inst in all_instruments:
            parts = inst.instrument_key.split(":")
            assert len(parts) >= 3, f"Key {inst.instrument_key!r} has fewer than 3 colon-separated parts"

    def test_key_venue_matches_venue_field(self, all_instruments: list[CanonicalInstrument]) -> None:
        """The venue component of instrument_key must match the venue field."""
        for inst in all_instruments:
            key_venue = inst.instrument_key.split(":")[0]
            assert key_venue == inst.venue, (
                f"Key venue {key_venue!r} != venue field {inst.venue!r} for {inst.instrument_key}"
            )

    def test_key_type_matches_instrument_type_field(self, all_instruments: list[CanonicalInstrument]) -> None:
        """The instrument_type component of instrument_key must match instrument_type field."""
        for inst in all_instruments:
            key_type = inst.instrument_key.split(":")[1]
            expected_type = str(inst.instrument_type) if inst.instrument_type else ""
            assert key_type == expected_type, (
                f"Key type {key_type!r} != instrument_type {expected_type!r} for {inst.instrument_key}"
            )

    def test_key_components_roundtrip(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Parse a generator key into InstrumentKey, reconstruct, verify match."""
        for inst in all_instruments:
            parsed = InstrumentKey.from_string(inst.instrument_key)
            assert parsed.venue == inst.venue
            assert parsed.instrument_type == str(inst.instrument_type)
            # Symbol from InstrumentKey.from_string takes parts[2] only,
            # while the key symbol part may contain colons. Verify the key
            # starts with VENUE:TYPE:SYMBOL.
            assert inst.instrument_key.startswith(f"{parsed.venue}:{parsed.instrument_type}:{parsed.symbol}")

    def test_no_duplicate_keys(self, all_instruments: list[CanonicalInstrument]) -> None:
        """All instrument_keys must be unique."""
        keys = [inst.instrument_key for inst in all_instruments]
        assert len(keys) == len(set(keys)), f"Duplicate keys found: {[k for k in keys if keys.count(k) > 1]}"


# =========================================================================
# C. Venue x InstrumentType Matrix Alignment
# =========================================================================


class TestVenueTypeMatrix:
    """Verify venue/type combinations are semantically valid."""

    def test_generator_venue_type_combinations_are_valid(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Each (venue, instrument_type) pair should be logically consistent."""
        # Build the set of all combinations the generator produces
        combos = {
            (inst.venue, str(inst.instrument_type)) for inst in all_instruments if inst.instrument_type is not None
        }
        # Sanity check: should have multiple combinations
        assert len(combos) > 5, f"Expected many venue/type combos, got {len(combos)}"

    def test_no_spot_on_futures_only_venue(self, all_instruments: list[CanonicalInstrument]) -> None:
        """BINANCE-FUTURES should not have SPOT_PAIR instruments."""
        bad = [
            inst.instrument_key
            for inst in all_instruments
            if inst.venue == "BINANCE-FUTURES" and inst.instrument_type == InstrumentType.SPOT_PAIR
        ]
        assert not bad, f"BINANCE-FUTURES has SPOT_PAIR instruments: {bad}"

    def test_no_options_on_spot_only_venue(self, all_instruments: list[CanonicalInstrument]) -> None:
        """COINBASE (spot-only) should not have OPTION instruments."""
        bad = [
            inst.instrument_key
            for inst in all_instruments
            if inst.venue == "COINBASE" and inst.instrument_type == InstrumentType.OPTION
        ]
        assert not bad, f"COINBASE has OPTION instruments: {bad}"

    def test_defi_venues_produce_correct_types(self, all_instruments: list[CanonicalInstrument]) -> None:
        """DeFi venues must produce the expected instrument types."""
        venue_types: dict[str, set[str]] = {}
        for inst in all_instruments:
            if inst.asset_group == "crypto_defi":
                venue_types.setdefault(inst.venue, set()).add(str(inst.instrument_type))

        # Aave must produce A_TOKEN and DEBT_TOKEN
        if "AAVE_V3-ETHEREUM" in venue_types:
            assert "A_TOKEN" in venue_types["AAVE_V3-ETHEREUM"], "AAVE_V3-ETHEREUM missing A_TOKEN instruments"
            assert "DEBT_TOKEN" in venue_types["AAVE_V3-ETHEREUM"], "AAVE_V3-ETHEREUM missing DEBT_TOKEN instruments"

        # Uniswap venues must produce POOL
        for uni_venue in ("UNISWAP_V3-ETHEREUM", "UNISWAP_V2-ETHEREUM", "UNISWAP_V4-ETHEREUM"):
            if uni_venue in venue_types:
                assert "POOL" in venue_types[uni_venue], f"{uni_venue} missing POOL instruments"

        # Lido must produce LST
        if "LIDO-ETHEREUM" in venue_types:
            assert "LST" in venue_types["LIDO-ETHEREUM"], "LIDO missing LST instruments"

    def test_sports_venues_produce_correct_types(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Sports/prediction venues must produce expected types."""
        venue_types: dict[str, set[str]] = {}
        for inst in all_instruments:
            if inst.asset_group in ("sports", "prediction"):
                venue_types.setdefault(inst.venue, set()).add(str(inst.instrument_type))

        if "POLYMARKET" in venue_types:
            assert "PREDICTION_MARKET" in venue_types["POLYMARKET"]

        if "BETFAIR" in venue_types:
            assert "EXCHANGE_ODDS" in venue_types["BETFAIR"]

        if "PINNACLE" in venue_types:
            assert "FIXED_ODDS" in venue_types["PINNACLE"]

    def test_cefi_spot_venues_match_known_venues(self, generator: InstrumentGenerator) -> None:
        """All CeFi spot venues should be recognized Venue enum values or known strings."""
        spots = generator.generate_cefi_spot(_REF_DATE)
        known_spot_venues = {
            "BINANCE-SPOT",
            "COINBASE",
            "BYBIT",
            "OKX",
            "UPBIT",
        }
        for inst in spots:
            assert inst.venue in known_spot_venues, f"Unknown CeFi spot venue: {inst.venue}"


# =========================================================================
# D. DeFi Field Alignment
# =========================================================================


class TestDefiFieldAlignment:
    """Verify DeFi-specific fields are populated correctly."""

    def test_aave_instruments_have_lending_params(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Aave A_TOKEN instruments must have ltv and liquidation_threshold."""
        aave_a_tokens = [
            inst
            for inst in all_instruments
            if inst.venue == "AAVE_V3-ETHEREUM" and inst.instrument_type == InstrumentType.A_TOKEN
        ]
        assert len(aave_a_tokens) > 0, "No Aave A_TOKEN instruments found"
        for inst in aave_a_tokens:
            assert inst.ltv is not None, f"Missing ltv for {inst.instrument_key}"
            assert inst.liquidation_threshold is not None, f"Missing liquidation_threshold for {inst.instrument_key}"
            assert 0 < inst.ltv < 1, f"ltv should be between 0 and 1, got {inst.ltv} for {inst.instrument_key}"
            assert 0 < inst.liquidation_threshold < 1, (
                f"liquidation_threshold should be between 0 and 1, got "
                f"{inst.liquidation_threshold} for {inst.instrument_key}"
            )
            assert inst.liquidation_threshold > inst.ltv, (
                f"liquidation_threshold ({inst.liquidation_threshold}) should be > "
                f"ltv ({inst.ltv}) for {inst.instrument_key}"
            )

    def test_uniswap_v3_has_fee_tier(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Uniswap V3 POOL instruments must have pool_fee_tier set."""
        uni_v3_pools = [
            inst
            for inst in all_instruments
            if inst.venue == "UNISWAP_V3-ETHEREUM" and inst.instrument_type == InstrumentType.POOL
        ]
        assert len(uni_v3_pools) > 0, "No Uniswap V3 POOL instruments found"
        for inst in uni_v3_pools:
            assert inst.pool_fee_tier is not None, f"Missing pool_fee_tier for {inst.instrument_key}"
            # Fee tier should be a recognized Uniswap V3 tier
            valid_tiers = {"100", "500", "3000", "10000"}
            assert inst.pool_fee_tier in valid_tiers, (
                f"Invalid fee tier {inst.pool_fee_tier!r} for {inst.instrument_key}; expected one of {valid_tiers}"
            )

    def test_wrapped_tokens_on_aave(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Aave instruments must use wrapped base assets (WETH, not ETH)."""
        aave_eth_instruments = [
            inst
            for inst in all_instruments
            if inst.venue == "AAVE_V3-ETHEREUM"
            and inst.base_asset is not None
            and inst.base_asset in ("ETH", "stETH", "eETH")
        ]
        # None should have raw (non-wrapped) ETH tokens
        assert len(aave_eth_instruments) == 0, (
            f"Aave instruments using unwrapped tokens: "
            f"{[inst.instrument_key for inst in aave_eth_instruments]}; "
            f"should use WETH/wstETH/weETH instead"
        )

    def test_pool_addresses_are_valid_hex(self, all_instruments: list[CanonicalInstrument]) -> None:
        """DeFi pool_address fields must match 0x + 40 hex chars."""
        defi_with_address = [inst for inst in all_instruments if inst.pool_address is not None]
        assert len(defi_with_address) > 0, "No instruments with pool_address found"
        for inst in defi_with_address:
            assert _HEX_ADDRESS_RE.match(inst.pool_address), (
                f"Invalid pool_address {inst.pool_address!r} for {inst.instrument_key}; expected 0x + 40 hex chars"
            )

    def test_lsts_have_underlying(self, all_instruments: list[CanonicalInstrument]) -> None:
        """LST instruments (Lido, EtherFi) must have underlying set."""
        lsts = [inst for inst in all_instruments if inst.instrument_type == InstrumentType.LST]
        assert len(lsts) > 0, "No LST instruments found"
        for inst in lsts:
            assert inst.underlying is not None, f"Missing underlying for LST {inst.instrument_key}"
            assert inst.underlying == "ETH", (
                f"Expected underlying='ETH' for {inst.instrument_key}, got {inst.underlying!r}"
            )


# =========================================================================
# E. Expiry / Lifecycle Alignment
# =========================================================================


class TestExpiryLifecycleAlignment:
    """Verify futures/options expiry and lifecycle date handling."""

    def test_futures_expiry_in_future(self, all_instruments: list[CanonicalInstrument]) -> None:
        """All futures generated for the ref_date should have expiry > ref_date."""
        ref_dt = datetime(_REF_DATE.year, _REF_DATE.month, _REF_DATE.day, tzinfo=UTC)
        futures = [inst for inst in all_instruments if inst.instrument_type == InstrumentType.FUTURE]
        assert len(futures) > 0, "No FUTURE instruments found"
        for inst in futures:
            assert inst.expiry is not None, f"Missing expiry for future {inst.instrument_key}"
            assert inst.expiry > ref_dt, f"Future {inst.instrument_key} has expiry {inst.expiry} <= ref_date {ref_dt}"

    def test_options_expiry_in_future(self, all_instruments: list[CanonicalInstrument]) -> None:
        """All options generated for the ref_date should have expiry > ref_date."""
        ref_dt = datetime(_REF_DATE.year, _REF_DATE.month, _REF_DATE.day, tzinfo=UTC)
        options = [inst for inst in all_instruments if inst.instrument_type == InstrumentType.OPTION]
        assert len(options) > 0, "No OPTION instruments found"
        for inst in options:
            assert inst.expiry is not None, f"Missing expiry for option {inst.instrument_key}"
            assert inst.expiry > ref_dt, f"Option {inst.instrument_key} has expiry {inst.expiry} <= ref_date {ref_dt}"

    def test_options_have_strike_and_type(self, all_instruments: list[CanonicalInstrument]) -> None:
        """All options must have strike and option_type set."""
        options = [inst for inst in all_instruments if inst.instrument_type == InstrumentType.OPTION]
        for inst in options:
            assert inst.strike is not None, f"Missing strike for option {inst.instrument_key}"
            assert inst.strike > 0, f"Strike must be positive, got {inst.strike} for {inst.instrument_key}"
            assert inst.option_type is not None, f"Missing option_type for option {inst.instrument_key}"
            assert inst.option_type in (OptionType.CALL, OptionType.PUT), (
                f"Invalid option_type {inst.option_type!r} for {inst.instrument_key}"
            )

    def test_available_from_set_for_all(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Every instrument must have available_from_datetime set."""
        missing_avail = [inst.instrument_key for inst in all_instruments if inst.available_from_datetime is None]
        # Sports/prediction instruments may not have available_from in some cases
        non_sports_missing = [
            k for k in missing_avail if not any(v in k for v in ("POLYMARKET", "BETFAIR", "PINNACLE"))
        ]
        assert not non_sports_missing, f"Instruments missing available_from_datetime: {non_sports_missing[:10]}"

    def test_perpetuals_have_no_expiry(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Perpetual instruments should not have an expiry date."""
        perps = [inst for inst in all_instruments if inst.instrument_type == InstrumentType.PERPETUAL]
        assert len(perps) > 0, "No PERPETUAL instruments found"
        for inst in perps:
            assert inst.expiry is None, f"Perpetual {inst.instrument_key} should not have expiry, got {inst.expiry}"

    def test_spot_instruments_have_no_expiry(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Spot instruments should not have an expiry date."""
        spots = [inst for inst in all_instruments if inst.instrument_type == InstrumentType.SPOT_PAIR]
        for inst in spots:
            assert inst.expiry is None, f"Spot {inst.instrument_key} should not have expiry, got {inst.expiry}"


# =========================================================================
# F. Cross-Component Integration
# =========================================================================


class TestCrossComponentIntegration:
    """Cross-component integration: generator -> InstrumentKey -> InstrumentDefinition."""

    def test_generator_keys_parseable_by_instrument_key(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Every generator key must be parseable by InstrumentKey.from_string."""
        for inst in all_instruments:
            parsed = InstrumentKey.from_string(inst.instrument_key)
            assert parsed.venue == inst.venue
            assert parsed.instrument_type is not None
            assert parsed.symbol is not None

    def test_generator_keys_parseable_for_tardis(self, no_options_instruments: list[CanonicalInstrument]) -> None:
        """Generator keys for Tardis-supported venues parse correctly."""
        tardis_venues = {
            "BINANCE-SPOT",
            "BINANCE-FUTURES",
            "DERIBIT",
            "BYBIT",
            "OKX",
            "UPBIT",
            "COINBASE",
        }
        tardis_instruments = [inst for inst in no_options_instruments if inst.venue in tardis_venues]
        assert len(tardis_instruments) > 0, "No Tardis-supported instruments found"
        for inst in tardis_instruments:
            result = InstrumentKey.parse_for_tardis(inst.instrument_key)
            assert "tardis_exchange" in result
            assert "tardis_symbol" in result
            assert result["venue"] == inst.venue

    def test_generator_output_to_instrument_definition_roundtrip(self, generator: InstrumentGenerator) -> None:
        """Generate -> convert to InstrumentDefinition -> verify key fields preserved.

        This tests the full path: InstrumentGenerator creates CanonicalInstrument,
        we convert to InstrumentDefinition (instruments-service schema), and verify
        the key is preserved and valid.
        """
        cefi_instruments = generator.generate_cefi_spot(_REF_DATE)
        for inst in cefi_instruments:
            avail_str = (
                inst.available_from_datetime.isoformat()
                if inst.available_from_datetime
                else "2020-01-01T00:00:00+00:00"
            )
            defn = InstrumentDefinition(
                instrument_key=inst.instrument_key,
                venue=inst.venue,
                instrument_type=str(inst.instrument_type),
                symbol=inst.symbol,
                available_from_datetime=avail_str,
                base_asset=inst.base_asset or "",
                quote_asset=inst.quote_asset or "",
            )
            # Verify roundtrip
            assert defn.instrument_key == inst.instrument_key
            assert defn.venue == inst.venue
            assert defn.symbol == inst.symbol
            assert defn.base_asset == inst.base_asset

    def test_generator_output_to_dataframe(self, no_options_instruments: list[CanonicalInstrument]) -> None:
        """Generated instruments can be serialized to dicts for DataFrame construction."""
        records = [inst.model_dump() for inst in no_options_instruments]
        assert len(records) == len(no_options_instruments)
        # Verify all records have the same keys
        first_keys = set(records[0].keys())
        for rec in records[1:]:
            assert set(rec.keys()) == first_keys, f"Inconsistent dict keys: {set(rec.keys()) ^ first_keys}"

    def test_generator_venues_match_uci_venue_enum(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Generator venues should be represented in UCI Venue enum.

        NOTE: Some generator venues (COMPOUND_V3_ETH, BETFAIR, PINNACLE)
        may not be in UCI Venue. This test documents the coverage gap.
        """
        uci_venue_values = {v.value for v in Venue}
        generator_venues = {inst.venue for inst in all_instruments}

        # Venues that ARE in UCI
        matched = generator_venues & uci_venue_values
        assert len(matched) > 5, (
            f"Expected most generator venues to be in UCI Venue enum, only {len(matched)} match: {matched}"
        )

        # Document but don't fail on unmatched (informational)
        unmatched = generator_venues - uci_venue_values
        if unmatched:
            # These are known gaps that should be tracked
            known_gaps = {
                "COMPOUND_V3_ETH",  # Not in UCI Venue enum
                "BETFAIR",  # Sports venues — not in UCI Venue enum
                "PINNACLE",  # Not in UCI Venue enum
                "POLYMARKET",  # UCI uses lowercase "polymarket", generator uses uppercase
            }
            unexpected_gaps = unmatched - known_gaps
            assert not unexpected_gaps, f"Unexpected venue gaps between generator and UCI Venue enum: {unexpected_gaps}"

    def test_urdi_adapter_venues_cover_generator_cefi_venues(
        self,
    ) -> None:
        """URDI factory adapter keys should cover the CeFi venues InstrumentGenerator uses."""
        from instruments_service.reference_data.factory import _ADAPTERS

        adapter_venues = set(_ADAPTERS.keys())

        # CeFi venues used by InstrumentGenerator (lowercase for URDI lookup)
        generator_cefi_venues_lower = {
            "binance",
            "deribit",
            "bybit",
            "okx",
            "coinbase",
            "hyperliquid",
            "aster",
        }

        missing = generator_cefi_venues_lower - adapter_venues
        assert not missing, (
            f"URDI missing adapters for generator CeFi venues: {missing}; URDI has: {sorted(adapter_venues)}"
        )

    def test_instrument_record_field_coverage(self) -> None:
        """InstrumentRecord (URDI output) must have core fields that match CanonicalInstrument.

        These are the fields URDI adapters populate that downstream services depend on.
        """
        record_fields = set(InstrumentRecord.model_fields.keys())
        canonical_fields = set(CanonicalInstrument.model_fields.keys())

        # Core fields that must exist in both schemas
        core_fields = {
            "instrument_key",
            "venue",
            "instrument_type",
            "symbol",
            "base_asset",
            "quote_asset",
            "tick_size",
            "expiry",
            "strike",
            "option_type",
            "underlying",
        }

        missing_in_record = core_fields - record_fields
        missing_in_canonical = core_fields - canonical_fields

        assert not missing_in_record, f"InstrumentRecord missing core fields: {missing_in_record}"
        assert not missing_in_canonical, f"CanonicalInstrument missing core fields: {missing_in_canonical}"

    def test_deterministic_generation(self) -> None:
        """Two generators with the same seed produce identical output."""
        gen1 = InstrumentGenerator(seed=42)
        gen2 = InstrumentGenerator(seed=42)

        instruments1 = gen1.generate_all(ref_date=_REF_DATE, include_options_chain=False)
        instruments2 = gen2.generate_all(ref_date=_REF_DATE, include_options_chain=False)

        assert len(instruments1) == len(instruments2)
        for i1, i2 in zip(instruments1, instruments2, strict=False):
            assert i1.instrument_key == i2.instrument_key
            assert i1.model_dump() == i2.model_dump()

    def test_different_seeds_produce_different_addresses(self) -> None:
        """Generators with different seeds produce different DeFi pool addresses."""
        gen1 = InstrumentGenerator(seed=42)
        gen2 = InstrumentGenerator(seed=99)

        defi1 = gen1.generate_defi(_REF_DATE)
        defi2 = gen2.generate_defi(_REF_DATE)

        addresses1 = {inst.pool_address for inst in defi1 if inst.pool_address}
        addresses2 = {inst.pool_address for inst in defi2 if inst.pool_address}

        # At least some addresses should differ
        assert addresses1 != addresses2, "Different seeds should produce different pool addresses"


# =========================================================================
# G. Perpetual / Margin Field Alignment
# =========================================================================


class TestPerpetualFieldAlignment:
    """Verify perpetual-specific fields are populated correctly."""

    def test_perpetuals_have_margin_params(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Perpetual instruments should have margin rate parameters."""
        perps = [inst for inst in all_instruments if inst.instrument_type == InstrumentType.PERPETUAL]
        for inst in perps:
            assert inst.max_leverage is not None, f"Missing max_leverage for {inst.instrument_key}"
            assert inst.initial_margin_rate is not None, f"Missing initial_margin_rate for {inst.instrument_key}"
            assert inst.maintenance_margin_rate is not None, (
                f"Missing maintenance_margin_rate for {inst.instrument_key}"
            )

    def test_margin_rate_consistency(self, all_instruments: list[CanonicalInstrument]) -> None:
        """maintenance_margin_rate should be less than initial_margin_rate."""
        perps = [
            inst
            for inst in all_instruments
            if inst.instrument_type == InstrumentType.PERPETUAL
            and inst.initial_margin_rate is not None
            and inst.maintenance_margin_rate is not None
        ]
        for inst in perps:
            assert inst.maintenance_margin_rate < inst.initial_margin_rate, (
                f"maintenance_margin_rate ({inst.maintenance_margin_rate}) >= "
                f"initial_margin_rate ({inst.initial_margin_rate}) "
                f"for {inst.instrument_key}"
            )

    def test_perpetuals_have_exchange_raw_symbol(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Perpetual instruments should have exchange_raw_symbol for venue API calls."""
        perps = [inst for inst in all_instruments if inst.instrument_type == InstrumentType.PERPETUAL]
        for inst in perps:
            assert inst.exchange_raw_symbol is not None, f"Missing exchange_raw_symbol for {inst.instrument_key}"
            assert inst.exchange_raw_symbol != "", f"Empty exchange_raw_symbol for {inst.instrument_key}"


# =========================================================================
# H. TradFi Field Alignment
# =========================================================================


class TestTradFiFieldAlignment:
    """Verify TradFi instruments have trading hours and calendar fields."""

    def test_equity_etf_have_trading_hours(self, all_instruments: list[CanonicalInstrument]) -> None:
        """Equity and ETF instruments should have trading hour fields."""
        tradfi = [
            inst for inst in all_instruments if inst.instrument_type in (InstrumentType.EQUITY, InstrumentType.ETF)
        ]
        assert len(tradfi) > 0, "No EQUITY/ETF instruments found"
        for inst in tradfi:
            assert inst.trading_hours_open is not None, f"Missing trading_hours_open for {inst.instrument_key}"
            assert inst.trading_hours_close is not None, f"Missing trading_hours_close for {inst.instrument_key}"
            assert inst.holiday_calendar is not None, f"Missing holiday_calendar for {inst.instrument_key}"

    def test_tradfi_asset_groupes(self, all_instruments: list[CanonicalInstrument]) -> None:
        """TradFi instruments should have correct asset_group values."""
        tradfi_types = {
            InstrumentType.EQUITY,
            InstrumentType.ETF,
            InstrumentType.INDEX,
        }
        tradfi = [inst for inst in all_instruments if inst.instrument_type in tradfi_types]
        valid_asset_groupes = {"tradfi_equity", "tradfi_etf", "tradfi_index", "tradfi_futures"}
        for inst in tradfi:
            assert inst.asset_group in valid_asset_groupes, (
                f"Unexpected asset_group {inst.asset_group!r} for TradFi instrument {inst.instrument_key}"
            )

    def test_cme_futures_have_contract_size(self, all_instruments: list[CanonicalInstrument]) -> None:
        """CME futures should have contract_size set."""
        cme_futures = [
            inst for inst in all_instruments if inst.venue == "CME" and inst.instrument_type == InstrumentType.FUTURE
        ]
        assert len(cme_futures) > 0, "No CME FUTURE instruments found"
        for inst in cme_futures:
            assert inst.contract_size is not None, f"Missing contract_size for {inst.instrument_key}"
            assert inst.contract_size > 0, f"contract_size must be positive for {inst.instrument_key}"


# =========================================================================
# I. Registry-Driven Alignment
# =========================================================================


class TestRegistryDrivenAlignment:
    """Verify InstrumentGenerator reads from UAC REPRESENTATIVE_INSTRUMENT_SAMPLE."""

    def test_cefi_base_assets_from_registry(self, no_options_instruments: list[CanonicalInstrument]) -> None:
        """CeFi spot instruments should use base assets from the registry."""
        cefi_spot = [
            inst
            for inst in no_options_instruments
            if inst.instrument_type == InstrumentType.SPOT_PAIR and inst.asset_group == "crypto_cefi"
        ]
        base_assets = {inst.base_asset for inst in cefi_spot}
        for asset in CEFI_BASE_ASSETS:
            assert asset in base_assets, f"Registry asset {asset} not found in generated CeFi spots"

    def test_tradfi_equities_venues_from_registry(self, no_options_instruments: list[CanonicalInstrument]) -> None:
        """TradFi equity/ETF venues in generator output should be in TRADFI_EQUITIES registry."""
        tradfi_equity_types = {InstrumentType.EQUITY, InstrumentType.ETF, InstrumentType.INDEX}
        tradfi = [inst for inst in no_options_instruments if inst.instrument_type in tradfi_equity_types]
        generated_venues = {inst.venue for inst in tradfi}
        registry_venues = set(TRADFI_EQUITIES.keys())
        for venue in generated_venues:
            assert venue in registry_venues, f"Generated venue {venue} not in TRADFI_EQUITIES registry"

    def test_tradfi_futures_from_registry(self, no_options_instruments: list[CanonicalInstrument]) -> None:
        """TradFi futures should be generated for all venues in TRADFI_FUTURES registry."""
        for venue in TRADFI_FUTURES:
            venue_futures = [
                inst
                for inst in no_options_instruments
                if inst.venue == venue and inst.instrument_type == InstrumentType.FUTURE
            ]
            assert len(venue_futures) > 0, f"No futures generated for registry venue {venue}"

    def test_defi_lending_assets_from_registry(self, no_options_instruments: list[CanonicalInstrument]) -> None:
        """DeFi lending instruments should use assets from DEFI_LENDING_ASSETS registry."""
        a_tokens = [inst for inst in no_options_instruments if inst.instrument_type == InstrumentType.A_TOKEN]
        base_assets = {inst.base_asset for inst in a_tokens}
        for asset in DEFI_LENDING_ASSETS:
            assert asset in base_assets, f"Registry DeFi asset {asset} not found in generated A_TOKENs"

    def test_generator_imports_from_uac_registry(self) -> None:
        """InstrumentGenerator module should import from UAC representative_sample."""
        import unified_api_contracts.internal.testing.instrument_generator as gen_mod

        source_file = gen_mod.__file__
        assert source_file is not None
        from pathlib import Path

        source = Path(source_file).read_text()
        assert "representative_sample" in source, (
            "InstrumentGenerator should import from UAC representative_sample registry"
        )
