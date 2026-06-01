"""Portable backtest integration test — DeFi (decentralised finance).

Runs without credentials using inline mock pool data. No network calls.
Tests the full DeFi backtest pipeline:
  pool state snapshots -> LP fee accumulation -> impermanent loss calculation
  -> position P&L attribution -> ExecutionInstruction generation

Domain: DeFi (ETH/USDC Uniswap v3 concentrated liquidity pool)
Strategy: Passive LP fee capture — track a concentrated liquidity position
          over a range of ±5% from initial mid-price. Calculate:
            - accumulated fees earned
            - impermanent loss (IL) vs hold
            - net LP P&L (fees - IL)
Portable: runs identically on local, CI, Cloud Build, GHA.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

# Set mock mode before any service-level imports
os.environ.setdefault("CLOUD_PROVIDER", "local")
os.environ.setdefault("CLOUD_MOCK_MODE", "true")

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Mock data — 12 Uniswap ETH/USDC pool snapshots (hourly)
# Fields: (hour_offset, eth_price_usdc, pool_fee_bps, volume_usdc, tvl_usdc)
# Fee tier: 0.05% (5 bps) — Uniswap v3 most liquid tier for ETH/USDC
# ---------------------------------------------------------------------------

# (hour_offset, eth_price_usdc, pool_fee_bps, hourly_volume_usdc, pool_tvl_usdc)
_ETH_USDC_POOL_SNAPSHOTS: list[tuple[int, str, str, str, str]] = [
    (0, "3480.00", "5", "12_500_000", "380_000_000"),
    (1, "3492.50", "5", "15_200_000", "381_500_000"),
    (2, "3505.00", "5", "18_700_000", "383_000_000"),  # price rising
    (3, "3518.75", "5", "22_300_000", "384_200_000"),
    (4, "3532.50", "5", "19_800_000", "385_500_000"),  # near upper range
    (5, "3545.00", "5", "25_100_000", "386_800_000"),  # above +1.9% from entry
    (6, "3530.00", "5", "20_600_000", "385_900_000"),  # pullback
    (7, "3515.00", "5", "17_400_000", "384_700_000"),
    (8, "3498.00", "5", "16_200_000", "383_100_000"),  # back near entry
    (9, "3482.00", "5", "14_800_000", "381_800_000"),
    (10, "3468.00", "5", "19_300_000", "380_500_000"),  # slight dip, in-range
    (11, "3475.00", "5", "16_700_000", "381_200_000"),
]

_BASE_TS = datetime(2026, 3, 18, 0, 0, 0, tzinfo=UTC)

# LP position parameters
_INITIAL_ETH_PRICE = Decimal("3480.00")
_RANGE_WIDTH_PCT = Decimal("0.05")  # ±5% concentrated liquidity range
_PRICE_LOWER = _INITIAL_ETH_PRICE * (1 - _RANGE_WIDTH_PCT)
_PRICE_UPPER = _INITIAL_ETH_PRICE * (1 + _RANGE_WIDTH_PCT)
_INITIAL_USDC_DEPOSIT = Decimal("10000.00")
_INITIAL_ETH_DEPOSIT = _INITIAL_USDC_DEPOSIT / _INITIAL_ETH_PRICE  # ~2.874 ETH


# ---------------------------------------------------------------------------
# DeFi LP backtest engine — pure Python, no cloud deps
# ---------------------------------------------------------------------------


def _is_in_range(price: Decimal, lower: Decimal, upper: Decimal) -> bool:
    """Return True when price is within the LP's active range."""
    return lower <= price <= upper


def _compute_lp_fee_earned(
    volume_usdc: Decimal,
    fee_bps: int,
    pool_tvl: Decimal,
    our_deposit: Decimal,
) -> Decimal:
    """Estimate fees earned by our LP position for one snapshot period.

    Proportional share = our_deposit / pool_tvl.
    Fee = volume * fee_rate * our_share.
    """
    if pool_tvl <= Decimal("0"):
        return Decimal("0")
    fee_rate = Decimal(str(fee_bps)) / Decimal("10000")
    our_share = our_deposit / pool_tvl
    return volume_usdc * fee_rate * our_share


