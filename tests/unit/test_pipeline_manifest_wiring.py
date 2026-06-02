"""SIT: verify the end-to-end pipeline manifest/data-status wiring is connected.

The trading data pipeline is::

    instruments-service ──instruments──▶ MTDS ──raw_tick_data──▶ MDPS
        ──processed_candles──▶ features-service ──*_features──▶ strategy-service
        ──strategy_orders──▶ execution-service

UAC is schema-only (it defines the contracts, emits no rows). This module asserts,
by pure introspection (no network, no GCS, no service spin-up), that the three
wiring layers documented in
``unified-trading-pm/codex/04-architecture/e2e-pipeline-manifest-wiring.md`` hold:

1. Every ``PIPELINE_DEPENDENCIES`` upstream ``dataset`` resolves to a real
   ``PATH_REGISTRY`` key — i.e. ``check_upstream_ready()`` cannot ``KeyError`` at
   runtime on a typo'd edge.
2. Every declared upstream ``service_name`` is a recognized pipeline producer.
3. The readiness chain is transitively reachable from ``instruments-service`` down
   through ``strategy-service`` (no broken/disconnected hop).
4. ``AvailabilityRecord`` (v9) carries every stage-key column, so the manifest can
   *represent* each hop of the chain.
5. GAP G-EXEC — the strategy→execution readiness edge — is currently missing. It is
   asserted as ``xfail`` so this test passes today (surfacing the precise missing
   hop) and flips to ``xpass`` the moment the edge is wired, prompting cleanup.
"""

from __future__ import annotations

import dataclasses

import pytest
from unified_trading_library import (
    MANIFEST_SCHEMA_VERSION,
    PATH_REGISTRY,
    PIPELINE_DEPENDENCIES,
    AvailabilityRecord,
)

# The full ordered producer chain (UAC excluded — it is schema-only and emits no rows).
# Each non-source producer is expected to declare an upstream in PIPELINE_DEPENDENCIES.
PIPELINE_SOURCE = "instruments-service"
EXPECTED_PRODUCERS: frozenset[str] = frozenset(
    {
        "instruments-service",
        "market-tick-data-service",
        "market-data-processing-service",
        # features-service is consolidated; PIPELINE_DEPENDENCIES still keys the
        # historical per-family service names, all of which are this producer.
        "features-onchain-service",
        "features-delta-one-service",
        "features-volatility-service",
        "strategy-service",
        "execution-service",
    }
)

# Stage-key columns the manifest row must carry to represent every hop of the chain.
REQUIRED_STAGE_KEY_COLUMNS: frozenset[str] = frozenset(
    {
        "service_name",  # which stage wrote the row
        "data_type",  # raw_tick_data / processed_candles / feature group
        "instrument_type",  # spot / perpetuals / pool / lending / …
        "feature_group",  # features stage
        "feature_family",  # features stage parent classification
        "strategy_id",  # strategy stage
        "client_id",  # strategy / execution stage
        "instruction_type",  # execution stage (TRADE / SWAP / LEND / …)
    }
)


def test_pipeline_dependency_datasets_resolve_to_path_registry() -> None:
    """Every upstream dataset edge must be a real PATH_REGISTRY key (no runtime KeyError)."""
    unknown: list[tuple[str, str]] = []
    for consumer, deps in PIPELINE_DEPENDENCIES.items():
        for dep in deps:
            if dep.dataset not in PATH_REGISTRY:
                unknown.append((consumer, dep.dataset))
    assert not unknown, (
        "PIPELINE_DEPENDENCIES references dataset(s) not in PATH_REGISTRY "
        f"(check_upstream_ready would KeyError): {unknown}. "
        f"Valid keys: {sorted(PATH_REGISTRY)}"
    )


def test_pipeline_dependency_services_are_known_producers() -> None:
    """Every declared upstream service_name must be a recognized pipeline producer."""
    unknown: list[tuple[str, str]] = []
    for consumer, deps in PIPELINE_DEPENDENCIES.items():
        for dep in deps:
            if dep.service_name not in EXPECTED_PRODUCERS:
                unknown.append((consumer, dep.service_name))
    assert not unknown, (
        f"PIPELINE_DEPENDENCIES references unknown upstream service(s): {unknown}. "
        f"Known producers: {sorted(EXPECTED_PRODUCERS)}"
    )


