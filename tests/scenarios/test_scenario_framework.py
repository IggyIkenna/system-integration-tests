"""Tests for the event-driven scenario framework.

Validates the core framework classes, event sequencing, assertion evaluation,
result reporting, domain filtering, and per-domain scenario definitions.

At least 10 tests covering:
- Playbook creation
- Event sequencing (timestamp ordering)
- Assertion evaluation (eq, gt, lt, contains)
- Result reporting (pass/fail structure)
- Domain filtering
- TradFi scenario definitions (5 exist)
- CeFi scenario definitions (5 exist)
- DeFi scenario definitions (5 exist)
- Sports scenario definitions (5 exist)

All tests are credential-free and require no live services.
"""

from __future__ import annotations

import pytest

from tests.scenarios.framework import (
    ScenarioAssertion,
    ScenarioDomain,
    ScenarioEvent,
    ScenarioPlaybook,
    get_all_scenarios_for_domain,
)

pytestmark = [pytest.mark.code_test, pytest.mark.unit]  # pyright: ignore[reportUnknownMemberType]


# ---------------------------------------------------------------------------
# Test: playbook creation
# ---------------------------------------------------------------------------


def test_playbook_creation() -> None:
    """ScenarioPlaybook can be instantiated with events and assertions."""
    playbook = ScenarioPlaybook(
        name="test_create",
        domain=ScenarioDomain.CEFI,
        events=[
            ScenarioEvent(timestamp_offset_ms=0, event_type="tick", data={"price": "100.00"}),
        ],
        assertions=[
            ScenarioAssertion(field="price", operator="eq", expected="100.00"),
        ],
    )
    assert playbook.name == "test_create"
    assert playbook.domain == ScenarioDomain.CEFI
    assert len(playbook.events) == 1
    assert len(playbook.assertions) == 1


# ---------------------------------------------------------------------------
# Test: event sequencing
# ---------------------------------------------------------------------------


def test_event_sequencing() -> None:
    """Events are sorted by timestamp_offset_ms regardless of insertion order."""
    playbook = ScenarioPlaybook(
        name="test_ordering",
        domain=ScenarioDomain.TRADFI,
        events=[
            ScenarioEvent(timestamp_offset_ms=3000, event_type="c", data={"seq": "3"}),
            ScenarioEvent(timestamp_offset_ms=1000, event_type="a", data={"seq": "1"}),
            ScenarioEvent(timestamp_offset_ms=2000, event_type="b", data={"seq": "2"}),
        ],
        assertions=[],
    )
    offsets = [e.timestamp_offset_ms for e in playbook.events]
    assert offsets == [1000, 2000, 3000], f"Expected sorted offsets, got {offsets}"


# ---------------------------------------------------------------------------
# Test: assertion evaluation — equality
# ---------------------------------------------------------------------------


def test_assertion_evaluation_eq() -> None:
    """The 'eq' operator passes when field value matches expected."""
    playbook = ScenarioPlaybook(
        name="test_eq",
        domain=ScenarioDomain.CEFI,
        events=[
            ScenarioEvent(timestamp_offset_ms=0, event_type="tick", data={"price": "42.00"}),
        ],
        assertions=[
            ScenarioAssertion(field="price", operator="eq", expected="42.00"),
        ],
    )
    result = playbook.play()
    assert result.passed
    assert result.assertions_passed == 1
    assert result.assertions_failed == 0


# ---------------------------------------------------------------------------
# Test: assertion evaluation — inequality fails
# ---------------------------------------------------------------------------


def test_assertion_evaluation_eq_fails() -> None:
    """The 'eq' operator fails when field value does not match."""
    playbook = ScenarioPlaybook(
        name="test_eq_fail",
        domain=ScenarioDomain.CEFI,
        events=[
            ScenarioEvent(timestamp_offset_ms=0, event_type="tick", data={"price": "42.00"}),
        ],
        assertions=[
            ScenarioAssertion(field="price", operator="eq", expected="99.00"),
        ],
    )
    result = playbook.play()
    assert not result.passed
    assert result.assertions_failed == 1
    assert len(result.errors) > 0


