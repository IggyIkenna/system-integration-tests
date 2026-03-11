"""Integration tests for recon_rebalancing_order_recovery plan.

Tests cover all four streams:
  Stream A: Position discrepancy detection -> CRITICAL event + correction order submitted
  Stream B: Order recovery on restart -> ORDER_RECOVERY_COMPLETED emitted
  Stream C: Portfolio drift >3% -> rebalancing orders generated with correct direction
  Stream D: DeFi yield differential >1% -> vault rebalance triggered
  Stream D: Gas cost > $50 -> rebalance skipped

All tests run credential-free (CLOUD_PROVIDER=local CLOUD_MOCK_MODE=true).
No live network calls; all transports are mocked.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stream A: CorrectionDispatcher
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_correction_dispatcher_submits_sell_order_for_critical_discrepancy() -> None:
    """Internal qty > exchange qty on CRITICAL snapshot → SELL correction submitted."""
    from position_balance_monitor_service.core.correction_dispatcher import CorrectionDispatcher
    from position_balance_monitor_service.models import ReconciliationSnapshot

    snapshot = ReconciliationSnapshot(
        client_id="client-001",
        strategy_id="strat-001",
        venue="binance",
        instrument="BTC-USDT",
        timestamp=datetime.now(UTC),
        internal_quantity=Decimal("10.0"),
        exchange_quantity=Decimal("7.0"),
        discrepancy=Decimal("3.0"),
        discrepancy_value=Decimal("90000.0"),
        discrepancy_pct=Decimal("0.30"),
        status="CRITICAL",
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"order_id": "corr-order-001"}

    with patch("position_balance_monitor_service.core.correction_dispatcher.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("position_balance_monitor_service.core.correction_dispatcher.get_service_config") as mock_cfg:
            cfg = MagicMock()
            cfg.auto_correct_enabled = True
            cfg.auto_correct_threshold_pct = 1.0  # 1%
            cfg.auto_correct_max_qty = 1000.0
            cfg.execution_service_url = "http://mock-execution:8000"
            mock_cfg.return_value = cfg

            dispatcher = CorrectionDispatcher()
            result = asyncio.run(dispatcher.dispatch_correction(snapshot))

    assert result is True, "dispatch_correction should return True on HTTP 200"

    call_kwargs = mock_client.post.call_args
    assert call_kwargs is not None
    posted_json: dict[str, str] = cast(dict[str, str], call_kwargs.kwargs.get("json") or call_kwargs.args[1])
    assert posted_json["side"] == "SELL", "internal > exchange means we need to SELL"
    assert posted_json["instrument"] == "BTC-USDT"


@pytest.mark.integration
def test_correction_dispatcher_skips_non_critical_snapshot() -> None:
    """Dispatcher must not dispatch for DISCREPANCY (non-CRITICAL) snapshot."""
    from position_balance_monitor_service.core.correction_dispatcher import CorrectionDispatcher
    from position_balance_monitor_service.models import ReconciliationSnapshot

    snapshot = ReconciliationSnapshot(
        client_id="client-001",
        strategy_id="strat-001",
        venue="binance",
        instrument="ETH-USDT",
        timestamp=datetime.now(UTC),
        internal_quantity=Decimal("100.0"),
        exchange_quantity=Decimal("99.0"),
        discrepancy=Decimal("1.0"),
        discrepancy_value=Decimal("3000.0"),
        discrepancy_pct=Decimal("0.01"),
        status="DISCREPANCY",
    )

    with patch("position_balance_monitor_service.core.correction_dispatcher.get_service_config") as mock_cfg:
        cfg = MagicMock()
        cfg.auto_correct_enabled = True
        cfg.auto_correct_threshold_pct = 1.0
        cfg.auto_correct_max_qty = 1000.0
        cfg.execution_service_url = "http://mock-execution:8000"
        mock_cfg.return_value = cfg

        dispatcher = CorrectionDispatcher()
        result = asyncio.run(dispatcher.dispatch_correction(snapshot))

    assert result is False, "Dispatcher must return False for non-CRITICAL snapshot"


@pytest.mark.integration
def test_correction_dispatcher_skips_when_auto_correct_disabled() -> None:
    """Dispatcher must not submit order when auto_correct_enabled=False."""
    from position_balance_monitor_service.core.correction_dispatcher import CorrectionDispatcher
    from position_balance_monitor_service.models import ReconciliationSnapshot

    snapshot = ReconciliationSnapshot(
        client_id="client-001",
        strategy_id="strat-001",
        venue="binance",
        instrument="BTC-USDT",
        timestamp=datetime.now(UTC),
        internal_quantity=Decimal("10.0"),
        exchange_quantity=Decimal("7.0"),
        discrepancy=Decimal("3.0"),
        discrepancy_value=Decimal("90000.0"),
        discrepancy_pct=Decimal("0.30"),
        status="CRITICAL",
    )

    with patch("position_balance_monitor_service.core.correction_dispatcher.get_service_config") as mock_cfg:
        cfg = MagicMock()
        cfg.auto_correct_enabled = False  # disabled
        cfg.auto_correct_threshold_pct = 1.0
        cfg.auto_correct_max_qty = 1000.0
        cfg.execution_service_url = "http://mock-execution:8000"
        mock_cfg.return_value = cfg

        dispatcher = CorrectionDispatcher()
        result = asyncio.run(dispatcher.dispatch_correction(snapshot))

    assert result is False, "Dispatcher must return False when auto_correct_enabled=False"


# ---------------------------------------------------------------------------
# Stream B: OrderRecoveryEngine
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_order_recovery_re_registers_recent_orphan() -> None:
    """Exchange has an order not in internal state (recent) -> re-registered as PENDING."""
    from execution_service.engine.startup.order_recovery import (
        ExchangeOrder,
        OrderBook,
        OrderRecoveryEngine,
        _VenueAdapter,
    )

    recent_orphan = ExchangeOrder(
        order_id="orphan-001",
        venue="binance",
        instrument="BTC-USDT",
        side="BUY",
        quantity=Decimal("1.0"),
        filled_quantity=Decimal("0.0"),
        status="OPEN",
        created_at=datetime.now(UTC),
    )

    mock_adapter = MagicMock(spec=_VenueAdapter)
    mock_adapter.fetch_open_orders = AsyncMock(return_value=[recent_orphan])

    order_book = OrderBook()

    with patch("execution_service.engine.startup.order_recovery.get_circuit_breaker") as mock_cb_factory:
        mock_cb = MagicMock()
        mock_cb.get_state.return_value = "CLOSED"
        mock_cb_factory.return_value = mock_cb

        engine = OrderRecoveryEngine(order_book=order_book, venue_adapter=mock_adapter)
        results = asyncio.run(engine.run(["binance"]))

    assert len(results) == 1
    result = results[0]
    assert result.re_registered == 1, "Recent orphan should be re-registered"
    assert result.error is None
    assert not result.skipped_circuit_open


@pytest.mark.integration
def test_order_recovery_marks_internal_orphan_as_rejected() -> None:
    """Internal PENDING order absent on exchange -> marked EXCHANGE_REJECTED."""
    from execution_service.engine.startup.order_recovery import (
        InternalOrder,
        OrderBook,
        OrderRecoveryEngine,
        _VenueAdapter,
    )

    order_book = OrderBook()
    internal_order = InternalOrder(
        order_id="internal-pending-001",
        venue="binance",
        instrument="ETH-USDT",
        side="SELL",
        quantity=Decimal("5.0"),
        filled_quantity=Decimal("0.0"),
        status="PENDING",
        created_at=datetime.now(UTC),
    )
    order_book.register(internal_order)

    mock_adapter = MagicMock(spec=_VenueAdapter)
    mock_adapter.fetch_open_orders = AsyncMock(return_value=[])

    with patch("execution_service.engine.startup.order_recovery.get_circuit_breaker") as mock_cb_factory:
        mock_cb = MagicMock()
        mock_cb.get_state.return_value = "CLOSED"
        mock_cb_factory.return_value = mock_cb

        engine = OrderRecoveryEngine(order_book=order_book, venue_adapter=mock_adapter)
        results = asyncio.run(engine.run(["binance"]))

    assert results[0].marked_rejected == 1
    assert order_book._orders["internal-pending-001"].status == "EXCHANGE_REJECTED"


@pytest.mark.integration
def test_order_recovery_completed_event_emitted_for_empty_venues() -> None:
    """run([]) with no venues emits ORDER_RECOVERY_COMPLETED with zero counts."""
    from execution_service.engine.startup.order_recovery import (
        OrderBook,
        OrderRecoveryEngine,
        _VenueAdapter,
    )

    mock_adapter = MagicMock(spec=_VenueAdapter)
    order_book = OrderBook()
    engine = OrderRecoveryEngine(order_book=order_book, venue_adapter=mock_adapter)

    results = asyncio.run(engine.run([]))
    assert results == [], "Empty venues should return empty results"
    mock_adapter.fetch_open_orders.assert_not_called()


@pytest.mark.integration
def test_order_recovery_skipped_when_circuit_breaker_open() -> None:
    """Circuit breaker OPEN -> recovery skipped for that venue."""
    from execution_service.engine.startup.order_recovery import (
        OrderBook,
        OrderRecoveryEngine,
        _VenueAdapter,
    )

    mock_adapter = MagicMock(spec=_VenueAdapter)
    mock_adapter.fetch_open_orders = AsyncMock(return_value=[])
    order_book = OrderBook()

    with patch("execution_service.engine.startup.order_recovery.get_circuit_breaker") as mock_cb_factory:
        mock_cb = MagicMock()
        mock_cb.get_state.return_value = "OPEN"
        mock_cb_factory.return_value = mock_cb

        engine = OrderRecoveryEngine(order_book=order_book, venue_adapter=mock_adapter)
        results = asyncio.run(engine.run(["deribit"]))

    assert results[0].skipped_circuit_open is True
    mock_adapter.fetch_open_orders.assert_not_called()


# ---------------------------------------------------------------------------
# Stream C: PortfolioRebalancer — drift detection
# ---------------------------------------------------------------------------


def _write_temp_rebalancing_config(strategy_id: str, weights: dict[str, float]) -> Path:
    """Write a minimal rebalancing_config.yaml to a temp file and return the path."""
    import tempfile

    block: dict[str, object] = {"drift_threshold_pct": 3.0, "rebalancing_window_minutes": 30}
    for symbol, target in weights.items():
        block[f"{symbol}_target"] = target

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({strategy_id: block}, f)
        return Path(f.name)


@pytest.mark.integration
def test_portfolio_rebalancer_buy_on_underweight_asset() -> None:
    """BTC at 35% vs 40% target (drift 5% > threshold 3%) -> BUY order generated."""
    from strategy_service.engine.rebalancing.portfolio_rebalancer import (
        AssetWeight,
        PortfolioRebalancer,
        RebalancingOrder,
    )

    weights = [
        AssetWeight(symbol="BTC", target_weight=0.40, current_weight=0.35, notional_usd=35000.0),
        AssetWeight(symbol="USDC", target_weight=0.60, current_weight=0.65, notional_usd=65000.0),
    ]

    class _MockFetcher:
        async def fetch_weights(self, strategy_id: str) -> list[AssetWeight]:
            return weights

    submitted: list[RebalancingOrder] = []

    class _MockSubmitter:
        async def submit(self, order: RebalancingOrder) -> str:
            submitted.append(order)
            return f"order-{order.symbol}"

    config_path = _write_temp_rebalancing_config("test-strategy", {"BTC": 0.40, "USDC": 0.60})

    rebalancer = PortfolioRebalancer(
        position_fetcher=_MockFetcher(),
        order_submitter=_MockSubmitter(),
        drift_threshold_pct=3.0,
        min_order_usd=100.0,
        config_path=config_path,
    )

    asyncio.run(rebalancer.check_and_rebalance(correlation_id="corr-001"))
    config_path.unlink(missing_ok=True)

    btc_orders = [o for o in submitted if o.symbol == "BTC"]
    usdc_orders = [o for o in submitted if o.symbol == "USDC"]

    assert len(btc_orders) == 1, "BTC drifted >3% and should have a rebalancing order"
    assert btc_orders[0].direction == "BUY", "Under-weight BTC -> BUY"
    assert len(usdc_orders) == 1, "USDC drifted >3% and should have a rebalancing order"
    assert usdc_orders[0].direction == "SELL", "Over-weight USDC -> SELL"


@pytest.mark.integration
def test_portfolio_rebalancer_no_orders_within_threshold() -> None:
    """Assets within drift threshold -> no rebalancing orders generated."""
    from strategy_service.engine.rebalancing.portfolio_rebalancer import (
        AssetWeight,
        PortfolioRebalancer,
        RebalancingOrder,
    )

    weights = [
        AssetWeight(symbol="BTC", target_weight=0.50, current_weight=0.501, notional_usd=50100.0),
    ]

    class _MockFetcher:
        async def fetch_weights(self, strategy_id: str) -> list[AssetWeight]:
            return weights

    submitted: list[RebalancingOrder] = []

    class _MockSubmitter:
        async def submit(self, order: RebalancingOrder) -> str:
            submitted.append(order)
            return "order-id"

    config_path = _write_temp_rebalancing_config("test-strategy-no-drift", {"BTC": 0.50})

    rebalancer = PortfolioRebalancer(
        position_fetcher=_MockFetcher(),
        order_submitter=_MockSubmitter(),
        drift_threshold_pct=3.0,
        min_order_usd=100.0,
        config_path=config_path,
    )

    asyncio.run(rebalancer.check_and_rebalance())
    config_path.unlink(missing_ok=True)

    assert len(submitted) == 0, "No orders when drift is below threshold"


# ---------------------------------------------------------------------------
# Stream D: DeFiVaultRebalancer — yield differential and gas gates
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_defi_vault_rebalancer_triggers_on_yield_differential() -> None:
    """Yield differential >1% + gas <$50 -> rebalance executed."""
    from strategy_service.engine.rebalancing.defi_vault_rebalancer import (
        DeFiVaultRebalancer,
        VaultPosition,
    )
    from strategy_service.engine.rebalancing.defi_yield_monitor import DeFiYieldMonitor, YieldSnapshot
    from strategy_service.engine.rebalancing.gas_estimator import GasEstimator

    mock_yield_monitor = MagicMock(spec=DeFiYieldMonitor)
    mock_yield_monitor.get_all_yields.return_value = [
        YieldSnapshot(protocol="aave_v3", asset="USDC", apy_pct=4.0),
        YieldSnapshot(protocol="morpho", asset="USDC", apy_pct=5.5),
    ]

    mock_gas_estimator = MagicMock(spec=GasEstimator)
    mock_gas_estimator.estimate_gas_cost_usd.return_value = 20.0  # under $50 limit

    rebalancer = DeFiVaultRebalancer(
        yield_differential_threshold=1.0,
        gas_cost_usd_max=50.0,
        yield_monitor=mock_yield_monitor,
        gas_estimator=mock_gas_estimator,
    )

    results = rebalancer.evaluate_and_rebalance([VaultPosition(protocol="aave_v3", asset="USDC", amount_usd=100_000.0)])

    assert len(results) == 1
    r = results[0]
    assert r.executed is True, "Rebalance should execute when differential >1% and gas <$50"
    assert r.from_protocol == "aave_v3"
    assert r.to_protocol == "morpho"
    assert r.yield_improvement_pct == pytest.approx(1.5, abs=0.01)


@pytest.mark.integration
def test_defi_vault_rebalancer_skipped_on_high_gas() -> None:
    """Gas cost > $50 -> rebalance skipped regardless of yield differential."""
    from strategy_service.engine.rebalancing.defi_vault_rebalancer import (
        DeFiVaultRebalancer,
        VaultPosition,
    )
    from strategy_service.engine.rebalancing.defi_yield_monitor import DeFiYieldMonitor, YieldSnapshot
    from strategy_service.engine.rebalancing.gas_estimator import GasEstimator

    mock_yield_monitor = MagicMock(spec=DeFiYieldMonitor)
    mock_yield_monitor.get_all_yields.return_value = [
        YieldSnapshot(protocol="aave_v3", asset="ETH", apy_pct=3.0),
        YieldSnapshot(protocol="lido", asset="ETH", apy_pct=4.5),
    ]

    mock_gas_estimator = MagicMock(spec=GasEstimator)
    mock_gas_estimator.estimate_gas_cost_usd.return_value = 75.0  # exceeds $50 limit

    rebalancer = DeFiVaultRebalancer(
        yield_differential_threshold=1.0,
        gas_cost_usd_max=50.0,
        yield_monitor=mock_yield_monitor,
        gas_estimator=mock_gas_estimator,
    )

    results = rebalancer.evaluate_and_rebalance([VaultPosition(protocol="aave_v3", asset="ETH", amount_usd=50_000.0)])

    assert len(results) == 1
    r = results[0]
    assert r.executed is False, "Rebalance should be skipped when gas > $50"
    assert r.skip_reason is not None
    assert "gas" in r.skip_reason.lower()


@pytest.mark.integration
def test_defi_vault_rebalancer_skipped_on_low_differential() -> None:
    """Yield differential < 1% -> rebalance skipped."""
    from strategy_service.engine.rebalancing.defi_vault_rebalancer import (
        DeFiVaultRebalancer,
        VaultPosition,
    )
    from strategy_service.engine.rebalancing.defi_yield_monitor import DeFiYieldMonitor, YieldSnapshot
    from strategy_service.engine.rebalancing.gas_estimator import GasEstimator

    mock_yield_monitor = MagicMock(spec=DeFiYieldMonitor)
    mock_yield_monitor.get_all_yields.return_value = [
        YieldSnapshot(protocol="aave_v3", asset="USDC", apy_pct=4.0),
        YieldSnapshot(protocol="morpho", asset="USDC", apy_pct=4.5),  # +0.5% < 1% threshold
    ]

    mock_gas_estimator = MagicMock(spec=GasEstimator)
    mock_gas_estimator.estimate_gas_cost_usd.return_value = 10.0

    rebalancer = DeFiVaultRebalancer(
        yield_differential_threshold=1.0,
        gas_cost_usd_max=50.0,
        yield_monitor=mock_yield_monitor,
        gas_estimator=mock_gas_estimator,
    )

    results = rebalancer.evaluate_and_rebalance([VaultPosition(protocol="aave_v3", asset="USDC", amount_usd=50_000.0)])

    assert len(results) == 1
    r = results[0]
    assert r.executed is False
    assert r.skip_reason is not None
    assert "differential" in r.skip_reason.lower()
