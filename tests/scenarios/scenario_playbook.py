"""Scenario playbook framework for end-to-end strategy testing.

Provides dataclasses and a runner for executing multi-step scenario playbooks
that validate the full trading pipeline: market data -> strategy signal ->
execution instruction -> position update.

Usage::

    playbook = ScenarioPlaybook(
        name="momentum_happy_path",
        description="Validate momentum signal generation from tick data",
        steps=[
            ScenarioStep(
                action="ingest_market_tick",
                params={"venue": "binance", "symbol": "BTC-USDT", "price": "65000.0"},
                expected_result={"tick_ingested": True},
                timeout_seconds=5,
            ),
            ...
        ],
        expected_outcomes={"signal_generated": True, "instruction_created": True},
    )
    runner = ScenarioRunner()
    report = runner.execute(playbook)
    assert report.passed
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

# Scalar result type used in step expected/actual results.
StepValue = bool | str | int | float

# Callable signature for action handlers.
ActionHandlerFn = Callable[
    [dict[str, str], dict[str, object]],
    dict[str, StepValue],
]


class StepStatus(Enum):
    """Outcome of a single scenario step."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class ScenarioStep:
    """A single step in a scenario playbook.

    Attributes:
        action: Identifier for the action to perform (e.g. "ingest_market_tick").
        params: Parameters passed to the action handler.
        expected_result: Key-value pairs that must appear in the step result.
        timeout_seconds: Maximum wall-clock time for this step.
    """

    action: str
    params: dict[str, str]
    expected_result: dict[str, StepValue]
    timeout_seconds: int = 10


@dataclass(frozen=True)
class ScenarioPlaybook:
    """A named, self-contained end-to-end scenario.

    Attributes:
        name: Human-readable scenario identifier (kebab-case recommended).
        description: What this scenario validates.
        steps: Ordered list of steps to execute.
        expected_outcomes: Final assertions after all steps complete.
    """

    name: str
    description: str
    steps: tuple[ScenarioStep, ...]
    expected_outcomes: dict[str, StepValue]


@dataclass
class StepResult:
    """Result of executing a single ScenarioStep."""

    step: ScenarioStep
    status: StepStatus = StepStatus.PENDING
    actual_result: dict[str, StepValue] = field(default_factory=dict)
    error_message: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class ScenarioReport:
    """Aggregated result of executing a full ScenarioPlaybook."""

    playbook_name: str
    step_results: list[StepResult] = field(default_factory=list)
    outcome_mismatches: dict[str, str] = field(default_factory=dict)
    total_elapsed_seconds: float = 0.0

    @property
    def passed(self) -> bool:
        """True if every step passed and all expected outcomes match."""
        all_steps_ok = all(r.status == StepStatus.PASSED for r in self.step_results)
        return all_steps_ok and len(self.outcome_mismatches) == 0

    @property
    def summary(self) -> str:
        """One-line summary: PASS/FAIL + counts."""
        passed_count = sum(1 for r in self.step_results if r.status == StepStatus.PASSED)
        total = len(self.step_results)
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"[{verdict}] {self.playbook_name}: "
            f"{passed_count}/{total} steps passed, "
            f"{len(self.outcome_mismatches)} outcome mismatches, "
            f"{self.total_elapsed_seconds:.2f}s"
        )


class ScenarioRunner:
    """Executes scenario playbooks using registered action handlers.

    Register handlers via :meth:`register`, then call :meth:`execute` with a
    playbook.  Each handler receives ``(params, context)`` and must return a
    result dict.  The runner compares the result against ``expected_result``.

    Example::

        runner = ScenarioRunner()

        def handle_tick(
            params: dict[str, str],
            context: dict[str, object],
        ) -> dict[str, bool | str | int | float]:
            return {"tick_ingested": True}

        runner.register("ingest_market_tick", handle_tick)
        report = runner.execute(playbook)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandlerFn] = {}
        self._context: dict[str, object] = {}

    def register(self, action: str, handler: ActionHandlerFn) -> None:
        """Register a handler function for the given action name.

        Args:
            action: The action identifier matching :attr:`ScenarioStep.action`.
            handler: A callable ``(params, context) -> result dict``.
        """
        self._handlers[action] = handler

    def execute(self, playbook: ScenarioPlaybook) -> ScenarioReport:
        """Execute all steps in *playbook* sequentially and return a report."""
        report = ScenarioReport(playbook_name=playbook.name)
        self._context = {}
        t0 = time.monotonic()

        for step in playbook.steps:
            step_result = self._run_step(step)
            report.step_results.append(step_result)

            # Stop on first failure (fail-fast)
            if step_result.status != StepStatus.PASSED:
                remaining_idx = playbook.steps.index(step) + 1
                for remaining_step in playbook.steps[remaining_idx:]:
                    skipped = StepResult(step=remaining_step, status=StepStatus.SKIPPED)
                    report.step_results.append(skipped)
                break

        # Validate expected outcomes against accumulated context
        for key, expected_val in playbook.expected_outcomes.items():
            actual_val = self._context.get(key)
            if actual_val != expected_val:
                report.outcome_mismatches[key] = f"expected={expected_val!r}, actual={actual_val!r}"

        report.total_elapsed_seconds = time.monotonic() - t0
        return report

    @staticmethod
    def _check_result_mismatches(
        step: ScenarioStep,
        result: dict[str, StepValue],
    ) -> list[str]:
        """Compare expected vs actual result and return mismatch descriptions."""
        mismatches: list[str] = []
        for key, expected in step.expected_result.items():
            actual = result.get(key)
            if actual != expected:
                mismatches.append(f"{key}: expected={expected!r}, got={actual!r}")
        return mismatches

    def _run_step(self, step: ScenarioStep) -> StepResult:
        """Execute a single step with timeout enforcement."""
        handler = self._handlers.get(step.action)
        if handler is None:
            return StepResult(
                step=step,
                status=StepStatus.FAILED,
                error_message=f"No handler registered for action '{step.action}'",
            )

        t0 = time.monotonic()
        try:
            result: dict[str, StepValue] = handler(step.params, self._context)
            elapsed = time.monotonic() - t0
            return self._evaluate_step_result(step, result, elapsed)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            return StepResult(
                step=step,
                status=StepStatus.FAILED,
                elapsed_seconds=elapsed,
                error_message=f"{type(exc).__name__}: {exc}",
            )

    def _evaluate_step_result(
        self,
        step: ScenarioStep,
        result: dict[str, StepValue],
        elapsed: float,
    ) -> StepResult:
        """Evaluate a step's result against expectations and timeout."""
        if elapsed > step.timeout_seconds:
            return StepResult(
                step=step,
                status=StepStatus.TIMED_OUT,
                actual_result=result,
                elapsed_seconds=elapsed,
                error_message=f"Step took {elapsed:.2f}s, timeout={step.timeout_seconds}s",
            )

        mismatches = self._check_result_mismatches(step, result)
        if mismatches:
            return StepResult(
                step=step,
                status=StepStatus.FAILED,
                actual_result=result,
                elapsed_seconds=elapsed,
                error_message="; ".join(mismatches),
            )

        # Merge result into context for downstream steps
        for k, v in result.items():
            self._context[k] = v

        return StepResult(
            step=step,
            status=StepStatus.PASSED,
            actual_result=result,
            elapsed_seconds=elapsed,
        )