# ---------------------------------------------------------------------------
# Test: assertion evaluation — gt operator
# ---------------------------------------------------------------------------


def test_assertion_evaluation_gt() -> None:
    """The 'gt' operator passes when actual > expected."""
    playbook = ScenarioPlaybook(
        name="test_gt",
        domain=ScenarioDomain.TRADFI,
        events=[
            ScenarioEvent(timestamp_offset_ms=0, event_type="metric", data={"count": 10}),
        ],
        assertions=[
            ScenarioAssertion(field="count", operator="gt", expected=5),
        ],
    )
    result = playbook.play()
    assert result.passed
    assert result.assertions_passed == 1


# ---------------------------------------------------------------------------
# Test: assertion evaluation — contains operator
# ---------------------------------------------------------------------------


def test_assertion_evaluation_contains() -> None:
    """The 'contains' operator checks substring presence."""
    playbook = ScenarioPlaybook(
        name="test_contains",
        domain=ScenarioDomain.DEFI,
        events=[
            ScenarioEvent(timestamp_offset_ms=0, event_type="log", data={"message": "gas spike detected on ethereum"}),
        ],
        assertions=[
            ScenarioAssertion(field="message", operator="contains", expected="gas spike"),
        ],
    )
    result = playbook.play()
    assert result.passed


# ---------------------------------------------------------------------------
# Test: result reporting structure
# ---------------------------------------------------------------------------


def test_result_reporting() -> None:
    """ScenarioResult contains correct counts and metadata."""
    playbook = ScenarioPlaybook(
        name="test_reporting",
        domain=ScenarioDomain.SPORTS,
        events=[
            ScenarioEvent(timestamp_offset_ms=0, event_type="a", data={"x": "1"}),
            ScenarioEvent(timestamp_offset_ms=100, event_type="b", data={"y": "2"}),
            ScenarioEvent(timestamp_offset_ms=200, event_type="c", data={"z": "3"}),
        ],
        assertions=[
            ScenarioAssertion(field="x", operator="eq", expected="1"),
            ScenarioAssertion(field="y", operator="eq", expected="2"),
            ScenarioAssertion(field="z", operator="eq", expected="wrong"),
        ],
    )
    result = playbook.play()
    assert not result.passed
    assert result.events_played == 3
    assert result.assertions_passed == 2
    assert result.assertions_failed == 1
    assert result.name == "test_reporting"
    assert result.domain == ScenarioDomain.SPORTS


# ---------------------------------------------------------------------------
# Test: domain filtering
# ---------------------------------------------------------------------------


def test_domain_filtering() -> None:
    """get_all_scenarios_for_domain returns only scenarios for the requested domain."""
    for domain in ScenarioDomain:
        scenarios = get_all_scenarios_for_domain(domain)
        assert len(scenarios) > 0, f"No scenarios found for domain {domain}"
        for scenario in scenarios:
            assert scenario.domain == domain, (
                f"Scenario {scenario.name} has domain {scenario.domain}, expected {domain}"
            )


# ---------------------------------------------------------------------------
# Test: missing field in state
# ---------------------------------------------------------------------------


def test_assertion_missing_field() -> None:
    """Assertions referencing absent state keys should fail gracefully."""
    playbook = ScenarioPlaybook(
        name="test_missing",
        domain=ScenarioDomain.CEFI,
        events=[
            ScenarioEvent(timestamp_offset_ms=0, event_type="tick", data={"price": "100"}),
        ],
        assertions=[
            ScenarioAssertion(field="nonexistent_field", operator="eq", expected="value"),
        ],
    )
    result = playbook.play()
    assert not result.passed
    assert result.assertions_failed == 1


# ---------------------------------------------------------------------------
# Test: empty scenario (no events, no assertions)
# ---------------------------------------------------------------------------


def test_empty_scenario_passes() -> None:
    """A scenario with no events and no assertions should trivially pass."""
    playbook = ScenarioPlaybook(
        name="test_empty",
        domain=ScenarioDomain.TRADFI,
        events=[],
        assertions=[],
    )
    result = playbook.play()
    assert result.passed
    assert result.events_played == 0
    assert result.assertions_passed == 0
    assert result.assertions_failed == 0


