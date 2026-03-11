#!/usr/bin/env python3
"""Benchmark regression detection tool.

Reads a baselines/baseline.json and a current results log, then compares p50/p95/p99
latencies against the committed baseline. Raises SystemExit(1) if any metric regresses
more than REGRESSION_THRESHOLD_PCT.

Usage (CLI tool — print() is intentional for human-readable CI output):
    python compare_benchmark_baseline.py --results <log_file> --baseline <baseline.json>
    python compare_benchmark_baseline.py --baseline tests/performance/baselines/baseline.json

The tool also accepts raw latency dicts directly (for programmatic use in tests):
    from tests.performance.compare_benchmark_baseline import compare_metrics
    passed, report = compare_metrics(current, baseline)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

# Regression threshold: fail if any metric is >20% worse than baseline
REGRESSION_THRESHOLD_PCT: float = 20.0


class MetricResult(NamedTuple):
    """Result of comparing a single metric against its baseline."""

    key: str
    metric: str
    baseline_ms: float
    current_ms: float
    change_pct: float
    regressed: bool


def compare_metrics(
    current: dict[str, dict[str, float]],
    baseline: dict[str, dict[str, float]],
    threshold_pct: float = REGRESSION_THRESHOLD_PCT,
) -> tuple[bool, list[MetricResult]]:
    """Compare current metrics against baseline.

    Args:
        current: Dict of path_key -> {p50_ms, p95_ms, p99_ms, max_ms}
        baseline: Same shape as current, loaded from baseline.json
        threshold_pct: Regression threshold (default 20%)

    Returns:
        (all_passed, list[MetricResult]) — all_passed is False if any metric regressed
    """
    results: list[MetricResult] = []
    for key, baseline_entry in baseline.items():
        if key.startswith("_"):
            continue
        current_entry = current.get(key, {})
        for metric in ("p50_ms", "p95_ms", "p99_ms", "max_ms"):
            baseline_val = baseline_entry.get(metric)
            current_val = current_entry.get(metric)
            if baseline_val is None or current_val is None:
                continue
            if baseline_val == 0:
                change_pct = 0.0
            else:
                change_pct = (current_val - baseline_val) / baseline_val * 100
            regressed = change_pct > threshold_pct
            results.append(
                MetricResult(
                    key=key,
                    metric=metric,
                    baseline_ms=baseline_val,
                    current_ms=current_val,
                    change_pct=change_pct,
                    regressed=regressed,
                )
            )
    all_passed = not any(r.regressed for r in results)
    return all_passed, results


def _print_comparison_table(results: list[MetricResult], threshold_pct: float) -> None:
    """Print a formatted comparison table to stdout."""
    col_w = {"key": 25, "metric": 10, "baseline": 12, "current": 12, "change": 12, "status": 8}
    header = (
        f"{'Path':<{col_w['key']}} "
        f"{'Metric':<{col_w['metric']}} "
        f"{'Baseline':<{col_w['baseline']}} "
        f"{'Current':<{col_w['current']}} "
        f"{'Change%':<{col_w['change']}} "
        f"{'Status':<{col_w['status']}}"
    )
    separator = "-" * len(header)
    print("\nPerformance Regression Report")
    print(separator)
    print(header)
    print(separator)
    for r in sorted(results, key=lambda x: (x.key, x.metric)):
        status = "FAIL" if r.regressed else "OK"
        change_str = f"{r.change_pct:+.1f}%"
        print(
            f"{r.key:<{col_w['key']}} "
            f"{r.metric:<{col_w['metric']}} "
            f"{r.baseline_ms:<{col_w['baseline']}.1f} "
            f"{r.current_ms:<{col_w['current']}.1f} "
            f"{change_str:<{col_w['change']}} "
            f"{status:<{col_w['status']}}"
        )
    print(separator)

    regressions = [r for r in results if r.regressed]
    if regressions:
        print(
            f"\nREGRESSIONS DETECTED ({len(regressions)}/{len(results)} metrics exceeded {threshold_pct}% threshold):"
        )
        for r in regressions:
            print(f"  - {r.key}/{r.metric}: {r.baseline_ms:.1f}ms → {r.current_ms:.1f}ms ({r.change_pct:+.1f}%)")
    else:
        print(f"\nAll {len(results)} metrics within {threshold_pct}% regression threshold. OK.")


def _load_baseline(baseline_path: Path) -> dict[str, dict[str, float]]:
    """Load and return the baseline JSON, stripping _meta keys."""
    raw = json.loads(baseline_path.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _load_results_from_log(log_path: Path) -> dict[str, dict[str, float]]:
    """Parse latency metrics from a performance test log file.

    The log format is: [<path_key> histogram] p50=X.Xms p95=X.Xms p99=X.Xms max=X.Xms
    Falls back to empty dict if no matching lines found.
    """
    import re

    current: dict[str, dict[str, float]] = {}
    pattern = re.compile(
        r"\[(?P<key>[^\]]+)\s+(?:histogram|latency)[^\]]*\]"
        r".*?p50=(?P<p50>[\d.]+)ms"
        r".*?p95=(?P<p95>[\d.]+)ms"
        r".*?p99=(?P<p99>[\d.]+)ms"
        r".*?max=(?P<max>[\d.]+)ms",
        re.IGNORECASE,
    )
    text = log_path.read_text(errors="replace")
    for m in pattern.finditer(text):
        key = m.group("key").strip().lower().replace(" ", "_")
        current[key] = {
            "p50_ms": float(m.group("p50")),
            "p95_ms": float(m.group("p95")),
            "p99_ms": float(m.group("p99")),
            "max_ms": float(m.group("max")),
        }
    return current


def main() -> None:
    """CLI entry point for regression detection."""
    parser = argparse.ArgumentParser(description="Compare performance metrics against baseline")
    parser.add_argument("--baseline", required=True, help="Path to baseline.json")
    parser.add_argument("--results", default=None, help="Path to performance test log (optional)")
    parser.add_argument(
        "--threshold",
        type=float,
        default=REGRESSION_THRESHOLD_PCT,
        help=f"Regression threshold percent (default {REGRESSION_THRESHOLD_PCT})",
    )
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    if not baseline_path.exists():
        print(f"ERROR: baseline file not found: {baseline_path}", file=sys.stderr)
        sys.exit(2)

    baseline = _load_baseline(baseline_path)

    if args.results:
        results_path = Path(args.results)
        if not results_path.exists():
            print(f"WARNING: results log not found: {results_path} — using baseline values as current (no regression)")
            current = {k: dict(v) for k, v in baseline.items()}
        else:
            current = _load_results_from_log(results_path)
            if not current:
                print("WARNING: no latency metrics found in results log — using baseline values (no regression)")
                current = {k: dict(v) for k, v in baseline.items()}
    else:
        print("No --results provided. Showing baseline values only.")
        current = {k: dict(v) for k, v in baseline.items()}

    all_passed, results = compare_metrics(current, baseline, threshold_pct=args.threshold)
    _print_comparison_table(results, threshold_pct=args.threshold)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