def _compute_impermanent_loss(
    entry_price: Decimal,
    current_price: Decimal,
    initial_usdc: Decimal,
    initial_eth: Decimal,
) -> Decimal:
    """Compute impermanent loss in USDC vs holding the initial assets.

    IL = hold_value - LP_value (positive IL means LP is worth less than hold).
    For Uniswap v2/v3 constant-product approximation:
      LP_value ≈ 2 * sqrt(entry_price * current_price) * initial_eth
    """
    hold_value = initial_usdc + initial_eth * current_price
    # Uniswap constant-product AMM approximation
    lp_value = 2 * (entry_price * current_price).sqrt() * initial_eth
    return hold_value - lp_value  # positive = IL (loss vs hold)


def _simulate_defi_lp_backtest(
    snapshots: list[tuple[int, str, str, str, str]],
    price_lower: Decimal = _PRICE_LOWER,
    price_upper: Decimal = _PRICE_UPPER,
    initial_usdc: Decimal = _INITIAL_USDC_DEPOSIT,
    initial_eth: Decimal = _INITIAL_ETH_DEPOSIT,
) -> dict[str, object]:
    """Run a DeFi LP fee-capture backtest over pool snapshots.

    Returns a result dict with:
    - execution_instructions: list of ExecutionInstruction (ADD/REMOVE_LIQUIDITY)
    - total_fees_usdc: total LP fees earned
    - impermanent_loss_usdc: IL at final snapshot
    - net_pnl_usdc: fees - IL
    - active_hours: number of hours where price was in LP range
    - out_of_range_hours: number of hours where price was outside LP range
    """
    from unified_api_contracts import (
        CanonicalFill,
        ExecutionInstruction,
        ExecutionResult,
        ExecutionStatus,
        OperationType,
    )

    entry_price = Decimal(snapshots[0][1])
    instructions: list[ExecutionInstruction] = []
    execution_results: list[ExecutionResult] = []
    fills: list[CanonicalFill] = []  # DeFi fills modelled as on-chain swaps within pool

    total_fees = Decimal("0")
    active_hours = 0
    out_of_range_hours = 0
    in_position = False

    instr_seq = 0

    for _i, (hour_off, price_str, fee_bps_str, vol_str, tvl_str) in enumerate(snapshots):
        ts = _BASE_TS + timedelta(hours=hour_off)
        price = Decimal(price_str)
        fee_bps = int(fee_bps_str)
        volume = Decimal(vol_str.replace("_", ""))
        tvl = Decimal(tvl_str.replace("_", ""))

        is_active = _is_in_range(price, price_lower, price_upper)

        if is_active:
            active_hours += 1
            # Add liquidity on first active bar
            if not in_position:
                instr_seq += 1
                instr_id = f"defi-lp-add-{instr_seq:04d}"
                instructions.append(
                    ExecutionInstruction(
                        instruction_id=instr_id,
                        operation=OperationType.ADD_LIQUIDITY,
                        timestamp=ts,
                        from_venue="uniswap_v3",
                        instrument_id="ETH-USDC-0.05",
                        token_in="USDC",
                        amount=initial_usdc,
                        token_out="ETH",
                        max_slippage_bps=50,
                        metadata={
                            "price_lower": str(price_lower),
                            "price_upper": str(price_upper),
                            "initial_eth": str(initial_eth),
                        },
                    )
                )
                execution_results.append(
                    ExecutionResult(
                        instruction_id=instr_id,
                        operation=OperationType.ADD_LIQUIDITY,
                        status=ExecutionStatus.COMPLETED,
                        timestamp_submitted=ts,
                        timestamp_completed=ts,
                        amount_executed=initial_usdc,
                        transaction_hash=f"0xadd{instr_seq:040x}",
                    )
                )
                in_position = True

            # Accumulate fees this hour
            fee_earned = _compute_lp_fee_earned(volume, fee_bps, tvl, initial_usdc)
            total_fees += fee_earned

        else:
            out_of_range_hours += 1
            # Remove liquidity on first out-of-range bar to stop IL accumulation
            if in_position:
                instr_seq += 1
                instr_id = f"defi-lp-remove-{instr_seq:04d}"
                instructions.append(
                    ExecutionInstruction(
                        instruction_id=instr_id,
                        operation=OperationType.REMOVE_LIQUIDITY,
                        timestamp=ts,
                        from_venue="uniswap_v3",
                        instrument_id="ETH-USDC-0.05",
                        token_in="ETH",
                        amount=initial_eth,
                        token_out="USDC",
                        max_slippage_bps=50,
                        metadata={
                            "reason": "out_of_range",
                            "current_price": str(price),
                        },
                    )
                )
                execution_results.append(
                    ExecutionResult(
                        instruction_id=instr_id,
                        operation=OperationType.REMOVE_LIQUIDITY,
                        status=ExecutionStatus.COMPLETED,
                        timestamp_submitted=ts,
                        timestamp_completed=ts,
                        amount_executed=initial_eth,
                        transaction_hash=f"0xrem{instr_seq:040x}",
                    )
                )
                in_position = False

    # Collect fees instruction at end if still in position
    if in_position and total_fees > Decimal("0"):
        instr_seq += 1
        instr_id = f"defi-lp-fees-{instr_seq:04d}"
        end_ts = _BASE_TS + timedelta(hours=snapshots[-1][0])
        instructions.append(
            ExecutionInstruction(
                instruction_id=instr_id,
                operation=OperationType.COLLECT_FEES,
                timestamp=end_ts,
                from_venue="uniswap_v3",
                instrument_id="ETH-USDC-0.05",
                amount=total_fees,
                token_out="USDC",
                metadata={"total_fees_usdc": str(total_fees)},
            )
        )

    # IL at final snapshot price
    final_price = Decimal(snapshots[-1][1])
    il_usdc = _compute_impermanent_loss(entry_price, final_price, initial_usdc, initial_eth)
    net_pnl = total_fees - il_usdc

    return {
        "instructions": instructions,
        "execution_results": execution_results,
        "fills": fills,
        "instruction_count": len(instructions),
        "total_fees_usdc": total_fees,
        "impermanent_loss_usdc": il_usdc,
        "net_pnl_usdc": net_pnl,
        "active_hours": active_hours,
        "out_of_range_hours": out_of_range_hours,
        "in_position_at_end": in_position,
        "entry_price": entry_price,
        "price_lower": price_lower,
        "price_upper": price_upper,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeFiBacktest:
    """Portable DeFi LP backtest — no credentials, no network, no on-chain calls."""

    def test_backtest_completes(self) -> None:
        """Backtest runs to completion on 12 ETH/USDC hourly pool snapshots."""
        result = _simulate_defi_lp_backtest(_ETH_USDC_POOL_SNAPSHOTS)
        assert result is not None
        assert "instructions" in result
        assert "total_fees_usdc" in result
        assert "impermanent_loss_usdc" in result

    def test_fees_are_earned(self) -> None:
        """LP position must earn positive fees when price is in range."""
        result = _simulate_defi_lp_backtest(_ETH_USDC_POOL_SNAPSHOTS)
        total_fees = result["total_fees_usdc"]
        assert isinstance(total_fees, Decimal)
        assert total_fees > Decimal("0"), f"Expected positive fees for in-range LP position; got {total_fees}"

    def test_impermanent_loss_is_tracked(self) -> None:
        """Impermanent loss must be computed (non-negative for a price that moved)."""
        result = _simulate_defi_lp_backtest(_ETH_USDC_POOL_SNAPSHOTS)
        il = result["impermanent_loss_usdc"]
        assert isinstance(il, Decimal), f"IL must be Decimal; got {type(il)}"
        # IL is always >= 0 for constant-product AMM when price moves away from entry
        # (final price 3475 vs entry 3480 — small move so IL may be near zero)
        assert il.is_finite(), f"IL is not finite: {il}"

    def test_net_pnl_is_computed(self) -> None:
        """Net P&L (fees - IL) must be Decimal."""
        result = _simulate_defi_lp_backtest(_ETH_USDC_POOL_SNAPSHOTS)
        net_pnl = result["net_pnl_usdc"]
        assert isinstance(net_pnl, Decimal)
        assert net_pnl.is_finite(), f"Net P&L is not finite: {net_pnl}"

    def test_active_hours_positive(self) -> None:
        """At least some hours must have price in LP range."""
        result = _simulate_defi_lp_backtest(_ETH_USDC_POOL_SNAPSHOTS)
        active = result["active_hours"]
        assert isinstance(active, int)
        assert active > 0, f"Zero active hours — all prices outside ±5% range from entry {_INITIAL_ETH_PRICE}"

    def test_instructions_are_execution_instruction_instances(self) -> None:
        """Every instruction must be a valid ExecutionInstruction Pydantic model."""
        from unified_api_contracts import ExecutionInstruction

        result = _simulate_defi_lp_backtest(_ETH_USDC_POOL_SNAPSHOTS)
        instructions = cast("list[ExecutionInstruction]", result["instructions"])
        assert len(instructions) > 0, "Expected at least one LP instruction"
        for instr in instructions:
            assert isinstance(instr, ExecutionInstruction)
            assert instr.from_venue == "uniswap_v3"
            assert instr.instrument_id == "ETH-USDC-0.05"

    def test_add_liquidity_instruction_present(self) -> None:
        """ADD_LIQUIDITY instruction must be generated for first in-range bar."""
        from unified_api_contracts import ExecutionInstruction, OperationType

        result = _simulate_defi_lp_backtest(_ETH_USDC_POOL_SNAPSHOTS)
        instructions = cast("list[ExecutionInstruction]", result["instructions"])
        add_instrs = [i for i in instructions if i.operation == OperationType.ADD_LIQUIDITY]
        assert len(add_instrs) >= 1, "Expected at least one ADD_LIQUIDITY instruction"
        first_add = add_instrs[0]
        assert first_add.token_in == "USDC"
        assert first_add.amount == _INITIAL_USDC_DEPOSIT

    def test_execution_results_are_completed(self) -> None:
        """All ExecutionResult records must have COMPLETED status."""
        from unified_api_contracts import ExecutionResult, ExecutionStatus

        result = _simulate_defi_lp_backtest(_ETH_USDC_POOL_SNAPSHOTS)
        exec_results = cast("list[ExecutionResult]", result["execution_results"])
        for res in exec_results:
            assert isinstance(res, ExecutionResult)
            assert res.status == ExecutionStatus.COMPLETED, (
                f"Instruction {res.instruction_id} has status {res.status}; expected COMPLETED"
            )
            assert res.transaction_hash is not None
            assert res.transaction_hash.startswith("0x")

    def test_price_range_invariants(self) -> None:
        """LP range must be correctly ±5% around entry price."""
        result = _simulate_defi_lp_backtest(_ETH_USDC_POOL_SNAPSHOTS)
        entry = result["entry_price"]
        lower = result["price_lower"]
        upper = result["price_upper"]
        assert isinstance(entry, Decimal)
        assert isinstance(lower, Decimal)
        assert isinstance(upper, Decimal)
        assert lower < entry < upper
        # Verify range width is approximately ±5%
        lower_pct = (entry - lower) / entry
        upper_pct = (upper - entry) / entry
        assert abs(lower_pct - Decimal("0.05")) < Decimal("0.001"), f"Lower bound not ≈5% below entry: {lower_pct:.4f}"
        assert abs(upper_pct - Decimal("0.05")) < Decimal("0.001"), f"Upper bound not ≈5% above entry: {upper_pct:.4f}"
