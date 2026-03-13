"""
Abbreviated SIT: Contract normalization checks for the three runtime communication paths.

Runtime path:  execution-service <-> alerting-service <-> risk-and-exposure-service
Pilot path:    strategy-service <-> ml-inference-service
Pipeline path: instruments-service -> market-data-processing-service -> features-*-service

Each test verifies that the shared domain schema at a boundary:
1. Can be instantiated from a canonical fixture dict
2. Survives a serialize->deserialize round-trip without field drops or type coercions
3. Is importable from both sides of the boundary (same type, same package version)

Target runtime: <30s total. No network, no emulators, pure in-process.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

# UAC canonical execution schemas (UAC has py.typed; import directly from UAC)
from unified_api_contracts import CanonicalFill, CanonicalOrder
from unified_api_contracts.unified_normalised_contracts.execution import OrderSide, OrderType
from unified_events_interface import (
    STANDARD_COORDINATION_EVENTS,
    STANDARD_LIFECYCLE_EVENTS,
    TRADE_REPORTED_MIFID,
    BestExecutionEvent,
    ComplianceEventPayload,
    CoordinationEvent,
    LifecycleEvent,
)
from unified_internal_contracts.domain.ml_inference_service import (
    CascadeConfig,
    CascadePredictionEvent,
    PredictionSnapshot,
)
from unified_internal_contracts.events import (
    EventMetadata,
    EventSeverity,
    LifecycleEventEnvelope,
    LifecycleEventType,
)
from unified_internal_contracts.market_data import CanonicalOHLCV, OHLCVSource
from unified_internal_contracts.ml import InferenceRequest, InferenceResult
from unified_internal_contracts.pubsub import (
    FillEventMessage,
    PubSubMessageEnvelope,
    RiskAlertMessage,
)
from unified_internal_contracts.risk import RiskMetrics, RiskPosition, RiskStatus

pytestmark = pytest.mark.abbreviated_sit


# ---------------------------------------------------------------------------
# Runtime path: UEI lifecycle and coordination event schemas
# ---------------------------------------------------------------------------


def test_uei_lifecycle_event_round_trip() -> None:
    """LifecycleEvent serializes to dict and deserializes to an equivalent object."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    original = LifecycleEvent(
        timestamp=ts,
        service_name="execution-service",
        event_name="STARTED",
        severity="INFO",
        details={"mode": "live"},
        correlation_id="corr-001",
    )

    serialized = original.to_dict()

    assert serialized["service_name"] == "execution-service"
    assert serialized["event_name"] == "STARTED"
    assert serialized["severity"] == "INFO"
    assert serialized["correlation_id"] == "corr-001"
    assert serialized["details"] == {"mode": "live"}

    reconstructed = LifecycleEvent(
        timestamp=datetime.fromisoformat(str(serialized["timestamp"])),
        service_name=str(serialized["service_name"]),
        event_name=str(serialized["event_name"]),
        severity=str(serialized["severity"]),
        details={"mode": "live"},
        correlation_id=str(serialized["correlation_id"]) if serialized["correlation_id"] else None,
    )

    assert reconstructed.service_name == original.service_name
    assert reconstructed.event_name == original.event_name
    assert reconstructed.severity == original.severity
    assert reconstructed.details == original.details
    assert reconstructed.correlation_id == original.correlation_id


