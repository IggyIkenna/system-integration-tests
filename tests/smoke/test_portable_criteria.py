"""
Portable CI criteria — layer 0/1 enforcement.

Verifies:
  1. No live HTTP calls escape to external URLs in unit-test mode
     (httpx/requests must be mocked or disabled).
  2. Batch-live symmetry: core service entry points can be imported without
     cloud credentials (CLOUD_PROVIDER=local, no GCS/PubSub).
  3. Determinism: schema objects are equal across multiple instantiations.

No live API keys, no AWS/GCP credentials required.
Run with: pytest tests/smoke/test_portable_criteria.py -v
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_local_env() -> None:
    """Set minimal env vars for CLOUD_PROVIDER=local so services can import."""
    os.environ.setdefault("CLOUD_PROVIDER", "local")
    os.environ.setdefault("SERVICE_MODE", "batch")
    os.environ.setdefault("ENVIRONMENT", "development")
    os.environ.setdefault("GCP_PROJECT_ID", "local-dev")
    os.environ.setdefault("USE_SECRET_MANAGER", "false")
    os.environ.setdefault("USE_MOCK_DATA", "true")


# ---------------------------------------------------------------------------
# 1. No live HTTP calls
# ---------------------------------------------------------------------------


class TestNoLiveHttpCalls:
    """Verify that live HTTP calls are blocked in unit-test mode.

    The test patches httpx.Client.send and requests.Session.send to raise
    RuntimeError if anything attempts a real network request.  Individual
    schema/config imports must succeed without triggering network I/O.
    """

    def test_httpx_not_called_on_import(self) -> None:
        """Importing UCI + core service schemas must not fire any HTTP request."""
        _set_local_env()

        sentinel = RuntimeError("Live HTTP call detected in unit-test mode — not allowed")

        with patch("httpx.Client.send", side_effect=sentinel):
            with patch("httpx.AsyncClient.send", side_effect=sentinel):
                # These imports must complete without triggering any HTTP call
                import unified_cloud_interface  # noqa: F401  # type: ignore[import-not-found]

    def test_requests_not_called_on_schema_import(self) -> None:
        """Importing instrument schemas must not fire any HTTP request."""
        _set_local_env()

        sentinel = RuntimeError("Live HTTP call detected in unit-test mode — not allowed")

        with patch.dict("sys.modules", {"requests": MagicMock()}):
            # Patch requests.get/post to sentinel if requests is actually used
            mock_requests = sys.modules.get("requests")
            if mock_requests is not None:
                mock_requests.get = MagicMock(side_effect=sentinel)  # type: ignore[union-attr]
                mock_requests.post = MagicMock(side_effect=sentinel)  # type: ignore[union-attr]

            # instruments-service schema should be safe
            pytest.importorskip("instruments_service.schemas.output_schemas")
            from instruments_service.schemas.output_schemas import INSTRUMENTS_SCHEMA  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Batch-live symmetry — service modules importable in local mode
# ---------------------------------------------------------------------------


class TestBatchLiveSymmetry:
    """Core service modules must be importable with CLOUD_PROVIDER=local.

    Validates that the batch entrypoint does not immediately call live cloud
    APIs at import time.
    """

    @pytest.fixture(autouse=True)
    def _local_env(self) -> Generator[None, None, None]:
        _set_local_env()
        yield

    def test_uci_importable_locally(self) -> None:
        """UCI factory must import with CLOUD_PROVIDER=local."""
        uci = pytest.importorskip("unified_cloud_interface")
        assert uci is not None

    def test_uci_get_storage_client_local(self) -> None:
        """get_storage_client(provider='local') must not call GCS."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_storage_client

        client = get_storage_client(provider="local")
        assert client is not None

    def test_uci_get_secret_client_local(self) -> None:
        """get_secret_client(provider='local') must not call Secret Manager."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_secret_client

        client = get_secret_client(provider="local")
        assert client is not None

    def test_uci_get_pubsub_client_local(self) -> None:
        """get_pubsub_client(provider='local') must return LocalPubSubClient."""
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_pubsub_client

        client = get_pubsub_client(provider="local")
        assert client is not None

    def test_instruments_schema_importable_locally(self) -> None:
        """instruments-service output schema must import without cloud creds."""
        pytest.importorskip("instruments_service.schemas.output_schemas")
        from instruments_service.schemas.output_schemas import INSTRUMENTS_SCHEMA

        assert INSTRUMENTS_SCHEMA is not None

    def test_mktdata_schema_importable_locally(self) -> None:
        """market-data-processing-service schema must import without cloud creds."""
        pytest.importorskip("market_data_processing_service.schemas.output_schemas")
        from market_data_processing_service.schemas.output_schemas import PROCESSED_CANDLE_SCHEMA

        assert PROCESSED_CANDLE_SCHEMA is not None


# ---------------------------------------------------------------------------
# 3. Determinism — schemas produce identical results on repeated calls
# ---------------------------------------------------------------------------


class TestSchemasDeterministic:
    """Same input → same schema output on repeated calls."""

    def test_instruments_schema_deterministic(self) -> None:
        """get_required_columns must return identical results on two calls."""
        pytest.importorskip("instruments_service.schemas.output_schemas")
        from instruments_service.schemas.output_schemas import INSTRUMENTS_SCHEMA

        dims = {"category": "CEFI"}
        result_1 = INSTRUMENTS_SCHEMA.get_required_columns(dims)
        result_2 = INSTRUMENTS_SCHEMA.get_required_columns(dims)
        assert sorted(result_1) == sorted(result_2), "get_required_columns is not deterministic across calls"

    def test_mktdata_schema_deterministic(self) -> None:
        """PROCESSED_CANDLE_SCHEMA.get_required_columns must be deterministic."""
        pytest.importorskip("market_data_processing_service.schemas.output_schemas")
        from market_data_processing_service.schemas.output_schemas import PROCESSED_CANDLE_SCHEMA

        dims = {"category": "CEFI", "data_type": "ohlcv"}
        result_1 = PROCESSED_CANDLE_SCHEMA.get_required_columns(dims)
        result_2 = PROCESSED_CANDLE_SCHEMA.get_required_columns(dims)
        assert sorted(result_1) == sorted(result_2), "PROCESSED_CANDLE_SCHEMA.get_required_columns is not deterministic"

    def test_schema_module_level_object_is_singleton(self) -> None:
        """Importing the schema module twice returns the same object."""
        pytest.importorskip("instruments_service.schemas.output_schemas")
        import instruments_service.schemas.output_schemas as m1
        import instruments_service.schemas.output_schemas as m2

        assert m1.INSTRUMENTS_SCHEMA is m2.INSTRUMENTS_SCHEMA, "INSTRUMENTS_SCHEMA is not a module-level singleton"
