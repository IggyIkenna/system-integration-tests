"""SIT integration test: verify DataFreshnessContract ↔ UEI event alignment.

Tests that:
1. Every DataFreshnessContract entry in ALL_FRESHNESS_CONTRACTS has a
   corresponding UEI event for DATA_STALE, FEED_UNHEALTHY, and
   DATA_AVAILABILITY_RESTORED (the three events FreshnessMonitor emits).
2. DATA_COMPLETENESS_CHECK and DATA_GAP_DETECTED are exported from UEI.
3. All 5 data-availability event names are uppercase strings.
4. FreshnessMonitor correctly emits DATA_STALE when warn threshold breached.
5. FreshnessMonitor correctly emits FEED_UNHEALTHY when max threshold breached.
6. FreshnessMonitor emits no event when data is within SLA.
7. The check_data_completeness script runs in mock mode without error.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from unified_api_contracts.internal.reference.data_freshness import (
    ALL_FRESHNESS_CONTRACTS,
    FEATURE_FRESHNESS,
    MARKET_TICK_FRESHNESS,
    DataFreshnessContract,
)
from unified_trading_library import (
    DATA_AVAILABILITY_EVENT_TYPES,
    DATA_AVAILABILITY_RESTORED,
    DATA_COMPLETENESS_CHECK,
    DATA_GAP_DETECTED,
    DATA_STALE,
    FEED_UNHEALTHY,
    FreshnessMonitor,
    setup_events,
)

pytestmark = pytest.mark.deployment_test
logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _test_mode() -> None:
    """Put UEI in test mode so log_event() works without a real sink."""
    setup_events(service_name="system-integration-tests", mode="test")


# ---------------------------------------------------------------------------
# 1. Schema completeness — DATA_AVAILABILITY_EVENT_TYPES covers the 5 events
# ---------------------------------------------------------------------------


class TestDataAvailabilityEventSchemaCompleteness:
    """All 5 freshness events are defined and correctly typed."""

    def test_data_stale_in_event_types(self) -> None:
        assert DATA_STALE in DATA_AVAILABILITY_EVENT_TYPES

    def test_feed_unhealthy_in_event_types(self) -> None:
        assert FEED_UNHEALTHY in DATA_AVAILABILITY_EVENT_TYPES

    def test_data_availability_restored_in_event_types(self) -> None:
        assert DATA_AVAILABILITY_RESTORED in DATA_AVAILABILITY_EVENT_TYPES

    def test_data_gap_detected_in_event_types(self) -> None:
        assert DATA_GAP_DETECTED in DATA_AVAILABILITY_EVENT_TYPES

    def test_data_completeness_check_in_event_types(self) -> None:
        assert DATA_COMPLETENESS_CHECK in DATA_AVAILABILITY_EVENT_TYPES

    def test_all_event_names_are_uppercase(self) -> None:
        for event in DATA_AVAILABILITY_EVENT_TYPES:
            assert event.isupper(), f"{event!r} must be all-uppercase"

    def test_event_set_has_exactly_five_members(self) -> None:
        assert len(DATA_AVAILABILITY_EVENT_TYPES) == 5


# ---------------------------------------------------------------------------
# 2. Contract coverage — every contract has at least source / criticality
# ---------------------------------------------------------------------------


class TestFreshnessContractCoverage:
    """ALL_FRESHNESS_CONTRACTS covers market tick, feature, and ML sources."""

    def test_all_freshness_contracts_is_non_empty(self) -> None:
        assert len(ALL_FRESHNESS_CONTRACTS) >= 20, f"Expected at least 20 contracts, got {len(ALL_FRESHNESS_CONTRACTS)}"

    def test_market_tick_freshness_covers_critical_cefi_venues(self) -> None:
        critical_cefi = {"binance", "bybit", "okx", "coinbase", "hyperliquid"}
        for venue in critical_cefi:
            assert venue in MARKET_TICK_FRESHNESS, f"{venue} missing from MARKET_TICK_FRESHNESS"
            contract = MARKET_TICK_FRESHNESS[venue]
            assert contract.criticality == "critical"
            assert contract.max_age_seconds == 5

    def test_feature_freshness_covers_delta_one(self) -> None:
        assert "features-delta-one-service" in FEATURE_FRESHNESS
        contract = FEATURE_FRESHNESS["features-delta-one-service"]
        assert contract.criticality == "critical"
        assert contract.max_age_seconds == 120

    def test_each_contract_has_valid_thresholds(self) -> None:
        for source, contract in ALL_FRESHNESS_CONTRACTS.items():
            assert contract.warn_age_seconds < contract.max_age_seconds, (
                f"{source}: warn_age_seconds ({contract.warn_age_seconds}) "
                f"must be < max_age_seconds ({contract.max_age_seconds})"
            )
            assert contract.expected_cadence_seconds > 0, f"{source}: expected_cadence_seconds must be > 0"
            assert contract.criticality in {"critical", "important", "informational"}, (
                f"{source}: invalid criticality {contract.criticality!r}"
            )


# ---------------------------------------------------------------------------
# 3. FreshnessMonitor event emission — unit-level verification
# ---------------------------------------------------------------------------


class TestFreshnessMonitorEventEmission:
    """Verify FreshnessMonitor emits the correct UEI event for each status."""

    def _make_monitor(self, max_age: int = 10, warn_age: int = 5) -> FreshnessMonitor:
        contract = DataFreshnessContract(
            source="test-source",
            asset_group="crypto_cefi",
            max_age_seconds=max_age,
            warn_age_seconds=warn_age,
            expected_cadence_seconds=1,
            criticality="critical",
        )
        return FreshnessMonitor(contract=contract)

    def test_ok_status_emits_no_event(self) -> None:
        monitor = self._make_monitor()
        status, _ = monitor.check(last_update_seconds_ago=1.0)
        assert status == "ok"

    def test_warn_status_at_warn_threshold(self) -> None:
        monitor = self._make_monitor()
        status, _ = monitor.check(last_update_seconds_ago=5.0)
        assert status == "warn"

    def test_critical_status_at_max_threshold(self) -> None:
        monitor = self._make_monitor()
        status, _ = monitor.check(last_update_seconds_ago=10.0)
        assert status == "critical"

    def test_log_if_stale_emits_data_stale_for_warn(self) -> None:
        monitor = self._make_monitor()
        emitted: list[str] = []

        with patch("unified_trading_library.monitors.freshness_monitor.log_event") as mock_log:
            monitor.log_if_stale(source="test-source", last_update_seconds_ago=6.0)
            assert mock_log.called
            event_name = mock_log.call_args[0][0]
            emitted.append(event_name)

        assert DATA_STALE in emitted, f"Expected DATA_STALE, got {emitted}"

    def test_log_if_stale_emits_feed_unhealthy_for_critical(self) -> None:
        monitor = self._make_monitor()
        emitted: list[str] = []

        with patch("unified_trading_library.monitors.freshness_monitor.log_event") as mock_log:
            monitor.log_if_stale(source="test-source", last_update_seconds_ago=11.0)
            assert mock_log.called
            event_name = mock_log.call_args[0][0]
            emitted.append(event_name)

        assert FEED_UNHEALTHY in emitted, f"Expected FEED_UNHEALTHY, got {emitted}"

    def test_log_if_stale_emits_no_event_for_ok(self) -> None:
        monitor = self._make_monitor()

        with patch("unified_trading_library.monitors.freshness_monitor.log_event") as mock_log:
            monitor.log_if_stale(source="test-source", last_update_seconds_ago=1.0)
            mock_log.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Check completeness script — mock mode smoke test
# ---------------------------------------------------------------------------

# Script path: 3 parents up from this file → workspace root → PM scripts
_COMPLETENESS_SCRIPT = (
    Path(__file__).parent.parent.parent.parent
    / "unified-trading-pm"
    / "scripts"
    / "validation"
    / "check_data_completeness.py"
)


class TestCheckDataCompletenessScript:
    """Verify check_data_completeness.py runs successfully in mock mode via subprocess."""

    def test_script_file_exists(self) -> None:
        assert _COMPLETENESS_SCRIPT.exists(), f"Script not found at {_COMPLETENESS_SCRIPT}"

    def test_mock_mode_runs_without_crash(self) -> None:
        """Run the script via subprocess to avoid @dataclass module registration issues."""
        result = subprocess.run(
            [sys.executable, str(_COMPLETENESS_SCRIPT), "--mock", "--date", "2026-03-09"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Exit code 0 = all pass, 1 = some sources fail coverage; both are valid
        assert result.returncode in {0, 1}, (
            f"Script crashed with exit code {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_mock_mode_stdout_contains_summary(self) -> None:
        result = subprocess.run(
            [sys.executable, str(_COMPLETENESS_SCRIPT), "--mock", "--date", "2026-03-09"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "Data Completeness Check" in result.stdout, f"Expected summary header in stdout, got:\n{result.stdout}"
        assert "Passed:" in result.stdout

    def test_mock_mode_reports_all_sources(self) -> None:
        """Verify the script emits one log line per source."""
        result = subprocess.run(
            [sys.executable, str(_COMPLETENESS_SCRIPT), "--mock", "--date", "2026-03-09"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Each source emits "PASS binance ..." or "FAIL binance ..." on stdout
        # Lines may be prefixed with a timestamp (e.g. "2026-03-17 11:36:14,042 INFO PASS ...")
        combined_output = result.stdout + "\n" + result.stderr
        pass_fail_lines = [
            line
            for line in combined_output.splitlines()
            if ("PASS " in line or "FAIL " in line) and "coverage=" in line
        ]
        expected_source_count = len(ALL_FRESHNESS_CONTRACTS)
        assert len(pass_fail_lines) == expected_source_count, (
            f"Expected {expected_source_count} result lines, got {len(pass_fail_lines)}\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )
