"""Layer 3a smoke - coverage-matrix GCS state assertions.

Verifies that TEST-bucket state across representative (category x venue x
data_type) cells is internally consistent with the per-category path layout
SSOT and manifest v5 schema. Runs against real GCS when
``GCS_TEST_BUCKET_ENABLED=1`` and ADC / service-account creds are available;
skips cleanly otherwise so the unit-test QG stays green.

Design
------

The prior TRIGGER step (CLI invocation that wrote the data) is NOT exercised
here - SIT's canon is HTTP + GCS/PubSub assertions, not subprocess CLI calls
into services (see ``system-integration-tests/README.md``). Instead this test
verifies that whatever populated the TEST buckets (dev-local
``<service>/scripts/smoke_matrix.py`` or staging CI) left consistent state:

* Step 1 (trigger) - performed elsewhere. Pre-condition is assumed.
* Step 2 (parquet on disk) - enforced here: at least one ``.parquet`` exists
  under the cell's category-specific prefix (see SSOT
  ``codex/02-data/per-category-bucket-layouts.md``).
* Step 3 (manifest row) - enforced here: a row in
  ``_index/availability_index.parquet`` matches the cell and has
  ``capture_status in {captured, empty_confirmed}``. ``empty_confirmed`` is
  a PASS - "we tried, legitimately zero rows" is valuable signal.

Representative cells
--------------------

One cell per distinct partition shape, covering every category:

* **CEFI** x BINANCE-SPOT x trades - standard venue-partitioned
* **TRADFI** x CME x trades - standard venue-partitioned
* **DEFI** x UNISWAP x trades - standard (chain= partition enforced in MTDS
  layer; here we only assert instruments-store which has no chain partition)
* **SPORTS** x ODDS_API x odds - SPORTS-specific ``sports_reference/`` tree
  + ``entity=`` partition (no venue= segment)
* **PREDICTION** x POLYMARKET x trades - PREDICTION-specific (no venue=
  segment, shards by ``instrument_type=``)

Extending coverage
------------------

Add a row to ``_CELLS`` and parametrisation expands automatically. Tests are
independent - each cell skips individually if its TEST bucket is missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import cast

import pytest

from tests.smoke.coverage_matrix_cells import (
    CELLS,
    CellSpec,
    expected_parquet_prefix,
    make_test_bucket_name,
)

# Re-export for the parametriser. Keep this module's surface tiny.
__all__ = ["test_coverage_matrix_cell_has_parquet_and_manifest"]


# ---------------------------------------------------------------------------
# Module-level skip-gate: require explicit opt-in + available creds.
# ---------------------------------------------------------------------------
#
# The SIT CI job runs this file at ``pytest -m smoke`` time. In unit-only mode
# (no real GCS, no ADC) every cell would otherwise fail with a credentials
# error. We skip the entire module unless the operator has explicitly enabled
# the coverage matrix smoke via ``GCS_TEST_BUCKET_ENABLED=1``.
#
# When enabled, individual cells still skip cleanly if their specific TEST
# bucket is missing - that just means no dev-local smoke has seeded that
# category yet.
# ---------------------------------------------------------------------------


def _coverage_smoke_enabled() -> bool:
    return os.environ.get("GCS_TEST_BUCKET_ENABLED", "").strip() == "1"


pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        not _coverage_smoke_enabled(),
        reason=(
            "Coverage-matrix smoke disabled. Set GCS_TEST_BUCKET_ENABLED=1 "
            "and ensure ADC / service-account creds are present before "
            "running."
        ),
    ),
]


@dataclass(frozen=True)
class _CellOutcome:
    """What we observed for one cell. All fields optional to ease debugging."""

    parquet_found: bool
    parquet_example: str | None
    manifest_row_found: bool
    manifest_capture_status: str | None
    manifest_error_reason: str | None


def _check_cell(cell: CellSpec) -> _CellOutcome:
    """Execute the 2-step GCS+manifest assertion for *cell*.

    Returns ``_CellOutcome`` with what was (or wasn't) observed. Tests
    interpret the outcome and raise accordingly. Splitting check from assert
    keeps the test body readable and lets future tests re-use the probe.
    """
    # Late import keeps the module loadable in unit-only mode.
    from unified_trading_library import get_storage_client, read_availability_index

    project_id: str = os.environ.get("GCP_PROJECT_ID", "test-project")
    bucket_name: str = make_test_bucket_name(cell.category, project_id)
    prefix: str = expected_parquet_prefix(cell)

    # google-cloud-storage SDK types Client/Bucket/Blob as Unknown under strict
    # basedpyright. Same pattern as every other smoke_matrix.py in the
    # workspace - scoped pyright ignores at the SDK boundary only.
    client = get_storage_client()  # pyright: ignore[reportAny]
    bucket = client.bucket(bucket_name)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownVariableType, reportUnknownMemberType]
    if not bucket.exists():  # pyright: ignore[reportUnknownMemberType]
        pytest.skip(
            f"TEST bucket {bucket_name!r} does not exist. Seed it by running "
            f"the dev-local smoke for {cell.category}: "
            f"`IS_TEST_RUN=true python <service>/scripts/smoke_matrix.py "
            f"--execute --asset-group {cell.category}`."
        )

    blob_iter = client.list_blobs(bucket_name, prefix=prefix, max_results=5)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownVariableType, reportUnknownMemberType]
    blob_names: list[str] = [b.name for b in blob_iter]  # pyright: ignore[reportUnknownVariableType]
    parquet_blobs: list[str] = [n for n in blob_names if n.endswith(".parquet")]
    parquet_found: bool = bool(parquet_blobs)
    parquet_example: str | None = parquet_blobs[0] if parquet_blobs else None

    # Manifest v5 lives at gs://{bucket}/_index/availability_index.parquet.
    # read_availability_index returns an empty DataFrame on 404; a missing
    # row for this cell shows as manifest_row_found=False.
    index_df = read_availability_index(bucket_name)
    manifest_row_found: bool = False
    manifest_capture_status: str | None = None
    manifest_error_reason: str | None = None
    if not index_df.empty:
        match = index_df
        for col, val in cell.manifest_filter().items():
            if col in match.columns:
                match = match[match[col] == val]
        if not match.empty:
            manifest_row_found = True
            row = match.iloc[-1]  # Latest row per cell.
            # pandas Series.get() returns Any; cast at the SDK boundary.
            cs_raw = cast(str, row.get("capture_status", ""))
            er_raw = cast(str, row.get("error_reason", ""))
            manifest_capture_status = cs_raw if cs_raw else None
            manifest_error_reason = er_raw if er_raw else None

    return _CellOutcome(
        parquet_found=parquet_found,
        parquet_example=parquet_example,
        manifest_row_found=manifest_row_found,
        manifest_capture_status=manifest_capture_status,
        manifest_error_reason=manifest_error_reason,
    )


def _cell_test_id(c: CellSpec) -> str:
    return c.test_id


@pytest.mark.parametrize("cell", CELLS, ids=_cell_test_id)
def test_coverage_matrix_cell_has_parquet_and_manifest(cell: CellSpec) -> None:
    """3-step assertion contract - steps 2 & 3 on TEST-bucket state.

    Step 1 (trigger) is a pre-condition satisfied by dev-local smoke runs or
    staging CI. Step 2 (parquet) + Step 3 (manifest row with
    ``capture_status in {captured, empty_confirmed}``) are enforced here.

    A cell PASSES when:
      - the TEST bucket for its category exists AND
      - at least one ``.parquet`` matches the category-specific prefix AND
      - a manifest row matches the cell's shard key AND
      - that row's ``capture_status`` is ``captured`` or ``empty_confirmed``.

    ``attempted_failed`` is FAIL; the test surfaces the recorded
    ``error_reason`` in the assertion message for triage.
    """
    outcome: _CellOutcome = _check_cell(cell)

    if not outcome.parquet_found:
        pytest.fail(
            f"[{cell.test_id}] no .parquet at prefix "
            f"gs://{make_test_bucket_name(cell.category, os.environ.get('GCP_PROJECT_ID', 'test-project'))}/"
            f"{expected_parquet_prefix(cell)} - "
            f"Step 2 failed. Re-run dev-local smoke for {cell.category}."
        )

    if not outcome.manifest_row_found:
        pytest.fail(
            f"[{cell.test_id}] parquet at {outcome.parquet_example!r} but no "
            f"manifest row in _index/availability_index.parquet matching "
            f"{cell.manifest_filter()!r} - Step 3 failed. Adapter may not be "
            f"calling ManifestWriter.record_captured/record_empty/record_failed."
        )

    if outcome.manifest_capture_status not in {"captured", "empty_confirmed"}:
        pytest.fail(
            f"[{cell.test_id}] manifest row has "
            f"capture_status={outcome.manifest_capture_status!r} "
            f"(error_reason={outcome.manifest_error_reason!r}). Only "
            f"`captured` and `empty_confirmed` count as PASS; "
            f"`attempted_failed` is a triage signal - see playbook "
            f"codex/15-runbooks/smoke-testing-playbook.md."
        )
