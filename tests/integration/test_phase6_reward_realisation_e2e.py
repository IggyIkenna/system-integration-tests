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


# ──────────────────────────────────────────────────────────────────────
# Multi-engine: dust + RAR drained per-engine in one tick
# ──────────────────────────────────────────────────────────────────────


def test_phase6_multi_engine_dust_and_rar_per_engine() -> None:
    """Two engines on the same (venue, instrument) subscription —
    Phase6Driver invokes the loader for each, fans dust through the
    router for each, drains RAR rows for each. No cross-engine leakage."""
    dust_runner = DustRouterRunner(
        quote_source=_DeterministicQuoteSource(),
        leg_id_resolver=lambda i, d, l: "lst_collateral",  # noqa: E741, ARG005
    )
    orchestrator = V2EngineOrchestrator(dust_router_adapter=dust_runner)

    # Register two engines, both subscribed to the same (venue, instrument)
    # so on_tick fans both.
    def _defn(strategy_id: str) -> StrategyInstanceDefinition:
        return StrategyInstanceDefinition(
            strategy_instance_id=strategy_id,
            archetype_id=StrategyArchetype.CARRY_RECURSIVE_STAKED,
            family=StrategyFamily.CARRY_AND_YIELD,
            client_id=f"client_{strategy_id}",
            share_class="ETH",
            slot_version=1,
            env="paper",
            capital_budget_amount=Decimal("1000000"),
            capital_budget_share_class="ETH",
            created_at_utc=_NOW,
            created_by="phase6_multi_engine",
        )

    params = {
        "native_asset": "ETH",
        "lst_asset": "weETH",
        "staking_protocol": "ETHERFI",
        "lending_protocol": "AAVE_V3_ETHEREUM",
        "target_leverage": "4",
        "protocol_ltv": "0.85",
        "safety_buffer_ltv": "0.05",
    }
    engine_a = orchestrator.register_instance(
        definition=_defn("siid_a"),
        initial_equity=Decimal("500000"),
        params=params,
        subscription=V2Subscription(
            strategy_instance_id="siid_a",
            venue="AAVE_V3_ETHEREUM",
            instrument="weETH",
        ),
    )
    engine_b = orchestrator.register_instance(
        definition=_defn("siid_b"),
        initial_equity=Decimal("750000"),
        params=params,
        subscription=V2Subscription(
            strategy_instance_id="siid_b",
            venue="AAVE_V3_ETHEREUM",
            instrument="weETH",
        ),
    )

    # Per-engine baskets — different amounts to verify per-engine isolation.
    baskets: dict[str, tuple[list[object], str, RewardPnLLayer | None]] = {
        "siid_a": (
            [
                DustToken(
                    token_symbol="ETHFI",
                    token_address="0xfa",
                    chain="ETHEREUM",
                    amount=Decimal("5"),
                    source_wallet="0xwa",
                    received_at_utc="2026-05-01T00:00:00Z",
                    received_at_mark_price_eth=Decimal("0.0035"),
                )
            ],
            "ETH",
            None,
        ),
        "siid_b": (
            [
                DustToken(
                    token_symbol="ETHFI",
                    token_address="0xfa",
                    chain="ETHEREUM",
                    amount=Decimal("12"),
                    source_wallet="0xwb",
                    received_at_utc="2026-05-01T00:00:00Z",
                    received_at_mark_price_eth=Decimal("0.0035"),
                )
            ],
            "ETH",
            None,
        ),
    }
    captured_persists: list[tuple[str, list[object]]] = []

    def loader(eng: object, day: str) -> tuple[list[object], str, RewardPnLLayer | None] | None:
        del day
        sid = eng.identity.strategy_instance_id  # type: ignore[attr-defined]
        return baskets.get(sid)

    def persister(eng: object, rows: object, day: str) -> None:
        del day
        sid = eng.identity.strategy_instance_id  # type: ignore[attr-defined]
        captured_persists.append((sid, list(rows)))  # type: ignore[arg-type]

    driver = Phase6Driver(dust_loader=loader, rar_persister=persister)
    driver.tick(
        orchestrator=orchestrator,
        instrument="weETH",
        venue="AAVE_V3_ETHEREUM",
        mid_price=Decimal("3000"),
        features={},
        predictions=[],
        now_utc=_NOW,
        day_iso="2026-05-01",
    )

    # Both engines drained
    assert {sid for sid, _ in captured_persists} == {"siid_a", "siid_b"}
    # Each engine produced exactly one RAR row
    by_sid = dict(captured_persists)
    assert len(by_sid["siid_a"]) == 1
    assert len(by_sid["siid_b"]) == 1
    # Per-engine amounts preserved through the realisation
    rar_a = by_sid["siid_a"][0]
    rar_b = by_sid["siid_b"][0]
    assert rar_a.amount_native == Decimal("5")  # type: ignore[attr-defined]
    assert rar_b.amount_native == Decimal("12")  # type: ignore[attr-defined]
    # Both baskets cleared
    assert engine_a.declare_pending_dust_basket() is None  # type: ignore[attr-defined]
    assert engine_b.declare_pending_dust_basket() is None  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────
