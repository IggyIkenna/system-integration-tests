"""Unit tests for the scorecard trend tracker.

Validates:
    - Score persistence to/from JSON file
    - Delta computation between runs
    - Regression blocking when score drops > threshold
    - Summary formatting
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.scorecard.scorecard_tracker import RegressionError, ScorecardRun, ScorecardTracker

pytestmark = [pytest.mark.code_test, pytest.mark.unit]  # pyright: ignore[reportUnknownMemberType]


@pytest.fixture()
def scores_path(tmp_path: Path) -> Path:
    """Temporary path for scorecard JSON file."""
    return tmp_path / "scorecard" / "scores.json"


@pytest.fixture()
def tracker(scores_path: Path) -> ScorecardTracker:
    """Fresh tracker with a temporary scores file."""
    return ScorecardTracker(scores_path=scores_path)


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


def test_record_creates_json_file(tracker: ScorecardTracker, scores_path: Path) -> None:
    """Recording a run should create the JSON file on disk."""
    assert not scores_path.exists()
    tracker.record_run({"deployment_flow": 100.0, "kill_switch": 85.0})
    assert scores_path.exists()


def test_record_and_reload(scores_path: Path) -> None:
    """Scores should survive a tracker reload from disk."""
    tracker1 = ScorecardTracker(scores_path=scores_path)
    tracker1.record_run({"deployment_flow": 95.0, "alert_routing": 80.0})

    # Create a fresh tracker from the same file
    tracker2 = ScorecardTracker(scores_path=scores_path)
    assert tracker2.latest is not None
    assert tracker2.latest.scores == {"deployment_flow": 95.0, "alert_routing": 80.0}
    expected_total = 87.5
    assert tracker2.latest.total_score == expected_total


def test_multiple_runs_persisted(scores_path: Path) -> None:
    """Multiple runs should all be persisted and recoverable."""
    tracker = ScorecardTracker(scores_path=scores_path)
    tracker.record_run({"a": 50.0})
    tracker.record_run({"a": 60.0})
    tracker.record_run({"a": 70.0})

    reloaded = ScorecardTracker(scores_path=scores_path)
    assert len(reloaded.runs) == 3
    assert reloaded.runs[0].total_score == 50.0
    assert reloaded.runs[2].total_score == 70.0


def test_max_history_pruning(scores_path: Path) -> None:
    """Runs beyond max_history should be pruned (oldest removed)."""
    tracker = ScorecardTracker(scores_path=scores_path, max_history=3)
    for i in range(5):
        tracker.record_run({"score": float(i * 10)})

    assert len(tracker.runs) == 3
    # Only the last 3 runs should remain
    assert tracker.runs[0].total_score == 20.0
    assert tracker.runs[2].total_score == 40.0


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------


def test_first_run_has_no_delta(tracker: ScorecardTracker) -> None:
    """First run should have delta_from_previous = None."""
    run = tracker.record_run({"deployment_flow": 100.0})
    assert run.delta_from_previous is None


def test_delta_positive(tracker: ScorecardTracker) -> None:
    """Score increase should produce positive delta."""
    tracker.record_run({"a": 50.0})
    run2 = tracker.record_run({"a": 75.0})
    assert run2.delta_from_previous is not None
    assert run2.delta_from_previous == 25.0


def test_delta_negative(tracker: ScorecardTracker) -> None:
    """Score decrease should produce negative delta."""
    tracker.record_run({"a": 80.0})
    run2 = tracker.record_run({"a": 60.0})
    assert run2.delta_from_previous is not None
    assert run2.delta_from_previous == -20.0


def test_delta_zero(tracker: ScorecardTracker) -> None:
    """Same score should produce zero delta."""
    tracker.record_run({"a": 50.0, "b": 50.0})
    run2 = tracker.record_run({"a": 50.0, "b": 50.0})
    assert run2.delta_from_previous is not None
    assert run2.delta_from_previous == 0.0


# ---------------------------------------------------------------------------
# Regression blocking
# ---------------------------------------------------------------------------


def test_no_regression_under_threshold(tracker: ScorecardTracker) -> None:
    """Drops within threshold should not raise."""
    tracker.record_run({"a": 80.0})
    tracker.record_run({"a": 76.0})  # -4 points, within default 5.0
    tracker.check_regression(max_drop=5.0)  # Should not raise


def test_regression_at_exact_threshold(tracker: ScorecardTracker) -> None:
    """Drop exactly at threshold should not raise (> not >=)."""
    tracker.record_run({"a": 80.0})
    tracker.record_run({"a": 75.0})  # -5 points exactly
    tracker.check_regression(max_drop=5.0)  # Should not raise


def test_regression_beyond_threshold(tracker: ScorecardTracker) -> None:
    """Drop beyond threshold should raise RegressionError."""
    tracker.record_run({"a": 80.0})
    tracker.record_run({"a": 74.0})  # -6 points
    with pytest.raises(RegressionError, match="Score regression detected"):  # pyright: ignore[reportUnknownMemberType]
        tracker.check_regression(max_drop=5.0)


def test_no_regression_with_single_run(tracker: ScorecardTracker) -> None:
    """Single run should never trigger regression (no comparison possible)."""
    tracker.record_run({"a": 10.0})
    tracker.check_regression(max_drop=0.0)  # Should not raise even with 0 threshold


def test_regression_custom_threshold(tracker: ScorecardTracker) -> None:
    """Custom threshold should be respected."""
    tracker.record_run({"a": 100.0})
    tracker.record_run({"a": 97.0})  # -3 points
    tracker.check_regression(max_drop=5.0)  # OK

    tracker.record_run({"a": 94.0})  # -3 more, but from 97
    tracker.check_regression(max_drop=5.0)  # OK (only -3 from previous)

    tracker.record_run({"a": 80.0})  # -14 from previous
    with pytest.raises(RegressionError):  # pyright: ignore[reportUnknownMemberType]
        tracker.check_regression(max_drop=10.0)


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------


def test_total_score_is_average(tracker: ScorecardTracker) -> None:
    """Total score should be the average of all journey scores."""
    run = tracker.record_run({"a": 100.0, "b": 50.0, "c": 75.0})
    assert run.total_score == 75.0


def test_empty_scores(tracker: ScorecardTracker) -> None:
    """Empty scores dict should produce total_score = 0."""
    run = tracker.record_run({})
    assert run.total_score == 0.0


def test_single_journey(tracker: ScorecardTracker) -> None:
    """Single journey score equals total."""
    run = tracker.record_run({"deployment_flow": 88.0})
    assert run.total_score == 88.0


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------


def test_format_summary_no_runs(scores_path: Path) -> None:
    """Summary with no runs should say so."""
    tracker = ScorecardTracker(scores_path=scores_path)
    assert "No scorecard runs recorded" in tracker.format_summary()


def test_format_summary_single_run(tracker: ScorecardTracker) -> None:
    """Summary for a single run should show score but no delta."""
    tracker.record_run({"deployment_flow": 85.0, "kill_switch": 90.0})
    summary = tracker.format_summary()
    assert "87.5/100" in summary
    assert "deployment_flow" in summary
    assert "kill_switch" in summary


def test_format_summary_with_delta(tracker: ScorecardTracker) -> None:
    """Summary with two runs should show deltas."""
    tracker.record_run({"deployment_flow": 80.0})
    tracker.record_run({"deployment_flow": 90.0})
    summary = tracker.format_summary()
    assert "+10" in summary


def test_format_summary_negative_delta(tracker: ScorecardTracker) -> None:
    """Negative delta should be shown with minus sign."""
    tracker.record_run({"deployment_flow": 90.0})
    tracker.record_run({"deployment_flow": 80.0})
    summary = tracker.format_summary()
    assert "-10" in summary


# ---------------------------------------------------------------------------
# ScorecardRun serialization
# ---------------------------------------------------------------------------


def test_scorecard_run_roundtrip() -> None:
    """ScorecardRun should survive to_dict -> from_dict roundtrip."""
    run = ScorecardRun(
        timestamp="2026-03-15T00:00:00+00:00",
        scores={"a": 100.0, "b": 50.0},
        total_score=75.0,
        delta_from_previous=-5.0,
    )
    serialized = run.to_dict()
    deserialized = ScorecardRun.from_dict(serialized)
    assert deserialized.timestamp == run.timestamp
    assert deserialized.scores == run.scores
    assert deserialized.total_score == run.total_score
    assert deserialized.delta_from_previous == run.delta_from_previous


def test_scorecard_run_from_dict_handles_nulls() -> None:
    """from_dict should handle None delta gracefully."""
    run_data = ScorecardRun(
        timestamp="2026-03-15T00:00:00Z",
        scores={"a": 100.0},
        total_score=100.0,
        delta_from_previous=None,
    )
    serialized = run_data.to_dict()
    run = ScorecardRun.from_dict(serialized)
    assert run.delta_from_previous is None


# ---------------------------------------------------------------------------
# Corrupted file handling
# ---------------------------------------------------------------------------


def test_corrupted_json_recovers(scores_path: Path) -> None:
    """Tracker should gracefully handle a corrupted JSON file."""
    scores_path.parent.mkdir(parents=True, exist_ok=True)
    scores_path.write_text("not valid json!!!", encoding="utf-8")

    tracker = ScorecardTracker(scores_path=scores_path)
    assert len(tracker.runs) == 0

    # Should still be able to record new runs
    run = tracker.record_run({"a": 50.0})
    assert run.total_score == 50.0


def test_empty_file_recovers(scores_path: Path) -> None:
    """Tracker should handle an empty file gracefully."""
    scores_path.parent.mkdir(parents=True, exist_ok=True)
    scores_path.write_text("", encoding="utf-8")

    tracker = ScorecardTracker(scores_path=scores_path)
    assert len(tracker.runs) == 0
