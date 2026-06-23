"""SIT Phase 8 — honest-coverage emission-flow scenarios.

Tests the VM-emits → manifest-writer → coverage-status chain using
in-memory ManifestWriter (no GCS bucket required — batch_size=0, no
.write() call). Verifies that capture_status values are correctly staged
in the writer's record buffer across the four emission paths.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from unified_api_contracts import (
    EMPTY_CONFIRMED_REASONS,
    FetchEvidence,
    LegacyBlankErrorReasonError,
    PipelineMode,
    UnprovenHonestAbsenceError,
)
from unified_trading_library import CaptureStatus, ManifestWriter, UnknownEmptyConfirmedReasonError

pytestmark = pytest.mark.unit

_DATE = "2026-05-18"
_VENUE = "BINANCE-SPOT"
_DATA_TYPE = "trades"
_SERVICE = "market-tick-data-service"
_PM = PipelineMode.BATCH_DATABENTO


def _writer() -> ManifestWriter:
    return ManifestWriter(service_name=_SERVICE, catalogue_bucket="", batch_size=0, per_vm_shards=False)


def _clean_fetch_evidence(source: str = "databento", endpoint: str = "https://api.test/endpoint") -> FetchEvidence:
    return FetchEvidence(
        http_status=200,
        response_received=True,
        rows_in_response=0,
        source=source,
        endpoint=endpoint,
        attempted_at=datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC),
    )


class TestCapturedEmissionFlow:
    """Scenario 1 — successful data fetch → captured row staged."""

    def test_add_produces_captured_status(self) -> None:
        w = _writer()
        w.add(date=_DATE, venue=_VENUE, data_type=_DATA_TYPE, row_count=1000)
        assert len(w._records) == 1
        rec = w._records[0]
        assert rec.capture_status == CaptureStatus.CAPTURED.value

    def test_add_preserves_venue_and_data_type(self) -> None:
        w = _writer()
        w.add(date=_DATE, venue="DERIBIT", data_type="options", row_count=42)
        rec = w._records[0]
        assert rec.venue == "DERIBIT"
        assert rec.data_type == "options"

    def test_multiple_adds_all_captured(self) -> None:
        w = _writer()
        venues = ["BINANCE-SPOT", "OKX", "BYBIT"]
        for v in venues:
            w.add(date=_DATE, venue=v, data_type=_DATA_TYPE, row_count=500)
        assert len(w._records) == 3
        assert all(r.capture_status == CaptureStatus.CAPTURED.value for r in w._records)


class TestEmptyConfirmedEmissionFlow:
    """Scenario 2 — source returned zero rows → empty_confirmed row staged."""

    def test_record_empty_produces_empty_confirmed_status(self) -> None:
        w = _writer()
        w.record_empty(
            row_key={"date": _DATE, "venue": _VENUE, "data_type": _DATA_TYPE},
            reason="SOURCE_RETURNED_ZERO",
            pipeline_mode=_PM,
            fetch_evidence=_clean_fetch_evidence(),
        )
        assert len(w._records) == 1
        rec = w._records[0]
        assert rec.capture_status == CaptureStatus.EMPTY_CONFIRMED.value

    def test_record_empty_stores_error_reason(self) -> None:
        w = _writer()
        w.record_empty(
            row_key={"date": _DATE, "venue": _VENUE, "data_type": _DATA_TYPE},
            reason="SOURCE_RETURNED_ZERO",
            pipeline_mode=_PM,
            fetch_evidence=_clean_fetch_evidence(),
        )
        assert w._records[0].error_reason == "SOURCE_RETURNED_ZERO"

    def test_record_empty_source_returned_zero_requires_fetch_evidence(self) -> None:
        w = _writer()
        with pytest.raises(UnprovenHonestAbsenceError):
            w.record_empty(
                row_key={"date": _DATE, "venue": _VENUE, "data_type": _DATA_TYPE},
                reason="SOURCE_RETURNED_ZERO",
                pipeline_mode=_PM,
            )

    def test_record_empty_rejects_blank_reason(self) -> None:
        w = _writer()
        with pytest.raises(LegacyBlankErrorReasonError):
            w.record_empty(
                row_key={"date": _DATE, "venue": _VENUE, "data_type": _DATA_TYPE},
                reason="",
                pipeline_mode=_PM,
            )

    def test_record_empty_rejects_unknown_reason(self) -> None:
        w = _writer()
        with pytest.raises(UnknownEmptyConfirmedReasonError):
            w.record_empty(
                row_key={"date": _DATE, "venue": _VENUE, "data_type": _DATA_TYPE},
                reason="NOT_A_REAL_REASON",
                pipeline_mode=_PM,
            )

    def test_all_empty_confirmed_reasons_accepted(self) -> None:
        for reason in sorted(EMPTY_CONFIRMED_REASONS):
            w = _writer()
            # SOURCE_RETURNED_ZERO requires FetchEvidence proving a clean 200+empty fetch
            evidence = _clean_fetch_evidence() if reason == "SOURCE_RETURNED_ZERO" else None
            w.record_empty(
                row_key={"date": _DATE, "venue": _VENUE, "data_type": _DATA_TYPE},
                reason=reason,
                pipeline_mode=_PM,
                fetch_evidence=evidence,
            )
            assert w._records[0].capture_status == CaptureStatus.EMPTY_CONFIRMED.value


class TestAttemptedFailedEmissionFlow:
    """Scenario 3 — adapter fetch error → attempted_failed row staged."""

    def test_record_failed_produces_attempted_failed_status(self) -> None:
        w = _writer()
        w.record_failed(
            row_key={"date": _DATE, "venue": _VENUE, "data_type": _DATA_TYPE},
            error="VENUE_TIMEOUT",
            pipeline_mode=_PM,
        )
        assert len(w._records) == 1
        rec = w._records[0]
        assert rec.capture_status == CaptureStatus.ATTEMPTED_FAILED.value

    def test_record_failed_stores_error_string(self) -> None:
        w = _writer()
        w.record_failed(
            row_key={"date": _DATE, "venue": _VENUE, "data_type": _DATA_TYPE},
            error="RATE_LIMIT_EXCEEDED",
            pipeline_mode=_PM,
        )
        assert w._records[0].error_reason == "RATE_LIMIT_EXCEEDED"


class TestMixedStatusBatch:
    """Scenario 4 — one batch with all four capture_status values staged correctly."""

    def test_mixed_statuses_tracked_independently(self) -> None:
        w = _writer()
        w.add(date=_DATE, venue="BINANCE-SPOT", data_type="trades", row_count=100)
        w.record_empty(
            row_key={"date": _DATE, "venue": "KRAKEN", "data_type": "trades"},
            reason="SOURCE_RETURNED_ZERO",
            pipeline_mode=_PM,
            fetch_evidence=_clean_fetch_evidence(source="kraken"),
        )
        w.record_failed(
            row_key={"date": _DATE, "venue": "BITFINEX", "data_type": "trades"},
            error="TIMEOUT",
            pipeline_mode=_PM,
        )
        statuses = [r.capture_status for r in w._records]
        assert CaptureStatus.CAPTURED.value in statuses
        assert CaptureStatus.EMPTY_CONFIRMED.value in statuses
        assert CaptureStatus.ATTEMPTED_FAILED.value in statuses
        assert len(w._records) == 3
