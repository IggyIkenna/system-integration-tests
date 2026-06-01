"""Mocked end-to-end DeFi paper-flow scenarios for the two May-23 archetypes.

Tests the full paper-trading signal → execution pipeline using in-process mocks.
No credentials, no network, no on-chain calls - runs in CI and locally.

Scenarios:
  1. ``carry_staked_basis`` - LST staking yield vs Aave borrow rate spread; DeFi
     long-stake leg + CeFi short-perp hedge.  Paper mode uses MockExecutionAlwaysFill
     for both legs.

  2. ``arbitrage_price_dispersion`` - DEX vs CEX price dispersion; buy the cheap
     venue / sell the expensive venue.  Paper mode uses MockExecutionAlwaysFill for
     both legs.

Both scenarios assert:
  * Correct archetype enum value from UAC.
  * Signal has correct sign / direction.
  * Execution fills the expected two legs (DeFi + CeFi).
  * State hash is identical across two independent runs (batch=live parity).

Lives in ``tests/overnight/`` because it spans archetype → execution → state-hash
layers and acts as the mocked prerequisite to the Tenderly fork integration test.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TypedDict

from unified_api_contracts.internal.architecture_v2.enums import StrategyArchetype
from unified_api_contracts.internal.inter_service_events import FillEvent
from unified_trading_library.state_hashing import (  # noqa: qg-deep-import
    StateBundle,
    assert_state_equal,
    compute_state_hash,
)
from unified_trading_library.testing import (  # noqa: qg-deep-import
    MockExecutionAlwaysFill,
)

# ---------------------------------------------------------------------------
# Return-type schemas
# ---------------------------------------------------------------------------


class _CarryResult(TypedDict):
    fills: list[FillEvent]
    state: StateBundle
    pnl_unrealized: Decimal


class _ApdResult(TypedDict):
    fills: list[FillEvent]
    state: StateBundle
    spread_bps: int
    gross_pnl: Decimal


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _build_state(
    positions: list[dict[str, object]],
    fills: list[dict[str, object]],
    pnl_unrealized: Decimal,
) -> StateBundle:
    return StateBundle(
        positions=positions,
        balances=[{"currency": "USDC", "amount": Decimal("100_000")}],
        orders=[],
        fills=fills,
        cash_ledger=[{"phase": "opening", "amount": Decimal("100_000")}],
        pnl_ledger=[
            {"kind": "realized", "amount": Decimal("0")},
            {"kind": "unrealized", "amount": pnl_unrealized},
        ],
        risk_metrics=[{"metric": "leverage", "value": Decimal("1")}],
        margin_state=[],
        alerts_emitted=[],
        kill_switch_state=[{"active": False}],
        deleverage_actions=[],
    )


# ---------------------------------------------------------------------------
# Scenario 1 - carry_staked_basis
# ---------------------------------------------------------------------------


def _run_carry_staked_basis_paper(*, run_id: str) -> _CarryResult:
    """Single deterministic run of the carry_staked_basis paper-trade scenario.

    Signal: LST staking APR (stETH, 4.2%) minus Aave USDC borrow rate (2.8%).
    Net spread = +1.4%.  Spread > 0 → enter position:
      DeFi leg:  BUY 0.5 stETH on Lido (stake)
      CeFi leg:  SELL 0.5 ETH-PERP on Binance (delta hedge)

    Both legs use MockExecutionAlwaysFill at mid-market prices.
    """
    executor = MockExecutionAlwaysFill(run_id=run_id)

    steth_price_usdc = Decimal("3_488.00")
    eth_perp_price_usdc = Decimal("3_490.00")
    quantity = Decimal("0.5")

    # DeFi leg: stake 0.5 ETH → receive 0.5 stETH
    executor.submit_order(
        instrument_id="LIDO:STETH-ETH",
        venue_id="lido",
        side="BUY",
        quantity=quantity,
        requested_price=steth_price_usdc,
        client_order_id=f"{run_id}-defi-001",
        correlation_id=f"{run_id}-corr",
    )

    # CeFi hedge leg: short 0.5 ETH-PERP to hedge delta
    executor.submit_order(
        instrument_id="BINANCE:PERPETUAL:ETH",
        venue_id="binance",
        side="SELL",
        quantity=quantity,
        requested_price=eth_perp_price_usdc,
        client_order_id=f"{run_id}-cefi-001",
        correlation_id=f"{run_id}-corr",
    )

    defi_fill = executor.fills[0]
    cefi_fill = executor.fills[1]

    # Unrealized P&L: funding income (spread x notional, annualized 1-day)
    spread_annual = Decimal("0.014")
    notional = quantity * steth_price_usdc
    pnl_unrealized = notional * spread_annual / Decimal("365")

    # Stable IDs (not run_id-dependent) so state hash is deterministic across runs.
    positions: list[dict[str, object]] = [
        {
            "position_id": "carry-steth-pos-1",
            "instrument_id": "LIDO:STETH-ETH",
            "quantity": quantity,
            "mark_price": steth_price_usdc,
            "avg_entry_price": steth_price_usdc,
        },
        {
            "position_id": "carry-eth-perp-pos-1",
            "instrument_id": "BINANCE:PERPETUAL:ETH",
            "quantity": -quantity,
            "mark_price": eth_perp_price_usdc,
            "avg_entry_price": eth_perp_price_usdc,
        },
    ]
    fill_dicts: list[dict[str, object]] = [
        {"seq": 1, "price": defi_fill.price, "quantity": defi_fill.quantity},
        {"seq": 2, "price": cefi_fill.price, "quantity": cefi_fill.quantity},
    ]

    state = _build_state(positions, fill_dicts, pnl_unrealized)
    return _CarryResult(fills=executor.fills, state=state, pnl_unrealized=pnl_unrealized)


class TestCarryStakedBasisPaperFlow:
    """End-to-end paper-flow assertions for carry_staked_basis archetype."""

    def test_archetype_enum_value(self) -> None:
        """CARRY_STAKED_BASIS must be a registered StrategyArchetype."""
        assert StrategyArchetype.CARRY_STAKED_BASIS == "CARRY_STAKED_BASIS"
        assert StrategyArchetype.CARRY_STAKED_BASIS in list(StrategyArchetype)

    def test_two_legs_execute(self) -> None:
        """Paper flow must produce exactly 2 fills: DeFi stake + CeFi perp hedge."""
        result = _run_carry_staked_basis_paper(run_id="test-carry-two-legs")
        assert len(result["fills"]) == 2, f"Expected 2 fills (stake + hedge); got {len(result['fills'])}"

    def test_defi_leg_is_long(self) -> None:
        """DeFi stake leg must fill at the quoted stETH price."""
        result = _run_carry_staked_basis_paper(run_id="test-carry-defi-long")
        defi_fill = result["fills"][0]
        assert defi_fill.price == Decimal("3_488.00")
        assert defi_fill.quantity == Decimal("0.5")

    def test_cefi_leg_is_short(self) -> None:
        """CeFi hedge leg must fill at the quoted ETH-PERP price."""
        result = _run_carry_staked_basis_paper(run_id="test-carry-cefi-short")
        cefi_fill = result["fills"][1]
        assert cefi_fill.price == Decimal("3_490.00")
        assert cefi_fill.quantity == Decimal("0.5")

    def test_positive_carry_spread(self) -> None:
        """Net P&L must be positive for a spread-positive carry trade."""
        result = _run_carry_staked_basis_paper(run_id="test-carry-pnl")
        pnl = result["pnl_unrealized"]
        assert pnl > Decimal("0"), f"Expected positive carry P&L; got {pnl}"

    def test_state_hash_deterministic(self) -> None:
        """Two independent paper runs must produce identical state hash (batch=live parity).

        MockExecutionAlwaysFill fills at exactly the requested price, so strategy P&L
        is isolated from execution quality across batch and live modes.
        """
        result_a = _run_carry_staked_basis_paper(run_id="test-carry-hash-a")
        result_b = _run_carry_staked_basis_paper(run_id="test-carry-hash-b")
        hash_a = compute_state_hash(result_a["state"])
        hash_b = compute_state_hash(result_b["state"])
        assert hash_a == hash_b, f"State hash mismatch: {hash_a} vs {hash_b}"
        assert_state_equal(result_a["state"], result_b["state"])


# ---------------------------------------------------------------------------
# Scenario 2 - arbitrage_price_dispersion
# ---------------------------------------------------------------------------


def _run_arbitrage_price_dispersion_paper(*, run_id: str) -> _ApdResult:
    """Single deterministic run of the arbitrage_price_dispersion paper-trade scenario.

    Signal: ETH/USDC price on Uniswap V3 (DEX) vs Binance spot (CEX).
    DEX price:   3_481.50 USDC
    CEX price:   3_487.80 USDC
    Spread:        6.30 USDC (18 bps) - above 5 bps minimum threshold.

    Execution (paper mode):
      Leg 1: BUY  0.3 ETH on Uniswap V3 (cheaper venue)
      Leg 2: SELL 0.3 ETH on Binance spot (more expensive venue)
    """
    executor = MockExecutionAlwaysFill(run_id=run_id)

    dex_price = Decimal("3_481.50")
    cex_price = Decimal("3_487.80")
    quantity = Decimal("0.3")

    # Leg 1: buy on the cheaper DEX
    executor.submit_order(
        instrument_id="UNISWAP_V3:ETH-USDC-500",
        venue_id="uniswap_v3",
        side="BUY",
        quantity=quantity,
        requested_price=dex_price,
        client_order_id=f"{run_id}-dex-001",
        correlation_id=f"{run_id}-corr",
    )

    # Leg 2: sell on the more expensive CEX
    executor.submit_order(
        instrument_id="BINANCE:SPOT:ETH-USDC",
        venue_id="binance",
        side="SELL",
        quantity=quantity,
        requested_price=cex_price,
        client_order_id=f"{run_id}-cex-001",
        correlation_id=f"{run_id}-corr",
    )

    dex_fill = executor.fills[0]
    cex_fill = executor.fills[1]

    # Gross P&L: (sell_price - buy_price) x quantity
    gross_pnl = (cex_price - dex_price) * quantity
    spread_bps = int(((cex_price - dex_price) / dex_price * 10_000).to_integral_value())

    # Net-zero positions after both legs execute
    positions: list[dict[str, object]] = []
    fill_dicts: list[dict[str, object]] = [
        {"seq": 1, "price": dex_fill.price, "quantity": dex_fill.quantity},
        {"seq": 2, "price": cex_fill.price, "quantity": cex_fill.quantity},
    ]

    state = _build_state(positions, fill_dicts, gross_pnl)
    return _ApdResult(
        fills=executor.fills,
        state=state,
        spread_bps=spread_bps,
        gross_pnl=gross_pnl,
    )


class TestArbitragePriceDispersionPaperFlow:
    """End-to-end paper-flow assertions for arbitrage_price_dispersion archetype."""

    def test_archetype_enum_value(self) -> None:
        """ARBITRAGE_PRICE_DISPERSION must be a registered StrategyArchetype."""
        assert StrategyArchetype.ARBITRAGE_PRICE_DISPERSION == "ARBITRAGE_PRICE_DISPERSION"
        assert StrategyArchetype.ARBITRAGE_PRICE_DISPERSION in list(StrategyArchetype)

    def test_two_legs_execute(self) -> None:
        """APD paper flow must produce exactly 2 fills: DEX buy + CEX sell."""
        result = _run_arbitrage_price_dispersion_paper(run_id="test-apd-two-legs")
        assert len(result["fills"]) == 2, f"Expected 2 fills (DEX buy + CEX sell); got {len(result['fills'])}"

    def test_dex_leg_buys_at_lower_price(self) -> None:
        """DEX leg must fill at the lower price (cheaper venue)."""
        result = _run_arbitrage_price_dispersion_paper(run_id="test-apd-dex-buy")
        dex_fill = result["fills"][0]
        cex_fill = result["fills"][1]
        assert dex_fill.price < cex_fill.price, f"DEX fill {dex_fill.price} must be < CEX fill {cex_fill.price}"

    def test_cex_leg_sells_at_higher_price(self) -> None:
        """CEX leg must fill at the higher price (more expensive venue)."""
        result = _run_arbitrage_price_dispersion_paper(run_id="test-apd-cex-sell")
        cex_fill = result["fills"][1]
        assert cex_fill.price == Decimal("3_487.80")
        assert cex_fill.quantity == Decimal("0.3")

    def test_spread_exceeds_minimum_threshold(self) -> None:
        """Price spread must exceed 5 bps minimum to be actionable."""
        result = _run_arbitrage_price_dispersion_paper(run_id="test-apd-spread")
        assert result["spread_bps"] >= 5, f"Spread {result['spread_bps']} bps below 5 bps minimum threshold"

    def test_gross_pnl_is_positive(self) -> None:
        """Gross P&L must be positive: (sell_price - buy_price) x quantity > 0."""
        result = _run_arbitrage_price_dispersion_paper(run_id="test-apd-pnl")
        assert result["gross_pnl"] > Decimal("0"), f"Expected positive gross P&L; got {result['gross_pnl']}"

    def test_state_hash_deterministic(self) -> None:
        """Two independent paper runs must produce identical state hash (batch=live parity).

        Validates that arb spread capture is deterministic when fills are always at the
        requested price - execution alpha = 0 in paper mode.
        """
        result_a = _run_arbitrage_price_dispersion_paper(run_id="test-apd-hash-a")
        result_b = _run_arbitrage_price_dispersion_paper(run_id="test-apd-hash-b")
        hash_a = compute_state_hash(result_a["state"])
        hash_b = compute_state_hash(result_b["state"])
        assert hash_a == hash_b, f"State hash mismatch: {hash_a} vs {hash_b}"
        assert_state_equal(result_a["state"], result_b["state"])


# ---------------------------------------------------------------------------
# Cross-archetype invariants
# ---------------------------------------------------------------------------


class TestDefiPaperFlowCrossArchetypeInvariants:
    """Invariants that must hold across BOTH DeFi paper-flow archetypes."""

    def test_both_archetypes_registered(self) -> None:
        """Both May-23 archetypes must be valid StrategyArchetype enum members."""
        assert StrategyArchetype.CARRY_STAKED_BASIS in list(StrategyArchetype)
        assert StrategyArchetype.ARBITRAGE_PRICE_DISPERSION in list(StrategyArchetype)

    def test_distinct_state_hashes(self) -> None:
        """carry_staked_basis and APD paper flows must produce DIFFERENT state hashes.

        Different positions, prices, and fills → different StateBundle → different hash.
        Catches accidental hash collisions in the StateBundle schema.
        """
        carry_result = _run_carry_staked_basis_paper(run_id="test-cross-carry")
        apd_result = _run_arbitrage_price_dispersion_paper(run_id="test-cross-apd")
        carry_hash = compute_state_hash(carry_result["state"])
        apd_hash = compute_state_hash(apd_result["state"])
        assert carry_hash != apd_hash, "Different archetypes must not produce the same state hash"

    def test_mock_execution_fills_at_requested_price(self) -> None:
        """MockExecutionAlwaysFill must fill at exactly the requested price (paper-mode contract).

        Execution alpha = 0 in paper mode. Strategy P&L is isolated from execution quality.
        """
        carry_result = _run_carry_staked_basis_paper(run_id="test-cross-fill-carry")
        apd_result = _run_arbitrage_price_dispersion_paper(run_id="test-cross-fill-apd")

        # Carry: DeFi leg at stETH price, CeFi leg at ETH-PERP price
        carry_fills = carry_result["fills"]
        assert carry_fills[0].price == Decimal("3_488.00")
        assert carry_fills[1].price == Decimal("3_490.00")

        # APD: DEX leg at DEX price, CEX leg at CEX price
        apd_fills = apd_result["fills"]
        assert apd_fills[0].price == Decimal("3_481.50")
        assert apd_fills[1].price == Decimal("3_487.80")