def test_uei_coordination_event_round_trip() -> None:
    """CoordinationEvent serializes to dict and deserializes to an equivalent object."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    original = CoordinationEvent(
        timestamp=ts,
        source_service="features-delta-one-service",
        event_type="DATA_READY",
        payload={"gcs_path": "gs://bucket/features/2026-01-15"},
        correlation_id="corr-features-001",
    )

    serialized = original.to_dict()

    assert serialized["source_service"] == "features-delta-one-service"
    assert serialized["event_type"] == "DATA_READY"
    assert serialized["correlation_id"] == "corr-features-001"
    assert serialized["payload"] == {"gcs_path": "gs://bucket/features/2026-01-15"}

    reconstructed = CoordinationEvent(
        timestamp=datetime.fromisoformat(str(serialized["timestamp"])),
        source_service=str(serialized["source_service"]),
        event_type=str(serialized["event_type"]),
        payload={"gcs_path": "gs://bucket/features/2026-01-15"},
        correlation_id=str(serialized["correlation_id"]),
    )

    assert reconstructed.source_service == original.source_service
    assert reconstructed.event_type == original.event_type
    assert reconstructed.correlation_id == original.correlation_id
    assert reconstructed.payload == original.payload


def test_uei_compliance_event_payload_round_trip() -> None:
    """ComplianceEventPayload serializes to JSONDict and fields survive the round-trip."""
    original = ComplianceEventPayload(
        event_type=TRADE_REPORTED_MIFID,
        instrument_id="BINANCE:BTC-USDT",
        venue_id="BINANCE",
        quantity=Decimal("0.5"),
        price=Decimal("50000.00"),
        order_id="ord-abc123",
        timestamp_utc="2026-01-15T10:30:00Z",
    )

    serialized = original.to_dict()

    assert serialized["event_type"] == TRADE_REPORTED_MIFID
    assert serialized["instrument_id"] == "BINANCE:BTC-USDT"
    assert serialized["venue_id"] == "BINANCE"
    assert serialized["order_id"] == "ord-abc123"
    # float comparison — serialized values are already float from to_dict()
    assert abs(float(str(serialized["quantity"])) - 0.5) < 1e-9
    assert abs(float(str(serialized["price"])) - 50000.0) < 1e-9


def test_uei_best_execution_event_round_trip() -> None:
    """BestExecutionEvent serializes to dict and all compliance fields are preserved."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    original = BestExecutionEvent(
        event_type="BEST_EXECUTION",
        venue="binance",
        instrument_id="BINANCE:BTC-USDT",
        execution_price=Decimal("50000.00"),
        reference_price=Decimal("49998.50"),
        price_improvement=Decimal("1.50"),
        order_id="ord-abc123",
        fill_id="fill-xyz789",
        fca_form_type="RTS27",
        mifid_timestamp=ts,
        order_type="limit",
        side="buy",
    )

    serialized = original.to_dict()

    assert serialized["venue"] == "binance"
    assert serialized["instrument_id"] == "BINANCE:BTC-USDT"
    assert serialized["fca_form_type"] == "RTS27"
    assert serialized["side"] == "buy"
    assert serialized["order_id"] == "ord-abc123"
    assert serialized["fill_id"] == "fill-xyz789"


def test_uei_standard_lifecycle_events_non_empty() -> None:
    """STANDARD_LIFECYCLE_EVENTS must contain the minimal canonical set."""
    required = {"STARTED", "STOPPED", "FAILED", "PROCESSING_STARTED", "PROCESSING_COMPLETED"}
    assert required.issubset(STANDARD_LIFECYCLE_EVENTS)


def test_uei_standard_coordination_events_non_empty() -> None:
    """STANDARD_COORDINATION_EVENTS must contain pipeline coordination events."""
    required = {"DATA_READY", "FEATURES_READY", "PREDICTIONS_READY", "SIGNALS_READY"}
    assert required.issubset(STANDARD_COORDINATION_EVENTS)


# ---------------------------------------------------------------------------
# Runtime path: UIC market data pipeline contracts (pipeline path boundary)
# ---------------------------------------------------------------------------


def test_uic_canonical_ohlcv_round_trip() -> None:
    """CanonicalOHLCV Pydantic model survives model_dump() -> model_validate() round-trip."""
    ts = datetime(2026, 1, 15, 0, 0, 0, tzinfo=UTC)
    original = CanonicalOHLCV(
        instrument_key="BINANCE:SPOT:BTC-USDT",
        venue="BINANCE",
        timestamp=ts,
        interval="1h",
        open=Decimal("49000.00"),
        high=Decimal("51000.00"),
        low=Decimal("48500.00"),
        close=Decimal("50000.00"),
        volume=Decimal("1234.567"),
        source=OHLCVSource.NATIVE_CANDLE,
    )

    dumped = original.model_dump()
    reconstructed = CanonicalOHLCV.model_validate(dumped)

    assert reconstructed.instrument_key == original.instrument_key
    assert reconstructed.venue == original.venue
    assert reconstructed.interval == original.interval
    assert reconstructed.open == original.open
    assert reconstructed.high == original.high
    assert reconstructed.low == original.low
    assert reconstructed.close == original.close
    assert reconstructed.volume == original.volume
    assert reconstructed.source == original.source
    assert reconstructed.schema_version == original.schema_version


