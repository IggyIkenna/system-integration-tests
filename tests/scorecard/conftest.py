"""Scorecard pytest integration — tracks flow-validation scores across runs.

Adds a ``--scorecard-path`` CLI option (default: ``.scorecard/scores.json``).
After the test session, records pass/fail scores per journey and prints a summary.
Blocks regression if score drops > 5 points (configurable via ``--scorecard-max-drop``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.scorecard.scorecard_tracker import RegressionError, ScorecardTracker

# Journey-to-marker mapping for score extraction.
_JOURNEY_MARKERS: dict[str, str] = {
    "deployment_flow": "critical",
    "kill_switch_flow": "critical",
    "alert_routing_flow": "high",
    "log_query_flow": "medium",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register scorecard CLI options."""
    group = parser.getgroup("scorecard", "Flow validation scorecard tracking")
    group.addoption(
        "--scorecard-path",
        default=os.environ.get("SCORECARD_PATH", ".scorecard/scores.json"),
        help="Path to the scorecard JSON file (default: .scorecard/scores.json)",
    )
    group.addoption(
        "--scorecard-max-drop",
        type=float,
        default=5.0,
        help="Maximum allowed score drop in points before failing (default: 5.0)",
    )
    group.addoption(
        "--scorecard-enabled",
        action="store_true",
        default=False,
        help="Enable scorecard tracking and regression checking",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register scorecard marker."""
    config.addinivalue_line("markers", "scorecard: Test contributes to flow-validation scorecard")


class ScorecardPlugin:
    """Pytest plugin that collects real_flow test results and writes scorecard."""

    def __init__(self, scores_path: Path, max_drop: float) -> None:
        self._scores_path = scores_path
        self._max_drop = max_drop
        self._journey_results: dict[str, list[bool]] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Collect pass/fail for real_flow tests."""
        if report.when != "call":
            return

        # Extract journey name from the test module
        module_name = report.nodeid.split("/")[-1].split("::")[0]
        # e.g. test_deployment_flow.py -> deployment_flow
        journey_key = module_name.replace("test_", "").replace(".py", "")

        if journey_key not in _JOURNEY_MARKERS:
            return

        if journey_key not in self._journey_results:
            self._journey_results[journey_key] = []
        self._journey_results[journey_key].append(report.passed)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        """Record scores and check for regression after all tests complete."""
        if not self._journey_results:
            return

        # Compute score per journey (percentage of tests that passed, 0-100)
        scores: dict[str, float] = {}
        for journey, results in self._journey_results.items():
            if results:
                passed = sum(1 for r in results if r)
                scores[journey] = (passed / len(results)) * 100
            else:
                scores[journey] = 0.0

        tracker = ScorecardTracker(scores_path=self._scores_path)
        tracker.record_run(scores)

        # Store summary for terminal output
        summary = tracker.format_summary()
        session.config.stash.setdefault(_SCORECARD_SUMMARY_KEY, summary)

    def pytest_terminal_summary(
        self,
        terminalreporter: object,
        exitstatus: int,
        config: pytest.Config,
    ) -> None:
        """Print scorecard summary in terminal output."""
        summary = config.stash.get(_SCORECARD_SUMMARY_KEY, "")
        if not summary:
            return

        _write_section(terminalreporter, summary)

        # Check regression after printing summary
        tracker = ScorecardTracker(scores_path=self._scores_path)
        try:
            tracker.check_regression(max_drop=self._max_drop)
        except RegressionError as exc:
            _write_line(terminalreporter, f"REGRESSION BLOCKED: {exc}")
            pytest.fail(str(exc))  # pyright: ignore[reportUnknownMemberType]


def _write_section(reporter: object, text: str) -> None:
    """Write a bordered section to the terminal reporter, if available."""
    tw: object = getattr(reporter, "_tw", None)  # pyright: ignore[reportAny]
    if tw is None:
        return
    sep = getattr(tw, "sep", None)
    line = getattr(tw, "line", None)
    if callable(sep) and callable(line):
        sep("=", "FLOW VALIDATION SCORECARD")  # pyright: ignore[reportAny]
        line(text)  # pyright: ignore[reportAny]
        sep("=")  # pyright: ignore[reportAny]


def _write_line(reporter: object, text: str) -> None:
    """Write a single line to the terminal reporter, if available."""
    tw: object = getattr(reporter, "_tw", None)  # pyright: ignore[reportAny]
    if tw is None:
        return
    line = getattr(tw, "line", None)
    if callable(line):
        line(text, red=True)  # pyright: ignore[reportAny]


# Stash key for passing summary between hooks
_SCORECARD_SUMMARY_KEY = pytest.StashKey[str]()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Register the scorecard plugin if --scorecard-enabled is set."""
    enabled: bool = bool(
        session.config.getoption("--scorecard-enabled", default=False)  # pyright: ignore[reportAny]
    )
    if not enabled:
        return

    scores_path_str: str = str(
        session.config.getoption("--scorecard-path", default=".scorecard/scores.json")  # pyright: ignore[reportAny]
    )
    scores_path = Path(scores_path_str)
    max_drop: float = float(
        str(session.config.getoption("--scorecard-max-drop", default=5.0))  # pyright: ignore[reportAny]
    )

    plugin = ScorecardPlugin(scores_path=scores_path, max_drop=max_drop)
    session.config.pluginmanager.register(plugin, name="scorecard_tracker")
