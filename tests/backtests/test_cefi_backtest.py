"""Portable backtest integration test — CeFi (centralised exchange).

Runs without credentials using inline mock OHLCV data. No network calls.
Tests the full backtest pipeline:
  data loading -> feature calculation -> strategy signal -> order generation
  -> fill simulation -> P&L attribution

Domain: CeFi (BTCUSDT on Binance)
Strategy: Simple momentum — buy if close > SMA(5), sell/flat otherwise.
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
# Mock data — 15 BTC-USDT candles (hourly), realistic 2026-03 price range
# ---------------------------------------------------------------------------

# (timestamp_offset_hours, open, high, low, close, volume_btc)
_BTCUSDT_OHLCV: list[tuple[int, str, str, str, str, str]] = [
    (0, "82100.00", "82450.00", "81900.00", "82300.00", "180.5"),
    (1, "82300.00", "82750.00", "82150.00", "82650.00", "210.3"),
    (2, "82650.00", "83200.00", "82500.00", "83100.00", "245.8"),
    (3, "83100.00", "83350.00", "82900.00", "83250.00", "195.1"),
    (4, "83250.00", "83600.00", "83050.00", "83500.00", "228.7"),
    (5, "83500.00", "83700.00", "82800.00", "82950.00", "312.4"),  # reversal candle
    (6, "82950.00", "83100.00", "82600.00", "82700.00", "267.9"),
    (7, "82700.00", "82900.00", "82200.00", "82350.00", "290.5"),
    (8, "82350.00", "82500.00", "81800.00", "81900.00", "340.2"),
    (9, "81900.00", "82150.00", "81500.00", "81600.00", "380.8"),
    (10, "81600.00", "82000.00", "81400.00", "81850.00", "295.3"),
    (11, "81850.00", "82300.00", "81700.00", "82200.00", "250.1"),
    (12, "82200.00", "82600.00", "82000.00", "82500.00", "220.6"),
    (13, "82500.00", "82900.00", "82300.00", "82800.00", "198.4"),
    (14, "82800.00", "83200.00", "82650.00", "83150.00", "175.9"),
]

_BASE_TS = datetime(2026, 3, 18, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Backtest engine — pure Python, no cloud deps
# ---------------------------------------------------------------------------


def _compute_sma(closes: list[Decimal], window: int) -> Decimal | None:
    """Compute simple moving average over the last *window* closes."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _simulate_cefi_momentum_backtest(
    ohlcv: list[tuple[int, str, str, str, str, str]],
    sma_window: int = 5,
    qty_btc: Decimal = Decimal("0.1"),
    fee_rate: Decimal = Decimal("0.0004"),  # 4bps taker fee (Binance standard)
) -> dict[str, object]:
    """Run a simple momentum backtest on the given OHLCV data.

    Signal: buy if close > SMA(sma_window), else flat (no short selling).
    Fill simulation: next open price (execution on bar open after signal bar).

    Returns a result dict with fills, P&L, and diagnostics.
    """
    from unified_api_contracts import CanonicalFill, CanonicalOrder, OrderSide, OrderStatus, OrderType

    closes: list[Decimal] = []
    orders: list[CanonicalOrder] = []
    fills: list[CanonicalFill] = []
    position: Decimal = Decimal("0")
    cash: Decimal = Decimal("100000.00")  # starting USDT
    entry_price: Decimal = Decimal("0")
    order_seq = 0

    for i, (offset_h, _open_p, _high, _low, close_p, _vol) in enumerate(ohlcv):
        ts = _BASE_TS + timedelta(hours=offset_h)
        close = Decimal(close_p)
        closes.append(close)

        sma = _compute_sma(closes, sma_window)
        if sma is None:
            continue  # not enough history for signal

        signal_buy = close > sma
        # Need next bar's open to fill — skip last bar
        if i + 1 >= len(ohlcv):
            continue

        next_open = Decimal(ohlcv[i + 1][1])
        next_ts = _BASE_TS + timedelta(hours=ohlcv[i + 1][0])
        order_seq += 1
        order_id = f"cefi-bt-{order_seq:04d}"
        fill_id = f"fill-bt-{order_seq:04d}"

        if signal_buy and position == Decimal("0"):
            # Enter long
            fee = next_open * qty_btc * fee_rate
            orders.append(
                CanonicalOrder(
                    order_id=order_id,
                    timestamp=ts,
                    venue="binance",
                    instrument_id="BTCUSDT",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=qty_btc,
                    price=next_open,
                    status=OrderStatus.FILLED,
                    filled_quantity=qty_btc,
                    average_fill_price=next_open,
                    strategy_id="cefi_momentum_bt",
                )
            )
            fills.append(
                CanonicalFill(
                    fill_id=fill_id,
                    order_id=order_id,
                    timestamp=next_ts,
                    venue="binance",
                    instrument_id="BTCUSDT",
                    side=OrderSide.BUY,
                    price=next_open,
                    quantity=qty_btc,
                    fee=fee,
                    fee_currency="USDT",
                    is_maker=False,
                    strategy_id="cefi_momentum_bt",
                )
            )
            cash -= next_open * qty_btc + fee
            position = qty_btc
            entry_price = next_open

        elif not signal_buy and position > Decimal("0"):
            # Exit long
            fee = next_open * qty_btc * fee_rate
            realized_pnl = (next_open - entry_price) * qty_btc - fee
            orders.append(
                CanonicalOrder(
                    order_id=order_id,
                    timestamp=ts,
                    venue="binance",
                    instrument_id="BTCUSDT",
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=qty_btc,
                    price=next_open,
                    status=OrderStatus.FILLED,
                    filled_quantity=qty_btc,
                    average_fill_price=next_open,
                    strategy_id="cefi_momentum_bt",
                )
            )
            fills.append(
                CanonicalFill(
                    fill_id=fill_id,
                    order_id=order_id,
                    timestamp=next_ts,
                    venue="binance",
                    instrument_id="BTCUSDT",
                    side=OrderSide.SELL,
                    price=next_open,
                    quantity=qty_btc,
                    fee=fee,
                    fee_currency="USDT",
                    is_maker=False,
                    realized_pnl=realized_pnl,
                    strategy_id="cefi_momentum_bt",
                )
            )
            cash += next_open * qty_btc - fee
            position = Decimal("0")
            entry_price = Decimal("0")

    # Mark-to-market: liquidate any open position at last bar close
    final_close = Decimal(ohlcv[-1][4])
    mtm_value = position * final_close
    total_equity = cash + mtm_value
    starting_equity = Decimal("100000.00")
    total_pnl = total_equity - starting_equity

    buy_fills = [f for f in fills if f.side == OrderSide.BUY]
    sell_fills = [f for f in fills if f.side == OrderSide.SELL]

    return {
        "fills": fills,
        "orders": orders,
        "fill_count": len(fills),
        "buy_fill_count": len(buy_fills),
        "sell_fill_count": len(sell_fills),
        "total_pnl": total_pnl,
        "final_cash": cash,
        "final_position": position,
        "total_equity": total_equity,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCeFiBacktest:
    """Portable CeFi momentum backtest — no credentials, no network."""

    def test_backtest_completes(self) -> None:
        """Backtest runs to completion on 15 BTC-USDT hourly candles."""
        result = _simulate_cefi_momentum_backtest(_BTCUSDT_OHLCV)
        assert result is not None
        assert "fills" in result
        assert "total_pnl" in result

    def test_fill_count_is_positive(self) -> None:
        """Momentum strategy must produce at least one fill on the sample data."""
        result = _simulate_cefi_momentum_backtest(_BTCUSDT_OHLCV)
        fill_count = result["fill_count"]
        assert isinstance(fill_count, int)
        assert fill_count > 0, (
            f"Expected at least one fill; got {fill_count}. Check that the SMA window is shorter than the candle count."
        )

    def test_buy_and_sell_fills_present(self) -> None:
        """Round-trip trades must produce both buy and sell fills."""
        result = _simulate_cefi_momentum_backtest(_BTCUSDT_OHLCV)
        assert result["buy_fill_count"] > 0, "No buy fills generated"
        assert result["sell_fill_count"] > 0, "No sell fills generated"

    def test_pnl_is_computed(self) -> None:
        """Total P&L must be a finite Decimal (positive or negative)."""
        result = _simulate_cefi_momentum_backtest(_BTCUSDT_OHLCV)
        pnl = result["total_pnl"]
        assert isinstance(pnl, Decimal)
        # Sanity band: ±10% of starting 100k capital on 15 hourly candles
        assert abs(pnl) < Decimal("10000.00"), f"P&L of {pnl} looks unrealistic for a 15-bar hourly backtest"

    def test_fills_are_canonical_fill_instances(self) -> None:
        """Every fill must be a valid CanonicalFill Pydantic model instance."""
        from unified_api_contracts import CanonicalFill

        result = _simulate_cefi_momentum_backtest(_BTCUSDT_OHLCV)
        fills = cast("list[CanonicalFill]", result["fills"])
        for fill in fills:
            assert isinstance(fill, CanonicalFill)
            assert fill.venue == "binance"
            assert fill.instrument_id == "BTCUSDT"
            assert fill.quantity == Decimal("0.1")
            assert fill.fee is not None
            assert fill.fee > Decimal("0")

    def test_orders_are_canonical_order_instances(self) -> None:
        """Every order must be a valid CanonicalOrder Pydantic model instance."""
        from unified_api_contracts import CanonicalOrder

        result = _simulate_cefi_momentum_backtest(_BTCUSDT_OHLCV)
        orders = cast("list[CanonicalOrder]", result["orders"])
        for order in orders:
            assert isinstance(order, CanonicalOrder)
            assert order.venue == "binance"
            assert order.strategy_id == "cefi_momentum_bt"

    def test_final_position_is_non_negative(self) -> None:
        """Long-only strategy must never hold a negative position."""
        result = _simulate_cefi_momentum_backtest(_BTCUSDT_OHLCV)
        final_pos = result["final_position"]
        assert isinstance(final_pos, Decimal)
        assert final_pos >= Decimal("0"), f"Negative position not allowed in long-only: {final_pos}"

    def test_total_equity_is_positive(self) -> None:
        """Total equity (cash + mark-to-market) must remain positive."""
        result = _simulate_cefi_momentum_backtest(_BTCUSDT_OHLCV)
        equity = result["total_equity"]
        assert isinstance(equity, Decimal)
        assert equity > Decimal("0"), f"Account blown up: equity = {equity}"