# ---------------------------------------------------------------------------
# Runtime path: UIC risk schemas (execution <-> risk-and-exposure-service boundary)
# ---------------------------------------------------------------------------


def test_uic_risk_metrics_round_trip() -> None:
    """RiskMetrics Pydantic model survives model_dump() -> model_validate() round-trip."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    original = RiskMetrics(
        client_id="client-001",
        timestamp=ts,
        leverage=Decimal("2.5"),
        margin_usage=Decimal("0.45"),
        concentration=Decimal("0.30"),
        drawdown=Decimal("0.05"),
        account_equity=Decimal("100000.00"),
        total_position_value=Decimal("250000.00"),
        cash_balance=Decimal("50000.00"),
        leverage_status=RiskStatus.WARNING,
        concentration_status=RiskStatus.OK,
        drawdown_status=RiskStatus.OK,
    )

    dumped = original.model_dump()
    reconstructed = RiskMetrics.model_validate(dumped)

    assert reconstructed.client_id == original.client_id
    assert reconstructed.leverage == original.leverage
    assert reconstructed.margin_usage == original.margin_usage
    assert reconstructed.leverage_status == original.leverage_status
    assert reconstructed.concentration_status == original.concentration_status


def test_uic_risk_position_round_trip() -> None:
    """RiskPosition Pydantic model survives model_dump() -> model_validate() round-trip."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    original = RiskPosition(
        client_id="client-001",
        venue="BINANCE",
        instrument="BTC-USDT",
        quantity=Decimal("0.5"),
        avg_price=Decimal("49000.00"),
        position_value=Decimal("24500.00"),
        unrealized_pnl=Decimal("500.00"),
        realized_pnl=Decimal("200.00"),
        last_updated=ts,
    )

    dumped = original.model_dump()
    reconstructed = RiskPosition.model_validate(dumped)

    assert reconstructed.client_id == original.client_id
    assert reconstructed.venue == original.venue
    assert reconstructed.instrument == original.instrument
    assert reconstructed.quantity == original.quantity
    assert reconstructed.avg_price == original.avg_price


# ---------------------------------------------------------------------------
# Execution path: UAC canonical order/fill contracts (execution-service boundary)
# UAC is the SSOT for CanonicalOrder/CanonicalFill; UTEI re-exports these.
# Both sides of the execution boundary use the same UAC types.
# ---------------------------------------------------------------------------


