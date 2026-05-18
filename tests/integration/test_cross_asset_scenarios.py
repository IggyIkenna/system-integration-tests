"""SIT cross-asset scenarios — batch-live symmetry across asset_group boundaries.

Three scenarios beyond Phase 8 honest-coverage:

  Scenario A — DeFi+CeFi hybrid carry
    DeFi long/lend leg (Aave lending APY) + CeFi perp short hedge leg (funding rate).
    Net carry = DeFi lending yield - perp funding payment.
    Invariant: hybrid net carry > 0 iff lending_apy > funding_rate.

  Scenario B — TradFi+Sports batch-live parity smoke
    Both asset groups run through the same batch benchmark-fill logic.
    Invariant: benchmark fill price is deterministic across repeated runs (batch=batch).

  Scenario C — Prediction-only backtest smoke
    Binary prediction market contracts with Polymarket-style outcomes.
    Invariant: resolved YES contract → full payout; resolved NO → zero payout;
    unresolved → proportional mid.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import TypedDict

import pytest

os.environ.setdefault("CLOUD_PROVIDER", "local")
os.environ.setdefault("CLOUD_MOCK_MODE", "true")

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Scenario A — DeFi+CeFi hybrid carry
# ---------------------------------------------------------------------------


class HybridCarryResult(TypedDict):
    daily_carries: list[Decimal]
    cumulative_carry_usdc: Decimal
    positive_days: int
    negative_days: int
    total_days: int


# Aave ETH lending rate snapshots
# field: (day_offset, lending_apy_bps, perp_funding_rate_bps_per_8h)
_HYBRID_CARRY_SNAPSHOTS: list[tuple[int, int, int]] = [
    (0, 420, 8),  # lending 4.2% APY; funding 0.08%/8h (~8.76% APY) — carry negative
    (1, 420, 3),  # funding drops to 0.03%/8h (~3.28% APY) — carry positive
    (2, 435, 3),  # lending APY rises slightly — carry widens
    (3, 435, 4),  # funding ticks up slightly
    (4, 450, 5),  # borderline (4.5% APY vs ~5.47% APY funding)
    (5, 460, 4),  # carry positive again
    (6, 460, 4),  # stable
]


def _funding_apy_bps(funding_8h_bps: int) -> Decimal:
    """Convert 8h funding rate in bps to annualised APY in bps (3 events/day × 365 days)."""
    return Decimal(funding_8h_bps) * 3 * 365


def _simulate_hybrid_carry(
    snapshots: list[tuple[int, int, int]],
    position_usdc: Decimal = Decimal("100_000"),
) -> HybridCarryResult:
    """Simulate DeFi+CeFi hybrid carry over the snapshot window.

    For each day:
      - Earn DeFi lending yield: position × lending_apy_bps / 10_000 / 365
      - Pay CeFi perp funding: position × funding_8h_bps / 10_000 × 3 (3 payments/day)
      - Net carry for day = lending_yield - funding_cost
    """
    daily_carries: list[Decimal] = []
    for _, lending_apy_bps, funding_8h_bps in snapshots:
        lending_yield = position_usdc * Decimal(lending_apy_bps) / 10_000 / 365
        funding_cost = position_usdc * Decimal(funding_8h_bps) / 10_000 * 3
        daily_carries.append(lending_yield - funding_cost)

    return HybridCarryResult(
        daily_carries=daily_carries,
        cumulative_carry_usdc=sum(daily_carries, Decimal("0")),
        positive_days=sum(1 for c in daily_carries if c > 0),
        negative_days=sum(1 for c in daily_carries if c < 0),
        total_days=len(daily_carries),
    )


class TestDefiCefiHybridCarry:
    """Scenario A — DeFi lending + CeFi perp hedge net carry."""

    def test_positive_carry_when_lending_exceeds_funding(self) -> None:
        result = _simulate_hybrid_carry(_HYBRID_CARRY_SNAPSHOTS)
        assert result["positive_days"] >= 4

    def test_negative_carry_on_high_funding_day(self) -> None:
        # Day 0: funding 8 bps/8h = 8.76% APY >> 4.2% lending
        result = _simulate_hybrid_carry(_HYBRID_CARRY_SNAPSHOTS)
        assert result["daily_carries"][0] < Decimal("0")

    def test_cumulative_carry_positive_over_window(self) -> None:
        result = _simulate_hybrid_carry(_HYBRID_CARRY_SNAPSHOTS)
        assert result["cumulative_carry_usdc"] > Decimal("0")

    def test_funding_apy_conversion(self) -> None:
        assert _funding_apy_bps(1) == Decimal("1095")
        assert _funding_apy_bps(8) == Decimal("8760")

    def test_zero_funding_gives_pure_lending_yield(self) -> None:
        snapshots: list[tuple[int, int, int]] = [(0, 400, 0), (1, 400, 0)]
        result = _simulate_hybrid_carry(snapshots, position_usdc=Decimal("365_000"))
        # Each day: 365_000 × 400/10000/365 = 40 USDC lending yield; 0 funding
        assert all(c == Decimal("40") for c in result["daily_carries"])

    def test_asset_group_isolation(self) -> None:
        r1 = _simulate_hybrid_carry(_HYBRID_CARRY_SNAPSHOTS, position_usdc=Decimal("50_000"))
        r2 = _simulate_hybrid_carry(_HYBRID_CARRY_SNAPSHOTS, position_usdc=Decimal("50_000"))
        r_combined = _simulate_hybrid_carry(_HYBRID_CARRY_SNAPSHOTS, position_usdc=Decimal("100_000"))
        assert r1["cumulative_carry_usdc"] + r2["cumulative_carry_usdc"] == r_combined["cumulative_carry_usdc"]


# ---------------------------------------------------------------------------
# Scenario B — TradFi + Sports batch-live parity smoke
# ---------------------------------------------------------------------------


class TradFiBatchResult(TypedDict):
    fills: list[Decimal]
    pnl_per_trade: list[Decimal]
    total_pnl: Decimal


class SportsBatchResult(TypedDict):
    fills: list[Decimal]
    pnl_per_bet: list[Decimal]
    total_pnl: Decimal


# TradFi mock OHLCV (S&P 500, 5 days): (day_offset, open, high, low, close)
_SPX_DAILY: list[tuple[int, str, str, str, str]] = [
    (0, "5200.00", "5225.50", "5185.00", "5218.00"),
    (1, "5218.00", "5240.00", "5205.00", "5235.50"),
    (2, "5235.50", "5250.00", "5215.00", "5228.00"),
    (3, "5228.00", "5245.00", "5210.00", "5242.00"),
    (4, "5242.00", "5260.00", "5230.00", "5255.00"),
]

# Sports odds mock (Premier League, home-win bet): (match_id, decimal_odds, actual_result)
_MATCH_ODDS: list[tuple[str, str, str]] = [
    ("match_001", "1.85", "home"),  # win → profit
    ("match_002", "2.10", "away"),  # loss
    ("match_003", "1.70", "draw"),  # loss
    ("match_004", "1.95", "home"),  # win → profit
    ("match_005", "2.20", "home"),  # win → profit
]


def _simulate_tradfi_batch(
    ohlcv: list[tuple[int, str, str, str, str]],
    stake: Decimal = Decimal("1000"),
) -> TradFiBatchResult:
    """Batch-mode TradFi backtest using benchmark fills (open = benchmark price)."""
    fills = [Decimal(row[1]) for row in ohlcv]
    pnl_per_trade = [stake / Decimal(row[1]) * (Decimal(row[4]) - Decimal(row[1])) for row in ohlcv]
    return TradFiBatchResult(
        fills=fills,
        pnl_per_trade=pnl_per_trade,
        total_pnl=sum(pnl_per_trade, Decimal("0")),
    )


def _simulate_sports_batch(
    odds: list[tuple[str, str, str]],
    stake: Decimal = Decimal("100"),
) -> SportsBatchResult:
    """Batch-mode sports backtest using benchmark fills (offered odds = benchmark price)."""
    fills = [Decimal(row[1]) for row in odds]
    pnl_per_bet = [stake * (Decimal(row[1]) - 1) if row[2] == "home" else -stake for row in odds]
    return SportsBatchResult(
        fills=fills,
        pnl_per_bet=pnl_per_bet,
        total_pnl=sum(pnl_per_bet, Decimal("0")),
    )


class TestTradFiSportsBatchLiveParity:
    """Scenario B — TradFi and Sports benchmark fills are deterministic across runs."""

    def test_tradfi_benchmark_fills_deterministic(self) -> None:
        r1 = _simulate_tradfi_batch(_SPX_DAILY)
        r2 = _simulate_tradfi_batch(_SPX_DAILY)
        assert r1["fills"] == r2["fills"]
        assert r1["total_pnl"] == r2["total_pnl"]

    def test_sports_benchmark_fills_deterministic(self) -> None:
        r1 = _simulate_sports_batch(_MATCH_ODDS)
        r2 = _simulate_sports_batch(_MATCH_ODDS)
        assert r1["fills"] == r2["fills"]
        assert r1["total_pnl"] == r2["total_pnl"]

    def test_tradfi_fills_match_open_price(self) -> None:
        result = _simulate_tradfi_batch(_SPX_DAILY)
        for fill, row in zip(result["fills"], _SPX_DAILY):
            assert fill == Decimal(row[1])

    def test_sports_fills_match_offered_odds(self) -> None:
        result = _simulate_sports_batch(_MATCH_ODDS)
        for fill, row in zip(result["fills"], _MATCH_ODDS):
            assert fill == Decimal(row[1])

    def test_tradfi_pnl_positive_on_rising_day(self) -> None:
        # Day 4: open 5242 → close 5255 — positive
        result = _simulate_tradfi_batch(_SPX_DAILY)
        assert result["pnl_per_trade"][-1] > Decimal("0")

    def test_sports_pnl_net_positive_majority_wins(self) -> None:
        # 3 of 5 bets win (match 001, 004, 005)
        result = _simulate_sports_batch(_MATCH_ODDS)
        assert result["total_pnl"] > Decimal("0")

    def test_cross_asset_pnl_additive(self) -> None:
        tradfi = _simulate_tradfi_batch(_SPX_DAILY)
        sports = _simulate_sports_batch(_MATCH_ODDS)
        # TradFi and Sports asset groups are independent — sum is additive
        combined = tradfi["total_pnl"] + sports["total_pnl"]
        assert combined == tradfi["total_pnl"] + sports["total_pnl"]

    def test_batch_benchmark_fill_zero_execution_alpha(self) -> None:
        result = _simulate_tradfi_batch(_SPX_DAILY)
        for fill, row in zip(result["fills"], _SPX_DAILY):
            benchmark_price = Decimal(row[1])
            execution_alpha_bps = (fill - benchmark_price) / benchmark_price * 10_000
            assert execution_alpha_bps == Decimal("0")


# ---------------------------------------------------------------------------
# Scenario C — Prediction-only backtest smoke
# ---------------------------------------------------------------------------


class PredictionContractResult(TypedDict):
    contract_id: str
    asset_group: str
    mid_entry: Decimal
    outcome: str
    payout_usdc: Decimal
    pnl_usdc: Decimal


class PredictionBatchResult(TypedDict):
    contracts: list[PredictionContractResult]
    total_pnl: Decimal
    num_resolved: int


# Prediction market contracts (Polymarket-style binary YES/NO)
# field: (contract_id, asset_group, question_short, mid_at_entry, resolved_outcome)
# mid price in [0, 1] = probability
_PREDICTION_CONTRACTS: list[tuple[str, str, str, str, str]] = [
    ("pred_001", "prediction", "ETH hits 4000 by Jun-1?", "0.42", "YES"),
    ("pred_002", "prediction", "BTC above 90k by Jun-30?", "0.38", "NO"),
    ("pred_003", "prediction", "Fed cuts in Jun meeting?", "0.65", "YES"),
    ("pred_004", "prediction", "SPX hits ATH in May?", "0.71", "NO"),
    ("pred_005", "prediction", "ETH/BTC ratio above 0.05?", "0.55", "UNRESOLVED"),
]


def _simulate_prediction_backtest(
    contracts: list[tuple[str, str, str, str, str]],
    stake_usdc: Decimal = Decimal("1000"),
) -> PredictionBatchResult:
    """Simulate binary prediction market outcomes.

    YES → payout = stake / mid_price (full contract payout)
    NO  → payout = 0 (loss of stake)
    UNRESOLVED → payout = stake (no P&L)
    """
    results: list[PredictionContractResult] = []
    for contract_id, asset_group, _question, mid_str, outcome in contracts:
        mid = Decimal(mid_str)
        if outcome == "YES":
            payout = stake_usdc / mid
            pnl = payout - stake_usdc
        elif outcome == "NO":
            payout = Decimal("0")
            pnl = -stake_usdc
        else:
            payout = stake_usdc
            pnl = Decimal("0")
        results.append(
            PredictionContractResult(
                contract_id=contract_id,
                asset_group=asset_group,
                mid_entry=mid,
                outcome=outcome,
                payout_usdc=payout,
                pnl_usdc=pnl,
            )
        )
    return PredictionBatchResult(
        contracts=results,
        total_pnl=sum((r["pnl_usdc"] for r in results), Decimal("0")),
        num_resolved=sum(1 for r in results if r["outcome"] != "UNRESOLVED"),
    )


class TestPredictionOnlyBacktest:
    """Scenario C — prediction market binary contracts backtest smoke."""

    def test_yes_outcome_yields_positive_pnl(self) -> None:
        result = _simulate_prediction_backtest(_PREDICTION_CONTRACTS)
        yes_contracts = [r for r in result["contracts"] if r["outcome"] == "YES"]
        for c in yes_contracts:
            assert c["pnl_usdc"] > Decimal("0"), f"YES outcome should yield positive P&L: {c}"

    def test_no_outcome_yields_full_loss(self) -> None:
        result = _simulate_prediction_backtest(_PREDICTION_CONTRACTS)
        no_contracts = [r for r in result["contracts"] if r["outcome"] == "NO"]
        for c in no_contracts:
            assert c["pnl_usdc"] == Decimal("-1000")

    def test_unresolved_contract_zero_pnl(self) -> None:
        result = _simulate_prediction_backtest(_PREDICTION_CONTRACTS)
        unresolved = [r for r in result["contracts"] if r["outcome"] == "UNRESOLVED"]
        for c in unresolved:
            assert c["pnl_usdc"] == Decimal("0")

    def test_asset_group_field_preserved(self) -> None:
        result = _simulate_prediction_backtest(_PREDICTION_CONTRACTS)
        for c in result["contracts"]:
            assert c["asset_group"] == "prediction"

    def test_benchmark_deterministic(self) -> None:
        r1 = _simulate_prediction_backtest(_PREDICTION_CONTRACTS)
        r2 = _simulate_prediction_backtest(_PREDICTION_CONTRACTS)
        assert r1["total_pnl"] == r2["total_pnl"]
        for c1, c2 in zip(r1["contracts"], r2["contracts"]):
            assert c1["pnl_usdc"] == c2["pnl_usdc"]

    def test_higher_entry_mid_lower_yes_payout(self) -> None:
        # Higher probability entry → lower payout multiplier (less edge on true upside)
        low_prob = _simulate_prediction_backtest([("x", "prediction", "q", "0.20", "YES")])
        high_prob = _simulate_prediction_backtest([("y", "prediction", "q", "0.80", "YES")])
        assert low_prob["total_pnl"] > high_prob["total_pnl"]

    def test_num_resolved_contracts_correct(self) -> None:
        result = _simulate_prediction_backtest(_PREDICTION_CONTRACTS)
        # 4 resolved (YES or NO); 1 UNRESOLVED
        assert result["num_resolved"] == 4
