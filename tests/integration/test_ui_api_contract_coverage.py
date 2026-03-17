"""SIT integration test: UI -> API endpoint coverage contract.

For every UI stack declared in ui-api-mapping.json that has a mapped API,
validate that:
1. The mapping JSON is parseable and structurally valid.
2. Every stack with a UI has a mapped api_port.
3. The declared API module name follows the underscore convention.
4. Settlement stack specifically maps to trading-analytics-api (confirmed canonical).

This test runs purely from the mapping file — no live services required.
It is a structural contract test, not an HTTP integration test.

The companion live-endpoint test (requires running services) is tracked in
ui_trader_acceptance_testing_2026_03_15 (ph0-run-all-smoke-tests).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.code_test

# ---------------------------------------------------------------------------
# Fixture: load the mapping SSOT
# ---------------------------------------------------------------------------

_MAPPING_PATH = (
    Path(__file__).parent.parent.parent.parent / "unified-trading-pm" / "scripts" / "dev" / "ui-api-mapping.json"
)

# Expected API module suffix pattern (underscore, not dash)
_API_MODULE_CONVENTION = "_api"

# UIs with explicit API routes that must be validated
_REQUIRED_STACKS = {
    "deployment",
    "onboarding",
    "execution-analytics",
    "strategy",
    "settlement",
    "live-health-monitor",
    "logs-dashboard",
    "ml-training",
    "trading-analytics",
    "batch-audit",
    "client-reporting",
}

# Port range for APIs: 8004-8025 (8004-8016 stacks, 8018-8025 service workers)
_API_PORT_MIN = 8004
_API_PORT_MAX = 8025

# Port range for UIs: 5173-5184
_UI_PORT_MIN = 5173
_UI_PORT_MAX = 5184


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _load_mapping() -> dict[str, object]:
    """Load and parse ui-api-mapping.json."""
    assert _MAPPING_PATH.exists(), (
        f"ui-api-mapping.json not found at {_MAPPING_PATH}. SSOT: unified-trading-pm/scripts/dev/ui-api-mapping.json"
    )
    raw = _MAPPING_PATH.read_text(encoding="utf-8")
    parsed: dict[str, object] = json.loads(raw)
    return parsed


# ---------------------------------------------------------------------------
# 1. Structural validity
# ---------------------------------------------------------------------------


class TestMappingStructure:
    """ui-api-mapping.json must be parseable and have correct structure."""

    def test_mapping_file_exists(self) -> None:
        assert _MAPPING_PATH.exists(), f"Mapping file not found: {_MAPPING_PATH}"

    def test_mapping_has_stacks_key(self) -> None:
        mapping = _load_mapping()
        assert "stacks" in mapping, "ui-api-mapping.json must have top-level 'stacks' key"

    def test_stacks_is_non_empty_dict(self) -> None:
        mapping = _load_mapping()
        stacks = mapping["stacks"]
        assert isinstance(stacks, dict), "'stacks' must be a dict"
        assert len(stacks) > 0, "'stacks' must be non-empty"  # type: ignore[arg-type]

    def test_all_required_stacks_present(self) -> None:
        mapping = _load_mapping()
        stacks = mapping["stacks"]
        assert isinstance(stacks, dict)
        missing = _REQUIRED_STACKS - set(stacks.keys())
        assert not missing, f"Required stacks missing from ui-api-mapping.json: {sorted(missing)}"


# ---------------------------------------------------------------------------
# 2. Per-stack field validation
# ---------------------------------------------------------------------------


class TestPerStackFields:
    """Every stack entry must have valid api, api_port, and api_module fields."""

    def _stacks(self) -> dict[str, dict[str, object]]:
        mapping = _load_mapping()
        stacks = mapping["stacks"]
        assert isinstance(stacks, dict)
        return stacks  # type: ignore[return-value]

    def test_every_stack_has_api_field(self) -> None:
        stacks = self._stacks()
        for name, stack in stacks.items():
            assert isinstance(stack, dict)
            assert "api" in stack, f"Stack '{name}' missing 'api' field"

    def test_every_stack_has_api_port_field(self) -> None:
        stacks = self._stacks()
        for name, stack in stacks.items():
            assert isinstance(stack, dict)
            assert "api_port" in stack, f"Stack '{name}' missing 'api_port' field"

    def test_every_stack_has_api_module_field(self) -> None:
        stacks = self._stacks()
        for name, stack in stacks.items():
            assert isinstance(stack, dict)
            assert "api_module" in stack, f"Stack '{name}' missing 'api_module' field"

    def test_api_ports_in_valid_range(self) -> None:
        """All API ports must be in the 8004-8016 range."""
        stacks = self._stacks()
        for name, stack in stacks.items():
            assert isinstance(stack, dict)
            port = stack.get("api_port")
            if port is None:
                continue  # API-only stacks without a port (e.g. ml-inference)
            assert isinstance(port, int), f"Stack '{name}' api_port must be int, got {type(port)}"
            assert _API_PORT_MIN <= port <= _API_PORT_MAX, (
                f"Stack '{name}' api_port {port} outside expected range {_API_PORT_MIN}-{_API_PORT_MAX}"
            )

    def test_ui_ports_in_valid_range(self) -> None:
        """All UI ports (where set) must be in the 5173-5183 range.

        Exception: non-Vite stacks (e.g. odum-website on port 3000) are excluded
        from the Vite dev-server port range check.
        """
        # Stacks that use non-Vite dev servers and have ports outside the Vite range
        _NON_VITE_STACKS = {"odum-website"}
        stacks = self._stacks()
        for name, stack in stacks.items():
            assert isinstance(stack, dict)
            port = stack.get("ui_port")
            if port is None:
                continue  # Non-UI stacks
            if name in _NON_VITE_STACKS:
                continue  # Non-Vite stack — different port range is expected
            assert isinstance(port, int), f"Stack '{name}' ui_port must be int, got {type(port)}"
            assert _UI_PORT_MIN <= port <= _UI_PORT_MAX, (
                f"Stack '{name}' ui_port {port} outside expected range {_UI_PORT_MIN}-{_UI_PORT_MAX}"
            )

    def test_api_module_uses_underscore_convention(self) -> None:
        """API module names must use underscores (Python package names), not dashes."""
        stacks = self._stacks()
        for name, stack in stacks.items():
            assert isinstance(stack, dict)
            module = stack.get("api_module")
            if module is None:
                continue
            assert isinstance(module, str)
            assert "-" not in module, f"Stack '{name}' api_module '{module}' uses dashes — must use underscores"
            assert module.endswith(_API_MODULE_CONVENTION), f"Stack '{name}' api_module '{module}' must end with '_api'"

    def test_no_duplicate_api_ports(self) -> None:
        """Each API port must be assigned to exactly one stack."""
        stacks = self._stacks()
        port_to_stacks: dict[int, list[str]] = {}
        for name, stack in stacks.items():
            assert isinstance(stack, dict)
            port = stack.get("api_port")
            if port is None or not isinstance(port, int):
                continue
            port_to_stacks.setdefault(port, []).append(name)
        # Multiple stacks can share an API (e.g. strategy + execution-analytics share execution-results-api)
        # This is intentional — just log it. Only fail on completely distinct APIs at same port.
        for port, names in port_to_stacks.items():
            if len(names) > 1:
                apis = {stacks[n]["api"] for n in names}  # type: ignore[index]
                assert len(apis) == 1, (
                    f"Port {port} assigned to stacks {names} with DIFFERENT APIs {apis}. "
                    "Each distinct API must have a unique port."
                )

    def test_no_duplicate_ui_ports(self) -> None:
        """Each UI port must be unique."""
        stacks = self._stacks()
        port_to_stacks: dict[int, list[str]] = {}
        for name, stack in stacks.items():
            assert isinstance(stack, dict)
            port = stack.get("ui_port")
            if port is None or not isinstance(port, int):
                continue
            port_to_stacks.setdefault(port, []).append(name)
        dupes = {p: names for p, names in port_to_stacks.items() if len(names) > 1}
        assert not dupes, f"Duplicate UI ports found: {dupes}"


# ---------------------------------------------------------------------------
# 3. Settlement-specific contract assertions
# ---------------------------------------------------------------------------


class TestSettlementStackContract:
    """settlement stack must be mapped to trading-analytics-api on port 8012."""

    def _settlement_stack(self) -> dict[str, object]:
        mapping = _load_mapping()
        stacks = mapping["stacks"]
        assert isinstance(stacks, dict)
        assert "settlement" in stacks, "settlement stack must be in ui-api-mapping.json"
        stack = stacks["settlement"]
        assert isinstance(stack, dict)
        return stack

    def test_settlement_api_is_trading_analytics_api(self) -> None:
        stack = self._settlement_stack()
        assert stack.get("api") == "trading-analytics-api", (
            f"settlement stack must map to trading-analytics-api, got '{stack.get('api')}'. "
            "settlement-api as a separate repo is not needed — trading-analytics-api "
            "already has all /settlement/* routes."
        )

    def test_settlement_api_port_is_8012(self) -> None:
        stack = self._settlement_stack()
        assert stack.get("api_port") == 8012, (
            f"settlement API port must be 8012 (trading-analytics-api), got {stack.get('api_port')}"
        )

    def test_settlement_api_module_is_trading_analytics_api(self) -> None:
        stack = self._settlement_stack()
        assert stack.get("api_module") == "trading_analytics_api", (
            f"settlement api_module must be 'trading_analytics_api', got '{stack.get('api_module')}'"
        )

    def test_settlement_ui_port_is_5176(self) -> None:
        stack = self._settlement_stack()
        assert stack.get("ui_port") == 5176, f"settlement-ui must be on port 5176, got {stack.get('ui_port')}"

    def test_settlement_ui_is_settlement_ui(self) -> None:
        stack = self._settlement_stack()
        assert stack.get("ui") == "settlement-ui", (
            f"settlement stack ui must be 'settlement-ui', got '{stack.get('ui')}'"
        )


# ---------------------------------------------------------------------------
# 4. Key stacks spot-check
# ---------------------------------------------------------------------------


class TestKeyStacksSpotCheck:
    """Spot-check a few other important stack/port assignments."""

    def _stacks(self) -> dict[str, dict[str, object]]:
        mapping = _load_mapping()
        stacks = mapping["stacks"]
        assert isinstance(stacks, dict)
        return stacks  # type: ignore[return-value]

    def test_deployment_stack_api_port(self) -> None:
        stacks = self._stacks()
        assert stacks["deployment"]["api_port"] == 8004

    def test_logs_dashboard_api_port(self) -> None:
        stacks = self._stacks()
        assert stacks["logs-dashboard"]["api_port"] == 8013

    def test_logs_dashboard_api_is_batch_audit(self) -> None:
        stacks = self._stacks()
        assert stacks["logs-dashboard"]["api"] == "batch-audit-api"

    def test_trading_analytics_stack_api_port(self) -> None:
        stacks = self._stacks()
        assert stacks["trading-analytics"]["api_port"] == 8012

    def test_batch_audit_stack_api_port(self) -> None:
        stacks = self._stacks()
        assert stacks["batch-audit"]["api_port"] == 8013

    def test_client_reporting_stack_api_port(self) -> None:
        stacks = self._stacks()
        assert stacks["client-reporting"]["api_port"] == 8014

    def test_total_stacks_count_at_least_ten(self) -> None:
        stacks = self._stacks()
        assert len(stacks) >= 10, (
            f"Expected at least 10 stacks in ui-api-mapping.json, found {len(stacks)}. "
            "Regression guard against accidental deletion."
        )
