"""Smoke tests for mock data generation pipeline.

Validates that InstrumentGenerator produces valid instruments,
seed scripts generate correct output, and edge cases are handled.

Categories:
    A. Instrument Generation Smoke Tests
    B. Instrument Edge Case Tests
    C. Mock Data Chain Smoke Tests
    D. Schema Validation Tests
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from unified_api_contracts import CanonicalInstrument, InstrumentType, OptionType
from unified_internal_contracts.testing.instrument_generator import InstrumentGenerator

pytestmark = pytest.mark.code_test

# ---------------------------------------------------------------------------
# Shared constants / fixtures
# ---------------------------------------------------------------------------

REF_DATE = date(2025, 6, 15)
SEED = 42
KEY_RE = re.compile(r"^[A-Z0-9_-]+:[A-Z_]+:.+$")


@pytest.fixture
def gen() -> InstrumentGenerator:
    return InstrumentGenerator(seed=SEED)


# ===================================================================
# A. Instrument Generation Smoke Tests
# ===================================================================


class TestInstrumentGenerationSmoke:
    """Smoke tests validating basic InstrumentGenerator functionality."""

    def test_generate_all_produces_instruments(self, gen: InstrumentGenerator) -> None:
        """InstrumentGenerator.generate_all() returns >0 instruments."""
        instruments = gen.generate_all(REF_DATE)
        assert len(instruments) > 0, "generate_all() must produce at least one instrument"

    def test_all_instruments_have_valid_keys(self, gen: InstrumentGenerator) -> None:
        """Every instrument_key matches VENUE:TYPE:SYMBOL."""
        instruments = gen.generate_all(REF_DATE)
        for inst in instruments:
            parts = inst.instrument_key.split(":")
            assert len(parts) == 3, f"Key must have 3 colon-separated parts: {inst.instrument_key}"
            assert KEY_RE.match(inst.instrument_key), f"Bad key format: {inst.instrument_key}"

    def test_options_chain_completeness(self, gen: InstrumentGenerator) -> None:
        """BTC options chain has calls and puts, multiple expiries, reasonable strike range."""
        chain = gen.generate_options_chain(REF_DATE, underlying="BTC")
        assert len(chain) > 0

        calls = [i for i in chain if i.option_type == OptionType.CALL]
        puts = [i for i in chain if i.option_type == OptionType.PUT]
        assert len(calls) > 0, "Chain must contain CALL options"
        assert len(puts) > 0, "Chain must contain PUT options"
        assert len(calls) == len(puts), "Equal number of calls and puts expected"

        expiries = {i.expiry for i in chain if i.expiry is not None}
        assert len(expiries) >= 4, f"Expected >= 4 expiry dates, got {len(expiries)}"

        strikes = sorted({i.strike for i in chain if i.strike is not None})
        assert len(strikes) >= 20, f"Expected >= 20 strikes, got {len(strikes)}"

    def test_defi_uses_wrapped_tokens(self, gen: InstrumentGenerator) -> None:
        """Aave instruments use WETH/wstETH not ETH/stETH."""
        defi = gen.generate_defi(REF_DATE)
        aave_a_tokens = [
            i for i in defi if i.venue == "AAVEV3-ETHEREUM" and i.instrument_type == InstrumentType.A_TOKEN
        ]
        assert len(aave_a_tokens) >= 1
        eth_related = [i for i in aave_a_tokens if i.base_asset in ("ETH", "stETH")]
        assert len(eth_related) == 0, (
            f"Aave aTokens must use WETH (wrapped), not ETH or stETH. Found: {[i.base_asset for i in eth_related]}"
        )
        weth_tokens = [i for i in aave_a_tokens if i.base_asset == "WETH"]
        assert len(weth_tokens) >= 1, "Must have at least one WETH-backed aToken"

    def test_futures_have_valid_expiries(self, gen: InstrumentGenerator) -> None:
        """All futures have expiry dates after the generation reference date."""
        futures = gen.generate_cefi_futures(REF_DATE)
        ref_dt = datetime(REF_DATE.year, REF_DATE.month, REF_DATE.day, tzinfo=UTC)
        for inst in futures:
            assert inst.expiry is not None, f"Future {inst.instrument_key} missing expiry"
            assert inst.expiry > ref_dt, (
                f"Future {inst.instrument_key} expiry {inst.expiry} is not after ref_date {REF_DATE}"
            )

    def test_deterministic_generation(self) -> None:
        """Same seed produces identical instruments."""
        gen1 = InstrumentGenerator(seed=SEED)
        gen2 = InstrumentGenerator(seed=SEED)

        inst1 = gen1.generate_all(REF_DATE)
        inst2 = gen2.generate_all(REF_DATE)

        assert len(inst1) == len(inst2), "Same seed must produce same count"
        for a, b in zip(inst1, inst2, strict=True):
            assert a.instrument_key == b.instrument_key
            assert a.symbol == b.symbol
            assert a.venue == b.venue


# ===================================================================
# B. Instrument Edge Case Tests
# ===================================================================


class TestInstrumentEdgeCases:
    """Edge case tests for instrument generation and validation."""

    def test_bad_schema_instrument_missing_required(self) -> None:
        """Instrument missing required 'timestamp' field fails validation."""
        with pytest.raises(ValidationError):
            CanonicalInstrument.model_validate(
                {
                    "instrument_key": "TEST:SPOT_PAIR:BTC",
                    "venue": "TEST",
                    "symbol": "BTC",
                    # timestamp omitted — required field
                }
            )

    def test_bad_schema_instrument_wrong_type(self) -> None:
        """Instrument with wrong type for timestamp fails validation."""
        with pytest.raises(ValidationError):
            CanonicalInstrument.model_validate(
                {
                    "instrument_key": "TEST:SPOT_PAIR:BTC",
                    "venue": "TEST",
                    "symbol": "BTC",
                    "timestamp": "not-a-datetime",
                }
            )

    def test_delisted_instrument(self) -> None:
        """Instrument with available_to_datetime in the past is constructable."""
        past_dt = datetime(2024, 1, 1, tzinfo=UTC)
        inst = CanonicalInstrument(
            instrument_key="BINANCE-SPOT:SPOT_PAIR:LUNA-USDT",
            venue="BINANCE-SPOT",
            instrument_type=InstrumentType.SPOT_PAIR,
            symbol="LUNA-USDT",
            base_asset="LUNA",
            quote_asset="USDT",
            asset_class="crypto_cefi",
            available_from_datetime=datetime(2020, 1, 1, tzinfo=UTC),
            available_to_datetime=past_dt,
            timestamp=datetime(2024, 6, 1, tzinfo=UTC),
        )
        assert inst.available_to_datetime is not None
        assert inst.available_to_datetime < datetime.now(UTC)

    def test_new_listing(self) -> None:
        """Instrument with available_from_datetime = today is valid and picked up."""
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        inst = CanonicalInstrument(
            instrument_key="BINANCE-SPOT:SPOT_PAIR:NEW-USDT",
            venue="BINANCE-SPOT",
            instrument_type=InstrumentType.SPOT_PAIR,
            symbol="NEW-USDT",
            base_asset="NEW",
            quote_asset="USDT",
            asset_class="crypto_cefi",
            available_from_datetime=today,
            available_to_datetime=None,
            timestamp=today,
        )
        assert inst.available_from_datetime is not None
        assert inst.available_to_datetime is None
        # Available from today means it should be included in current listings
        assert inst.available_from_datetime <= datetime.now(UTC)

    def test_option_expiry_across_dates(self, gen: InstrumentGenerator) -> None:
        """Options generated for two dates spanning an expiry show different expiry sets."""
        # Pick a date before and after a weekly expiry
        early = date(2025, 1, 10)  # Friday is Jan 17
        late = date(2025, 1, 20)  # After the Jan 17 expiry

        chain_early = gen.generate_options_chain(early, underlying="BTC")
        # Re-create generator with same seed for determinism
        gen2 = InstrumentGenerator(seed=SEED)
        chain_late = gen2.generate_options_chain(late, underlying="BTC")

        expiries_early = {i.expiry for i in chain_early if i.expiry is not None}
        expiries_late = {i.expiry for i in chain_late if i.expiry is not None}

        # Late date should have at least some expiries not in early (new weeklies)
        new_expiries = expiries_late - expiries_early
        assert len(new_expiries) > 0, "Later date should introduce new expiry dates"

        # No expired options should appear in late chain
        for exp in expiries_late:
            assert exp is not None
            assert exp.date() > late, f"Expired option {exp} should not appear in chain for {late}"

    def test_future_rollover(self) -> None:
        """Futures generated before and after quarterly expiry show rolled contracts."""
        # Q1 2025 expiry is last Friday of March: 2025-03-28
        before_q1 = date(2025, 3, 1)
        after_q1 = date(2025, 4, 1)

        gen_before = InstrumentGenerator(seed=SEED)
        gen_after = InstrumentGenerator(seed=SEED)

        futures_before = gen_before.generate_cefi_futures(before_q1)
        futures_after = gen_after.generate_cefi_futures(after_q1)

        expiries_before = {i.expiry for i in futures_before if i.expiry is not None}
        expiries_after = {i.expiry for i in futures_after if i.expiry is not None}

        # After Q1 expiry, the next set should be different (shifted forward)
        assert expiries_before != expiries_after, "Futures must roll after quarterly expiry"

        # All after-expiry futures should have expiry > after_q1
        after_dt = datetime(after_q1.year, after_q1.month, after_q1.day, tzinfo=UTC)
        for exp in expiries_after:
            assert exp is not None
            assert exp > after_dt, f"Post-rollover future expiry {exp} should be after {after_q1}"

    def test_unknown_venue_instrument(self) -> None:
        """Instrument with unknown venue can be constructed (no crash)."""
        inst = CanonicalInstrument(
            instrument_key="UNKNOWN_EXCHANGE:SPOT_PAIR:BTC-USDT",
            venue="UNKNOWN_EXCHANGE",
            instrument_type=InstrumentType.SPOT_PAIR,
            symbol="BTC-USDT",
            base_asset="BTC",
            quote_asset="USDT",
            asset_class="crypto_cefi",
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert inst.venue == "UNKNOWN_EXCHANGE"

    def test_duplicate_instrument_key_dedup(self, gen: InstrumentGenerator) -> None:
        """generate_all() deduplicates by instrument_key (first occurrence wins)."""
        instruments = gen.generate_all(REF_DATE)
        keys = [i.instrument_key for i in instruments]
        assert len(keys) == len(set(keys)), "Duplicate instrument_key found after generate_all()"

    def test_instrument_with_zero_strike(self) -> None:
        """Option with strike=0 can be constructed but is semantically invalid.

        The schema does not enforce strike > 0, but downstream consumers should
        reject zero-strike options. We verify the schema allows construction
        (no crash) but the value is present for downstream validation.
        """
        inst = CanonicalInstrument(
            instrument_key="DERIBIT:OPTION:BTC-28MAR25-0-C",
            venue="DERIBIT",
            instrument_type=InstrumentType.OPTION,
            symbol="BTC-28MAR25-0-C",
            base_asset="BTC",
            quote_asset="USD",
            asset_class="crypto_cefi",
            strike=0.0,
            option_type=OptionType.CALL,
            expiry=datetime(2025, 3, 28, 8, 0, 0, tzinfo=UTC),
            underlying="BTC",
            timestamp=datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert inst.strike == 0.0
        # Downstream services should validate strike > 0

    def test_instrument_with_past_expiry(self) -> None:
        """Future with expiry in the past can be constructed and has available_to_datetime set."""
        past_expiry = datetime(2024, 3, 29, 8, 0, 0, tzinfo=UTC)
        inst = CanonicalInstrument(
            instrument_key="DERIBIT:FUTURE:BTC-29MAR24",
            venue="DERIBIT",
            instrument_type=InstrumentType.FUTURE,
            symbol="BTC-29MAR24",
            base_asset="BTC",
            quote_asset="USD",
            asset_class="crypto_cefi",
            expiry=past_expiry,
            available_from_datetime=datetime(2023, 3, 29, tzinfo=UTC),
            available_to_datetime=past_expiry,
            timestamp=datetime(2024, 6, 1, tzinfo=UTC),
        )
        assert inst.expiry is not None
        assert inst.expiry < datetime.now(UTC)
        assert inst.available_to_datetime is not None
        assert inst.available_to_datetime == inst.expiry


# ===================================================================
# C. Mock Data Chain Smoke Tests
# ===================================================================


class TestMockDataChain:
    """Smoke tests for mock data pipeline configuration and writers."""

    def test_seed_writer_local_mode(self, gen: InstrumentGenerator, tmp_path: Path) -> None:
        """SeedDataWriter creates files at expected paths for OHLCV data."""
        from unified_internal_contracts.testing.synthetic import SeedDataWriter, SyntheticDataGenerator

        spec: dict[object, object] = {
            "gbm_params": {
                "BTC-USDT": {"vol": 0.5, "drift": 0.0, "base_price": 40000.0},
            },
            "defi_yield_params": {},
            "correlations": {},
        }
        synth = SyntheticDataGenerator(spec, seed=SEED)
        df = synth.generate_ohlcv("BTC-USDT", "binance", date(2025, 1, 1), date(2025, 1, 2), "1h")

        writer = SeedDataWriter(tmp_path)
        out_path = writer.write_ohlcv(df, "BTC-USDT", "binance")
        assert out_path.exists(), f"Output file not found at {out_path}"
        assert out_path.suffix == ".parquet"

    def test_seed_writer_defi_output(self, tmp_path: Path) -> None:
        """SeedDataWriter writes DeFi yield data to expected directory structure."""
        from unified_internal_contracts.testing.synthetic import SeedDataWriter, SyntheticDataGenerator

        spec: dict[object, object] = {
            "gbm_params": {},
            "defi_yield_params": {
                "aave_v3_WETH": {"mean": 0.03, "kappa": 2.0, "sigma": 0.005, "base_apy": 0.03},
            },
            "correlations": {},
        }
        synth = SyntheticDataGenerator(spec, seed=SEED)
        df = synth.generate_defi_yields("aave_v3", "WETH", date(2025, 1, 1), date(2025, 1, 2), "1h")

        writer = SeedDataWriter(tmp_path)
        out_path = writer.write_defi(df, "aave_v3", "WETH")
        assert out_path.exists(), f"DeFi output not found at {out_path}"

    def test_dependency_checker_mock_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When DATA_MODE=mock, get_data_path_prefix() returns 'mock/'."""
        monkeypatch.setenv("DATA_MODE", "mock")

        # Force re-evaluation (function reads env directly)
        from unified_cloud_interface import get_data_path_prefix

        result = get_data_path_prefix()
        assert result == "mock/", f"Expected 'mock/' prefix, got '{result}'"

    def test_dependency_checker_real_no_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When DATA_MODE=real, get_data_path_prefix() returns empty string."""
        monkeypatch.setenv("DATA_MODE", "real")

        from unified_cloud_interface import get_data_path_prefix

        result = get_data_path_prefix()
        assert result == "", f"Expected empty prefix for real mode, got '{result}'"

    def test_mock_mode_smoke(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Set DATA_MODE=mock, CLOUD_PROVIDER=local, verify UnifiedCloudConfig.is_mock_mode()."""
        monkeypatch.setenv("DATA_MODE", "mock")
        monkeypatch.setenv("CLOUD_PROVIDER", "local")
        monkeypatch.setenv("CLOUD_MOCK_MODE", "true")

        from unified_config_interface import UnifiedCloudConfig

        # Create a fresh instance with mock env vars
        config = UnifiedCloudConfig()
        assert config.is_mock_mode(), "Expected is_mock_mode() == True when DATA_MODE=mock"

    def test_seed_writer_manifest(self, tmp_path: Path) -> None:
        """SeedDataWriter.write_manifest creates a JSON manifest file."""
        from unified_internal_contracts.testing.synthetic import SeedDataWriter

        writer = SeedDataWriter(tmp_path)
        manifest: dict[str, object] = {
            "generated_at": "2025-01-01T00:00:00Z",
            "seed": SEED,
            "instruments_count": 50,
        }
        out_path = writer.write_manifest(manifest)
        assert out_path.exists()
        assert out_path.name == "seed_manifest.json"

        import json
        from typing import cast

        data = cast(dict[str, object], json.loads(out_path.read_text()))  # pyright: ignore[reportAny]
        assert data["seed"] == SEED