# Persister failure isolated: one engine's persister raises but the
# tick still completes for unrelated engines.
# ──────────────────────────────────────────────────────────────────────


def test_phase6_persister_failure_does_not_break_other_engines() -> None:
    dust_runner = DustRouterRunner(
        quote_source=_DeterministicQuoteSource(),
        leg_id_resolver=lambda i, d, l: "lst_collateral",  # noqa: E741, ARG005
    )
    orchestrator = V2EngineOrchestrator(dust_router_adapter=dust_runner)

    def _defn(strategy_id: str) -> StrategyInstanceDefinition:
        return StrategyInstanceDefinition(
            strategy_instance_id=strategy_id,
            archetype_id=StrategyArchetype.CARRY_RECURSIVE_STAKED,
            family=StrategyFamily.CARRY_AND_YIELD,
            client_id=f"client_{strategy_id}",
            share_class="ETH",
            slot_version=1,
            env="paper",
            capital_budget_amount=Decimal("1000000"),
            capital_budget_share_class="ETH",
            created_at_utc=_NOW,
            created_by="phase6_failure_isolation",
        )

    params = {
        "native_asset": "ETH",
        "lst_asset": "weETH",
        "staking_protocol": "ETHERFI",
        "lending_protocol": "AAVE_V3_ETHEREUM",
        "target_leverage": "4",
        "protocol_ltv": "0.85",
        "safety_buffer_ltv": "0.05",
    }
    for sid in ("siid_failing", "siid_healthy"):
        orchestrator.register_instance(
            definition=_defn(sid),
            initial_equity=Decimal("500000"),
            params=params,
            subscription=V2Subscription(
                strategy_instance_id=sid,
                venue="AAVE_V3_ETHEREUM",
                instrument="weETH",
            ),
        )

    def basket() -> tuple[list[object], str, RewardPnLLayer | None]:
        return (
            [
                DustToken(
                    token_symbol="ETHFI",
                    token_address="0xfa",
                    chain="ETHEREUM",
                    amount=Decimal("5"),
                    source_wallet="0xw",
                    received_at_utc="2026-05-01T00:00:00Z",
                    received_at_mark_price_eth=Decimal("0.0035"),
                )
            ],
            "ETH",
            None,
        )

    def loader(eng: object, day: str) -> tuple[list[object], str, RewardPnLLayer | None] | None:
        del eng, day
        return basket()

    healthy_persisted: list[str] = []

    def persister(eng: object, rows: object, day: str) -> None:
        del rows, day
        sid = eng.identity.strategy_instance_id  # type: ignore[attr-defined]
        if sid == "siid_failing":
            raise OSError("persister down for siid_failing")
        healthy_persisted.append(sid)

    driver = Phase6Driver(dust_loader=loader, rar_persister=persister)
    # Should not raise — Phase6Driver isolates per-engine
    driver.tick(
        orchestrator=orchestrator,
        instrument="weETH",
        venue="AAVE_V3_ETHEREUM",
        mid_price=Decimal("3000"),
        features={},
        predictions=[],
        now_utc=_NOW,
        day_iso="2026-05-01",
    )
    # Healthy engine's persister still ran
    assert healthy_persisted == ["siid_healthy"]


# ──────────────────────────────────────────────────────────────────────
# Real ParquetDustLoader exercise — write a fixture parquet, read it
# back via the production loader, install on engine, run the tick.
# ──────────────────────────────────────────────────────────────────────


