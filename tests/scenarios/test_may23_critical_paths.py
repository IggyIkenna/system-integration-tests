"""May-23 DeFi cutover — critical path SIT coverage.

Three scenario playbooks gate the last automated CI step before paper → live_early
promotion:

* ``defi_carry_staked_basis_paper`` — Gate A1 — LST rates → strategy → exec → PBM
* ``defi_apd_paper`` — Gate A2 — DEX/CEX dispersion → strategy → exec → PBM
* ``defi_paper_to_live_early_gate`` — Gate G — promote → VM STARTED + DART gate

Plan-of-record: ``plans/active/issues/sit_may23_critical_path_coverage_gaps_2026_05_15.md``.
Master plan: ``plans/active/master_to_live_defi_2026_05_23.md``.

Authored 2026-05-15 (slot 7, Ikenna side). All scenarios are credential-free —
they exercise event sequencing + assertion contracts only. Live-execution
fixtures live under ``tests/overnight/test_defi_paper_flows.py`` and the
multi-service emulator stack (Wave E P4.6 successor).
"""

from __future__ import annotations

import pytest

from tests.scenarios.defi_scenarios import get_scenarios
from tests.scenarios.framework import ScenarioDomain, ScenarioPlaybook

pytestmark = [pytest.mark.code_test, pytest.mark.unit]  # pyright: ignore[reportUnknownMemberType]


# The three names below MUST stay in lockstep with ``defi_scenarios.py``. Any
# rename here is review-blocking against the issue doc — the gate IDs are
# referenced from the master plan continuous-verification column.
_GATE_A1 = "defi_carry_staked_basis_paper"
_GATE_A2 = "defi_apd_paper"
_GATE_G = "defi_paper_to_live_early_gate"

_MAY23_GATES: frozenset[str] = frozenset({_GATE_A1, _GATE_A2, _GATE_G})


def _find_scenario(name: str) -> ScenarioPlaybook:
    """Locate a DeFi scenario by name; fails the test if absent.

    Avoids the alternative pattern (looking up the private function symbol),
    which would couple the test to internal module structure.
    """
    for scenario in get_scenarios():
        if scenario.name == name:
            return scenario
    raise AssertionError(
        f"May-23 critical-path scenario {name!r} missing from "
        "tests/scenarios/defi_scenarios.py::get_scenarios — "
        "this is a May-23 gate regression."
    )


# ---------------------------------------------------------------------------
# Presence — every gate must exist + belong to the DEFI domain
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(  # pyright: ignore[reportUnknownMemberType]
    "scenario_name",
    sorted(_MAY23_GATES),
)
def test_critical_path_scenario_present(scenario_name: str) -> None:
    """Every May-23 critical-path scenario is exposed by ``get_scenarios``."""
    scenario = _find_scenario(scenario_name)
    assert scenario.domain == ScenarioDomain.DEFI


# ---------------------------------------------------------------------------
# Gate A1 — carry_staked_basis paper-mode pipeline end-to-end
# ---------------------------------------------------------------------------


def test_gate_a1_carry_staked_basis_paper_passes() -> None:
    """Gate A1: LST rates → strategy → execution → PBM manifest must all fire.

    Each pipeline stage emits a canonical event; the scenario's assertions
    fail-fast if any stage stops emitting its expected payload. This is the
    contract that the May-23 carry archetype paper-mode test relies on.
    """
    scenario = _find_scenario(_GATE_A1)
    result = scenario.play()
    assert result.passed, f"Gate A1 failed: {result.errors}"
    assert result.assertions_passed >= 6


def test_gate_a1_emits_carry_archetype_signal() -> None:
    """Gate A1 explicitly verifies the carry archetype signal fired.

    Sanity-check that the scenario carries the archetype id we expect — a
    rename of ``carry_staked_basis`` upstream would silently break the gate
    without this assertion.
    """
    scenario = _find_scenario(_GATE_A1)
    archetype_events = [ev for ev in scenario.events if ev.data.get("archetype") == "carry_staked_basis"]
    assert len(archetype_events) >= 2, (
        "Gate A1 must include at least 2 events tagged with archetype="
        "'carry_staked_basis' (feature emission + strategy signal)."
    )


