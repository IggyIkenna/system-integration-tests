"""Scenario playbook tests -- load YAML playbooks and execute with mock handlers.

Each YAML playbook in playbooks/ defines a multi-step scenario. This module
registers mock action handlers that simulate the trading pipeline and runs
each playbook through the ScenarioRunner.

All handlers are credential-free mock implementations. No live services needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # pyright: ignore[reportMissingModuleSource]

from tests.scenarios.scenario_playbook import (
    ActionHandlerFn,
    ScenarioPlaybook,
    ScenarioRunner,
    ScenarioStep,
    StepStatus,
    StepValue,
)

pytestmark = [pytest.mark.code_test, pytest.mark.unit]  # pyright: ignore[reportUnknownMemberType]

_PLAYBOOKS_DIR = Path(__file__).resolve().parent / "playbooks"

# Type aliases for YAML-parsed structures (keeps line lengths manageable).
_ScalarVal = str | int | float | bool
_StepDict = dict[str, _ScalarVal | list[object] | dict[str, _ScalarVal] | None]
_YamlPlaybook = dict[str, str | list[_StepDict] | dict[str, _ScalarVal] | None]


# ---------------------------------------------------------------------------
# Mock action handlers
# ---------------------------------------------------------------------------


def _handle_ingest_market_tick(
    params: dict[str, str],
    context: dict[str, object],
) -> dict[str, StepValue]:
    """Mock: Simulate ingesting a market tick."""
    venue = params.get("venue", "")
    symbol = params.get("symbol", "")
    price = params.get("price", "0")

    if not venue or not symbol:
        return {"tick_ingested": False}

    # Store in context for downstream steps
    context["last_tick_venue"] = venue
    context["last_tick_symbol"] = symbol
    context["last_tick_price"] = price

    return {"tick_ingested": True}


def _handle_evaluate_strategy_signal(
    params: dict[str, str],
    context: dict[str, object],
) -> dict[str, StepValue]:
    """Mock: Simulate strategy signal evaluation."""
    strategy_name = params.get("strategy_name", "")
    if not strategy_name:
        return {"signal_generated": False, "signal_direction": "none"}

    # Mock: all basis strategies generate a long signal on positive funding
    return {"signal_generated": True, "signal_direction": "long"}


def _handle_create_execution_instruction(
    params: dict[str, str],
    context: dict[str, object],
) -> dict[str, StepValue]:
    """Mock: Simulate creating an execution instruction."""
    strategy_name = params.get("strategy_name", "")
    side = params.get("side", "")
    quantity = params.get("quantity", "0")

    if not strategy_name or not side or float(quantity) <= 0:
        return {"instruction_created": False}

    context["last_instruction_side"] = side
    context["last_instruction_qty"] = quantity

    return {"instruction_created": True}


def _handle_update_position_state(
    params: dict[str, str],
    context: dict[str, object],
) -> dict[str, StepValue]:
    """Mock: Simulate position state update."""
    venue = params.get("venue", "")
    symbol = params.get("symbol", "")
    quantity = params.get("quantity", "0")

    if not venue or not symbol or float(quantity) <= 0:
        return {"position_updated": False}

    return {"position_updated": True}


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_MOCK_HANDLERS: dict[str, ActionHandlerFn] = {
    "ingest_market_tick": _handle_ingest_market_tick,
    "evaluate_strategy_signal": _handle_evaluate_strategy_signal,
    "create_execution_instruction": _handle_create_execution_instruction,
    "update_position_state": _handle_update_position_state,
}


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _normalise_yaml_values(
    raw_dict: dict[str, _ScalarVal],
) -> dict[str, StepValue]:
    """Normalise YAML-parsed values to the union type used by ScenarioStep."""
    result: dict[str, StepValue] = {}
    for k, v in raw_dict.items():
        if isinstance(v, (bool, int, float)):
            result[k] = v
        else:
            result[k] = str(v)
    return result


def _coerce_to_typed_dict(
    raw: dict[str, _ScalarVal],
) -> dict[str, _ScalarVal]:
    """Ensure all values in a dict conform to the expected scalar types."""
    return dict(raw)


def _extract_dict(
    parent: _StepDict,
    key: str,
) -> dict[str, _ScalarVal]:
    """Extract a sub-dict from a parsed YAML dict, with type narrowing."""
    val = parent.get(key)
    if isinstance(val, dict):
        return _coerce_to_typed_dict(val)
    return {}


def _parse_step(step_data: _StepDict) -> ScenarioStep:
    """Parse a single step dict from YAML into a ScenarioStep."""
    expected = _normalise_yaml_values(_extract_dict(step_data, "expected_result"))
    param_map = _extract_dict(step_data, "params")
    params: dict[str, str] = {k: str(v) for k, v in param_map.items()}

    action_val = step_data.get("action", "")
    timeout_val = step_data.get("timeout_seconds", 10)

    return ScenarioStep(
        action=str(action_val),
        params=params,
        expected_result=expected,
        timeout_seconds=int(timeout_val) if isinstance(timeout_val, (int, str)) else 10,
    )


def _load_playbook_from_yaml(yaml_path: Path) -> ScenarioPlaybook:
    """Parse a YAML file into a ScenarioPlaybook."""
    raw: _YamlPlaybook = yaml.safe_load(  # pyright: ignore[reportAny]
        yaml_path.read_text(encoding="utf-8")
    )

    raw_steps = raw.get("steps")
    steps: list[ScenarioStep] = []
    if isinstance(raw_steps, list):
        for sd in raw_steps:
            steps.append(_parse_step(sd))

    raw_outcomes = raw.get("expected_outcomes")
    outcomes_dict: dict[str, str | int | float | bool] = {}
    if isinstance(raw_outcomes, dict):
        outcomes_dict = dict(raw_outcomes)
    expected_outcomes = _normalise_yaml_values(outcomes_dict)

    return ScenarioPlaybook(
        name=str(raw.get("name", "")),
        description=str(raw.get("description", "")),
        steps=tuple(steps),
        expected_outcomes=expected_outcomes,
    )


def _discover_playbooks() -> list[Path]:
    """Find all .yaml playbook files in the playbooks directory."""
    if not _PLAYBOOKS_DIR.exists():
        return []
    return sorted(_PLAYBOOKS_DIR.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_playbooks_directory_exists() -> None:
    """The playbooks/ directory must exist and contain at least one YAML file."""
    assert _PLAYBOOKS_DIR.exists(), f"Playbooks directory not found: {_PLAYBOOKS_DIR}"
    yamls = list(_PLAYBOOKS_DIR.glob("*.yaml"))
    assert len(yamls) > 0, f"No .yaml playbooks found in {_PLAYBOOKS_DIR}"


@pytest.mark.parametrize(  # pyright: ignore[reportUnknownMemberType]
    "playbook_path",
    _discover_playbooks(),
    ids=[p.stem for p in _discover_playbooks()],
)
def test_playbook_loads_without_error(playbook_path: Path) -> None:
    """Each YAML playbook must parse into a valid ScenarioPlaybook."""
    playbook = _load_playbook_from_yaml(playbook_path)
    assert playbook.name, "Playbook name must not be empty"
    assert len(playbook.steps) > 0, f"Playbook '{playbook.name}' has zero steps"


@pytest.mark.parametrize(  # pyright: ignore[reportUnknownMemberType]
    "playbook_path",
    _discover_playbooks(),
    ids=[p.stem for p in _discover_playbooks()],
)
def test_playbook_executes_with_mock_handlers(playbook_path: Path) -> None:
    """Execute each playbook with mock handlers and verify pass."""
    playbook = _load_playbook_from_yaml(playbook_path)
    runner = ScenarioRunner()

    for action_name, handler in _MOCK_HANDLERS.items():
        runner.register(action_name, handler)

    report = runner.execute(playbook)

    # Print summary for visibility
    print(f"\n{report.summary}")
    for step_result in report.step_results:
        status_str = step_result.status.value
        print(f"  [{status_str:>8s}] {step_result.step.action} ({step_result.elapsed_seconds:.3f}s)")
        if step_result.error_message:
            print(f"           ERROR: {step_result.error_message}")

    assert report.passed, f"Playbook '{playbook.name}' failed:\n{report.summary}\n" + "\n".join(
        f"  {r.step.action}: {r.status.value} - {r.error_message}"
        for r in report.step_results
        if r.status != StepStatus.PASSED
    )


def test_scenario_runner_fail_fast_on_missing_handler() -> None:
    """Runner must fail-fast when a step has no registered handler."""
    playbook = ScenarioPlaybook(
        name="test-missing-handler",
        description="Should fail on unregistered action",
        steps=(
            ScenarioStep(
                action="nonexistent_action",
                params={},
                expected_result={"done": True},
            ),
            ScenarioStep(
                action="also_nonexistent",
                params={},
                expected_result={"done": True},
            ),
        ),
        expected_outcomes={},
    )
    runner = ScenarioRunner()
    report = runner.execute(playbook)

    assert not report.passed
    assert report.step_results[0].status == StepStatus.FAILED
    assert "No handler registered" in report.step_results[0].error_message
    # Second step should be skipped due to fail-fast
    assert report.step_results[1].status == StepStatus.SKIPPED


def test_scenario_runner_outcome_mismatch() -> None:
    """Runner must report outcome mismatches when context does not match expectations."""

    def always_succeed(
        params: dict[str, str],
        context: dict[str, object],
    ) -> dict[str, StepValue]:
        return {"result": True}

    playbook = ScenarioPlaybook(
        name="test-outcome-mismatch",
        description="Outcomes that will not match",
        steps=(
            ScenarioStep(
                action="do_thing",
                params={},
                expected_result={"result": True},
            ),
        ),
        expected_outcomes={"missing_key": True},
    )
    runner = ScenarioRunner()
    runner.register("do_thing", always_succeed)
    report = runner.execute(playbook)

    assert not report.passed
    assert "missing_key" in report.outcome_mismatches