# ---------------------------------------------------------------------------
# Test: TradFi scenario definitions
# ---------------------------------------------------------------------------


def test_tradfi_scenario_definitions() -> None:
    """TradFi domain must have exactly 5 defined scenarios, all playable."""
    scenarios = get_all_scenarios_for_domain(ScenarioDomain.TRADFI)
    assert len(scenarios) == 5, f"Expected 5 TradFi scenarios, got {len(scenarios)}"
    for scenario in scenarios:
        result = scenario.play()
        assert result.passed, f"TradFi scenario {scenario.name} failed: {result.errors}"


# ---------------------------------------------------------------------------
# Test: CeFi scenario definitions
# ---------------------------------------------------------------------------


def test_cefi_scenario_definitions() -> None:
    """CeFi domain must have exactly 5 defined scenarios, all playable."""
    scenarios = get_all_scenarios_for_domain(ScenarioDomain.CEFI)
    assert len(scenarios) == 5, f"Expected 5 CeFi scenarios, got {len(scenarios)}"
    for scenario in scenarios:
        result = scenario.play()
        assert result.passed, f"CeFi scenario {scenario.name} failed: {result.errors}"


# ---------------------------------------------------------------------------
# Test: DeFi scenario definitions
# ---------------------------------------------------------------------------


def test_defi_scenario_definitions() -> None:
    """DeFi domain must have exactly 5 defined scenarios, all playable."""
    scenarios = get_all_scenarios_for_domain(ScenarioDomain.DEFI)
    assert len(scenarios) == 5, f"Expected 5 DeFi scenarios, got {len(scenarios)}"
    for scenario in scenarios:
        result = scenario.play()
        assert result.passed, f"DeFi scenario {scenario.name} failed: {result.errors}"


# ---------------------------------------------------------------------------
# Test: Sports scenario definitions
# ---------------------------------------------------------------------------


def test_sports_scenario_definitions() -> None:
    """Sports domain must have exactly 5 defined scenarios, all playable."""
    scenarios = get_all_scenarios_for_domain(ScenarioDomain.SPORTS)
    assert len(scenarios) == 5, f"Expected 5 Sports scenarios, got {len(scenarios)}"
    for scenario in scenarios:
        result = scenario.play()
        assert result.passed, f"Sports scenario {scenario.name} failed: {result.errors}"


# ---------------------------------------------------------------------------
# Test: all scenarios have unique names
# ---------------------------------------------------------------------------


def test_all_scenario_names_unique() -> None:
    """Every scenario across all domains must have a unique name."""
    all_names: list[str] = []
    for domain in ScenarioDomain:
        for scenario in get_all_scenarios_for_domain(domain):
            all_names.append(scenario.name)
    unique_names = set(all_names)
    assert len(all_names) == len(unique_names), (
        f"Duplicate scenario names found: {[n for n in all_names if all_names.count(n) > 1]}"
    )


# ---------------------------------------------------------------------------
# Test: event data propagates to state correctly
# ---------------------------------------------------------------------------


def test_event_data_propagates_to_state() -> None:
    """Multiple events should accumulate state with both flat and namespaced keys."""
    playbook = ScenarioPlaybook(
        name="test_state_propagation",
        domain=ScenarioDomain.CEFI,
        events=[
            ScenarioEvent(timestamp_offset_ms=0, event_type="tick", data={"price": "100"}),
            ScenarioEvent(timestamp_offset_ms=100, event_type="signal", data={"direction": "long"}),
        ],
        assertions=[
            # Flat keys from both events
            ScenarioAssertion(field="price", operator="eq", expected="100"),
            ScenarioAssertion(field="direction", operator="eq", expected="long"),
            # Namespaced keys
            ScenarioAssertion(field="tick.price", operator="eq", expected="100"),
            ScenarioAssertion(field="signal.direction", operator="eq", expected="long"),
        ],
    )
    result = playbook.play()
    assert result.passed, f"State propagation failed: {result.errors}"
    assert result.assertions_passed == 4
