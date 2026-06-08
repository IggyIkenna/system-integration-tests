"""SIT integration test: UI -> API endpoint coverage contract.

For every UI stack declared in ui-api-mapping.json that has a mapped API,
validate that:
1. The mapping JSON is parseable and structurally valid.
2. Every stack with a UI has a mapped api_port.
3. The declared API module name follows the underscore convention.
4. unified-trading stack maps to unified-trading-api / unified-trading-system-ui (consolidated canonical).

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

# Stacks declared in ui-api-mapping.json (consolidated UI/API model; archived split stacks removed)
_REQUIRED_STACKS = {
    "deployment",
    "client-reporting",
    "market-data",
    "user-management",
    "unified-trading",
    "auth",
}

# API HTTP ports from stacks (includes consolidated unified-trading-api, auth-api)
_API_PORT_MIN = 8004
_API_PORT_MAX = 8300

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
        """All API ports must be in the configured stack range (see _API_PORT_*)."""
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
        # Stacks that use non-Vite dev servers or alternate ports. unified-trading-system-ui
        # migrated from Vite (:5174 legacy) to Next.js (:3000); client-reporting + user-management
        # are likewise Next.js/archived. See ui-api-mapping.json stack $note fields.
        _NON_VITE_STACKS = {"odum-website", "client-reporting", "user-management", "unified-trading"}
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
        # Multiple stacks can share an API (e.g. auth + user-management share auth-api)
        # This is intentional — just log it. Only fail on completely distinct APIs at same port.
        for port, names in port_to_stacks.items():
            if len(names) > 1:
                apis = {stacks[n]["api"] for n in names}  # type: ignore[index]
                assert len(apis) == 1, (
                    f"Port {port} assigned to stacks {names} with DIFFERENT APIs {apis}. "
                    "Each distinct API must have a unique port."
                )

    def test_no_duplicate_ui_ports(self) -> None:
        """Each UI port must map to at most one logical UI (same app may serve multiple stacks)."""
        stacks = self._stacks()
        port_to_stacks: dict[int, list[str]] = {}
        for name, stack in stacks.items():
            assert isinstance(stack, dict)
            port = stack.get("ui_port")
            if port is None or not isinstance(port, int):
                continue
            port_to_stacks.setdefault(port, []).append(name)
        for port, names in port_to_stacks.items():
            if len(names) <= 1:
                continue
            uis = {stacks[n]["ui"] for n in names}  # type: ignore[index]
            assert len(uis) == 1, (
                f"Port {port} assigned to stacks {names} with DIFFERENT UIs {uis}. "
                "Each distinct UI dev server must have a unique port."
            )


# ---------------------------------------------------------------------------
# 3. Unified-trading stack (canonical consolidated UI + API)
# ---------------------------------------------------------------------------


class TestUnifiedTradingStackContract:
    """unified-trading stack maps to unified-trading-api and unified-trading-system-ui."""

    def _unified_trading_stack(self) -> dict[str, object]:
        mapping = _load_mapping()
        stacks = mapping["stacks"]
        assert isinstance(stacks, dict)
        assert "unified-trading" in stacks, "unified-trading stack must be in ui-api-mapping.json"
        stack = stacks["unified-trading"]
        assert isinstance(stack, dict)
        return stack

    def test_unified_trading_api_is_unified_trading_api(self) -> None:
        stack = self._unified_trading_stack()
        assert stack.get("api") == "unified-trading-api", (
            f"unified-trading stack must map to unified-trading-api, got '{stack.get('api')}'."
        )

    def test_unified_trading_api_port_is_8030(self) -> None:
        stack = self._unified_trading_stack()
        assert stack.get("api_port") == 8030, f"unified-trading API port must be 8030, got {stack.get('api_port')}"

    def test_unified_trading_api_module(self) -> None:
        stack = self._unified_trading_stack()
        assert stack.get("api_module") == "unified_trading_api", (
            f"unified-trading api_module must be 'unified_trading_api', got '{stack.get('api_module')}'"
        )

    def test_unified_trading_ui_port_is_3000(self) -> None:
        """unified-trading-system-ui runs on Next.js :3000 (Vite :5174 was retired).

        See the unified-trading stack ``$note`` in ui-api-mapping.json documenting the
        Next.js-on-:3000 migration from the legacy Vite :5174 dev server.
        """
        stack = self._unified_trading_stack()
        assert stack.get("ui_port") == 3000, (
            f"unified-trading Next.js stack must be on port 3000, got {stack.get('ui_port')}"
        )

    def test_unified_trading_ui_is_unified_trading_system_ui(self) -> None:
        stack = self._unified_trading_stack()
        assert stack.get("ui") == "unified-trading-system-ui", (
            f"unified-trading stack ui must be 'unified-trading-system-ui', got '{stack.get('ui')}'"
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

    def test_unified_trading_stack_api_port(self) -> None:
        stacks = self._stacks()
        assert stacks["unified-trading"]["api_port"] == 8030

    def test_client_reporting_stack_api_port(self) -> None:
        stacks = self._stacks()
        assert stacks["client-reporting"]["api_port"] == 8014

    def test_market_data_stack_api_port(self) -> None:
        stacks = self._stacks()
        assert stacks["market-data"]["api_port"] == 8016

    def test_total_stacks_count_at_least_six(self) -> None:
        stacks = self._stacks()
        assert len(stacks) >= 6, (
            f"Expected at least 6 stacks in ui-api-mapping.json, found {len(stacks)}. "
            "Regression guard against accidental deletion."
        )
