"""GATE-0 SIT: the literal gate — write → manifest(all 4 provenance cols) → union-read → gate.

This module proves, at the **public-CONTRACT level** (credential-free, no real GCS,
no service spin-up, no peer-service Python import), the four legs of GATE-0:

LEG 1 — the WRITER stamps all four provenance columns.
    Driving ``unified_trading_library``'s :class:`ManifestWriter` ``add()`` for a
    BATCH cell with ``pipeline_mode="batch_<source>"`` + ``source`` + ``transport``
    + ``cadence`` yields an :class:`AvailabilityRecord` that carries all four
    NON-blank (the in-memory ``_records`` path — no sink/GCS). A ``live_<source>``
    variant proves the same for a LIVE cell (the M1-BREAKING source-aware
    ``live_<source>`` migration has landed — the transitional alias is removed).

LEG 2 — the manifest SCHEMA carries all four.
    :class:`AvailabilityRecord` declares ``pipeline_mode`` / ``source`` /
    ``transport`` / ``cadence`` fields, AND ``pipeline_mode`` + ``cadence``
    (+ ``transport``) are members of UTL ``_ROW_KEY_COLUMNS`` (the row-key /
    group-by axis set the manifest layer keys rows + unions on).

LEG 3 — the UNION groups by ``pipeline_mode``.
    The four provenance dims (``pipeline_mode`` / ``source`` / ``transport`` /
    ``cadence``) are all manifest columns AND union axes (present in
    ``_ROW_KEY_COLUMNS``) — so a provenance union/breakdown can group by each.
    Asserted at the contract level only (the UAC/UTL surface); the
    deployment-api union consumer is NOT imported (no service↔service dep).

LEG 4 — the GATE.
    Via the ROOT FACADE ``from unified_api_contracts import could_exist,
    select_for_mode, Mode``:
      * ``could_exist(asset_group, data_type, mode)`` is ``False`` for an
        impossible cell and ``True`` for a possible one (the M3 seed guardrail).
      * ``select_for_mode(consumer_mode, available_modes)`` picks the contextual
        mode — live-context → LIVE, batch-context → BATCH, and ``replay`` is
        ALWAYS the middle (gap-fill) tier (the M4 read-precedence).

Per the no-service↔service-dependency HARD RULE this module asserts against the
UAC + UTL PUBLIC CONTRACT only: it never imports deployment-api / mtds /
features / mdps as Python deps. Mirrors the introspection style of
``tests/unit/test_pipeline_manifest_wiring.py``.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest
from unified_api_contracts import Mode, could_exist, select_for_mode
from unified_trading_library import AvailabilityRecord, ManifestWriter

# _ROW_KEY_COLUMNS (the manifest row-key / union axis SSOT) has no top-level
# re-export — the deep manifest_writer path is the only public surface for it,
# and GATE-0 LEG 2/3 must assert against it directly.
from unified_trading_library.manifest_writer import _ROW_KEY_COLUMNS  # noqa: qg-deep-import

pytestmark = pytest.mark.code_test

# The four provenance columns GATE-0 tracks through write → manifest → union.
PROVENANCE_COLUMNS: frozenset[str] = frozenset({"pipeline_mode", "source", "transport", "cadence"})

# A shard the M3 guardrail recognises as POSSIBLE (≥1 source serving it can run
# every mode — e.g. hyperliquid serves cefi perp_funding in batch/live/replay).
_POSSIBLE_SHARD: tuple[str, str] = ("cefi", "perp_funding")
# A shard no registered source serves → could_exist is False for every mode.
_IMPOSSIBLE_SHARD: tuple[str, str] = ("defi", "__no_such_gate0_data_type__")


# ─────────────────────────────────────────────────────────────────────────────
# LEG 1 — the writer stamps all four provenance columns (BATCH cell).
# ─────────────────────────────────────────────────────────────────────────────
def test_leg1_writer_stamps_all_four_provenance_columns_for_batch_cell() -> None:
    """ManifestWriter.add() for a batch cell yields an AvailabilityRecord with all 4 cols NON-blank.

    Drives the in-memory ``_records`` path (no sink, no GCS, no credentials):
    a ``batch_<source>`` cell with explicit ``source`` / ``transport`` / ``cadence``
    must round-trip every provenance column onto the resulting record.
    """
    writer = ManifestWriter(service_name="market-tick-data-service")
    writer.add(
        dataset_id="raw_tick_data",
        asset_group="cefi",
        processing_date=date(2026, 6, 1),
        row_count=1440,
        venue="hyperliquid",
        data_type="perp_funding",
        instrument_type="perpetuals",
        pipeline_mode="batch_hyperliquid",
        source="hyperliquid",
        transport="rest",
        cadence="t1_daily",
    )
    assert writer._records, "ManifestWriter.add() did not append an in-memory record"
    record = writer._records[-1]
    assert isinstance(record, AvailabilityRecord)

    stamped = {col: getattr(record, col) for col in PROVENANCE_COLUMNS}
    blank = {col: val for col, val in stamped.items() if not str(val).strip()}
    assert not blank, (
        f"batch_<source> add() left provenance column(s) blank: {blank}. "
        f"All four must be stamped non-blank — got {stamped}."
    )
    # The mode tag must reflect the batch source, not a bare/legacy mode.
    assert record.pipeline_mode == "batch_hyperliquid"


def test_leg1_writer_stamps_all_four_provenance_columns_for_live_cell() -> None:
    """live_<source> variant — the writer stamps all four provenance columns for a LIVE cell.

    The M1-BREAKING migration to per-source ``live_<source>`` pipeline_mode keys
    has landed (the transitional alias is removed), so this drives the same add()
    path with ``pipeline_mode="live_hyperliquid"`` and asserts all four provenance
    columns stamp non-blank for a LIVE cell — identical to the batch leg.
    """
    writer = ManifestWriter(service_name="market-tick-data-service")
    writer.add(
        dataset_id="raw_tick_data",
        asset_group="cefi",
        processing_date=date(2026, 6, 1),
        row_count=1440,
        venue="hyperliquid",
        data_type="perp_funding",
        instrument_type="perpetuals",
        pipeline_mode="live_hyperliquid",
        source="hyperliquid",
        transport="websocket",
        cadence="continuous_live",
    )
    record = writer._records[-1]
    stamped = {col: getattr(record, col) for col in PROVENANCE_COLUMNS}
    assert all(str(v).strip() for v in stamped.values()), stamped


# ─────────────────────────────────────────────────────────────────────────────
# LEG 2 — the manifest schema carries all four (fields + row-key axes).
# ─────────────────────────────────────────────────────────────────────────────
def test_leg2_availability_record_declares_all_four_provenance_fields() -> None:
    """AvailabilityRecord must declare pipeline_mode / source / transport / cadence fields."""
    fields = {f.name for f in dataclasses.fields(AvailabilityRecord)}
    missing = PROVENANCE_COLUMNS - fields
    assert not missing, f"AvailabilityRecord is missing provenance field(s): {missing}. Present: {sorted(fields)}"


def test_leg2_pipeline_mode_cadence_transport_are_row_key_columns() -> None:
    """pipeline_mode + cadence (+ transport) must be UTL _ROW_KEY_COLUMNS members.

    These are the columns the manifest layer keys rows on (and therefore can
    group/union by). If a provenance dim is NOT a row-key column it cannot be a
    union axis.
    """
    required_axes = {"pipeline_mode", "cadence", "transport"}
    missing = required_axes - set(_ROW_KEY_COLUMNS)
    assert not missing, (
        f"_ROW_KEY_COLUMNS is missing required union axis/axes: {missing}. "
        f"Present row-key columns: {list(_ROW_KEY_COLUMNS)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# LEG 3 — the union groups by pipeline_mode (contract-level union axes).
# ─────────────────────────────────────────────────────────────────────────────
def test_leg3_union_breakdown_dims_are_manifest_columns_and_axes() -> None:
    """All four provenance dims are BOTH AvailabilityRecord columns AND _ROW_KEY_COLUMNS axes.

    Contract-level proof that a provenance union/breakdown (the deployment-api
    consumer, NOT imported here) can group by pipeline_mode / source / transport /
    cadence: each is a manifest column AND a groupable row-key axis. Asserts the
    UAC/UTL contract only — no deployment-api / mtds / features import.
    """
    record_fields = {f.name for f in dataclasses.fields(AvailabilityRecord)}
    row_key_axes = set(_ROW_KEY_COLUMNS)

    not_a_column = PROVENANCE_COLUMNS - record_fields
    not_an_axis = PROVENANCE_COLUMNS - row_key_axes
    assert not not_a_column, f"Union dim(s) not a manifest column: {not_a_column}"
    assert not not_an_axis, f"Union dim(s) not a groupable row-key axis: {not_an_axis}"


# ─────────────────────────────────────────────────────────────────────────────
# LEG 4 — the gate (could_exist + select_for_mode via the root facade).
# ─────────────────────────────────────────────────────────────────────────────
def test_leg4_could_exist_rejects_impossible_and_accepts_possible_cell() -> None:
    """could_exist() is False for an impossible (shard, mode) and True for a possible one."""
    ag_imp, dt_imp = _IMPOSSIBLE_SHARD
    for mode in Mode:
        assert could_exist(ag_imp, dt_imp, mode) is False, (
            f"could_exist falsely accepted impossible cell ({ag_imp}, {dt_imp}, {mode.value})"
        )

    ag_ok, dt_ok = _POSSIBLE_SHARD
    accepting = [m for m in Mode if could_exist(ag_ok, dt_ok, m)]
    assert accepting, (
        f"could_exist rejected EVERY mode for the expected-possible cell ({ag_ok}, {dt_ok}); "
        "a registered source serving it should make at least one mode possible."
    )


def test_leg4_select_for_mode_picks_contextual_mode() -> None:
    """select_for_mode picks the context mode; replay is ALWAYS the middle (gap-fill) tier."""
    all_modes = frozenset(Mode)

    # live-context → LIVE; batch-context → BATCH when its own mode is available.
    assert select_for_mode(Mode.LIVE, all_modes) == Mode.LIVE
    assert select_for_mode(Mode.BATCH, all_modes) == Mode.BATCH

    # replay is the MIDDLE tier in both context orders:
    #   live-context  [LIVE, REPLAY, BATCH] → drop LIVE  ⇒ REPLAY
    #   batch-context [BATCH, REPLAY, LIVE] → drop BATCH ⇒ REPLAY
    assert select_for_mode(Mode.LIVE, frozenset({Mode.BATCH, Mode.REPLAY})) == Mode.REPLAY
    assert select_for_mode(Mode.BATCH, frozenset({Mode.LIVE, Mode.REPLAY})) == Mode.REPLAY

    # a replay-context reader reuses the live order and lands on the present mode.
    assert select_for_mode(Mode.REPLAY, frozenset({Mode.REPLAY})) == Mode.REPLAY
