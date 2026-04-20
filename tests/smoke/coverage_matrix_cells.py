"""Representative cell enumeration + pure-function helpers for the coverage
matrix smoke test.

Split out from ``test_coverage_matrix_smoke.py`` so the helpers (bucket name
resolution, expected prefix derivation, manifest filter construction) can be
unit-tested without GCP credentials.

SSOT references
---------------

* Per-category bucket + path layouts —
  ``unified-trading-pm/codex/02-data/per-category-bucket-layouts.md``.
* Manifest v5 schema —
  ``unified-trading-pm/codex/02-data/availability-manifest-and-data-status.md``.
* TEST-bucket naming convention — adapters set
  ``IS_TEST_RUN=true`` to route writes to ``<name>-test-<project_id>``
  buckets with 7-day lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class CellSpec:
    """A single (service × category × venue × data_type) coverage cell.

    ``chain`` + ``league`` + ``instrument_type`` carry the category-specific
    extra partition levels when they apply (DeFi, SPORTS, PREDICTION
    respectively). All three are optional; pure-function helpers derive the
    correct prefix shape from the combination of fields.
    """

    service: str
    category: str
    venue: str
    data_type: str
    chain: str | None = None
    league: str | None = None
    instrument_type: str | None = None

    @property
    def test_id(self) -> str:
        """Parametrize id. Shows in ``pytest -v`` for quick triage."""
        parts: list[str] = [self.service, self.category, self.venue, self.data_type]
        if self.chain:
            parts.append(f"chain={self.chain}")
        if self.league:
            parts.append(f"league={self.league}")
        if self.instrument_type:
            parts.append(f"it={self.instrument_type}")
        return ":".join(parts)

    def manifest_filter(self) -> dict[str, str]:
        """Shard-key filter used to locate the manifest row for this cell.

        ManifestWriter v5 writes ``venue``, ``data_type``, ``category`` plus
        the extra dimensions (``chain``, ``league_id``, ``instrument_type``)
        when they apply. See the manifest-v5 SSOT.
        """
        out: dict[str, str] = {
            "category": self.category,
            "venue": self.venue,
            "data_type": self.data_type,
        }
        if self.chain:
            out["chain"] = self.chain
        if self.league:
            out["league_id"] = self.league
        if self.instrument_type:
            out["instrument_type"] = self.instrument_type
        return out


# ---------------------------------------------------------------------------
# Representative cells — one per distinct partition shape / category.
#
# We deliberately keep this list SMALL (5 rows) so the parametrised test
# remains a smoke layer (<5 min) rather than a full matrix run. The per-
# service ``scripts/smoke_matrix.py`` scripts already enumerate the full
# 1,146-cell matrix for developers who need complete coverage locally.
# ---------------------------------------------------------------------------

CELLS: tuple[CellSpec, ...] = (
    CellSpec(
        service="instruments-service",
        category="CEFI",
        venue="BINANCE-SPOT",
        data_type="trades",
    ),
    CellSpec(
        service="instruments-service",
        category="TRADFI",
        venue="CME",
        data_type="trades",
    ),
    CellSpec(
        service="instruments-service",
        category="DEFI",
        venue="UNISWAP",
        data_type="trades",
        chain="ETHEREUM",
    ),
    CellSpec(
        service="instruments-service",
        category="SPORTS",
        venue="ODDS_API",
        data_type="odds",
        league="CHAMPIONSHIP",
    ),
    CellSpec(
        service="instruments-service",
        category="PREDICTION",
        venue="POLYMARKET",
        data_type="trades",
        instrument_type="BTC",
    ),
)


def make_test_bucket_name(category: str, project_id: str) -> str:
    """Return the TEST-bucket name for *category* under *project_id*.

    Template: ``instruments-store-{category_lower}-test-{project_id}``.
    Matches the ``IS_TEST_RUN=true`` routing baked into every adapter's
    ``get_bucket_name`` path. SSOT: `codex/02-data/per-category-bucket-layouts.md`.
    """
    return f"instruments-store-{category.lower()}-test-{project_id}"


def expected_parquet_prefix(cell: CellSpec, date_str: str | None = None) -> str:
    """Derive the expected GCS prefix for *cell*'s parquet output.

    Category-specific layouts:

    * **SPORTS** → ``sports_reference/by_date/day={date}/entity={entity}/``
      (different tree, ``entity=`` replaces ``venue=``)
    * **everything else** → ``instrument_availability/by_date/day={date}/venue={venue}/``

    Default *date_str* is "yesterday UTC" — picked because instruments-service
    runs in batch nightly and "today" is often incomplete. Pass an explicit
    ISO date for deterministic tests.
    """
    if date_str is None:
        date_str = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()

    if cell.category == "SPORTS":
        # SPORTS writes to the sports_reference/ tree with entity= partition.
        # For this smoke we use data_type as a stand-in for entity because the
        # representative cells hold the same value in both dimensions
        # (odds → entity=odds, fixtures → entity=fixtures, etc.). See the
        # SSOT matrix for the full entity list.
        return f"sports_reference/by_date/day={date_str}/entity={cell.data_type}/"

    return f"instrument_availability/by_date/day={date_str}/venue={cell.venue}/"
