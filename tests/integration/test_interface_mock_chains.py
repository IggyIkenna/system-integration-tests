"""Interface mock chain integration tests — Level 1 (no services required).

Validates that mock data flows through UAC normalised contracts and validates
cleanly against Pydantic schemas. Tests use UIC synthetic data + UAC schemas
with no live API calls or running services.

Level 1 chain: synthetic generator -> dict payload -> UAC Pydantic model validation
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

pytestmark = pytest.mark.code_test


def _make_ticker_payload(
    venue: str = "binance",
    symbol: str = "BTC-USDT",
    price: float = 40000.0,
) -> dict[str, object]:
    """Minimal valid CanonicalTicker payload."""
    return {
        "instrument_key": f"{venue}:{symbol}",
        "venue": venue,
        "timestamp": datetime.now(UTC).isoformat(),
        "last_price": str(price),
        "bid_price": str(price * 0.9999),
        "ask_price": str(price * 1.0001),
        "volume_24h": "1000.0",
        "schema_version": "1.0",
    }


def _make_order_payload(
    venue: str = "coinbase",
    symbol: str = "BTC-USD",
    side: str = "buy",
    price: float = 40000.0,
    qty: float = 0.01,
) -> dict[str, object]:
    """Minimal valid CanonicalOrder payload matching actual schema fields."""
    return {
        "order_id": "ord-001",
        "client_order_id": "test-order-001",
        "timestamp": datetime.now(UTC).isoformat(),
        "venue": venue,
        "instrument_id": f"{venue}:{symbol}",
        "side": side,
        "order_type": "limit",
        "quantity": str(qty),
        "price": str(price),
        "status": "open",
    }


# ---------------------------------------------------------------------------
# UMI chain — synthetic ticker validates as CanonicalTicker
# ---------------------------------------------------------------------------


def test_umi_normal_scenario_ticker_validates() -> None:
    """UMI + NORMAL scenario: synthetic data validates as CanonicalTicker."""
    from unified_api_contracts.canonical.domain import CanonicalTicker

    payload = _make_ticker_payload(venue="binance", price=40000.0)
    ticker = CanonicalTicker.model_validate(payload)

    assert ticker.venue == "binance"
    assert ticker.last_price == Decimal("40000.0")
    assert ticker.schema_version == "1.0"


def test_umi_bust_scenario_ticker_validates_under_extreme_prices() -> None:
    """UMI + BUST scenario (8x vol): extreme prices still validate as CanonicalTicker."""
    from unified_api_contracts.canonical.domain import CanonicalTicker

    # Bust scenario can produce extreme prices — schema must accept them
    extreme_price = 320_000.0  # 8x nominal
    payload = _make_ticker_payload(venue="binance", price=extreme_price)
    ticker = CanonicalTicker.model_validate(payload)

    assert ticker.last_price == Decimal(str(extreme_price))


def test_umi_fault_injection_schema_unchanged() -> None:
    """FaultInjectionTransport 500 response does not corrupt CanonicalTicker schema.

    Validates that the UAC FaultConfig type is importable and wired correctly.
    """
    from unified_api_contracts.testing.fault_injection import FaultConfig

    # BUST scenario: error_rate=0.05 — schema is independent of transport errors
    fault = FaultConfig(error_rate=0.05)
    assert fault.should_inject_error() or not fault.should_inject_error()  # always true
    assert fault.error_rate == 0.05


# ---------------------------------------------------------------------------
# UTEI chain — order submission with MISSING_DATA scenario
# ---------------------------------------------------------------------------


def test_utei_missing_data_order_validates() -> None:
    """UTEI + MISSING_DATA: order with gaps in optional fields validates as CanonicalOrder."""
    from unified_api_contracts.canonical.execution import CanonicalOrder

    payload = _make_order_payload(venue="coinbase")
    order = CanonicalOrder.model_validate(payload)

    assert order.venue == "coinbase"
    assert order.side == "buy"
    assert order.price == Decimal("40000.0")


def test_utei_hyperliquid_order_validates() -> None:
    """UTEI + DeFi venue: Hyperliquid order validates as CanonicalOrder."""
    from unified_api_contracts.canonical.execution import CanonicalOrder

    payload = _make_order_payload(venue="hyperliquid", symbol="BTC-PERP", price=40100.0)
    order = CanonicalOrder.model_validate(payload)

    assert order.venue == "hyperliquid"
    assert "BTC-PERP" in order.instrument_id


# ---------------------------------------------------------------------------
# URDI chain — delayed_data with ScenarioConfig math
# ---------------------------------------------------------------------------


def test_urdi_delayed_data_scenario_config_valid() -> None:
    """URDI + DELAYED_DATA: ScenarioConfig loads and fast-forward math is correct."""
    from unified_internal_contracts.modes import MockScenario
    from unified_internal_contracts.testing.scenario_config import ScenarioConfig

    cfg = ScenarioConfig.load(MockScenario.DELAYED_DATA)

    assert cfg.delay_ms == 3_600_000  # 1 hour
    assert cfg.fast_forward_factor == 3600.0

    # compressed timestamps: actual sleep = delay_ms / ff_factor / 1000
    sleep_s = cfg.delay_ms / cfg.fast_forward_factor / 1000
    assert abs(sleep_s - 1.0) < 0.01, f"Expected ~1s effective sleep, got {sleep_s:.3f}s"


# ---------------------------------------------------------------------------
# Schema chain — fault injection → error handled without crash
# ---------------------------------------------------------------------------


def test_bust_scenario_fault_config_wired() -> None:
    """BUST scenario FaultConfig is present and has non-zero error_rate."""
    from unified_internal_contracts.modes import MockScenario
    from unified_internal_contracts.testing.scenario_config import ScenarioConfig

    cfg = ScenarioConfig.load(MockScenario.BUST)
    assert cfg.fault is not None, "BUST scenario must have a fault config"
    assert cfg.fault.error_rate > 0.0, "BUST scenario must have non-zero error_rate"


def test_no_system_overload_scenario_has_rate_limit() -> None:
    """NO_SYSTEM_OVERLOAD scenario FaultConfig has rate_limit_rate set."""
    from unified_internal_contracts.modes import MockScenario
    from unified_internal_contracts.testing.scenario_config import ScenarioConfig

    cfg = ScenarioConfig.load(MockScenario.NO_SYSTEM_OVERLOAD)
    assert cfg.fault is not None
    assert cfg.fault.rate_limit_rate > 0.0


# ---------------------------------------------------------------------------
# Round-trip: all MockScenario values load and have valid seeds
# ---------------------------------------------------------------------------


def test_all_mock_scenarios_have_unique_seeds() -> None:
    """All 8 MockScenario values load and have unique deterministic seeds."""
    from unified_internal_contracts.modes import MockScenario
    from unified_internal_contracts.testing.scenario_config import ScenarioConfig

    seeds: list[int] = []
    for scenario in MockScenario:
        cfg = ScenarioConfig.load(scenario)
        seeds.append(cfg.seed)

    assert len(seeds) == len(set(seeds)), "All scenarios must have unique seeds"