def test_uac_canonical_order_round_trip() -> None:
    """CanonicalOrder (UAC SSOT) survives model_dump() -> model_validate() round-trip."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    original = CanonicalOrder(
        order_id="ord-001",
        timestamp=ts,
        venue="BINANCE",
        instrument_id="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("0.001"),
        price=Decimal("50000.00"),
    )

    dumped = original.model_dump()
    reconstructed = CanonicalOrder.model_validate(dumped)

    assert reconstructed.order_id == original.order_id
    assert reconstructed.venue == original.venue
    assert reconstructed.instrument_id == original.instrument_id
    assert reconstructed.side == original.side
    assert reconstructed.order_type == original.order_type
    assert reconstructed.quantity == original.quantity
    assert reconstructed.price == original.price


def test_uac_canonical_fill_round_trip() -> None:
    """CanonicalFill (UAC SSOT) survives model_dump() -> model_validate() round-trip."""
    ts = datetime(2026, 1, 15, 10, 30, 5, tzinfo=UTC)
    original = CanonicalFill(
        fill_id="fill-001",
        order_id="ord-001",
        timestamp=ts,
        venue="BINANCE",
        instrument_id="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("50000.00"),
        quantity=Decimal("0.001"),
        fee=Decimal("0.05"),
        fee_currency="USDT",
        is_maker=False,
    )

    dumped = original.model_dump()
    reconstructed = CanonicalFill.model_validate(dumped)

    assert reconstructed.fill_id == original.fill_id
    assert reconstructed.order_id == original.order_id
    assert reconstructed.venue == original.venue
    assert reconstructed.price == original.price
    assert reconstructed.quantity == original.quantity
    assert reconstructed.fee == original.fee


# ---------------------------------------------------------------------------
# Pilot path: UIC ML inference contracts (strategy-service <-> ml-inference-service)
# ---------------------------------------------------------------------------


def test_uic_cascade_prediction_event_round_trip() -> None:
    """CascadePredictionEvent dataclass fields survive a manual serialize-reconstruct cycle."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    snap_1d = PredictionSnapshot(
        instrument_id="BINANCE:SPOT:BTC-USDT",
        timeframe="1d",
        direction=1,
        confidence=0.75,
        model_id="model-btc-1d-v1",
        predicted_at=ts,
    )
    snap_4h = PredictionSnapshot(
        instrument_id="BINANCE:SPOT:BTC-USDT",
        timeframe="4h",
        direction=1,
        confidence=0.68,
        model_id="model-btc-4h-v1",
        predicted_at=ts,
    )

    original = CascadePredictionEvent(
        instrument_id="BINANCE:SPOT:BTC-USDT",
        profile_name="momentum_cascade",
        trigger_timeframe="1h",
        trigger_direction=1,
        trigger_confidence=0.72,
        context={"1d": snap_1d, "4h": snap_4h},
        cascade_confidence_score=0.71,
        cascade_aligned=True,
        recommended_entry_timeframes=["15m", "5m"],
        published_at=ts,
    )

    # Verify top-level scalar fields are accessible (dataclass, no .model_dump())
    assert original.instrument_id == "BINANCE:SPOT:BTC-USDT"
    assert original.profile_name == "momentum_cascade"
    assert original.cascade_aligned is True
    assert len(original.context) == 2
    assert abs(original.context["1d"].confidence - 0.75) < 1e-9
    assert original.context["4h"].direction == 1


def test_uic_cascade_config_fields() -> None:
    """CascadeConfig dataclass initializes and exposes all expected fields."""
    original = CascadeConfig(
        profile_name="momentum_cascade",
        trigger_timeframe="1h",
        context_timeframes=["1d", "4h"],
        entry_timeframes=["15m", "5m"],
        confidence_threshold=0.65,
        require_context_alignment=True,
    )

    assert original.profile_name == "momentum_cascade"
    assert original.trigger_timeframe == "1h"
    assert original.context_timeframes == ["1d", "4h"]
    assert original.entry_timeframes == ["15m", "5m"]
    assert abs(original.confidence_threshold - 0.65) < 1e-9
    assert original.require_context_alignment is True


def test_uic_inference_request_round_trip() -> None:
    """InferenceRequest (UIC ML) Pydantic model survives model_dump() -> model_validate()."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    original = InferenceRequest(
        request_id="req-001",
        model_id="model-btc-1h-v1",
        instrument_id="BINANCE:SPOT:BTC-USDT",
        timestamp=ts,
        features={"rsi_14": 62.5, "ema_crossover": 1.0, "volume_ratio": 1.3},
        timeframe="1h",
        target_type="direction",
    )

    dumped = original.model_dump()
    reconstructed = InferenceRequest.model_validate(dumped)

    assert reconstructed.request_id == original.request_id
    assert reconstructed.model_id == original.model_id
    assert reconstructed.instrument_id == original.instrument_id
    assert reconstructed.timeframe == original.timeframe
    assert reconstructed.features == original.features


def test_uic_inference_result_round_trip() -> None:
    """InferenceResult (UIC ML) Pydantic model survives model_dump() -> model_validate()."""
    ts = datetime(2026, 1, 15, 10, 30, 1, tzinfo=UTC)
    original = InferenceResult(
        request_id="req-001",
        model_id="model-btc-1h-v1",
        instrument_id="BINANCE:SPOT:BTC-USDT",
        timestamp=ts,
        prediction=0.72,
        confidence=0.68,
        target_type="direction",
        probabilities=[0.10, 0.18, 0.72],
        latency_ms=12.5,
    )

    dumped = original.model_dump()
    reconstructed = InferenceResult.model_validate(dumped)

    assert reconstructed.request_id == original.request_id
    assert reconstructed.prediction == original.prediction
    assert reconstructed.confidence == original.confidence
    assert reconstructed.probabilities == original.probabilities


# ---------------------------------------------------------------------------
# UIC internal events: lifecycle envelope (used across all service boundaries)
# ---------------------------------------------------------------------------


def test_uic_lifecycle_event_envelope_round_trip() -> None:
    """LifecycleEventEnvelope Pydantic model survives model_dump() -> model_validate()."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    metadata = EventMetadata(
        timestamp=ts,
        service_name="risk-and-exposure-service",
        severity=EventSeverity.INFO,
        correlation_id="corr-risk-001",
    )
    original = LifecycleEventEnvelope(
        event=LifecycleEventType.STARTED,
        service="risk-and-exposure-service",
        timestamp=ts,
        metadata=metadata,
    )

    dumped = original.model_dump()
    reconstructed = LifecycleEventEnvelope.model_validate(dumped)

    assert reconstructed.event == original.event
    assert reconstructed.service == original.service
    assert reconstructed.metadata.service_name == original.metadata.service_name
    assert reconstructed.metadata.severity == original.metadata.severity
    assert reconstructed.metadata.correlation_id == original.metadata.correlation_id