def test_readiness_chain_reaches_from_source_to_strategy() -> None:
    """The readiness graph must connect instruments-service ⇒ … ⇒ strategy-service.

    Walks the consumer→upstream-service edges and asserts that, starting from the
    services whose only upstream is the pipeline source, every intermediate hop up to
    strategy-service is transitively reachable. A disconnected/typo'd hop fails here.
    """
    # Build consumer -> set(upstream service_name) adjacency.
    upstream_of: dict[str, set[str]] = {
        consumer: {d.service_name for d in deps} for consumer, deps in PIPELINE_DEPENDENCIES.items()
    }
    # Every service reachable upstream from strategy-service (transitive closure).
    reachable: set[str] = set()
    frontier: list[str] = ["strategy-service"]
    while frontier:
        node = frontier.pop()
        for up in upstream_of.get(node, set()):
            if up not in reachable:
                reachable.add(up)
                frontier.append(up)
    # The whole spine up to strategy must be present in strategy's upstream closure.
    expected_spine = {
        PIPELINE_SOURCE,
        "market-tick-data-service",
        "market-data-processing-service",
    }
    missing = expected_spine - reachable
    assert not missing, (
        "Readiness chain from instruments-service to strategy-service is broken — "
        f"these spine stages are not transitively upstream of strategy-service: {missing}. "
        f"Reachable upstream of strategy-service: {sorted(reachable)}"
    )


def test_manifest_row_carries_every_stage_key_column() -> None:
    """AvailabilityRecord (v9) must be able to represent every hop of the chain."""
    assert MANIFEST_SCHEMA_VERSION >= 9, f"Expected manifest schema v9+, found {MANIFEST_SCHEMA_VERSION}"
    fields = {f.name for f in dataclasses.fields(AvailabilityRecord)}
    missing = REQUIRED_STAGE_KEY_COLUMNS - fields
    assert not missing, (
        f"AvailabilityRecord is missing stage-key column(s) needed for E2E "
        f"representation: {missing}. Present: {sorted(fields)}"
    )


def test_strategy_output_dataset_exists_for_execution_edge() -> None:
    """The strategy→execution edge has a canonical dataset to hang off of.

    Confirms the dataset that the (currently-missing) execution readiness edge would
    reference is already registered — so wiring G-EXEC is additive, not blocked on a
    new PATH_REGISTRY entry.
    """
    assert "strategy_orders" in PATH_REGISTRY, (
        "Expected 'strategy_orders' dataset in PATH_REGISTRY as the strategy→execution "
        f"readiness edge target. Keys: {sorted(PATH_REGISTRY)}"
    )


@pytest.mark.xfail(
    reason=(
        "GAP G-EXEC: execution-service has no PIPELINE_DEPENDENCIES entry, so the "
        "strategy→execution readiness hop is unwired. The 'strategy_orders' dataset "
        "exists; adding the edge is the documented follow-up. See "
        "codex/04-architecture/e2e-pipeline-manifest-wiring.md (Layer 2, G-EXEC). "
        "When wired, this xfail becomes xpass — remove the marker."
    ),
    strict=False,
)
def test_execution_service_declares_strategy_upstream() -> None:
    """execution-service should declare a strategy-output upstream to complete the chain."""
    deps = PIPELINE_DEPENDENCIES.get("execution-service", [])
    upstream_datasets = {d.dataset for d in deps}
    assert isinstance(deps, list)
    assert {"strategy_orders", "strategy_instructions"} & upstream_datasets, (
        "execution-service does not declare a strategy-output upstream "
        f"(found edges: {[(d.dataset, d.service_name) for d in deps]})"
    )


def test_upstream_dependency_shape_is_stable() -> None:
    """Guard the UpstreamDependency contract the test relies on (introspected live)."""
    sample = next(dep for deps in PIPELINE_DEPENDENCIES.values() for dep in deps)
    fields = {f.name for f in dataclasses.fields(sample)}
    assert {"dataset", "service_name", "required"} <= fields
