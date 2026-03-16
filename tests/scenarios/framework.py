"""Event-driven scenario testing framework for domain-specific market scenarios.

Provides a playbook model where scenarios consist of timed events and post-run
assertions. Each domain (TradFi, CeFi, DeFi, Sports) defines realistic event
sequences that stress-test the trading pipeline under adversarial conditions.

Usage::

    from tests.scenarios.framework import (
        ScenarioDomain,
        ScenarioEvent,
        ScenarioAssertion,
        ScenarioPlaybook,
    )

    playbook = ScenarioPlaybook(
        name="market_halt",
        domain=ScenarioDomain.TRADFI,
        events=[
            ScenarioEvent(timestamp_offset_ms=0, event_type="price_update", data={"price": "150.0"}),
            ScenarioEvent(timestamp_offset_ms=500, event_type="halt", data={"reason": "circuit_breaker"}),
        ],
        assertions=[
            ScenarioAssertion(field="trading_halted", operator="eq", expected=True),
        ],
    )
    result = playbook.play()
    assert result.passed
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ScenarioDomain(StrEnum):
    """Trading domain for scenario categorisation."""

    TRADFI = "tradfi"
    CEFI = "cefi"
    DEFI = "defi"
    SPORTS = "sports"


# Scalar value type used in event data and assertion expected values.
ScalarValue = bool | str | int | float | None


@dataclass(frozen=True)
class ScenarioEvent:
    """A single timed event in a scenario playbook.

    Attributes:
        timestamp_offset_ms: Milliseconds from scenario start when this event fires.
        event_type: Category of event (e.g. ``price_update``, ``halt``, ``gas_spike``).
        data: Arbitrary payload for the event.
    """

    timestamp_offset_ms: int
    event_type: str
    data: dict[str, ScalarValue]


@dataclass(frozen=True)
class ScenarioAssertion:
    """A post-scenario assertion that validates pipeline state.

    Attributes:
        field: Key to look up in the accumulated scenario state.
        operator: Comparison operator (``eq``, ``gt``, ``lt``, ``gte``, ``lte``, ``contains``).
        expected: The expected value (RHS of the comparison).
    """

    field: str
    operator: str  # eq, gt, lt, gte, lte, contains
    expected: ScalarValue


@dataclass
class ScenarioResult:
    """Outcome of executing a single scenario playbook.

    Attributes:
        name: Playbook name.
        domain: Domain the scenario belongs to.
        passed: Whether all assertions passed and no errors occurred.
        assertions_passed: Count of assertions that passed.
        assertions_failed: Count of assertions that failed.
        events_played: Number of events processed.
        errors: Error messages collected during execution.
    """

    name: str
    domain: ScenarioDomain
    passed: bool
    assertions_passed: int
    assertions_failed: int
    events_played: int
    errors: list[str]


# Valid comparison operator names.
_COMPARISON_OPERATORS: frozenset[str] = frozenset({"eq", "gt", "lt", "gte", "lte"})


def _eval_contains(
    field: str,
    actual: ScalarValue,
    expected: ScalarValue,
) -> tuple[bool, str]:
    """Evaluate a 'contains' assertion (substring check)."""
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False, (f"contains requires str operands: field={field!r} actual={actual!r} expected={expected!r}")
    if expected not in actual:
        return False, (f"contains failed: field={field!r} actual={actual!r} expected_substr={expected!r}")
    return True, ""


def _dispatch_comparison(
    operator_name: str,
    actual: bool | str | int | float,
    expected: bool | str | int | float,
) -> bool | None:
    """Run comparison and return result, or None for unknown operator."""
    if operator_name == "eq":
        return actual == expected
    if operator_name == "gt":
        return bool(actual > expected)  # pyright: ignore[reportOperatorIssue, reportUnknownArgumentType]
    if operator_name == "lt":
        return bool(actual < expected)  # pyright: ignore[reportOperatorIssue, reportUnknownArgumentType]
    if operator_name == "gte":
        return bool(actual >= expected)  # pyright: ignore[reportOperatorIssue, reportUnknownArgumentType]
    if operator_name == "lte":
        return bool(actual <= expected)  # pyright: ignore[reportOperatorIssue, reportUnknownArgumentType]
    return None


def _check_none_operands(
    field: str,
    operator_name: str,
    actual: ScalarValue,
    expected: ScalarValue,
) -> tuple[bool, str] | None:
    """Handle None operands. Returns result tuple, or None if both are non-None."""
    if actual is None and expected is not None:
        return False, f"field={field!r} is None in state, expected={expected!r}"
    if expected is None and actual is not None:
        return False, f"field={field!r}={actual!r} in state, expected=None"
    if actual is None and expected is None:
        eq_pass = operator_name == "eq"
        msg = "" if eq_pass else f"Assertion failed: {field!r} {operator_name} None, actual=None"
        return eq_pass, msg
    return None


def _eval_comparison(
    field: str,
    operator_name: str,
    actual: ScalarValue,
    expected: ScalarValue,
) -> tuple[bool, str]:
    """Evaluate a comparison assertion (eq, gt, lt, gte, lte)."""
    none_result = _check_none_operands(field, operator_name, actual, expected)
    if none_result is not None:
        return none_result

    # Both are non-None at this point; narrow for type checker.
    assert actual is not None
    assert expected is not None
    ok = _dispatch_comparison(operator_name, actual, expected)
    if ok is None:
        return False, f"Unhandled operator: {operator_name!r}"
    if not ok:
        return False, f"Assertion failed: {field!r} {operator_name} {expected!r}, actual={actual!r}"
    return True, ""


def _evaluate_assertion(
    assertion: ScenarioAssertion,
    state: dict[str, ScalarValue],
) -> tuple[bool, str]:
    """Evaluate a single assertion against accumulated state.

    Returns:
        ``(passed, error_message)`` where *error_message* is empty on success.
    """
    actual = state.get(assertion.field)

    if assertion.operator == "contains":
        return _eval_contains(assertion.field, actual, assertion.expected)

    if assertion.operator not in _COMPARISON_OPERATORS:
        return False, f"Unknown operator: {assertion.operator!r}"

    try:
        return _eval_comparison(assertion.field, assertion.operator, actual, assertion.expected)
    except TypeError as exc:
        return False, f"Type error comparing {assertion.field!r}: {exc}"


class ScenarioPlaybook:
    """An event-driven scenario with post-execution assertions.

    Events are played in ``timestamp_offset_ms`` order. Each event updates an
    internal state dict (keyed by ``event_type``). After all events are played,
    assertions are evaluated against the accumulated state.

    For domain-specific scenario definitions, see:
    - :mod:`tests.scenarios.tradfi_scenarios`
    - :mod:`tests.scenarios.cefi_scenarios`
    - :mod:`tests.scenarios.defi_scenarios`
    - :mod:`tests.scenarios.sports_scenarios`
    """

    def __init__(
        self,
        name: str,
        domain: ScenarioDomain,
        events: list[ScenarioEvent],
        assertions: list[ScenarioAssertion],
    ) -> None:
        self.name = name
        self.domain = domain
        self.events = sorted(events, key=lambda e: e.timestamp_offset_ms)
        self.assertions = list(assertions)

    def play(self) -> ScenarioResult:
        """Execute the scenario: play all events, then evaluate assertions.

        Returns:
            A :class:`ScenarioResult` summarising the outcome.
        """
        state: dict[str, ScalarValue] = {}
        errors: list[str] = []
        events_played = 0

        # Play events in chronological order, accumulating state.
        for event in self.events:
            try:
                self._apply_event(event, state)
                events_played += 1
            except Exception as exc:
                errors.append(f"Event {event.event_type}@{event.timestamp_offset_ms}ms: {exc}")

        # Evaluate assertions against accumulated state.
        passed_count = 0
        failed_count = 0
        for assertion in self.assertions:
            ok, err = _evaluate_assertion(assertion, state)
            if ok:
                passed_count += 1
            else:
                failed_count += 1
                errors.append(err)

        # Passed only when zero assertion failures AND zero event errors.
        has_event_errors = events_played < len(self.events)
        truly_passed = failed_count == 0 and not has_event_errors

        return ScenarioResult(
            name=self.name,
            domain=self.domain,
            passed=truly_passed,
            assertions_passed=passed_count,
            assertions_failed=failed_count,
            events_played=events_played,
            errors=errors,
        )

    @staticmethod
    def _apply_event(
        event: ScenarioEvent,
        state: dict[str, ScalarValue],
    ) -> None:
        """Apply a single event to the scenario state.

        Each data key is stored in state with a composite key
        ``{event_type}.{data_key}`` and also as just ``{data_key}`` for
        convenience (last-writer-wins for flat keys).
        """
        for key, value in event.data.items():
            # Namespaced key for specificity.
            state[f"{event.event_type}.{key}"] = value
            # Flat key for simple assertions.
            state[key] = value


def get_all_scenarios_for_domain(domain: ScenarioDomain) -> list[ScenarioPlaybook]:
    """Load all pre-defined scenarios for a given domain.

    Uses direct imports to avoid ``getattr`` / ``Any`` type issues with
    basedpyright strict mode.

    Returns:
        List of :class:`ScenarioPlaybook` instances for the domain.
    """
    if domain == ScenarioDomain.TRADFI:
        from tests.scenarios.tradfi_scenarios import get_scenarios as get_tradfi

        return get_tradfi()
    if domain == ScenarioDomain.CEFI:
        from tests.scenarios.cefi_scenarios import get_scenarios as get_cefi

        return get_cefi()
    if domain == ScenarioDomain.DEFI:
        from tests.scenarios.defi_scenarios import get_scenarios as get_defi

        return get_defi()
    # ScenarioDomain.SPORTS
    from tests.scenarios.sports_scenarios import get_scenarios as get_sports

    return get_sports()
