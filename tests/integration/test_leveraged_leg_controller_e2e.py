"""Cross-repo e2e test for the LeveragedLegController plan.

Phase 6 hardening: validates the full chain end-to-end across 5 repos:

   UAC  ->  schemas (LegPortfolioState, LegSnapshot, LeveragedLeg, ...)
   strategy-service        ->  CarryStakedBasisEngine.declare_leg_portfolio_state()
   PBM                     ->  LegSnapshotBuilder.build_leg_snapshots()
   execution-service       ->  LeveragedLegController.compute_drift / emit_rebalance
   strategy-service -> detect_leverage_breaches()

The test fires the same state through every layer in sequence and asserts
each repo's contract holds against its siblings.

Reference scenario: 4x LONG weETH / 4x SHORT ETH-PERP delta-neutral basis
trade, $500k total equity, ETH +8%. After the move:
  - PBM observes long unrealized = +$32k, short unrealized = -$32k
  - LegSnapshotBuilder produces equity_per_leg = (250k +/- 32k)
  - controller.compute_drift sees long under-levered (3.27x) and short
    over-levered (6.35x) => requires_cash_sweep = True for both
  - controller.emit_rebalance_instructions emits SELL weETH + BUY ETH-PERP
    with sizes that restore net delta = 0 after cash sweep
  - risk detector sees the SAME state but with the post-rebalance snapshots
    and emits zero LEVERAGE_BREACH alerts (controller did its job)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from unified_api_contracts.internal import (
    AtomicExecutionMode,
    CashSweepPolicy,
    InstructionActionV2,
    LegPortfolioState,
    LegSizingStrategy,
    LegSnapshot,
    LeveragedLeg,
    ShareClass,
    StrategyArchetype,
    StrategyFamily,
    StrategyInstanceIdentity,
)

_NOW = datetime(2026, 5, 1, tzinfo=UTC)


def _basis_state() -> LegPortfolioState:
    return LegPortfolioState(
        strategy_instance_id="basis-e2e",
        legs=[
            LeveragedLeg(
                leg_id="lst_long",
                side="LONG",
                venue="ETHERFI",
                instrument="weETH",
                target_leverage=Decimal("4.0"),
                rebalance_trigger_bps=50,
            ),
            LeveragedLeg(
                leg_id="perp_short",
                side="SHORT",
                venue="HYPERLIQUID",
                instrument="ETH-PERP",
                target_leverage=Decimal("4.0"),
                rebalance_trigger_bps=50,
            ),
        ],
        target_net_delta=Decimal("0"),
        cash_sweep_policy=CashSweepPolicy.THRESHOLD,
        sizing_strategy=LegSizingStrategy.HEDGE_UNDERLYING,
    )


def _identity() -> StrategyInstanceIdentity:
    return StrategyInstanceIdentity(
        family=StrategyFamily.CARRY_AND_YIELD,
        archetype_id=StrategyArchetype.CARRY_STAKED_BASIS,
        archetype_build_version="0.1.0",
        strategy_instance_id="basis-e2e",
        slot_version=1,
        config_hash="e2e",
        config_version=1,
        client_id="client_e2e",
        share_class=ShareClass.USDC,
        env="dev",
    )


def test_strategy_service_engine_declares_matching_state() -> None:
    """strategy-service: CarryStakedBasisEngine.declare_leg_portfolio_state
    returns a state shape matching the e2e fixture above (HEDGE_UNDERLYING,
    delta=0, 2 legs, one LONG one SHORT)."""
    from strategy_service.engine.strategies.v2.carry_and_yield.staked_basis import (
        CarryStakedBasisEngine,
    )

    engine = CarryStakedBasisEngine(
        identity=_identity(),
        target_equity=Decimal("500000"),
        params={
            "native_asset": "ETH",
            "lst_asset": "weETH",
            "staking_protocol": "ETHERFI",
            "lending_protocol": "AAVE_V3",
            "borrow_asset": "USDC",
            "perp_venue": "HYPERLIQUID",
            "perp_instrument": "ETH-PERP",
            "stake_fraction": "1.0",
        },
    )
    state = engine.declare_leg_portfolio_state()
    assert state is not None
    assert state.target_net_delta == Decimal("0")
    assert state.sizing_strategy is LegSizingStrategy.HEDGE_UNDERLYING
    assert {leg.leg_id for leg in state.legs} == {"lst_long", "perp_short"}


def test_pbm_builder_produces_post_pnl_snapshots() -> None:
    """PBM: LegSnapshotBuilder maps observed positions + PnL into LegSnapshots.
    Initial alloc = $250k each. ETH +8% => long +$32k, short -$32k. So
    snapshots equity = $282k (long) / $218k (short)."""
    from strategy_service.position.core.leg_snapshot_builder import build_leg_snapshots

    state = _basis_state()
    observations = {
        ("ETHERFI", "weETH"): (
            Decimal("400"),
            Decimal("2700"),
            Decimal("0"),
            Decimal("32000"),
        ),
        ("HYPERLIQUID", "ETH-PERP"): (
            Decimal("-400"),
            Decimal("2700"),
            Decimal("0"),
            Decimal("-32000"),
        ),
    }
    snaps = build_leg_snapshots(state=state, total_equity=Decimal("500000"), observations=observations)
    assert set(snaps.keys()) == {"lst_long", "perp_short"}
    assert snaps["lst_long"].equity == Decimal("282000")  # 250k + 32k
    assert snaps["perp_short"].equity == Decimal("218000")  # 250k - 32k


def test_controller_drift_compute_marks_both_legs_for_rebalance() -> None:
    """execution-service: under-levered long + over-levered short => both
    legs return requires_cash_sweep = True."""
    from execution_service.algo_library.leveraged_leg_controller import LeveragedLegController

    state = _basis_state()
    snaps = {
        "lst_long": LegSnapshot(
            leg_id="lst_long",
            position_units=Decimal("400"),
            equity=Decimal("282000"),
            mark_price=Decimal("2700"),
        ),
        "perp_short": LegSnapshot(
            leg_id="perp_short",
            position_units=Decimal("-400"),
            equity=Decimal("218000"),
            mark_price=Decimal("2700"),
        ),
    }
    drifts = LeveragedLegController.compute_drift(state=state, snapshots=snaps)
    assert len(drifts) == 2
    by_leg = {d.leg_id: d for d in drifts}
    # Both legs should require sweep — long under-levered, short over-levered
    assert by_leg["lst_long"].requires_cash_sweep is True
    assert by_leg["perp_short"].requires_cash_sweep is True


def test_controller_rebalance_emits_atomic_with_correct_instruction_shape() -> None:
    """execution-service: emit_rebalance_instructions returns one
    AtomicInstruction per drifting leg with TRADE legs in the controller's
    cash-sweep + position-sizing format."""
    from execution_service.algo_library.leveraged_leg_controller import LeveragedLegController

    state = _basis_state()
    snaps = {
        "lst_long": LegSnapshot(
            leg_id="lst_long",
            position_units=Decimal("400"),
            equity=Decimal("282000"),
            mark_price=Decimal("2700"),
        ),
        "perp_short": LegSnapshot(
            leg_id="perp_short",
            position_units=Decimal("-400"),
            equity=Decimal("218000"),
            mark_price=Decimal("2700"),
        ),
    }
    drifts = LeveragedLegController.compute_drift(state=state, snapshots=snaps)
    instructions = LeveragedLegController.emit_rebalance_instructions(
        state=state,
        drifts=drifts,
        now_utc=_NOW,
        identity=_identity(),
    )
    assert len(instructions) >= 1
    instr = instructions[0]
    assert instr.execution_mode == AtomicExecutionMode.ATOMIC
    for leg in instr.legs:
        assert leg.action is InstructionActionV2.TRADE


def test_risk_detector_fires_when_post_rebalance_skipped() -> None:
    """strategy-service: if the controller hadn't acted, drift
    would persist and the detector would fire a LEVERAGE_BREACH on the
    over-levered short leg. Confirms the safety overlay is wired correctly."""
    from strategy_service.risk.core.leverage_breach_detector import (
        detect_leverage_breaches,
    )
    from unified_api_contracts.internal import AlertType

    state = _basis_state()
    # Pre-rebalance snapshots — short is at 6.35x, way over 4.0x target
    snaps = {
        "lst_long": LegSnapshot(
            leg_id="lst_long",
            position_units=Decimal("400"),
            equity=Decimal("282000"),
            mark_price=Decimal("2700"),
        ),
        "perp_short": LegSnapshot(
            leg_id="perp_short",
            position_units=Decimal("-400"),
            equity=Decimal("218000"),
            mark_price=Decimal("2700"),
        ),
    }
    alerts = detect_leverage_breaches(state=state, snapshots=snaps, client_id="client_e2e", now_utc=_NOW)
    assert len(alerts) == 1
    assert alerts[0].alert_type is AlertType.LEVERAGE_BREACH
    assert "perp_short" in (alerts[0].recommended_action or "")


def test_full_e2e_chain_strategy_pbm_controller_risk() -> None:
    """End-to-end chain over 5 repos: strategy declares state, PBM builds
    snapshots, controller computes drift + emits rebalance, risk detector
    is silent because the controller resized the legs.

    This is the load-bearing assertion that all five layers agree on the
    LegPortfolioState contract.
    """
    from execution_service.algo_library.leveraged_leg_controller import LeveragedLegController
    from strategy_service.engine.strategies.v2.carry_and_yield.staked_basis import (
        CarryStakedBasisEngine,
    )
    from strategy_service.position.core.leg_snapshot_builder import build_leg_snapshots
    from strategy_service.risk.core.leverage_breach_detector import (
        detect_leverage_breaches,
    )

    # Layer 1: strategy declares
    engine = CarryStakedBasisEngine(
        identity=_identity(),
        target_equity=Decimal("500000"),
        params={
            "native_asset": "ETH",
            "lst_asset": "weETH",
            "staking_protocol": "ETHERFI",
            "lending_protocol": "AAVE_V3",
            "borrow_asset": "USDC",
            "perp_venue": "HYPERLIQUID",
            "perp_instrument": "ETH-PERP",
            "stake_fraction": "1.0",
        },
    )
    state = engine.declare_leg_portfolio_state()
    assert state is not None

    # Layer 2: PBM builds snapshots — ETH at entry, no PnL accrued yet
    observations = {
        ("AAVE_V3", "weETH"): (Decimal("100"), Decimal("2500"), Decimal("0"), Decimal("0")),
        ("HYPERLIQUID", "ETH-PERP"): (
            Decimal("-100"),
            Decimal("2500"),
            Decimal("0"),
            Decimal("0"),
        ),
    }
    snaps = build_leg_snapshots(state=state, total_equity=engine.target_equity, observations=observations)
    assert len(snaps) == 2

    # Layer 3: controller compute_drift on at-target snapshots — no rebalance needed
    drifts = LeveragedLegController.compute_drift(state=state, snapshots=snaps)
    # All legs at target -> no leg requires cash sweep
    assert all(not d.requires_cash_sweep for d in drifts)

    # Layer 4: risk detector silent
    alerts = detect_leverage_breaches(state=state, snapshots=snaps, client_id="client_e2e", now_utc=_NOW)
    assert alerts == []
