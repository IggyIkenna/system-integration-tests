"""TradFi domain scenario definitions.

Five realistic scenarios covering traditional financial market events:
1. market_halt — exchange-triggered trading halt (circuit breaker / LULD)
2. session_transition — pre-market -> regular -> after-hours transition
3. settlement — T+1 settlement cycle with position reconciliation
4. corporate_action — stock split adjusting positions and prices
5. circuit_breaker — multi-level circuit breaker (L1 -> L2 -> L3)

Each scenario returns a list of :class:`ScenarioEvent` instances with realistic
timestamps and data payloads. No live services required.
"""

from __future__ import annotations

from tests.scenarios.framework import (
    ScenarioAssertion,
    ScenarioDomain,
    ScenarioEvent,
    ScenarioPlaybook,
)

_DOMAIN = ScenarioDomain.TRADFI


def _market_halt() -> ScenarioPlaybook:
    """Exchange-triggered trading halt after rapid price decline."""
    return ScenarioPlaybook(
        name="tradfi_market_halt",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="price_update",
                data={"symbol": "SPY", "price": "450.00", "venue": "NASDAQ"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="price_update",
                data={"symbol": "SPY", "price": "435.00", "venue": "NASDAQ"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=2000,
                event_type="halt",
                data={"symbol": "SPY", "reason": "LULD_pause", "halt_duration_seconds": "300"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=302000,
                event_type="resume",
                data={"symbol": "SPY", "price": "438.00", "venue": "NASDAQ"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="reason", operator="eq", expected="LULD_pause"),
            ScenarioAssertion(field="halt_duration_seconds", operator="eq", expected="300"),
            ScenarioAssertion(field="symbol", operator="eq", expected="SPY"),
        ],
    )


def _session_transition() -> ScenarioPlaybook:
    """Pre-market to regular hours to after-hours transition."""
    return ScenarioPlaybook(
        name="tradfi_session_transition",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="session_change",
                data={"session": "pre_market", "market": "NYSE", "liquidity": "low"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="price_update",
                data={"symbol": "AAPL", "price": "185.50", "spread_bps": "15"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=5000,
                event_type="session_change",
                data={"session": "regular", "market": "NYSE", "liquidity": "high"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=6000,
                event_type="price_update",
                data={"symbol": "AAPL", "price": "186.20", "spread_bps": "2"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=20000,
                event_type="session_change",
                data={"session": "after_hours", "market": "NYSE", "liquidity": "low"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="session", operator="eq", expected="after_hours"),
            ScenarioAssertion(field="market", operator="eq", expected="NYSE"),
        ],
    )


def _settlement() -> ScenarioPlaybook:
    """T+1 settlement cycle with position reconciliation."""
    return ScenarioPlaybook(
        name="tradfi_settlement",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="trade_execution",
                data={"symbol": "MSFT", "side": "buy", "quantity": "100", "price": "420.00"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="settlement_initiate",
                data={"trade_id": "TRD-001", "settlement_date": "2026-03-17", "status": "pending"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=5000,
                event_type="settlement_confirm",
                data={"trade_id": "TRD-001", "status": "settled", "settled_quantity": "100"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=6000,
                event_type="position_reconcile",
                data={"symbol": "MSFT", "quantity": "100", "reconciled": "true"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="status", operator="eq", expected="settled"),
            ScenarioAssertion(field="reconciled", operator="eq", expected="true"),
            ScenarioAssertion(field="settled_quantity", operator="eq", expected="100"),
        ],
    )


def _corporate_action() -> ScenarioPlaybook:
    """Stock split (2:1) adjusting positions and reference prices."""
    return ScenarioPlaybook(
        name="tradfi_corporate_action",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="price_update",
                data={"symbol": "TSLA", "price": "800.00", "venue": "NASDAQ"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="corporate_action",
                data={"symbol": "TSLA", "action_type": "stock_split", "ratio": "2:1"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=2000,
                event_type="price_update",
                data={"symbol": "TSLA", "price": "400.00", "venue": "NASDAQ"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=3000,
                event_type="position_adjust",
                data={"symbol": "TSLA", "adjusted_quantity": "200", "adjusted_price": "400.00"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="action_type", operator="eq", expected="stock_split"),
            ScenarioAssertion(field="ratio", operator="eq", expected="2:1"),
            ScenarioAssertion(field="adjusted_quantity", operator="eq", expected="200"),
        ],
    )


def _circuit_breaker() -> ScenarioPlaybook:
    """Multi-level market-wide circuit breaker (Level 1 -> Level 2 -> Level 3)."""
    return ScenarioPlaybook(
        name="tradfi_circuit_breaker",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="price_update",
                data={"index": "SPX", "value": "5000.00", "change_pct": "-5.0"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=500,
                event_type="circuit_breaker",
                data={"level": "1", "trigger_pct": "-7", "halt_minutes": "15"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=900500,
                event_type="price_update",
                data={"index": "SPX", "value": "4400.00", "change_pct": "-12.0"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=901000,
                event_type="circuit_breaker",
                data={"level": "2", "trigger_pct": "-13", "halt_minutes": "15"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1801000,
                event_type="circuit_breaker",
                data={"level": "3", "trigger_pct": "-20", "halt_minutes": "0"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="level", operator="eq", expected="3"),
            ScenarioAssertion(field="trigger_pct", operator="eq", expected="-20"),
        ],
    )


def get_scenarios() -> list[ScenarioPlaybook]:
    """Return all TradFi scenarios."""
    return [
        _market_halt(),
        _session_transition(),
        _settlement(),
        _corporate_action(),
        _circuit_breaker(),
    ]
