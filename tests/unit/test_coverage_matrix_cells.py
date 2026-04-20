"""Unit tests for the coverage-matrix pure-function helpers.

These tests exercise ``tests/smoke/coverage_matrix_cells.py`` without GCS
access so the SIT quality-gates stay green in unit-only mode. The smoke file
itself (``test_coverage_matrix_smoke.py``) is gated on
``GCS_TEST_BUCKET_ENABLED=1`` and skips here.
"""

from __future__ import annotations

import pytest

from tests.smoke.coverage_matrix_cells import (
    CELLS,
    CellSpec,
    expected_parquet_prefix,
    make_test_bucket_name,
)

pytestmark = pytest.mark.unit


class TestMakeTestBucketName:
    def test_cefi_uses_lowercase_category(self) -> None:
        assert make_test_bucket_name("CEFI", "p1") == "instruments-store-cefi-test-p1"

    def test_sports_lowercase(self) -> None:
        assert make_test_bucket_name("SPORTS", "proj") == "instruments-store-sports-test-proj"

    def test_prediction_lowercase(self) -> None:
        assert make_test_bucket_name("PREDICTION", "abc") == "instruments-store-prediction-test-abc"


class TestExpectedParquetPrefix:
    def test_cefi_uses_instrument_availability_tree(self) -> None:
        cell = CellSpec(service="instruments-service", category="CEFI", venue="BINANCE-SPOT", data_type="trades")
        prefix = expected_parquet_prefix(cell, date_str="2026-04-20")
        assert prefix == "instrument_availability/by_date/day=2026-04-20/venue=BINANCE-SPOT/"

    def test_tradfi_uses_instrument_availability_tree(self) -> None:
        cell = CellSpec(service="instruments-service", category="TRADFI", venue="CME", data_type="trades")
        prefix = expected_parquet_prefix(cell, date_str="2026-04-20")
        assert prefix == "instrument_availability/by_date/day=2026-04-20/venue=CME/"

    def test_defi_uses_instrument_availability_tree(self) -> None:
        # instruments-store has no chain= partition; chain= lives in the MTDS
        # raw_tick_data tree only (see per-category-bucket-layouts.md).
        cell = CellSpec(
            service="instruments-service",
            category="DEFI",
            venue="UNISWAP",
            data_type="trades",
            chain="ETHEREUM",
        )
        prefix = expected_parquet_prefix(cell, date_str="2026-04-20")
        assert prefix == "instrument_availability/by_date/day=2026-04-20/venue=UNISWAP/"

    def test_sports_uses_sports_reference_tree_with_entity_partition(self) -> None:
        cell = CellSpec(
            service="instruments-service",
            category="SPORTS",
            venue="ODDS_API",
            data_type="odds",
            league="CHAMPIONSHIP",
        )
        prefix = expected_parquet_prefix(cell, date_str="2026-04-20")
        # SSOT requirement — SPORTS does NOT use instrument_availability/ or venue=.
        assert prefix == "sports_reference/by_date/day=2026-04-20/entity=odds/"

    def test_prediction_uses_instrument_availability_tree(self) -> None:
        cell = CellSpec(
            service="instruments-service",
            category="PREDICTION",
            venue="POLYMARKET",
            data_type="trades",
            instrument_type="BTC",
        )
        prefix = expected_parquet_prefix(cell, date_str="2026-04-20")
        assert prefix == "instrument_availability/by_date/day=2026-04-20/venue=POLYMARKET/"

    def test_default_date_is_yesterday_utc(self) -> None:
        cell = CellSpec(service="instruments-service", category="CEFI", venue="BINANCE-SPOT", data_type="trades")
        prefix = expected_parquet_prefix(cell)
        # Don't check the exact date (clock-dependent), just the shape.
        assert prefix.startswith("instrument_availability/by_date/day=")
        assert prefix.endswith("/venue=BINANCE-SPOT/")


class TestCellSpecTestId:
    def test_basic_id(self) -> None:
        cell = CellSpec(service="instruments-service", category="CEFI", venue="BINANCE-SPOT", data_type="trades")
        assert cell.test_id == "instruments-service:CEFI:BINANCE-SPOT:trades"

    def test_id_includes_chain_when_present(self) -> None:
        cell = CellSpec(
            service="instruments-service",
            category="DEFI",
            venue="UNISWAP",
            data_type="trades",
            chain="ETHEREUM",
        )
        assert cell.test_id == "instruments-service:DEFI:UNISWAP:trades:chain=ETHEREUM"

    def test_id_includes_league_when_present(self) -> None:
        cell = CellSpec(
            service="instruments-service",
            category="SPORTS",
            venue="ODDS_API",
            data_type="odds",
            league="CHAMPIONSHIP",
        )
        assert cell.test_id == "instruments-service:SPORTS:ODDS_API:odds:league=CHAMPIONSHIP"

    def test_id_includes_instrument_type_when_present(self) -> None:
        cell = CellSpec(
            service="instruments-service",
            category="PREDICTION",
            venue="POLYMARKET",
            data_type="trades",
            instrument_type="BTC",
        )
        assert cell.test_id == "instruments-service:PREDICTION:POLYMARKET:trades:it=BTC"


class TestCellSpecManifestFilter:
    def test_basic_shard_key(self) -> None:
        cell = CellSpec(service="instruments-service", category="CEFI", venue="BINANCE-SPOT", data_type="trades")
        assert cell.manifest_filter() == {
            "category": "CEFI",
            "venue": "BINANCE-SPOT",
            "data_type": "trades",
        }

    def test_defi_adds_chain(self) -> None:
        cell = CellSpec(
            service="instruments-service",
            category="DEFI",
            venue="UNISWAP",
            data_type="trades",
            chain="ETHEREUM",
        )
        assert cell.manifest_filter() == {
            "category": "DEFI",
            "venue": "UNISWAP",
            "data_type": "trades",
            "chain": "ETHEREUM",
        }

    def test_sports_adds_league_id(self) -> None:
        cell = CellSpec(
            service="instruments-service",
            category="SPORTS",
            venue="ODDS_API",
            data_type="odds",
            league="CHAMPIONSHIP",
        )
        assert cell.manifest_filter() == {
            "category": "SPORTS",
            "venue": "ODDS_API",
            "data_type": "odds",
            "league_id": "CHAMPIONSHIP",
        }

    def test_prediction_adds_instrument_type(self) -> None:
        cell = CellSpec(
            service="instruments-service",
            category="PREDICTION",
            venue="POLYMARKET",
            data_type="trades",
            instrument_type="BTC",
        )
        assert cell.manifest_filter() == {
            "category": "PREDICTION",
            "venue": "POLYMARKET",
            "data_type": "trades",
            "instrument_type": "BTC",
        }


class TestCellsList:
    def test_covers_every_category(self) -> None:
        categories = {cell.category for cell in CELLS}
        assert categories == {"CEFI", "TRADFI", "DEFI", "SPORTS", "PREDICTION"}

    def test_each_cell_has_distinct_test_id(self) -> None:
        ids = [cell.test_id for cell in CELLS]
        assert len(ids) == len(set(ids))