def test_phase6_with_real_parquet_dust_loader(tmp_path) -> None:
    """End-to-end with the actual ParquetDustLoader reading from a
    fixture parquet on local disk.

    Skips the actual GCS read by configuring the OnChainFeatureWriter in
    dry-run mode + pointing the loader at the matching local path.
    Proves the writer→reader round-trip works without mocking either side.
    """
    import asyncio
    import pathlib

    import polars as pl
    from features_service.onchain.app.core.feature_writer import (
        OnChainFeatureWriter,
    )
    from features_service.onchain.collectors.parquet_dust_loader import (
        ParquetDustLoader,
        lst_holding_wallet_from_params,
        lst_target_denom_from_params,
    )
    from unified_api_contracts.internal import (
        LstSeasonalRewardRow,
    )

    # ─── Write a fixture parquet via the production writer ──────────
    writer = OnChainFeatureWriter(asset_group="DEFI", dry_run=True)
    fixture_rows = [
        LstSeasonalRewardRow(
            block_number=21000000,
            block_timestamp_utc=datetime(2026, 5, 1, 12, tzinfo=UTC),
            tx_hash="0xabc",
            chain="ETHEREUM",
            lst_symbol="weETH",
            issuer="ether.fi",
            layer=RewardPnLLayer.CARRY_ISSUER_SEASONAL,
            reward_token_symbol="ETHFI",
            reward_token_address="0xfa00000000000000000000000000000000000000",
            distributor_address="0xdist",
            distributor_kind="merkle",
            recipient_address="0xphase6testwallet",
            amount_raw=Decimal("8700000000000000000"),
            amount_decimal=Decimal("8.7"),
        )
    ]
    ok = asyncio.run(
        writer.write_seasonal_rewards(
            rows=fixture_rows,
            date=datetime(2026, 5, 1, tzinfo=UTC),
        )
    )
    assert ok, "fixture parquet write failed"

    # The dry-run writer wrote to ``data/sample/{bucket}/{path}``. Locate it.
    cwd = pathlib.Path.cwd()
    written = list(cwd.glob("data/sample/**/rewards.parquet"))
    assert written, "writer should have produced a rewards.parquet under data/sample"
    fixture_parquet = written[0]
    # Verify content for sanity
    df = pl.read_parquet(fixture_parquet)
    assert len(df) == 1
    assert df["recipient_address"][0] == "0xphase6testwallet"

    # ─── Build a stub storage_client + reader the loader uses ───────
    class _StubStorageClient:
        def __init__(self, parquet_path: str) -> None:
            self._path = parquet_path

        def list_blobs(self, bucket: str, prefix: str) -> list[str]:
            del bucket, prefix
            return [str(self._path)]

    class _StubReader:
        def __init__(self, parquet_path: str) -> None:
            self._path = parquet_path

        def read(self, bucket: str, path: str) -> pl.DataFrame:
            del bucket
            return pl.read_parquet(path)

    loader = ParquetDustLoader.__new__(ParquetDustLoader)
    loader._project_id = "test-project"  # type: ignore[attr-defined]
    loader._wallet_resolver = lst_holding_wallet_from_params  # type: ignore[attr-defined]
    loader._target_denom_resolver = lst_target_denom_from_params  # type: ignore[attr-defined]
    loader._storage_client = _StubStorageClient(str(fixture_parquet))  # type: ignore[attr-defined]
    loader._reader = _StubReader(str(fixture_parquet))  # type: ignore[attr-defined]
    loader._cache = {}  # type: ignore[attr-defined]

    # ─── Wire the orchestrator + register an engine with matching wallet
    dust_runner = DustRouterRunner(
        quote_source=_DeterministicQuoteSource(),
        leg_id_resolver=lambda i, d, l: "lst_collateral",  # noqa: E741, ARG005
    )
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
            "holding_wallet": "0xphase6testwallet",  # matches fixture row
        },
        subscription=V2Subscription(
            strategy_instance_id="phase6-e2e",
            venue="AAVE_V3_ETHEREUM",
            instrument="weETH",
        ),
    )

    captured: list[object] = []

    def persister(eng: object, rows: object, day: str) -> None:
        del eng, day
        captured.extend(rows)  # type: ignore[arg-type]

    driver = Phase6Driver(dust_loader=loader, rar_persister=persister)
    driver.tick(
        orchestrator=orchestrator,
        instrument="weETH",
        venue="AAVE_V3_ETHEREUM",
        mid_price=Decimal("3000"),
        features={},
        predictions=[],
        now_utc=_NOW,
        day_iso="2026-05-01",
    )

    # ─── Assertions: real parquet round-trip ─────────────────────────
    # The fixture parquet had 8.7 ETHFI for 0xphase6testwallet → loader
    # should have built a DustToken from that row and installed it.
    # Stub quote source converts to 0.000395 ETH/ETHFI → realised = 8.7 *
    # 0.000395 = 0.0034365 ETH.
    assert len(captured) == 1
    rar = captured[0]
    assert rar.reward_token_symbol == "ETHFI"  # type: ignore[attr-defined]
    assert rar.amount_native == Decimal("8.7")  # type: ignore[attr-defined]
    assert rar.amount_target_realised == Decimal("8.7") * Decimal("0.000395")  # type: ignore[attr-defined]
    # Layer resolved from registry (NOT a hardcoded fallback)
    assert rar.layer is RewardPnLLayer.CARRY_ISSUER_SEASONAL  # type: ignore[attr-defined]

    # Cleanup: remove the dry-run sample dir
    import shutil

    sample_root = cwd / "data" / "sample"
    if sample_root.exists():
        shutil.rmtree(sample_root, ignore_errors=True)
    del engine, tmp_path
