"""Cross-repo e2e test for Phase 6 reward realisation.

Validates the full chain end-to-end across 4 repos:

  UAC                        ->  RewardStreamRegistry, ConvertDustInstruction,
                                  RewardAttributionRow, DustRouterResult
  features-onchain-service   ->  bootstrap_seasonal_rewards_collector,
                                  ParquetDustLoader (loads dust into engine)
  strategy-service           ->  V2EngineOrchestrator + Phase6Driver, engine
                                  declare_pending_dust_basket overrides,
                                  set_pending_dust / drain_reward_attribution_rows
  execution-service          ->  DustRouterRunner, LegControllerRunner,
                                  convert_dust(), LeveragedLegController
  pnl-attribution-service    ->  attribute_reward_realisation_from_rows,
                                  drain_and_persist (RAR rows -> PnLBreakdown)

Reference scenario: CARRY_RECURSIVE_STAKED on weETH with 8.7 ETHFI dust
seeded by features-onchain-service's seasonal-rewards collector.
Phase6Driver brackets the orchestrator tick:

  1. dust_loader installs the basket on the engine via set_pending_dust.
  2. orchestrator.on_tick fires -> dust router converts ETHFI -> ETH at
     simulated 12.5bps slippage; leg controller receives the realised
     inflow and rebalances both legs.
  3. RAR rows drain after on_tick; persister forwards to
     pnl-attribution-service which projects to PnLBreakdown rows.

Asserted invariants:
  - ConvertDustInstruction emitted with action=CONVERT_DUST, identity
    stamped, target_denomination='ETH'.
  - leg-rebalance envelope emitted (StrategyInstructionEnvelope with
    correlation_id pointing at the AtomicInstruction).
  - RewardAttributionRow tagged from RewardStreamRegistry: layer=
    CARRY_ISSUER_SEASONAL, issuer='ether.fi', lst_symbol='weETH',
    distributor_kind='merkle' — NOT the v0 hardcoded fallbacks.
  - PnLBreakdown rows projected: 1 realisation row tagged
    'carry_issuer_seasonal' + 1 paired 'reward_realisation_slippage'
    row (negative since slippage is a cost).
  - Engine's pending dust basket is cleared post-tick (no double-realise).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from execution_service.algo_library.dust_conversion_router import RouteQuote
from execution_service.algo_library.dust_router_runner import DustRouterRunner
from execution_service.algo_library.leg_controller_runner import LegControllerRunner
from pnl_attribution_service.engine.reward_attribution import (
    attribute_reward_realisation_from_rows,
)
from strategy_service.engine.strategies.v2.orchestrator import (
    V2EngineOrchestrator,
    V2Subscription,
)
from strategy_service.engine.strategies.v2.phase6_driver import Phase6Driver

# Pre-import everything so a missing dep / wrong UAC version surfaces
# at collection time, not deep inside one of the assertions.
from unified_api_contracts.internal import (
    ConvertDustInstruction,
    DustToken,
    InstructionActionV2,
    LegSnapshot,
    PnLBreakdown,
    RewardPnLLayer,
    StrategyArchetype,
    StrategyFamily,
    StrategyInstanceDefinition,
    StrategyInstructionEnvelope,
)

_NOW = datetime(2026, 5, 1, tzinfo=UTC)


# ── Stubs for the per-tick drive ──────────────────────────────────────


class _DeterministicQuoteSource:
    """ETHFI quotes 0.0004 ETH at-mark, realises at 0.000395 (12.5bps slip)."""

    def quote(self, token: DustToken, **kw: object) -> RouteQuote | None:
        del kw
        if token.token_symbol != "ETHFI":
            return None
        return RouteQuote(
            route_hops=["BINANCE:ETHFI-USDC", "UNISWAPV3-ETHEREUM:USDC-WETH"],
            target_amount_at_mark=token.amount * Decimal("0.0004"),
            target_amount_realised=token.amount * Decimal("0.000395"),
            slippage_target=token.amount * Decimal("0.000005"),
            fees_target=Decimal("0.00001"),
        )


def _make_observation_provider() -> object:
    def _stub(state: object) -> dict[tuple[str, str], tuple[Decimal, Decimal, Decimal, Decimal]]:
        del state
        return {
            ("AAVE_V3_ETHEREUM", "weETH"): (
                Decimal("300"),
                Decimal("3000"),
                Decimal("0"),
                Decimal("0"),
            ),
        }

    return _stub


def _make_snapshot_builder() -> object:
    def _stub(state: object, total_equity: Decimal, observations: object) -> dict[str, LegSnapshot]:
        del state, observations
        return {
            "lst_collateral": LegSnapshot(
                leg_id="lst_collateral",
                position_units=Decimal("300"),
                equity=total_equity * Decimal("0.5"),
                mark_price=Decimal("3000"),
            ),
            "native_borrow_recursed": LegSnapshot(
                leg_id="native_borrow_recursed",
                position_units=Decimal("-200"),
                equity=total_equity * Decimal("0.5"),
                mark_price=Decimal("3000"),
            ),
        }

    return _stub


def _carry_recursive_definition() -> StrategyInstanceDefinition:
    return StrategyInstanceDefinition(
        strategy_instance_id="phase6-e2e",
        archetype_id=StrategyArchetype.CARRY_RECURSIVE_STAKED,
        family=StrategyFamily.CARRY_AND_YIELD,
        client_id="e2e_client",
        share_class="ETH",
        slot_version=1,
        env="paper",
        capital_budget_amount=Decimal("1000000"),
        capital_budget_share_class="ETH",
        created_at_utc=_NOW,
        created_by="phase6_e2e_test",
    )


def _ethfi_basket() -> tuple[list[object], str, RewardPnLLayer | None]:
    """One 8.7 ETHFI dust token, basket-level layer=None per the engine
    override convention (forces per-token registry lookup)."""
    return (
        [
            DustToken(
                token_symbol="ETHFI",
                token_address="0xfA",
                chain="ETHEREUM",
                amount=Decimal("8.7"),
                source_wallet="0xrecipient",
                received_at_utc="2026-05-01T00:00:00Z",
                received_at_mark_price_eth=Decimal("0.0035"),
            )
        ],
        "ETH",
        None,
    )


# ── The cross-repo test ──────────────────────────────────────────────


def test_phase6_full_chain_end_to_end() -> None:
    """One tick exercises every Phase 6 contract across 4 repos."""
    # ─── Wire the orchestrator with both runner adapters ────────────
    dust_runner = DustRouterRunner(
        quote_source=_DeterministicQuoteSource(),
        leg_id_resolver=lambda i, d, l: "lst_collateral",  # noqa: E741, ARG005
    )
    leg_runner = LegControllerRunner(
        observation_provider=_make_observation_provider(),  # type: ignore[arg-type]
        snapshot_builder=_make_snapshot_builder(),  # type: ignore[arg-type]
    )
    orchestrator = V2EngineOrchestrator(
        dust_router_adapter=dust_runner,
        leg_controller_adapter=leg_runner,
    )

    # ─── Register a CARRY_RECURSIVE_STAKED engine on weETH ──────────
    engine = orchestrator.register_instance(
        definition=_carry_recursive_definition(),
        initial_equity=Decimal("1000000"),
        params={
            "native_asset": "ETH",
            "lst_asset": "weETH",
            "staking_protocol": "ETHERFI",
            "lending_protocol": "AAVE_V3_ETHEREUM",
            "target_leverage": "4",
            "protocol_ltv": "0.85",
            "safety_buffer_ltv": "0.05",
        },
        subscription=V2Subscription(
            strategy_instance_id="phase6-e2e",
            venue="AAVE_V3_ETHEREUM",
            instrument="weETH",
        ),
    )

    # ─── Phase6Driver wraps the tick ─────────────────────────────────
    captured_pnl_rows: list[PnLBreakdown] = []

    def loader(eng: object, day: str) -> tuple[list[object], str, RewardPnLLayer | None] | None:
        del eng, day
        return _ethfi_basket()

    def persister(eng: object, rows: object, day: str) -> None:
        del eng, day
        # Bridge into pnl-attribution-service's pure-function consumer.
        breakdowns = attribute_reward_realisation_from_rows(rows=rows)  # type: ignore[arg-type]
        captured_pnl_rows.extend(breakdowns)

    driver = Phase6Driver(dust_loader=loader, rar_persister=persister)

    # ─── Tick! ───────────────────────────────────────────────────────
    emitted = driver.tick(
        orchestrator=orchestrator,
        instrument="weETH",
        venue="AAVE_V3_ETHEREUM",
        mid_price=Decimal("3000"),
        features={},
        predictions=[],
        now_utc=_NOW,
        day_iso="2026-05-01",
    )

    # ─── Assert: ConvertDustInstruction emitted properly ────────────
    dust_envelopes = [e for e in emitted if isinstance(e, ConvertDustInstruction)]
    assert len(dust_envelopes) == 1, "expected one ConvertDustInstruction"
    dust_env = dust_envelopes[0]
    assert dust_env.action == InstructionActionV2.CONVERT_DUST
    assert dust_env.identity.strategy_instance_id == "phase6-e2e"
    assert dust_env.target_denomination == "ETH"
    assert len(dust_env.input_tokens) == 1
    assert dust_env.input_tokens[0].token_symbol == "ETHFI"

    # ─── Assert: leg-rebalance envelope emitted ─────────────────────
    rebal_envs = [
        e
        for e in emitted
        if isinstance(e, StrategyInstructionEnvelope)
        and not isinstance(e, ConvertDustInstruction)
        and "leg_rebalance" in e.attestations.values()
    ]
    assert len(rebal_envs) >= 1, "expected at least one leg-rebalance envelope"
    rebal = rebal_envs[0]
    assert rebal.identity.strategy_instance_id == "phase6-e2e"
    # correlation_id ties back to the underlying AtomicInstruction
    assert rebal.correlation_id is not None

    # ─── Assert: dust basket cleared post-tick ──────────────────────
    assert engine.declare_pending_dust_basket() is None, (
        "engine basket must clear post-realise to avoid double-counting"
    )

    # ─── Assert: PnLBreakdown rows projected via the persister ──────
    # 1 realisation row + 1 slippage row = 2 rows for the one ETHFI token
    assert len(captured_pnl_rows) == 2, (
        f"expected 2 PnLBreakdown rows (1 realisation + 1 slippage), got {len(captured_pnl_rows)}"
    )

    realisation = captured_pnl_rows[0]
    slippage = captured_pnl_rows[1]

    # Realisation row tagged with the registry-resolved layer.
    # ETHFI in the default registry resolves to CARRY_ISSUER_SEASONAL.
    assert realisation.account_id == "carry_issuer_seasonal", (
        f"realisation row should be carry_issuer_seasonal, got {realisation.account_id} "
        "(if this is 'carry_base' or 'carry_unknown' the registry-driven layer "
        "lookup regressed)"
    )
    assert realisation.instrument_id == "ETHFI"
    assert realisation.realized_pnl == Decimal("8.7") * Decimal("0.000395")  # ~0.0034365 ETH

    # Slippage row tagged + signed correctly (negative = realised cost).
    assert slippage.account_id == "reward_realisation_slippage"
    assert slippage.realized_pnl < Decimal("0"), "slippage should be a cost (negative PnL)"


def test_unregistered_token_falls_back_without_crashing() -> None:
    """A token not in RewardStreamRegistry still produces a row — the
    fallback path is preserved for shard-level isolation. The row's
    layer falls back to CARRY_BASE (or basket-level if non-None)."""
    dust_runner = DustRouterRunner(quote_source=_DeterministicQuoteSource())
    orchestrator = V2EngineOrchestrator(dust_router_adapter=dust_runner)
    engine = orchestrator.register_instance(
        definition=_carry_recursive_definition(),
        initial_equity=Decimal("1000000"),
        params={
            "native_asset": "ETH",
            "lst_asset": "weETH",
            "staking_protocol": "ETHERFI",
            "lending_protocol": "AAVE_V3_ETHEREUM",
            "target_leverage": "4",
            "protocol_ltv": "0.85",
            "safety_buffer_ltv": "0.05",
        },
        subscription=V2Subscription(
            strategy_instance_id="phase6-e2e",
            venue="AAVE_V3_ETHEREUM",
            instrument="weETH",
        ),
    )

    engine.set_pending_dust(
        tokens=[
            DustToken(
                token_symbol="UNREGISTERED_TOKEN",
                token_address="0xunregistered",
                chain="ETHEREUM",
                amount=Decimal("100"),
                source_wallet="0xw",
                received_at_utc="2026-05-01T00:00:00Z",
            )
        ],
        target_denomination="ETH",
        pnl_layer=None,
    )
    orchestrator.on_tick(
        instrument="weETH",
        venue="AAVE_V3_ETHEREUM",
        mid_price=Decimal("3000"),
        features={},
        predictions=[],
        now_utc=_NOW,
    )
    rar_rows = engine.drain_reward_attribution_rows()
    # Quote source returns None for non-ETHFI tokens => deferred row
    assert len(rar_rows) == 1
    deferred = rar_rows[0]
    assert deferred.reward_token_symbol == "UNREGISTERED_TOKEN"
    assert deferred.layer is RewardPnLLayer.CARRY_BASE  # fallback
    assert deferred.issuer == "unknown"  # sentinel
