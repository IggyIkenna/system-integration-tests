"""CeFi domain scenario definitions.

Five realistic scenarios covering centralised exchange events:
1. liquidation_cascade — cascading liquidations driving price down
2. funding_rate_spike — extreme funding rate triggering basis unwind
3. exchange_downtime — exchange API going offline mid-position
4. flash_crash — rapid price drop and recovery within seconds
5. ops_24_7 — continuous 24/7 session with midnight UTC rollover

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

_DOMAIN = ScenarioDomain.CEFI


def _liquidation_cascade() -> ScenarioPlaybook:
    """Cascading liquidations: price drops trigger margin calls, driving further drops."""
    return ScenarioPlaybook(
        name="cefi_liquidation_cascade",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="price_update",
                data={"symbol": "BTC-USDT", "price": "65000.00", "venue": "BINANCE"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=500,
                event_type="liquidation",
                data={"symbol": "BTC-USDT", "side": "long", "quantity": "50.0", "trigger_price": "64500.00"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="price_update",
                data={"symbol": "BTC-USDT", "price": "63000.00", "venue": "BINANCE"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1500,
                event_type="liquidation",
                data={"symbol": "BTC-USDT", "side": "long", "quantity": "120.0", "trigger_price": "63200.00"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=2000,
                event_type="price_update",
                data={"symbol": "BTC-USDT", "price": "60000.00", "venue": "BINANCE"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="price", operator="eq", expected="60000.00"),
            ScenarioAssertion(field="side", operator="eq", expected="long"),
            ScenarioAssertion(field="quantity", operator="eq", expected="120.0"),
        ],
    )


def _funding_rate_spike() -> ScenarioPlaybook:
    """Extreme positive funding rate triggering basis trade unwind."""
    return ScenarioPlaybook(
        name="cefi_funding_rate_spike",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="funding_rate",
                data={"symbol": "ETH-USDT", "rate": "0.0001", "venue": "BINANCE"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=28800000,
                event_type="funding_rate",
                data={"symbol": "ETH-USDT", "rate": "0.0050", "venue": "BINANCE"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=28800500,
                event_type="risk_alert",
                data={"alert_type": "funding_extreme", "symbol": "ETH-USDT", "action": "reduce_exposure"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=28801000,
                event_type="position_reduce",
                data={"symbol": "ETH-USDT", "reduce_pct": "50", "reason": "funding_rate_spike"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="rate", operator="eq", expected="0.0050"),
            ScenarioAssertion(field="action", operator="eq", expected="reduce_exposure"),
            ScenarioAssertion(field="reduce_pct", operator="eq", expected="50"),
        ],
    )


def _exchange_downtime() -> ScenarioPlaybook:
    """Exchange API goes offline while holding open positions."""
    return ScenarioPlaybook(
        name="cefi_exchange_downtime",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="price_update",
                data={"symbol": "BTC-USDT", "price": "65000.00", "venue": "BYBIT"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="connectivity",
                data={"venue": "BYBIT", "status": "offline", "error": "503_service_unavailable"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=5000,
                event_type="failover",
                data={"from_venue": "BYBIT", "to_venue": "BINANCE", "symbol": "BTC-USDT", "status": "active"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=30000,
                event_type="connectivity",
                data={"venue": "BYBIT", "status": "online", "error": ""},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="status", operator="eq", expected="online"),
            ScenarioAssertion(field="to_venue", operator="eq", expected="BINANCE"),
        ],
    )


def _flash_crash() -> ScenarioPlaybook:
    """Flash crash: price drops 15% in seconds and recovers within a minute."""
    return ScenarioPlaybook(
        name="cefi_flash_crash",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="price_update",
                data={"symbol": "SOL-USDT", "price": "150.00", "venue": "BINANCE"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=500,
                event_type="price_update",
                data={"symbol": "SOL-USDT", "price": "127.50", "venue": "BINANCE"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=800,
                event_type="anomaly_detected",
                data={"symbol": "SOL-USDT", "type": "flash_crash", "drop_pct": "-15.0"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=2000,
                event_type="price_update",
                data={"symbol": "SOL-USDT", "price": "145.00", "venue": "BINANCE"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=60000,
                event_type="price_update",
                data={"symbol": "SOL-USDT", "price": "149.00", "venue": "BINANCE"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="type", operator="eq", expected="flash_crash"),
            ScenarioAssertion(field="drop_pct", operator="eq", expected="-15.0"),
            ScenarioAssertion(field="price", operator="eq", expected="149.00"),
        ],
    )


def _ops_24_7() -> ScenarioPlaybook:
    """Continuous 24/7 operation across midnight UTC rollover."""
    return ScenarioPlaybook(
        name="cefi_24_7_ops",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="session_info",
                data={"time": "23:59:00Z", "session": "continuous", "day": "monday"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=60000,
                event_type="funding_rate",
                data={"symbol": "BTC-USDT", "rate": "0.0003", "venue": "BINANCE"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=61000,
                event_type="session_info",
                data={"time": "00:01:00Z", "session": "continuous", "day": "tuesday"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=62000,
                event_type="daily_pnl_reset",
                data={"pnl_yesterday": "1250.00", "pnl_today": "0.00", "rollover": "complete"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="session", operator="eq", expected="continuous"),
            ScenarioAssertion(field="rollover", operator="eq", expected="complete"),
            ScenarioAssertion(field="day", operator="eq", expected="tuesday"),
        ],
    )


def get_scenarios() -> list[ScenarioPlaybook]:
    """Return all CeFi scenarios."""
    return [
        _liquidation_cascade(),
        _funding_rate_spike(),
        _exchange_downtime(),
        _flash_crash(),
        _ops_24_7(),
    ]
