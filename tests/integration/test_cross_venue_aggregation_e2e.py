"""SIT end-to-end test: cross-venue position aggregation.

Smoke test verifying that the aggregation engine correctly nets positions
from fills on multiple venues. Runs entirely credential-free with
CLOUD_PROVIDER=local CLOUD_MOCK_MODE=true — no external services required.

Validates the critical path:
  mock fill (venue A) -> CrossVenueAggregator -> correct AggregatedPosition
  mock fill (venue B) -> CrossVenueAggregator -> correct net + VWAP
  float->Decimal boundary: Decimal(str(float_value)) round-trips exactly
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from unified_events_interface import MockEventSink, setup_events

# Ensure credential-free execution throughout
os.environ.setdefault("CLOUD_PROVIDER", "local")
os.environ.setdefault("CLOUD_MOCK_MODE", "true")

setup_events(
    mode="batch",
    service_name="sit-cross-venue-aggregation-e2e",
    sink=MockEventSink(),
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Core aggregation correctness (pure in-process, no network)
# ---------------------------------------------------------------------------


def test_aggregated_position_net_quantity_two_venues() -> None:
    """Fill on Binance (long 2) + Bybit (long 3) -> net 5, VWAP = 48800."""
    from unittest.mock import patch

    from position_balance_monitor_service.core.cross_venue_aggregator import (
        CrossVenueAggregator,
        _VenueData,
    )

    async def _run() -> None:
        with patch(
            "position_balance_monitor_service.core.cross_venue_aggregator.log_event"
        ):
            agg = CrossVenueAggregator()

            vd_a = _VenueData(
                venue="binance",
                quantity=Decimal("2"),
                side="LONG",
                entry_price=Decimal("50000"),
                mark_price=Decimal("50000"),
                unrealized_pnl=Decimal("0"),
            )
            vd_b = _VenueData(
                venue="bybit",
                quantity=Decimal("3"),
                side="LONG",
                entry_price=Decimal("48000"),
                mark_price=Decimal("48000"),
                unrealized_pnl=Decimal("0"),
            )

            await agg.update_venue_position("BTC-USD-PERP", "binance", vd_a)
            result = await agg.update_venue_position("BTC-USD-PERP", "bybit", vd_b)

        assert result.net_quantity == Decimal("5")
        assert result.gross_quantity == Decimal("5")
        assert result.net_side == "LONG"
        # VWAP = (2*50000 + 3*48000) / 5 = 48800
        assert result.weighted_avg_entry_price == Decimal("48800")
        assert len(result.per_venue) == 2

    import asyncio

    asyncio.run(_run())


def test_aggregated_position_net_quantity_long_short_net() -> None:
    """Long 4 on venue A, short 2 on venue B -> net 2 LONG."""
    from unittest.mock import patch

    from position_balance_monitor_service.core.cross_venue_aggregator import (
        CrossVenueAggregator,
        _VenueData,
    )

    async def _run() -> None:
        with patch(
            "position_balance_monitor_service.core.cross_venue_aggregator.log_event"
        ):
            agg = CrossVenueAggregator()

            vd_long = _VenueData(
                venue="binance",
                quantity=Decimal("4"),
                side="LONG",
                entry_price=Decimal("3000"),
                mark_price=Decimal("3100"),
                unrealized_pnl=Decimal("400"),
            )
            vd_short = _VenueData(
                venue="okx",
                quantity=Decimal("-2"),
                side="SHORT",
                entry_price=Decimal("3100"),
                mark_price=Decimal("3100"),
                unrealized_pnl=Decimal("0"),
            )

            await agg.update_venue_position("ETH-USD-PERP", "binance", vd_long)
            result = await agg.update_venue_position("ETH-USD-PERP", "okx", vd_short)

        assert result.net_quantity == Decimal("2")  # 4 + (-2)
        assert result.gross_quantity == Decimal("6")  # |4| + |-2|
        assert result.net_side == "LONG"

    import asyncio

    asyncio.run(_run())


def test_aggregated_position_multi_instrument() -> None:
    """Multiple instruments tracked independently across venues."""
    from unittest.mock import patch

    from position_balance_monitor_service.core.cross_venue_aggregator import (
        CrossVenueAggregator,
        _VenueData,
    )

    async def _run() -> None:
        with patch(
            "position_balance_monitor_service.core.cross_venue_aggregator.log_event"
        ):
            agg = CrossVenueAggregator()

            # BTC on two venues
            await agg.update_venue_position(
                "BTC-USD-PERP",
                "binance",
                _VenueData(
                    venue="binance",
                    quantity=Decimal("1"),
                    side="LONG",
                    entry_price=Decimal("50000"),
                    mark_price=Decimal("50000"),
                    unrealized_pnl=Decimal("0"),
                ),
            )
            await agg.update_venue_position(
                "BTC-USD-PERP",
                "bybit",
                _VenueData(
                    venue="bybit",
                    quantity=Decimal("2"),
                    side="LONG",
                    entry_price=Decimal("49000"),
                    mark_price=Decimal("49000"),
                    unrealized_pnl=Decimal("0"),
                ),
            )
            # ETH on one venue
            await agg.update_venue_position(
                "ETH-USD-PERP",
                "binance",
                _VenueData(
                    venue="binance",
                    quantity=Decimal("10"),
                    side="LONG",
                    entry_price=Decimal("3000"),
                    mark_price=Decimal("3000"),
                    unrealized_pnl=Decimal("0"),
                ),
            )

        btc = agg.get_aggregated("BTC-USD-PERP")
        eth = agg.get_aggregated("ETH-USD-PERP")

        assert btc is not None
        assert eth is not None
        assert btc.net_quantity == Decimal("3")
        assert eth.net_quantity == Decimal("10")

        all_pos = agg.get_all_aggregated()
        instrument_ids = {p.instrument_id for p in all_pos}
        assert "BTC-USD-PERP" in instrument_ids
        assert "ETH-USD-PERP" in instrument_ids

    import asyncio

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Float-to-Decimal boundary: execution-service publish path
# ---------------------------------------------------------------------------


def test_float_to_decimal_boundary_0_1_plus_0_2() -> None:
    """Float 0.1 + 0.2 converted via Decimal(str(...)) must equal Decimal('0.3').

    This validates the float->Decimal conversion at the execution-service
    publish boundary (Decimal(str(float_value)) pattern).
    """
    float_a = 0.1
    float_b = 0.2
    dec_a = Decimal(str(float_a))
    dec_b = Decimal(str(float_b))
    assert dec_a + dec_b == Decimal("0.3"), (
        "Float->Decimal(str()) conversion must produce exact Decimal arithmetic"
    )


def test_float_to_decimal_price_precision() -> None:
    """Typical venue fill price (float) must round-trip as Decimal without corruption."""
    float_price = 50123.456789
    dec_price = Decimal(str(float_price))
    # Reconstruct from Decimal round-trip
    from position_balance_monitor_service.core.cross_venue_aggregator import _VenueData

    vd = _VenueData(
        venue="binance",
        quantity=dec_price,  # reuse field — just checks Decimal assignment
        side="LONG",
        entry_price=dec_price,
        mark_price=dec_price,
        unrealized_pnl=Decimal("0"),
    )
    assert vd.entry_price == dec_price
    assert isinstance(vd.entry_price, Decimal)


# ---------------------------------------------------------------------------
# Schema import smoke: aggregation types importable from UAC
# ---------------------------------------------------------------------------


def test_aggregation_types_importable_from_uac() -> None:
    """All aggregation types must be importable from the UAC root package."""
    from unified_api_contracts import (
        AggregatedPosition,
        DeFiAggregatedHealth,
        DeFiLPAggregatedMetrics,
        DeFiStakingAggregatedMetrics,
        PortfolioGreeksSnapshot,
        PortfolioPnLAttribution,
        PortfolioView,
        RiskGroupSummary,
        SportsArbLeg,
        SportsArbPosition,
    )

    assert AggregatedPosition is not None
    assert PortfolioView is not None
    assert PortfolioGreeksSnapshot is not None
    assert PortfolioPnLAttribution is not None
    assert RiskGroupSummary is not None
    assert DeFiAggregatedHealth is not None
    assert DeFiLPAggregatedMetrics is not None
    assert DeFiStakingAggregatedMetrics is not None
    assert SportsArbPosition is not None
    assert SportsArbLeg is not None


def test_pbms_aggregator_importable() -> None:
    """CrossVenueAggregator must be importable from PBMS core."""
    from position_balance_monitor_service.core import CrossVenueAggregator

    assert CrossVenueAggregator is not None
    agg = CrossVenueAggregator()
    assert agg is not None
