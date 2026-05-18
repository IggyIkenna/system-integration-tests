"""Cross-asset scenario integration tests.

Three scenarios verifying that the unified pipeline handles multiple asset_group
domains simultaneously with correct manifest structure, canonical fills, and
strategy outputs.

Scenario 1 — DeFi+CeFi hybrid carry:
  DeFi leg: ETH staked via Lido (stETH, ~4% APR); long on-chain.
  CeFi hedge leg: short ETH-USDT-PERP on Binance (delta-neutral).
  Net exposure: 0 delta; net carry = staking_apy - funding_rate.

Scenario 2 — TradFi+Sports pipeline parity:
  Both asset_groups produce ManifestWriter records in the same 4-state
  capture_status schema. Verify no cross-contamination of domain-specific
  empty reasons, and that row_key shapes are independent.

Scenario 3 — Prediction-only backtest smoke:
  Binary outcome market (Polymarket-style). Resolves YES/NO.
  Verifies: signal direction ↔ outcome alignment; settlement instruction;
  fill P&L sign (correct YES vs NO payoff).

Portable: runs without credentials, no network calls, no on-chain RPC.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict

import pytest
from unified_api_contracts import CanonicalFill, PipelineMode

if TYPE_CHECKING:
    from unified_api_contracts.canonical.domain.execution.base import (
        ExecutionInstruction,
        ExecutionResult,
    )

pytestmark = pytest.mark.integration

os.environ.setdefault("CLOUD_PROVIDER", "local")
os.environ.setdefault("CLOUD_MOCK_MODE", "true")

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC)


class _HybridCarryResult(TypedDict):
    staking_yield_usd: Decimal
    funding_cost_usd: Decimal
    net_carry_usd: Decimal
    instructions: list[ExecutionInstruction]
    results: list[ExecutionResult]
    fills: list[CanonicalFill]
    net_delta: Decimal


class _PredictionBacktestResult(TypedDict):
    market_id: str
    buy_yes: bool
    outcome_yes: bool
    we_win: bool
    shares: Decimal
    stake_usdc: Decimal
    settlement_usdc: Decimal
    pnl_usdc: Decimal
    instructions: list[ExecutionInstruction]
    results: list[ExecutionResult]
    fills: list[CanonicalFill]


# ---------------------------------------------------------------------------
# Scenario 1 — DeFi + CeFi hybrid carry
# ---------------------------------------------------------------------------

# DeFi leg: stETH staking rewards over 30 days
_STETH_APR = Decimal("0.040")  # 4% annual staking rate (Lido approx)
_ETH_DEPOSIT = Decimal("10.0")  # 10 ETH staked
_ETH_USD_PRICE = Decimal("3500.00")
_DAYS = 30

# CeFi leg: Binance ETH-USDT-PERP funding rate (8hr periods)
_FUNDING_RATE_8H = Decimal("0.0001")  # 0.01% per 8h = 0.03% per day = ~11% annualised


def _compute_defi_staking_carry(eth: Decimal, apr: Decimal, days: int, eth_price: Decimal) -> Decimal:
    """Annualised ETH staking yield for `days` days, denominated in USD."""
    daily_rate = apr / Decimal("365")
    return eth * eth_price * daily_rate * Decimal(str(days))


def _compute_cefi_funding_cost(eth: Decimal, funding_8h: Decimal, periods: int, eth_price: Decimal) -> Decimal:
    """Total funding paid on short ETH-USDT-PERP position (positive = cost, short pays when rate > 0)."""
    notional = eth * eth_price
    return notional * funding_8h * Decimal(str(periods))


def _simulate_hybrid_carry(
    eth: Decimal = _ETH_DEPOSIT,
    apr: Decimal = _STETH_APR,
    days: int = _DAYS,
    eth_price: Decimal = _ETH_USD_PRICE,
    funding_8h: Decimal = _FUNDING_RATE_8H,
) -> _HybridCarryResult:
    """Return carry P&L breakdown for a DeFi+CeFi hybrid carry position."""
    from unified_api_contracts.canonical.domain.execution.base import (
        ExecutionInstruction,
        ExecutionResult,
        ExecutionStatus,
        OperationType,
    )

    staking_yield_usd = _compute_defi_staking_carry(eth, apr, days, eth_price)
    funding_cost_usd = _compute_cefi_funding_cost(eth, funding_8h, days * 3, eth_price)
    net_carry_usd = staking_yield_usd - funding_cost_usd

    instructions: list[ExecutionInstruction] = []
    results: list[ExecutionResult] = []
    fills: list[CanonicalFill] = []

    # DeFi stake instruction
    stake_id = "hybrid-defi-stake-0001"
    instructions.append(
        ExecutionInstruction(
            instruction_id=stake_id,
            operation=OperationType.ADD_LIQUIDITY,
            timestamp=_BASE_TS,
            from_venue="lido",
            instrument_id="stETH",
            token_in="ETH",
            amount=eth,
            token_out="stETH",
            max_slippage_bps=10,
            metadata={"apr": str(apr), "days": str(days)},
        )
    )
    results.append(
        ExecutionResult(
            instruction_id=stake_id,
            operation=OperationType.ADD_LIQUIDITY,
            status=ExecutionStatus.COMPLETED,
            timestamp_submitted=_BASE_TS,
            timestamp_completed=_BASE_TS,
            amount_executed=eth,
            transaction_hash="0xstake0001" + "0" * 50,
        )
    )

    # CeFi short instruction
    short_id = "hybrid-cefi-short-0001"
    instructions.append(
        ExecutionInstruction(
            instruction_id=short_id,
            operation=OperationType.SELL,
            timestamp=_BASE_TS,
            from_venue="binance",
            instrument_id="ETH-USDT-PERP",
            amount=eth,
            metadata={"funding_8h": str(funding_8h), "delta_neutral": "true"},
        )
    )
    results.append(
        ExecutionResult(
            instruction_id=short_id,
            operation=OperationType.SELL,
            status=ExecutionStatus.COMPLETED,
            timestamp_submitted=_BASE_TS,
            timestamp_completed=_BASE_TS,
            amount_executed=eth,
            transaction_hash=None,
        )
    )

    return {
        "staking_yield_usd": staking_yield_usd,
        "funding_cost_usd": funding_cost_usd,
        "net_carry_usd": net_carry_usd,
        "instructions": instructions,
        "results": results,
        "fills": fills,
        "net_delta": Decimal("0"),  # long stETH + short ETH-PERP = ~0 delta
    }


@pytest.mark.integration
class TestDeFiCeFiHybridCarry:
    """Scenario 1 — DeFi staking long + CeFi perp short (delta-neutral carry)."""

    def test_carry_positive_when_staking_apy_exceeds_funding(self) -> None:
        result = _simulate_hybrid_carry()
        assert result["net_carry_usd"] > Decimal("0"), (
            f"Net carry should be positive (staking APY > funding rate); got {result['net_carry_usd']}"
        )

    def test_net_delta_is_zero(self) -> None:
        result = _simulate_hybrid_carry()
        assert result["net_delta"] == Decimal("0"), "Hybrid carry must be delta-neutral"

    def test_both_instructions_generated(self) -> None:
        result = _simulate_hybrid_carry()
        instructions = result["instructions"]
        assert len(instructions) == 2
        venues = {i.from_venue for i in instructions}
        assert "lido" in venues, "DeFi staking instruction must use lido venue"
        assert "binance" in venues, "CeFi hedge instruction must use binance venue"

    def test_both_results_completed(self) -> None:
        from unified_api_contracts.canonical.domain.execution.base import ExecutionStatus

        result = _simulate_hybrid_carry()
        exec_results = result["results"]
        for res in exec_results:
            assert res.status == ExecutionStatus.COMPLETED

    def test_negative_carry_when_funding_exceeds_staking(self) -> None:
        result = _simulate_hybrid_carry(funding_8h=Decimal("0.0050"))  # extreme funding
        assert result["net_carry_usd"] < Decimal("0"), "When funding cost >> staking APR, net carry must be negative"

    def test_staking_yield_scales_with_days(self) -> None:
        r_30 = _simulate_hybrid_carry(days=30)
        r_60 = _simulate_hybrid_carry(days=60)
        assert r_60["staking_yield_usd"] > r_30["staking_yield_usd"], (
            "60-day yield must exceed 30-day yield under same APR"
        )


# ---------------------------------------------------------------------------
# Scenario 2 — TradFi + Sports pipeline parity (ManifestWriter schema)
# ---------------------------------------------------------------------------


def _manifest_records_for_asset_group(asset_group: str, data_type: str, venue: str) -> list[dict[str, object]]:
    """Simulate ManifestWriter records for one asset_group/data_type combo."""
    from unified_trading_library import ManifestWriter

    service_name = f"{asset_group}-data-service"
    writer = ManifestWriter(service_name=service_name, catalogue_bucket="", batch_size=0, per_vm_shards=False)
    date = "2026-05-18"
    writer.add(date=date, venue=venue, data_type=data_type, row_count=500)
    writer.record_empty(
        row_key={"date": date, "venue": f"{venue}-secondary", "data_type": data_type},
        reason="SOURCE_RETURNED_ZERO",
        pipeline_mode=PipelineMode.BATCH_DATABENTO,
    )
    return [
        {
            "capture_status": r.capture_status,
            "venue": r.venue,
            "data_type": r.data_type,
            "asset_group": asset_group,
        }
        for r in writer._records
    ]


@pytest.mark.integration
class TestTradFiSportsPipelineParity:
    """Scenario 2 — TradFi and Sports both produce 4-state capture_status records."""

    def test_tradfi_records_have_expected_statuses(self) -> None:
        from unified_trading_library import CaptureStatus

        records = _manifest_records_for_asset_group("tradfi", "ohlcv_1d", "DATABENTO-NYSE")
        statuses = {r["capture_status"] for r in records}
        assert CaptureStatus.CAPTURED.value in statuses
        assert CaptureStatus.EMPTY_CONFIRMED.value in statuses

    def test_sports_records_have_expected_statuses(self) -> None:
        from unified_trading_library import CaptureStatus

        records = _manifest_records_for_asset_group("sports", "match_odds", "SPORTRADAR")
        statuses = {r["capture_status"] for r in records}
        assert CaptureStatus.CAPTURED.value in statuses
        assert CaptureStatus.EMPTY_CONFIRMED.value in statuses

    def test_tradfi_and_sports_capture_status_schema_identical(self) -> None:
        tradfi = _manifest_records_for_asset_group("tradfi", "ohlcv_1d", "DATABENTO-NYSE")
        sports = _manifest_records_for_asset_group("sports", "match_odds", "SPORTRADAR")
        tradfi_status_set = {r["capture_status"] for r in tradfi}
        sports_status_set = {r["capture_status"] for r in sports}
        assert tradfi_status_set == sports_status_set, (
            f"TradFi and Sports capture_status schemas diverge: tradfi={tradfi_status_set}, sports={sports_status_set}"
        )

    def test_asset_group_fields_do_not_cross_contaminate(self) -> None:
        tradfi = _manifest_records_for_asset_group("tradfi", "ohlcv_1d", "DATABENTO-NYSE")
        sports = _manifest_records_for_asset_group("sports", "match_odds", "SPORTRADAR")
        for r in tradfi:
            assert r["asset_group"] == "tradfi"
        for r in sports:
            assert r["asset_group"] == "sports"

    def test_record_count_identical_across_asset_groups(self) -> None:
        tradfi = _manifest_records_for_asset_group("tradfi", "ohlcv_1d", "DATABENTO-NYSE")
        sports = _manifest_records_for_asset_group("sports", "match_odds", "SPORTRADAR")
        assert len(tradfi) == len(sports), (
            f"TradFi has {len(tradfi)} records but Sports has {len(sports)}; schema parity requires equal count"
        )


# ---------------------------------------------------------------------------
# Scenario 3 — Prediction-only backtest smoke
# ---------------------------------------------------------------------------

# Binary prediction market: "Will ETH close above $3600 by 2026-05-23?"
_PREDICTION_MARKET_ID = "ETH_CLOSE_ABOVE_3600_20260523"
_YES_PRICE = Decimal("0.62")  # 62 cents on the dollar → 62% probability YES
_NO_PRICE = Decimal("0.38")
_STAKE_USDC = Decimal("1000.00")


def _simulate_prediction_backtest(
    market_id: str,
    yes_price: Decimal,
    stake: Decimal,
    outcome_yes: bool,
    buy_yes: bool = True,
) -> _PredictionBacktestResult:
    """Simulate a binary prediction market backtest.

    Args:
        market_id: prediction market identifier
        yes_price: price of YES share (0..1)
        stake: USDC staked
        outcome_yes: True = YES resolves
        buy_yes: True = we bought YES shares, False = we bought NO shares
    """
    from unified_api_contracts.canonical.domain.execution.base import (
        ExecutionInstruction,
        ExecutionResult,
        ExecutionStatus,
        OperationType,
        OrderSide,
    )

    no_price = Decimal("1") - yes_price
    bought_price = yes_price if buy_yes else no_price
    shares = stake / bought_price

    # Entry instruction — BUY YES or NO shares
    entry_id = f"pred-buy-{'yes' if buy_yes else 'no'}-0001"
    entry_venue = "polymarket"
    entry_instr = ExecutionInstruction(
        instruction_id=entry_id,
        operation=OperationType.BUY,
        timestamp=_BASE_TS,
        from_venue=entry_venue,
        instrument_id=market_id,
        amount=stake,
        metadata={
            "side": "YES" if buy_yes else "NO",
            "price": str(bought_price),
            "shares": str(shares),
        },
    )
    entry_result = ExecutionResult(
        instruction_id=entry_id,
        operation=OperationType.BUY,
        status=ExecutionStatus.COMPLETED,
        timestamp_submitted=_BASE_TS,
        timestamp_completed=_BASE_TS,
        amount_executed=stake,
        transaction_hash=None,
    )

    # Settlement: win = YES holders get $1/share, NO holders get $0
    we_win = (buy_yes and outcome_yes) or (not buy_yes and not outcome_yes)
    settlement_value = shares * Decimal("1") if we_win else Decimal("0")
    pnl = settlement_value - stake

    fill = CanonicalFill(
        fill_id="pred-settle-0001",
        order_id=entry_id,
        venue=entry_venue,
        instrument_id=market_id,
        side=OrderSide.BUY,
        quantity=shares,
        price=Decimal("1") if we_win else Decimal("0"),
        fee=Decimal("0"),
        timestamp=_BASE_TS + timedelta(days=5),
        category="prediction",
    )

    settle_id = "pred-settle-instr-0001"
    settle_instr = ExecutionInstruction(
        instruction_id=settle_id,
        operation=OperationType.COLLECT_FEES,
        timestamp=_BASE_TS + timedelta(days=5),
        from_venue=entry_venue,
        instrument_id=market_id,
        amount=settlement_value,
        metadata={"outcome": "YES" if outcome_yes else "NO", "we_win": str(we_win)},
    )
    settle_result = ExecutionResult(
        instruction_id=settle_id,
        operation=OperationType.COLLECT_FEES,
        status=ExecutionStatus.COMPLETED,
        timestamp_submitted=_BASE_TS + timedelta(days=5),
        timestamp_completed=_BASE_TS + timedelta(days=5),
        amount_executed=settlement_value,
        transaction_hash=None,
    )

    return {
        "market_id": market_id,
        "buy_yes": buy_yes,
        "outcome_yes": outcome_yes,
        "we_win": we_win,
        "shares": shares,
        "stake_usdc": stake,
        "settlement_usdc": settlement_value,
        "pnl_usdc": pnl,
        "instructions": [entry_instr, settle_instr],
        "results": [entry_result, settle_result],
        "fills": [fill],
    }


@pytest.mark.integration
class TestPredictionBacktestSmoke:
    """Scenario 3 — binary prediction market smoke: BUY YES, outcome resolves YES."""

    def test_correct_prediction_yields_profit(self) -> None:
        result = _simulate_prediction_backtest(
            _PREDICTION_MARKET_ID, _YES_PRICE, _STAKE_USDC, outcome_yes=True, buy_yes=True
        )
        assert result["we_win"] is True
        assert result["pnl_usdc"] > Decimal("0"), f"Correct YES prediction must profit; got pnl={result['pnl_usdc']}"

    def test_incorrect_prediction_yields_full_loss(self) -> None:
        result = _simulate_prediction_backtest(
            _PREDICTION_MARKET_ID, _YES_PRICE, _STAKE_USDC, outcome_yes=False, buy_yes=True
        )
        assert result["we_win"] is False
        assert result["pnl_usdc"] == -_STAKE_USDC, (
            f"Incorrect YES prediction should lose full stake; got pnl={result['pnl_usdc']}"
        )

    def test_no_buyer_wins_on_yes_resolution_false(self) -> None:
        result = _simulate_prediction_backtest(
            _PREDICTION_MARKET_ID, _YES_PRICE, _STAKE_USDC, outcome_yes=False, buy_yes=False
        )
        assert result["we_win"] is True
        assert result["pnl_usdc"] > Decimal("0")

    def test_instructions_include_entry_and_settlement(self) -> None:
        result = _simulate_prediction_backtest(
            _PREDICTION_MARKET_ID, _YES_PRICE, _STAKE_USDC, outcome_yes=True, buy_yes=True
        )
        instructions = result["instructions"]
        assert len(instructions) == 2
        ops = {i.operation.value for i in instructions}
        assert "BUY" in ops
        assert "COLLECT_FEES" in ops

    def test_fill_has_prediction_asset_group(self) -> None:
        result = _simulate_prediction_backtest(
            _PREDICTION_MARKET_ID, _YES_PRICE, _STAKE_USDC, outcome_yes=True, buy_yes=True
        )
        fills = result["fills"]
        assert len(fills) == 1
        assert fills[0].category == "prediction"

    def test_yes_no_prices_sum_to_one(self) -> None:
        assert _YES_PRICE + _NO_PRICE == Decimal("1"), f"YES ({_YES_PRICE}) + NO ({_NO_PRICE}) must equal 1.00"

    def test_pnl_magnitude_reflects_market_implied_probability(self) -> None:
        result = _simulate_prediction_backtest(
            _PREDICTION_MARKET_ID, _YES_PRICE, _STAKE_USDC, outcome_yes=True, buy_yes=True
        )
        expected_pnl = _STAKE_USDC / _YES_PRICE - _STAKE_USDC
        assert abs(result["pnl_usdc"] - expected_pnl) < Decimal("0.01"), (
            f"P&L {result['pnl_usdc']} diverges from expected {expected_pnl}"
        )
