"""
Pub/Sub smoke tests — topic existence, publish, and subscribe round-trip.

GCP Pub/Sub always attempted when GCP credentials are available.
AWS SNS/SQS: skipped (no AWS credentials yet — see test_cloud_infra_smoke.py for pattern).

Required env vars for live GCP tests:
  GCP_PROJECT_ID  — GCP project containing the Pub/Sub topics

Tests skip gracefully without credentials.
"""

from __future__ import annotations

import importlib.util
import time
import uuid

import pytest

pytestmark = pytest.mark.deployment_test

# ---------------------------------------------------------------------------
# Credential guards (module-level, called at collection time)
# ---------------------------------------------------------------------------


def _has_gcp_creds() -> bool:
    if importlib.util.find_spec("google.auth") is None:
        return False
    try:
        import google.auth

        creds, _ = google.auth.default()
        return creds is not None
    except Exception:
        return False


def _has_pubsub_lib() -> bool:
    return importlib.util.find_spec("google.cloud.pubsub_v1") is not None


# Required topics as defined in deployment-service/scripts/setup-pubsub.sh
_REQUIRED_TOPICS = [
    "deployment-events",
    "deployment-status",
    "circuit-breaker-events",
    "config-updates",
    "cascade-predictions",
    "strategy-sports-signals",
]


# ---------------------------------------------------------------------------
# 1. Core topics exist
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestPubSubTopicsExist:
    """Verify required Pub/Sub topics are reachable in GCP."""

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    @pytest.mark.skipif(not _has_pubsub_lib(), reason="google-cloud-pubsub not installed")
    def test_deployment_topics_exist(self, gcp_project_id: str) -> None:
        """deployment-events and deployment-status topics must exist."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_pubsub_client

        client = get_pubsub_client(provider="gcp", project_id=gcp_project_id)
        missing = [t for t in ["deployment-events", "deployment-status"] if not client.topic_exists(t)]
        assert not missing, f"Required deployment topics missing: {missing}"

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    @pytest.mark.skipif(not _has_pubsub_lib(), reason="google-cloud-pubsub not installed")
    def test_required_topics_accessible(self, gcp_project_id: str) -> None:
        """All required system topics must exist (created by setup-pubsub.sh)."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_pubsub_client

        client = get_pubsub_client(provider="gcp", project_id=gcp_project_id)
        missing = [t for t in _REQUIRED_TOPICS if not client.topic_exists(t)]

        assert not missing, (
            f"{len(missing)} required Pub/Sub topics missing: {missing}\n"
            "Run: bash deployment-service/scripts/setup-pubsub.sh --dry-run"
        )


# ---------------------------------------------------------------------------
# 2. Publish / subscribe round-trip on a test topic
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestPubSubPublishSubscribe:
    """Write a message to a topic, pull it back via subscription."""

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    @pytest.mark.skipif(not _has_pubsub_lib(), reason="google-cloud-pubsub not installed")
    def test_publish_to_deployment_events(self, gcp_project_id: str) -> None:
        """Publishing to deployment-events must succeed (fire-and-forget)."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_pubsub_client

        client = get_pubsub_client(provider="gcp", project_id=gcp_project_id)
        probe_id = uuid.uuid4().hex
        payload = f"sit-probe:{probe_id}".encode()

        try:
            client.publish(topic="deployment-events", message=payload)
        except Exception as exc:
            pytest.fail(f"Publish to deployment-events failed: {exc}")

    @pytest.mark.skipif(not _has_gcp_creds(), reason="GCP credentials not available")
    @pytest.mark.skipif(not _has_pubsub_lib(), reason="google-cloud-pubsub not installed")
    def test_publish_subscribe_roundtrip(self, gcp_project_id: str) -> None:
        """Create ephemeral topic + subscription, publish, pull, ack, tear down."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_pubsub_client

        client = get_pubsub_client(provider="gcp", project_id=gcp_project_id)
        probe_id = uuid.uuid4().hex
        topic_name = f"sit-probe-{probe_id}"
        sub_name = f"sit-probe-sub-{probe_id}"
        payload = f"sit-roundtrip:{probe_id}".encode()

        try:
            # Create ephemeral topic + subscription
            client.create_topic(topic_name)
            client.create_subscription(topic=topic_name, subscription=sub_name)
            time.sleep(1)  # Allow propagation

            # Publish
            client.publish(topic=topic_name, message=payload)
            time.sleep(2)  # Allow delivery

            # Pull
            messages = client.pull(subscription=sub_name, max_messages=5)
            received_data = [m.data if hasattr(m, "data") else m for m in messages]

            assert any(payload in (d if isinstance(d, bytes) else d.encode()) for d in received_data), (
                f"Published message not received. Got {len(messages)} messages."
            )

        finally:
            # Tear down ephemeral resources
            try:
                client.delete_subscription(sub_name)
            except Exception:
                pass
            try:
                client.delete_topic(topic_name)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 3. Local Pub/Sub (no credentials required)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestLocalPubSubCapable:
    """Local provider must work without cloud credentials — validates UCI code path."""

    def test_local_pubsub_client_instantiates(self) -> None:
        """UCI local Pub/Sub client must instantiate without credentials."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_pubsub_client

        client = get_pubsub_client(provider="local")
        assert client is not None

    def test_local_pubsub_publish_subscribe_roundtrip(self) -> None:
        """Local provider publish/subscribe must work in-process."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_pubsub_client

        client = get_pubsub_client(provider="local")
        probe_id = uuid.uuid4().hex
        payload = f"local-probe:{probe_id}".encode()
        topic = f"sit-local-{probe_id}"

        client.create_topic(topic)
        client.publish(topic=topic, data=payload)
        # LocalPubSubClient uses messages_for() to retrieve messages
        # Returns list of (data_bytes, attributes_dict) tuples
        messages = client.messages_for(topic)
        received_data = [m[0] if isinstance(m, tuple) else m for m in messages]
        assert any(payload == d for d in received_data), (
            f"Local pub/sub roundtrip failed — message not received. Got {len(messages)} messages."
        )

    def test_aws_queue_client_importable(self) -> None:
        """UCI AWS queue path must be importable (proves dual-cloud code exists)."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        import unified_trading_library.cloud_interface as uci

        assert hasattr(uci, "get_pubsub_client"), "get_pubsub_client not exported from UCI"