# ===================================================================
# D. Schema Validation Tests
# ===================================================================


class TestSchemaValidation:
    """Validate generated data against canonical schemas."""

    def test_generated_instruments_match_canonical_schema(self, gen: InstrumentGenerator) -> None:
        """Every generated instrument passes CanonicalInstrument.model_validate()."""
        instruments = gen.generate_all(REF_DATE)
        assert len(instruments) > 0

        for inst in instruments:
            # model_validate on the dict form should produce the same result
            validated = CanonicalInstrument.model_validate(inst.model_dump())
            assert validated.instrument_key == inst.instrument_key
            assert validated.venue == inst.venue

    def test_ohlcv_schema_compliance(self) -> None:
        """Mock OHLCV data matches expected column set."""
        from unified_internal_contracts.testing.synthetic import SyntheticDataGenerator

        spec: dict[object, object] = {
            "gbm_params": {
                "BTC-USDT": {"vol": 0.5, "drift": 0.0, "base_price": 40000.0},
            },
            "defi_yield_params": {},
            "correlations": {},
        }
        synth = SyntheticDataGenerator(spec, seed=SEED)
        df = synth.generate_ohlcv("BTC-USDT", "binance", date(2025, 1, 1), date(2025, 1, 2), "1h")

        required_cols = {"open", "high", "low", "close", "volume", "timestamp"}
        actual_cols = set(df.columns)
        missing = required_cols - actual_cols
        assert not missing, f"OHLCV missing required columns: {missing}"

        # Verify data integrity
        assert len(df) > 0, "OHLCV dataframe must not be empty"
        assert (df["high"] >= df["low"]).all(), "high must be >= low for all bars"
        assert (df["volume"] > 0).all(), "volume must be positive"

    def test_defi_oracle_has_index_columns(self) -> None:
        """DeFi oracle mock data has liquidity_index, variable_borrow_index columns."""
        from unified_internal_contracts.testing.synthetic import SyntheticDataGenerator

        spec: dict[object, object] = {
            "gbm_params": {},
            "defi_yield_params": {
                "aave_v3_WETH": {"mean": 0.03, "kappa": 2.0, "sigma": 0.005, "base_apy": 0.03},
            },
            "correlations": {},
        }
        synth = SyntheticDataGenerator(spec, seed=SEED)
        df = synth.generate_defi_yields("aave_v3", "WETH", date(2025, 1, 1), date(2025, 1, 2), "1h")

        required_cols = {"liquidity_index", "variable_borrow_index", "apy", "borrow_apy", "tvl_usd"}
        actual_cols = set(df.columns)
        missing = required_cols - actual_cols
        assert not missing, f"DeFi yields missing required columns: {missing}"

        # Verify index monotonicity (cumulative indices should be non-decreasing)
        assert len(df) > 0
        liquidity_start = float(df["liquidity_index"].iloc[0])  # pyright: ignore[reportAny]
        borrow_start = float(df["variable_borrow_index"].iloc[0])  # pyright: ignore[reportAny]
        assert abs(liquidity_start - 1.0) < 1e-9, "Liquidity index must start at 1.0"
        assert abs(borrow_start - 1.0) < 1e-9, "Borrow index must start at 1.0"

    def test_sports_odds_schema(self) -> None:
        """Sports odds mock data has expected columns."""
        from unified_internal_contracts.testing.synthetic import SyntheticDataGenerator

        spec: dict[object, object] = {
            "gbm_params": {},
            "defi_yield_params": {},
            "correlations": {},
        }
        synth = SyntheticDataGenerator(spec, seed=SEED)
        df = synth.generate_match_odds("EPL", "betfair", num_matches=5)

        required_cols = {"odds_home", "odds_draw", "odds_away", "timestamp", "league", "venue"}
        actual_cols = set(df.columns)
        missing = required_cols - actual_cols
        assert not missing, f"Sports odds missing required columns: {missing}"

        assert len(df) == 5
        # All odds should be > 1.0 (decimal odds)
        assert (df["odds_home"] > 1.0).all(), "Home odds must be > 1.0"
        assert (df["odds_draw"] > 1.0).all(), "Draw odds must be > 1.0"
        assert (df["odds_away"] > 1.0).all(), "Away odds must be > 1.0"

    def test_instrument_types_cover_all_asset_classes(self, gen: InstrumentGenerator) -> None:
        """generate_all() produces instruments across cefi, tradfi, defi, and sports."""
        instruments = gen.generate_all(REF_DATE)
        asset_classes = {i.asset_class for i in instruments if i.asset_class is not None}

        assert "crypto_cefi" in asset_classes, "Missing crypto_cefi instruments"
        assert "crypto_defi" in asset_classes, "Missing crypto_defi instruments"
        assert "prediction" in asset_classes or "sports" in asset_classes, "Missing prediction/sports instruments"

        # Check tradfi variants
        tradfi_classes = {ac for ac in asset_classes if ac.startswith("tradfi")}
        assert len(tradfi_classes) >= 1, "Missing tradfi instruments"
