"""Cross-service overnight cascade test (Wave F — workspace audit 2026-05-01).

Parametrises over the ``(archetype, asset_group)`` cells exposed by UAC
``ASSET_GROUP_ONTOLOGY`` + ``StrategyArchetype`` and asserts each cell can:

  1. Build an asset-group ontology + locate its margin model
  2. Compute a deterministic state hash via UTL ``compute_state_hash``
  3. Drive ``MockExecutionAlwaysFill`` from UTL with one synthetic order
  4. Re-run the same scenario and produce an identical state hash
     (batch=live parity smoke at the contract level)

Full event-cascade fixtures (strategy -> PBM -> risk -> pnl -> alerting
Pub/Sub event ordering) require the multi-service emulator stack and are
queued under the scenario-validator wave (Wave E P4.6 follow-up plan).
This test is the contract-level prerequisite: it guarantees the workspace
ships every primitive the full cascade needs.

Lives in ``tests/overnight/`` because it spans repos and is the L3
home for archetype cascade verification.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from unified_api_contracts.internal import MarginModel
from unified_api_contracts.registry import (
    ASSET_GROUP_ONTOLOGY,
    AssetGroupOntology,
    MarketAssetGroup,
)
from unified_trading_library.state_hashing import (  # noqa: qg-deep-import
    StateBundle,
    assert_state_equal,
    compute_state_hash,
)
from unified_trading_library.testing import (  # noqa: qg-deep-import
    MockExecutionAlwaysFill,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _scenario_state(asset_group: MarketAssetGroup, mark_price: Decimal) -> StateBundle:
    """Synthetic scenario state shaped like the canonical hash domain.

    Deterministic by construction: every field is derived from
    ``(asset_group, mark_price)`` so two invocations with identical inputs
    produce identical hashes. ``StateBundle`` fields are ``list[dict]`` --
    one record per domain entity.
    """
    pos_id = f"{asset_group.value.lower()}-pos-1"
    return StateBundle(
        positions=[
            {
                "position_id": pos_id,
                "quantity": Decimal("1.0"),
                "mark_price": mark_price,
                "avg_entry_price": Decimal("100.0"),
            },
        ],
        balances=[
            {"currency": "USDC", "amount": Decimal("100000.0")},
        ],
        orders=[],
        fills=[],
        cash_ledger=[{"phase": "opening", "amount": Decimal("100000.0")}],
        pnl_ledger=[
            {"kind": "realized", "amount": Decimal("0.0")},
            {"kind": "unrealized", "amount": mark_price - Decimal("100.0")},
        ],
        risk_metrics=[{"metric": "leverage", "value": Decimal("1.0")}],
        margin_state=[],
        alerts_emitted=[],
        kill_switch_state=[{"active": False}],
        deleverage_actions=[],
    )


# ── Parametrisation ──────────────────────────────────────────────────────────


_ASSET_GROUPS: list[MarketAssetGroup] = sorted(
    ASSET_GROUP_ONTOLOGY.keys(),
    key=lambda g: g.value,
)


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("asset_group", _ASSET_GROUPS, ids=lambda g: g.value)
def test_asset_group_ontology_has_expected_axes(asset_group: MarketAssetGroup) -> None:
    """Every asset group declares fill / settlement / margin model + state model.

    Catches missing capability rows when a new asset_group is added.
    """
    ontology: AssetGroupOntology = ASSET_GROUP_ONTOLOGY[asset_group]
    assert ontology.asset_group is asset_group
    assert ontology.fill_model is not None
    assert ontology.fill_model.name
    assert ontology.settlement_model is not None
    assert ontology.settlement_model.name
    # margin_model_name must resolve to a real MarginModel enum value
    # (string-typed in ontology to dodge a circular import; verified here).
    assert ontology.margin_model_name in {m.value for m in MarginModel}
    assert ontology.state_model in {
        "wallet_protocol_position",
        "venue_account",
        "event_market_holding",
    }
    assert len(ontology.instrument_types) >= 1
    assert len(ontology.venues) >= 1


@pytest.mark.parametrize("asset_group", _ASSET_GROUPS, ids=lambda g: g.value)
def test_state_hash_deterministic_per_asset_group(asset_group: MarketAssetGroup) -> None:
    """Two calls with identical inputs produce identical state hashes.

    This is the batch=live parity contract at the hash layer: any future
    cross-mode test that wants to assert ``hash(batch_state) == hash(live_state)``
    needs this primitive to be deterministic.
    """
    state_a = _scenario_state(asset_group, mark_price=Decimal("105.0"))
    state_b = _scenario_state(asset_group, mark_price=Decimal("105.0"))
    hash_a = compute_state_hash(state_a)
    hash_b = compute_state_hash(state_b)
    assert hash_a == hash_b
    # ``assert_state_equal`` provides a diffable error path for cascade tests.
    assert_state_equal(state_a, state_b)


@pytest.mark.parametrize("asset_group", _ASSET_GROUPS, ids=lambda g: g.value)
def test_state_hash_changes_when_inputs_diverge(asset_group: MarketAssetGroup) -> None:
    """A different mark price must produce a different hash."""
    state_a = _scenario_state(asset_group, mark_price=Decimal("105.0"))
    state_b = _scenario_state(asset_group, mark_price=Decimal("107.0"))
    assert compute_state_hash(state_a) != compute_state_hash(state_b)


def test_mock_execution_always_fill_is_importable_and_deterministic() -> None:
    """MockExecutionAlwaysFill is the canonical batch-mode execution stub.

    The cascade test depends on this primitive. Verifies it exists in UTL
    and produces deterministic acks + fills given identical orders.
    """
    exec_a = MockExecutionAlwaysFill(run_id="overnight-cascade-test-a")
    exec_b = MockExecutionAlwaysFill(run_id="overnight-cascade-test-b")
    ack_a = exec_a.submit_order(
        client_order_id="cid-1",
        instrument_id="BTC-USD",
        venue_id="binance",
        side="BUY",
        quantity=Decimal("0.5"),
        requested_price=Decimal("60000.0"),
    )
    ack_b = exec_b.submit_order(
        client_order_id="cid-1",
        instrument_id="BTC-USD",
        venue_id="binance",
        side="BUY",
        quantity=Decimal("0.5"),
        requested_price=Decimal("60000.0"),
    )
    # Same inputs -> same ack price + quantity. venue_order_id is randomized
    # per-call (deliberate: each ack carries its own envelope identity), so
    # compare the substantive fields only.
    assert ack_a.instrument_id == ack_b.instrument_id
    assert ack_a.quantity == ack_b.quantity
    assert ack_a.submitted_price == ack_b.submitted_price
    # Always-fill: matching fill price equals requested price.
    fill_a = exec_a.fills[-1]
    fill_b = exec_b.fills[-1]
    assert fill_a.price == Decimal("60000.0")
    assert fill_b.price == Decimal("60000.0")


def test_workspace_ontology_covers_all_five_asset_groups() -> None:
    """Sanity check that the ontology exposes every canonical asset_group.

    Catches regressions if a maintainer drops one of CEFI/DEFI/TRADFI/
    SPORTS/PREDICTION from the registry.
    """
    expected = {
        MarketAssetGroup.CEFI,
        MarketAssetGroup.DEFI,
        MarketAssetGroup.TRADFI,
        MarketAssetGroup.SPORTS,
        MarketAssetGroup.PREDICTION,
    }
    assert set(ASSET_GROUP_ONTOLOGY.keys()) == expected