# ---------------------------------------------------------------------------
# Gate A2 — arbitrage_price_dispersion paper-mode end-to-end
# ---------------------------------------------------------------------------


def test_gate_a2_apd_paper_passes() -> None:
    """Gate A2: DEX + CEX dispersion → strategy → execution → PBM manifest.

    Also asserts the scenario explicitly rules out the silent-SKIP DeFi error
    routing path — per the issue-doc finding, APD must not be silently
    skipped on a recoverable error.
    """
    scenario = _find_scenario(_GATE_A2)
    result = scenario.play()
    assert result.passed, f"Gate A2 failed: {result.errors}"
    assert result.assertions_passed >= 6


def test_gate_a2_both_legs_fill() -> None:
    """Gate A2 covers both DEX and CEX legs of the hybrid execution path."""
    scenario = _find_scenario(_GATE_A2)
    fill_legs = {str(ev.data.get("leg", "")) for ev in scenario.events if ev.event_type == "execution_fill"}
    assert "dex_long" in fill_legs, "Gate A2 missing DEX long leg fill event"
    assert "cex_short" in fill_legs, "Gate A2 missing CEX short leg fill event"


def test_gate_a2_rejects_silent_skip() -> None:
    """Gate A2 must include the defi_error_routing event that confirms not-skipped."""
    scenario = _find_scenario(_GATE_A2)
    has_routing_check = any(
        ev.event_type == "defi_error_routing" and ev.data.get("skipped") == "false" for ev in scenario.events
    )
    assert has_routing_check, (
        "Gate A2 must emit defi_error_routing with skipped='false' so a silent "
        "SKIP on a recoverable error is caught by the SIT layer."
    )


# ---------------------------------------------------------------------------
# Gate G — paper_1d → live_early promote workflow
# ---------------------------------------------------------------------------


def test_gate_g_paper_to_live_early_passes() -> None:
    """Gate G: promote endpoint, MinimalCandidateManifest, VM STARTED, DART gate."""
    scenario = _find_scenario(_GATE_G)
    result = scenario.play()
    assert result.passed, f"Gate G failed: {result.errors}"


def test_gate_g_dart_gate_is_blocking_on_day_one() -> None:
    """Gate G must encode the DART manual-trade gate as ACTIVE + BLOCKING on day 1.

    Per `codex/04-architecture/promote-workflow-architecture.md`, the DART gate
    is the only safety net during the first 3 trading days — silently
    deactivating it would let live fills through without human approval.
    """
    scenario = _find_scenario(_GATE_G)
    day_one_events = [
        ev for ev in scenario.events if ev.event_type == "manual_gate_state" and ev.data.get("day_of_live") == 1
    ]
    assert len(day_one_events) >= 1, "Gate G missing day-1 manual_gate_state event"
    day_one = day_one_events[0]
    assert day_one.data.get("gate_active") == "true"
    assert day_one.data.get("gate_blocking") == "true"


def test_gate_g_promotes_to_live_early_not_live_full() -> None:
    """Per CLAUDE.md Promote Workflow: only ``paper_1d → live_early`` is valid May-23.

    ``live_full`` is post-cutover; a promote scenario that targets ``live_full``
    would silently violate the May-23 contract.
    """
    scenario = _find_scenario(_GATE_G)
    promote_events = [ev for ev in scenario.events if ev.event_type == "promote_request"]
    assert len(promote_events) == 1
    promote = promote_events[0]
    assert promote.data.get("from_mode") == "paper_1d"
    assert promote.data.get("to_mode") == "live_early"


# ---------------------------------------------------------------------------
# Suite-level — three gates wired together
# ---------------------------------------------------------------------------


def test_all_three_may23_gates_pass_together() -> None:
    """Aggregate gate: all three scenarios pass when played in sequence.

    A May-23 readiness check should be able to ask one yes/no question. This
    test answers it. Reference: ``master_to_live_defi_2026_05_23.md`` Group A.
    """
    results = [_find_scenario(name).play() for name in sorted(_MAY23_GATES)]
    failed = [r for r in results if not r.passed]
    assert not failed, "May-23 critical-path gates failing: " + ", ".join(f"{r.name}: {r.errors}" for r in failed)
