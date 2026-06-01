"""Scorecard trend tracking — persists flow-validation scores, shows deltas, blocks regression.

Usage:
    tracker = ScorecardTracker(scores_path=Path("scores.json"))
    tracker.record_run(scores={"deployment_flow": 100, "kill_switch": 85, "alert_routing": 90})
    tracker.check_regression(max_drop=5)   # raises RegressionError if score dropped > 5

JSON file format::

    {
        "runs": [
            {
                "timestamp": "2026-03-15T12:00:00Z",
                "scores": {"deployment_flow": 100, "kill_switch": 85},
                "total_score": 92.5,
                "delta_from_previous": null
            },
            ...
        ]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import cast

# JSON-safe scalar types used in serialization.
_JsonScalar = str | float | int | bool | None
_JsonDict = dict[str, _JsonScalar | dict[str, _JsonScalar] | list[object]]


class RegressionError(Exception):
    """Raised when score drops by more than the allowed threshold."""


@dataclass(frozen=True)
class ScorecardRun:
    """A single scorecard run result."""

    timestamp: str
    scores: dict[str, float]
    total_score: float
    delta_from_previous: float | None

    def to_dict(self) -> _JsonDict:
        result: _JsonDict = {
            "timestamp": self.timestamp,
            "scores": dict(self.scores),
            "total_score": self.total_score,
            "delta_from_previous": self.delta_from_previous,
        }
        return result

    @staticmethod
    def from_dict(data: _JsonDict) -> ScorecardRun:
        """Deserialize from a JSON-compatible dict."""
        scores: dict[str, float] = {}
        scores_raw = data.get("scores")
        if isinstance(scores_raw, dict):
            for k, v in scores_raw.items():
                if isinstance(v, (int, float)) or isinstance(v, str):
                    scores[str(k)] = float(v)
                else:
                    scores[str(k)] = 0.0

        delta_raw = data.get("delta_from_previous")
        delta_val: float | None = None
        if isinstance(delta_raw, (int, float)):
            delta_val = float(delta_raw)

        total_raw = data.get("total_score", 0.0)
        total_val = float(total_raw) if isinstance(total_raw, (int, float)) else 0.0

        timestamp_raw = data.get("timestamp", "")
        timestamp_val = str(timestamp_raw) if timestamp_raw is not None else ""

        return ScorecardRun(
            timestamp=timestamp_val,
            scores=scores,
            total_score=total_val,
            delta_from_previous=delta_val,
        )


@dataclass
class ScorecardTracker:
    """Persists scorecard runs to a JSON file and provides trend analysis.

    Attributes:
        scores_path: Path to the JSON file that stores run history.
        max_history: Maximum number of runs to retain (oldest are pruned).
    """

    scores_path: Path
    max_history: int = 100
    _runs: list[ScorecardRun] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_run(self, scores: dict[str, float]) -> ScorecardRun:
        """Record a new scorecard run and persist to disk.

        Args:
            scores: Mapping of journey name to score (0-100).

        Returns:
            The newly created ScorecardRun with computed delta.
        """
        total = self._compute_total(scores)
        previous_total = self._runs[-1].total_score if self._runs else None
        delta: float | None = None
        if previous_total is not None:
            delta = total - previous_total

        run = ScorecardRun(
            timestamp=datetime.now(UTC).isoformat(),
            scores=dict(scores),
            total_score=total,
            delta_from_previous=delta,
        )
        self._runs.append(run)

        # Prune oldest runs if over limit
        if len(self._runs) > self.max_history:
            self._runs = self._runs[-self.max_history :]

        self._save()
        return run

    def check_regression(self, max_drop: float = 5.0) -> None:
        """Raise RegressionError if the latest score dropped more than max_drop points.

        Args:
            max_drop: Maximum allowed score drop in absolute points.

        Raises:
            RegressionError: When the delta exceeds the threshold.
        """
        if len(self._runs) < 2:
            return  # Not enough data to compare

        latest = self._runs[-1]
        if latest.delta_from_previous is not None and latest.delta_from_previous < -max_drop:
            previous = self._runs[-2]
            raise RegressionError(
                f"Score regression detected: {previous.total_score:.1f} -> {latest.total_score:.1f} "
                f"(delta={latest.delta_from_previous:+.1f}, threshold=-{max_drop:.1f})"
            )

    @property
    def runs(self) -> list[ScorecardRun]:
        """All recorded runs in chronological order."""
        return list(self._runs)

    @property
    def latest(self) -> ScorecardRun | None:
        """Most recent run, or None if no runs recorded."""
        return self._runs[-1] if self._runs else None

    @property
    def previous(self) -> ScorecardRun | None:
        """Second-most-recent run, or None if fewer than 2 runs."""
        return self._runs[-2] if len(self._runs) >= 2 else None

    def format_summary(self) -> str:
        """Human-readable summary of the latest run vs previous.

        Returns a multi-line string suitable for pytest terminal output.
        """
        if not self._runs:
            return "No scorecard runs recorded."

        latest = self._runs[-1]
        lines: list[str] = [
            f"Scorecard: {latest.total_score:.1f}/100",
        ]

        if latest.delta_from_previous is not None:
            direction = "+" if latest.delta_from_previous >= 0 else ""
            lines.append(f"  Delta from previous: {direction}{latest.delta_from_previous:.1f}")

        lines.append("  Journey scores:")
        for journey, score in sorted(latest.scores.items()):
            prev_score: float | None = None
            if self.previous is not None:
                prev_score = self.previous.scores.get(journey)

            delta_str = ""
            if prev_score is not None:
                journey_delta = score - prev_score
                sign = "+" if journey_delta >= 0 else ""
                delta_str = f" ({sign}{journey_delta:.0f})"

            lines.append(f"    {journey}: {score:.0f}{delta_str}")

        lines.append(f"  Runs recorded: {len(self._runs)}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_total(scores: dict[str, float]) -> float:
        """Average score across all journeys."""
        if not scores:
            return 0.0
        return sum(scores.values()) / len(scores)

    def _load(self) -> None:
        """Load runs from the JSON file on disk."""
        if not self.scores_path.exists():
            self._runs = []
            return
        try:
            raw_text = self.scores_path.read_text(encoding="utf-8")
            data = cast(_JsonDict, json.loads(raw_text))  # pyright: ignore[reportAny]
            raw_runs_val = data.get("runs", [])
            if not isinstance(raw_runs_val, list):
                self._runs = []
                return
            raw_runs = cast(list[_JsonDict], raw_runs_val)
            self._runs = [ScorecardRun.from_dict(r) for r in raw_runs]
        except (json.JSONDecodeError, KeyError, ValueError):
            self._runs = []

    def _save(self) -> None:
        """Persist runs to the JSON file on disk."""
        self.scores_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"runs": [r.to_dict() for r in self._runs]}
        self.scores_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
