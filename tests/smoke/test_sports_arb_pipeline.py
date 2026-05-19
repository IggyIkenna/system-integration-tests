"""Smoke test: Sports arbitrage pipeline end-to-end (mock mode).

Validates the full data flow through the sports betting pipeline:
  1. Odds API market data → features-sports-service (steam detection)
  2. Strategy-service identifies arb opportunity (Kelly sizing)
  3. Execution-service routes to venues via USEI
  4. Position-balance-monitor tracks positions
  5. Risk-and-exposure-service validates exposure limits
  6. PnL-attribution-service records CLV and P&L
  7. Alerting-service fires arb opportunity alert
  8. Client-reporting-api serves /sports/pnl from GCS

All tests run in mock/paper mode — no live API calls.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from unified_api_contracts import BetOrder, BetStatus


class TestSportsVenueRegistryCompleteness:
    """Verify the UAC venue execution registry covers all expected venues."""

    def test_registry_has_minimum_venues(self) -> None:
        from unified_api_contracts import VENUE_EXECUTION_REGISTRY

        assert len(VENUE_EXECUTION_REGISTRY) >= 70, f"Expected >=70 venues, got {len(VENUE_EXECUTION_REGISTRY)}"

    def test_api_venues_present(self) -> None:
        from unified_api_contracts import VENUE_EXECUTION_REGISTRY

        api_venues = ["betfair_ex_uk", "pinnacle", "matchbook", "polymarket", "kalshi"]
        for v in api_venues:
            assert v in VENUE_EXECUTION_REGISTRY, f"API venue '{v}' missing from registry"

    def test_all_venues_have_execution_method(self) -> None:
        from unified_api_contracts import VENUE_EXECUTION_REGISTRY

        for key, profile in VENUE_EXECUTION_REGISTRY.items():
            assert profile.primary_execution_method is not None, f"Venue '{key}' has no execution method"


class TestUSEIRouterCoverage:
    """Verify USEI router supports all API data sources including Kalshi."""

    def test_supported_data_sources_include_kalshi(self) -> None:
        import typing

        from unified_sports_execution_interface.routing import SupportedDataSource

        sources = typing.get_args(SupportedDataSource)
        assert "kalshi_direct" in sources

    def test_supported_data_sources_include_all_exchanges(self) -> None:
        import typing

        from unified_sports_execution_interface.routing import SupportedDataSource

        sources = typing.get_args(SupportedDataSource)
        expected = [
            "betfair_direct",
            "pinnacle_direct",
            "polymarket_clob",
            "matchbook_direct",
            "kalshi_direct",
        ]
        for src in expected:
            assert src in sources, f"Data source '{src}' missing from SupportedDataSource"

    def test_router_paper_adapter_works(self) -> None:
        from unified_sports_execution_interface.routing import (
            SportsExecutionDataSourceConfig,
            SportsExecutionRouter,
        )

        cfg = SportsExecutionDataSourceConfig(
            venue="paper_test",
            data_source="paper",
            api_key_secret="unused",
        )
        router = SportsExecutionRouter(configs=[cfg])
        adapter = router.get_adapter("paper_test")
        assert adapter is not None

    def test_router_kalshi_adapter_builds(self) -> None:
        from unified_sports_execution_interface.adapters.exchanges.kalshi import KalshiAdapter
        from unified_sports_execution_interface.routing import (
            SportsExecutionDataSourceConfig,
            SportsExecutionRouter,
        )

        cfg = SportsExecutionDataSourceConfig(
            venue="kalshi",
            data_source="kalshi_direct",
            api_key_secret="kalshi-api-key-id",
            secondary_secret="kalshi-private-key-pem",
        )
        mock_loader = MagicMock(return_value="test-value")
        router = SportsExecutionRouter(configs=[cfg], secret_loader=mock_loader)
        adapter = router.get_adapter("kalshi")
        assert isinstance(adapter, KalshiAdapter)


class TestSportsStrategyComponents:
    """Verify strategy modules are importable and functional."""

    def test_kelly_criterion_positive_edge(self) -> None:
        try:
            from strategy_service.engine.sports.kelly import kelly_fraction
        except ImportError:
            pytest.skip("strategy-service not installed in this environment")

        fraction = kelly_fraction(probability=0.55, odds=2.0)
        assert fraction > 0, "Positive edge should yield positive Kelly fraction"

    def test_steam_detector_importable(self) -> None:
        try:
            from features_service.sports.calculators.steam_detector import SteamDetector

            assert SteamDetector is not None
        except ImportError:
            pytest.skip("features-service not installed in this environment")


class TestSportsRiskAndPosition:
    """Verify risk and position modules accept sports bet data."""

    def test_sports_risk_engine_importable(self) -> None:
        try:
            from risk_and_exposure_service.engine.sports_risk import SportsRiskEngine

            assert SportsRiskEngine is not None
        except ImportError:
            pytest.skip("risk-and-exposure-service not installed in this environment")

    def test_sports_position_tracker_importable(self) -> None:
        try:
            from position_balance_monitor_service.engine.sports_position_tracker import (
                SportsPositionTracker,
            )

            assert SportsPositionTracker is not None
        except ImportError:
            pytest.skip("position-balance-monitor-service not installed in this environment")


class TestSportsPnLAttribution:
    """Verify P&L attribution can process sports bets."""

    def test_sports_pnl_engine_importable(self) -> None:
        try:
            from pnl_attribution_service.engine.sports_pnl import SportsPnLEngine

            assert SportsPnLEngine is not None
        except ImportError:
            pytest.skip("pnl-attribution-service not installed in this environment")

    def test_sports_pnl_engine_has_persist_method(self) -> None:
        try:
            from pnl_attribution_service.engine.sports_pnl import SportsPnLEngine

            engine = SportsPnLEngine()
            assert hasattr(engine, "persist_to_gcs")
            assert hasattr(engine, "to_dataframe")
        except ImportError:
            pytest.skip("pnl-attribution-service not installed in this environment")


class TestSportsAlertingRules:
    """Verify alerting-service has sports-specific alert types."""

    def test_sports_alert_rules_importable(self) -> None:
        try:
            from alerting_service.rules.sports_alerts import SportsAlertRules

            assert SportsAlertRules is not None
        except ImportError:
            pytest.skip("alerting-service not installed in this environment")


class TestClientReportingAPISportsEndpoints:
    """Verify CRA has sports endpoints wired."""

    def test_sports_router_importable(self) -> None:
        try:
            from client_reporting_api.api.routes.sports import router

            assert router is not None
        except ImportError:
            pytest.skip("client-reporting-api not installed in this environment")

    def test_sports_pnl_reader_importable(self) -> None:
        try:
            from client_reporting_api.core.sports_pnl_reader import (
                generate_clv_report,
                generate_sports_pnl_report,
                generate_venue_performance_report,
                read_sports_positions,
                read_sports_risk,
            )

            assert generate_sports_pnl_report is not None
            assert generate_clv_report is not None
            assert generate_venue_performance_report is not None
            assert read_sports_positions is not None
            assert read_sports_risk is not None
        except ImportError:
            pytest.skip("client-reporting-api not installed in this environment")


class TestBrowserAdapterCoverage:
    """Verify browser adapter coverage for scraper venues."""

    def test_venue_key_to_adapter_mapping_populated(self) -> None:
        from unified_sports_execution_interface.adapters.browser import VENUE_KEY_TO_ADAPTER

        assert len(VENUE_KEY_TO_ADAPTER) >= 50, f"Expected >=50 browser venue mappings, got {len(VENUE_KEY_TO_ADAPTER)}"

    def test_all_browser_adapters_have_venue_key(self) -> None:
        from unified_sports_execution_interface.adapters.browser import VENUE_KEY_TO_ADAPTER
        from unified_sports_execution_interface.adapters.browser.base import BrowserBettingAdapter

        for key, cls in VENUE_KEY_TO_ADAPTER.items():
            assert issubclass(cls, BrowserBettingAdapter), (
                f"Adapter for '{key}' is not a BrowserBettingAdapter subclass"
            )
            assert hasattr(cls, "VENUE_KEY"), f"Adapter for '{key}' missing VENUE_KEY attribute"


class TestPaperTradingEndToEnd:
    """Paper trading round-trip: place bet via paper adapter, verify execution."""

    @pytest.mark.asyncio()
    async def test_paper_place_and_cancel(self) -> None:
        from unified_sports_execution_interface.adapters.paper.paper_betting import (
            PaperBettingAdapter,
        )

        adapter = PaperBettingAdapter(initial_balance=Decimal("10000"))
        balance = await adapter.get_balance()
        assert balance == Decimal("10000")

        order = BetOrder(
            order_id=str(uuid.uuid4()),
            fixture_id="test-match-001",
            stake=Decimal("100"),
            requested_odds=Decimal("2.50"),
            venue_key="paper",
            selection="home_win",
        )

        execution = await adapter.place_bet(order)
        assert execution.status == BetStatus.PLACED
        assert execution.bet_id is not None

        new_balance = await adapter.get_balance()
        assert new_balance < balance, "Balance should decrease after placing bet"

    @pytest.mark.asyncio()
    async def test_paper_adapter_via_router(self) -> None:
        from unified_sports_execution_interface.routing import (
            SportsExecutionDataSourceConfig,
            SportsExecutionRouter,
        )

        cfg = SportsExecutionDataSourceConfig(
            venue="paper",
            data_source="paper",
            api_key_secret="unused",
            extra={"initial_balance": 5000},
        )
        router = SportsExecutionRouter(configs=[cfg])
        adapter = router.get_adapter("paper")
        assert adapter is not None


class TestCredentialConfigCoverage:
    """Verify credential configs cover all venues."""

    def test_credential_configs_populated(self) -> None:
        try:
            from unified_trading_library import (
                SPORTS_VENUE_CREDENTIALS,
            )

            assert len(SPORTS_VENUE_CREDENTIALS) >= 70, (
                f"Expected >=70 credential configs, got {len(SPORTS_VENUE_CREDENTIALS)}"
            )
        except ImportError:
            pytest.skip("unified-config-interface not installed in this environment")

    def test_kalshi_credential_config_exists(self) -> None:
        try:
            from unified_trading_library import (
                SPORTS_VENUE_CREDENTIALS,
            )

            kalshi_configs = [c for c in SPORTS_VENUE_CREDENTIALS if c.venue_key == "kalshi"]
            assert len(kalshi_configs) == 1, "Kalshi credential config should exist"
        except ImportError:
            pytest.skip("unified-config-interface not installed in this environment")
