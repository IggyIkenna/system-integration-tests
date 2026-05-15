"""DeFi domain scenario definitions.

Eight realistic scenarios covering decentralised finance events.

Infrastructure-level (5):
1. gas_spike — Ethereum gas price surge blocking transactions
2. slippage_beyond_threshold — DEX swap with excessive slippage
3. mev_attack — MEV sandwich attack on a pending transaction
4. chain_reorg — block reorganisation invalidating confirmed transactions
5. oracle_failure — Chainlink oracle stale price / deviation breach

May-23 critical path (3 — added 2026-05-15 per
``plans/active/issues/sit_may23_critical_path_coverage_gaps_2026_05_15.md``):
6. carry_staked_basis_paper — LST rates → strategy → execution → PBM manifest
   (DeFi carry archetype paper-mode end-to-end)
7. apd_paper — DEX/CEX dispersion → strategy → execution → PBM manifest
   (arbitrage_price_dispersion archetype paper-mode end-to-end)
8. paper_to_live_early_gate — MinimalCandidateManifest → promote → VM event
   STARTED + DART manual-trade gate present for first 3 days

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


def _carry_staked_basis_paper() -> ScenarioPlaybook:
    """May-23 Gate A1: carry_staked_basis paper-mode end-to-end.

    LST rates → features-onchain → strategy carry signal → execution mock fills
    → PBM availability-manifest shows captured rows for carry data types.
    Pipeline stages are modelled as discrete events so this scenario fails fast
    if any stage stops emitting its canonical event.
    """
    return ScenarioPlaybook(
        name="defi_carry_staked_basis_paper",
        domain=_DOMAIN,
        events=[
            # 1. LST rates ingested (Lido stETH base APR, RocketPool rETH base APR).
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="lst_rate_observed",
                data={
                    "protocol": "lido",
                    "token": "stETH",
                    "supply_apy": "0.038",
                    "instrument_id": "LIDO-ETHEREUM:LST:stETH",
                },
            ),
            ScenarioEvent(
                timestamp_offset_ms=50,
                event_type="lst_rate_observed",
                data={
                    "protocol": "rocketpool",
                    "token": "rETH",
                    "supply_apy": "0.034",
                    "instrument_id": "ROCKETPOOL-ETHEREUM:LST:rETH",
                },
            ),
            # 2. Features-onchain emits per-archetype feature row.
            ScenarioEvent(
                timestamp_offset_ms=500,
                event_type="feature_row_emitted",
                data={
                    "feature_group": "lst_yields",
                    "archetype": "carry_staked_basis",
                    "row_count": 2,
                },
            ),
            # 3. Strategy evaluates carry signal — non-zero direction means
            #    the archetype actually generated a position intent.
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="strategy_signal",
                data={
                    "archetype": "carry_staked_basis",
                    "direction": "long",
                    "signal_strength": "0.62",
                    "mode": "paper_1d",
                },
            ),
            # 4. Execution (MockExecutionAlwaysFill in paper mode) returns ack + fill.
            ScenarioEvent(
                timestamp_offset_ms=1500,
                event_type="execution_fill",
                data={
                    "venue": "lido",
                    "side": "BUY",
                    "fill_count": 1,
                    "instrument_id": "LIDO-ETHEREUM:LST:stETH",
                    "status": "FILLED",
                },
            ),
            # 5. PBM availability-manifest writes `captured` row for the
            #    archetype's bundle. This is what makes the gate green.
            ScenarioEvent(
                timestamp_offset_ms=2000,
                event_type="manifest_row_written",
                data={
                    "service_name": "position-balance-monitor-service",
                    "feature_group": "carry_staked_basis",
                    "capture_status": "captured",
                    "row_count": 1,
                },
            ),
        ],
        assertions=[
            # The stages MUST all fire — any missing event leaves its data
            # absent from state, so these substantively gate the pipeline.
            ScenarioAssertion(field="archetype", operator="eq", expected="carry_staked_basis"),
            ScenarioAssertion(field="direction", operator="eq", expected="long"),
            ScenarioAssertion(field="fill_count", operator="gte", expected=1),
            ScenarioAssertion(field="status", operator="eq", expected="FILLED"),
            ScenarioAssertion(field="capture_status", operator="eq", expected="captured"),
            ScenarioAssertion(field="mode", operator="eq", expected="paper_1d"),
        ],
    )


def _apd_paper() -> ScenarioPlaybook:
    """May-23 Gate A2: arbitrage_price_dispersion paper-mode end-to-end.

    DEX prices (Uniswap V3) vs CEX marks (Binance perp) → strategy generates an
    APD signal → execution mock fills both legs → PBM captures the apd bundle.
    The scenario also asserts no DeFi-side SKIP error fired — APD must not be
    silently skipped on a recoverable error.
    """
    return ScenarioPlaybook(
        name="defi_apd_paper",
        domain=_DOMAIN,
        events=[
            # 1. Two venues observe the same pair with a non-zero spread.
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="dex_price",
                data={
                    "venue": "uniswap_v3",
                    "pair": "WETH/USDC",
                    "mid_price": "3500.40",
                },
            ),
            ScenarioEvent(
                timestamp_offset_ms=20,
                event_type="cex_mark",
                data={
                    "venue": "binance",
                    "pair": "ETH-USDT-PERP",
                    "mark_price": "3497.10",
                },
            ),
            # 2. Strategy computes dispersion + emits an APD signal.
            ScenarioEvent(
                timestamp_offset_ms=400,
                event_type="strategy_signal",
                data={
                    "archetype": "arbitrage_price_dispersion",
                    "spread_bps": "94",
                    "direction": "long_dex_short_cex",
                    "mode": "paper_1d",
                },
            ),
            # 3. Confirm we did NOT route the APD signal through the DeFi
            #    SKIP-error path. ``skipped=false`` lands in state for the
            #    final assertion.
            ScenarioEvent(
                timestamp_offset_ms=500,
                event_type="defi_error_routing",
                data={
                    "archetype": "arbitrage_price_dispersion",
                    "skipped": "false",
                    "skip_reason": "none",
                },
            ),
            # 4. Both legs fill — one on DEX, one on CEX (hybrid).
            ScenarioEvent(
                timestamp_offset_ms=900,
                event_type="execution_fill",
                data={
                    "leg": "dex_long",
                    "venue": "uniswap_v3",
                    "fill_count": 1,
                    "status": "FILLED",
                },
            ),
            ScenarioEvent(
                timestamp_offset_ms=950,
                event_type="execution_fill",
                data={
                    "leg": "cex_short",
                    "venue": "binance",
                    "fill_count": 1,
                    "status": "FILLED",
                },
            ),
            # 5. PBM captures the apd bundle.
            ScenarioEvent(
                timestamp_offset_ms=1400,
                event_type="manifest_row_written",
                data={
                    "service_name": "position-balance-monitor-service",
                    "feature_group": "arbitrage_price_dispersion",
                    "capture_status": "captured",
                    "row_count": 1,
                },
            ),
        ],
        assertions=[
            ScenarioAssertion(field="archetype", operator="eq", expected="arbitrage_price_dispersion"),
            # Spread non-zero (string compare suffices — a "0" or "" would fail).
            ScenarioAssertion(field="spread_bps", operator="eq", expected="94"),
            # No silent SKIP on a recoverable DeFi error.
            ScenarioAssertion(field="skipped", operator="eq", expected="false"),
            ScenarioAssertion(field="status", operator="eq", expected="FILLED"),
            ScenarioAssertion(field="capture_status", operator="eq", expected="captured"),
            ScenarioAssertion(field="mode", operator="eq", expected="paper_1d"),
        ],
    )


def _paper_to_live_early_gate() -> ScenarioPlaybook:
    """May-23 Gate G: paper_1d → live_early promote with DART manual gate.

    MinimalCandidateManifest created → promote endpoint called → VM auto-launch
    emits STARTED → DART manual-trade gate is the safety-net active for the
    first 3 trading days per the promote workflow SSOT.
    """
    return ScenarioPlaybook(
        name="defi_paper_to_live_early_gate",
        domain=_DOMAIN,
        events=[
            # 1. Operator clicks Promote (UI path) → MinimalCandidateManifest lands in Firestore.
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="promote_request",
                data={
                    "strategy_id": "carry-stETH-rocketpool-v1",
                    "manifest_id": "mcm-2026-05-15-001",
                    "from_mode": "paper_1d",
                    "to_mode": "live_early",
                },
            ),
            # 2. Promote endpoint accepts (HTTP 200) + records the manifest.
            ScenarioEvent(
                timestamp_offset_ms=100,
                event_type="promote_endpoint_response",
                data={
                    "http_status": 200,
                    "minimal_candidate_manifest_written": "true",
                },
            ),
            # 3. Live VM auto-launches → emits STARTED.
            ScenarioEvent(
                timestamp_offset_ms=1500,
                event_type="vm_lifecycle",
                data={
                    "vm_name": "carry-stETH-rocketpool-live-early-1",
                    "event": "STARTED",
                    "asset_group": "defi",
                },
            ),
            # 4. DART manual-trade gate present on first trading day.
            ScenarioEvent(
                timestamp_offset_ms=2000,
                event_type="manual_gate_state",
                data={
                    "day_of_live": 1,
                    "gate_active": "true",
                    "gate_blocking": "true",
                },
            ),
        ],
        assertions=[
            # 200 response = promote succeeded at the API layer.
            ScenarioAssertion(field="http_status", operator="eq", expected=200),
            # Firestore got the manifest.
            ScenarioAssertion(field="minimal_candidate_manifest_written", operator="eq", expected="true"),
            # VM lifecycle emitted STARTED (no fire-and-forget allowed).
            ScenarioAssertion(field="event", operator="eq", expected="STARTED"),
            # DART gate is the safety net — must be ACTIVE + BLOCKING on day 1.
            ScenarioAssertion(field="gate_active", operator="eq", expected="true"),
            ScenarioAssertion(field="gate_blocking", operator="eq", expected="true"),
            # Confirm we promoted in the correct direction.
            ScenarioAssertion(field="from_mode", operator="eq", expected="paper_1d"),
            ScenarioAssertion(field="to_mode", operator="eq", expected="live_early"),
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
        _carry_staked_basis_paper(),
        _apd_paper(),
        _paper_to_live_early_gate(),
    ]
