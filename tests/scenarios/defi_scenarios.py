"""DeFi domain scenario definitions.

Five realistic scenarios covering decentralised finance events:
1. gas_spike — Ethereum gas price surge blocking transactions
2. slippage_beyond_threshold — DEX swap with excessive slippage
3. mev_attack — MEV sandwich attack on a pending transaction
4. chain_reorg — block reorganisation invalidating confirmed transactions
5. oracle_failure — Chainlink oracle stale price / deviation breach

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

_DOMAIN = ScenarioDomain.DEFI


def _gas_spike() -> ScenarioPlaybook:
    """Ethereum gas price surge from 30 to 500 gwei, blocking pending transactions."""
    return ScenarioPlaybook(
        name="defi_gas_spike",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="gas_price",
                data={"chain": "ethereum", "gas_gwei": "30", "base_fee": "25"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=12000,
                event_type="gas_price",
                data={"chain": "ethereum", "gas_gwei": "500", "base_fee": "480"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=12500,
                event_type="tx_blocked",
                data={"tx_hash": "0xabc123", "reason": "gas_too_low", "required_gwei": "500"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=24000,
                event_type="gas_price",
                data={"chain": "ethereum", "gas_gwei": "45", "base_fee": "38"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=24500,
                event_type="tx_retry",
                data={"tx_hash": "0xabc123", "status": "submitted", "gas_gwei": "50"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="status", operator="eq", expected="submitted"),
            ScenarioAssertion(field="chain", operator="eq", expected="ethereum"),
        ],
    )


def _slippage_beyond_threshold() -> ScenarioPlaybook:
    """DEX swap where actual slippage exceeds the configured threshold."""
    return ScenarioPlaybook(
        name="defi_slippage_beyond_threshold",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="quote",
                data={"protocol": "uniswap_v3", "pair": "ETH/USDC", "expected_price": "3500.00"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=500,
                event_type="swap_execute",
                data={"protocol": "uniswap_v3", "pair": "ETH/USDC", "actual_price": "3465.00", "slippage_bps": "100"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=600,
                event_type="slippage_alert",
                data={"pair": "ETH/USDC", "threshold_bps": "50", "actual_bps": "100", "action": "revert"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="swap_reverted",
                data={"pair": "ETH/USDC", "reason": "slippage_exceeded", "refund_status": "complete"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="action", operator="eq", expected="revert"),
            ScenarioAssertion(field="refund_status", operator="eq", expected="complete"),
            ScenarioAssertion(field="actual_bps", operator="eq", expected="100"),
        ],
    )


def _mev_attack() -> ScenarioPlaybook:
    """MEV sandwich attack: frontrun + backrun around a victim swap."""
    return ScenarioPlaybook(
        name="defi_mev_attack",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="mempool_detect",
                data={"tx_hash": "0xvictim", "type": "swap", "pair": "WETH/USDC", "amount": "10.0"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=100,
                event_type="mev_frontrun",
                data={"attacker_tx": "0xfront", "pair": "WETH/USDC", "direction": "buy"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=200,
                event_type="mev_backrun",
                data={"attacker_tx": "0xback", "pair": "WETH/USDC", "direction": "sell", "profit_usd": "45.00"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=300,
                event_type="mev_protection",
                data={"action": "use_private_mempool", "provider": "flashbots", "protection": "enabled"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="protection", operator="eq", expected="enabled"),
            ScenarioAssertion(field="provider", operator="eq", expected="flashbots"),
        ],
    )


def _chain_reorg() -> ScenarioPlaybook:
    """Block reorganisation (2-block deep) invalidating a confirmed transaction."""
    return ScenarioPlaybook(
        name="defi_chain_reorg",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="block_confirmed",
                data={"chain": "ethereum", "block_number": "19000000", "tx_hash": "0xtx1"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=12000,
                event_type="block_confirmed",
                data={"chain": "ethereum", "block_number": "19000001", "tx_hash": "0xtx2"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=24000,
                event_type="chain_reorg",
                data={"chain": "ethereum", "reorg_depth": "2", "new_head": "19000001", "orphaned_blocks": "2"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=24500,
                event_type="tx_status_change",
                data={"tx_hash": "0xtx1", "old_status": "confirmed", "new_status": "pending", "action": "resubmit"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="reorg_depth", operator="eq", expected="2"),
            ScenarioAssertion(field="new_status", operator="eq", expected="pending"),
            ScenarioAssertion(field="action", operator="eq", expected="resubmit"),
        ],
    )


def _oracle_failure() -> ScenarioPlaybook:
    """Chainlink oracle reports stale price or deviation breach."""
    return ScenarioPlaybook(
        name="defi_oracle_failure",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="oracle_update",
                data={"oracle": "chainlink", "feed": "ETH/USD", "price": "3500.00", "heartbeat_ok": "true"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=3600000,
                event_type="oracle_stale",
                data={"oracle": "chainlink", "feed": "ETH/USD", "last_update_age_s": "7200", "heartbeat_ok": "false"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=3600500,
                event_type="risk_action",
                data={"action": "pause_protocol", "reason": "oracle_stale", "feed": "ETH/USD"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=7200000,
                event_type="oracle_update",
                data={"oracle": "chainlink", "feed": "ETH/USD", "price": "3480.00", "heartbeat_ok": "true"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="action", operator="eq", expected="pause_protocol"),
            ScenarioAssertion(field="reason", operator="eq", expected="oracle_stale"),
            ScenarioAssertion(field="heartbeat_ok", operator="eq", expected="true"),
        ],
    )


def get_scenarios() -> list[ScenarioPlaybook]:
    """Return all DeFi scenarios."""
    return [
        _gas_spike(),
        _slippage_beyond_threshold(),
        _mev_attack(),
        _chain_reorg(),
        _oracle_failure(),
    ]