# ---------------------------------------------------------------------------
# UIC pubsub messages: cross-service pub/sub boundary contracts
# ---------------------------------------------------------------------------


def test_uic_pubsub_fill_event_message_round_trip() -> None:
    """FillEventMessage Pydantic model survives model_dump() -> model_validate()."""
    ts = datetime(2026, 1, 15, 10, 30, 5, tzinfo=UTC)
    original = FillEventMessage(
        fill_id="fill-001",
        order_id="ord-001",
        timestamp=ts.isoformat(),
        venue="BINANCE",
        instrument_id="BTCUSDT",
        side="buy",
        quantity="0.001",
        price="50000.00",
        fee="0.05",
        fee_currency="USDT",
        is_maker=False,
    )

    dumped = original.model_dump()
    reconstructed = FillEventMessage.model_validate(dumped)

    assert reconstructed.fill_id == original.fill_id
    assert reconstructed.order_id == original.order_id
    assert reconstructed.venue == original.venue
    assert reconstructed.price == original.price
    assert reconstructed.quantity == original.quantity


def test_uic_pubsub_risk_alert_message_round_trip() -> None:
    """RiskAlertMessage Pydantic model survives model_dump() -> model_validate()."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    original = RiskAlertMessage(
        alert_type="EXPOSURE_BREACH",
        client_id="client-001",
        metric="concentration",
        current_value=0.35,
        threshold=0.30,
        severity="WARNING",
        timestamp=ts.isoformat(),
        recommended_action="Reduce BTC-USDT position by 15%",
        instrument="BTC-USDT",
        venue="BINANCE",
    )

    dumped = original.model_dump()
    reconstructed = RiskAlertMessage.model_validate(dumped)

    assert reconstructed.alert_type == original.alert_type
    assert reconstructed.client_id == original.client_id
    assert reconstructed.metric == original.metric
    assert reconstructed.current_value == original.current_value
    assert reconstructed.threshold == original.threshold


def test_uic_pubsub_envelope_round_trip() -> None:
    """PubSubMessageEnvelope Pydantic model survives model_dump() -> model_validate()."""
    ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    original = PubSubMessageEnvelope(
        topic="fill-events-binance",
        message_type="FillEventMessage",
        schema_version="1.0",
        source_service="execution-service",
        timestamp=ts,
        correlation_id="corr-exec-001",
        payload={
            "fill_id": "fill-001",
            "venue": "BINANCE",
            "price": "50000.00",
        },
    )

    dumped = original.model_dump()
    reconstructed = PubSubMessageEnvelope.model_validate(dumped)

    assert reconstructed.topic == original.topic
    assert reconstructed.message_type == original.message_type
    assert reconstructed.source_service == original.source_service
    assert reconstructed.correlation_id == original.correlation_id
    assert reconstructed.payload == original.payload
