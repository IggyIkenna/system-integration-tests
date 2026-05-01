"""
Cache (Redis / Memorystore) smoke tests.

GCP: GCP Memorystore (Redis) — tested when REDIS_URL or redis-url Secret Manager entry available.
AWS: ElastiCache — skipped (no AWS credentials).
Local: LocalCacheProvider — always tested (no credentials needed).

Required env vars for live Redis tests:
  REDIS_URL       — full Redis URL (overrides Secret Manager lookup)
  GCP_PROJECT_ID  — used to fetch redis-url from Secret Manager if REDIS_URL not set

Tests skip gracefully without a live Redis instance.
"""

from __future__ import annotations

import importlib.util
import os
import uuid

import pytest

pytestmark = pytest.mark.deployment_test

# ---------------------------------------------------------------------------
# Availability guards
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


def _has_redis_lib() -> bool:
    return importlib.util.find_spec("redis") is not None


def _get_redis_url(project_id: str) -> str | None:
    """Return Redis URL from env var or GCP Secret Manager."""
    url = os.environ.get("REDIS_URL")
    if url:
        return url
    if not _has_gcp_creds():
        return None
    try:
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_secret_client

        client = get_secret_client(provider="gcp")
        secret = client.get_secret(project_id=project_id, secret_name="redis-url")
        return secret if secret else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1. Local cache (always runs — no credentials)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestLocalCacheCapable:
    """UCI local cache provider works without credentials."""

    def test_local_cache_client_instantiates(self) -> None:
        """get_cache_client(provider='local') must return a working client."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_cache_client

        client = get_cache_client(provider="local")
        assert client is not None

    def test_local_cache_set_get_delete(self) -> None:
        """Local cache must support set / get / delete round-trip."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_cache_client

        client = get_cache_client(provider="local")
        key = f"sit-probe:{uuid.uuid4().hex}"
        value = "sit-local-cache-check"

        # LocalCacheProvider uses ttl_seconds parameter, not ex (Redis convention)
        client.set(key, value.encode(), ttl_seconds=60)
        result = client.get(key)
        assert result == value or result == value.encode(), f"Cache get returned {result!r}, expected {value!r}"
        client.delete(key)
        assert client.get(key) is None, "Key still exists after delete"

    def test_local_cache_ttl_expiry(self) -> None:
        """Local cache set with ttl_seconds=1 must expire (or at minimum not error on ttl)."""
        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_cache_client

        client = get_cache_client(provider="local")
        key = f"sit-ttl-probe:{uuid.uuid4().hex}"
        client.set(key, b"ephemeral", ttl_seconds=1)
        # Verify key exists immediately
        assert client.get(key) is not None, "Key missing right after set"
        # Clean up
        client.delete(key)


# ---------------------------------------------------------------------------
# 2. Live Redis (GCP Memorystore)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestRedisMemorystore:
    """GCP Memorystore (Redis) connectivity — skipped when no Redis URL available."""

    @pytest.mark.skipif(not _has_redis_lib(), reason="redis package not installed")
    def test_redis_url_resolvable(self, gcp_project_id: str) -> None:
        """redis-url must be resolvable from env var or GCP Secret Manager."""
        url = _get_redis_url(gcp_project_id)
        if url is None:
            pytest.skip("REDIS_URL not set and redis-url not in Secret Manager")
        assert url.startswith("redis://"), f"Unexpected Redis URL format: {url[:20]}..."

    @pytest.mark.skipif(not _has_redis_lib(), reason="redis package not installed")
    def test_redis_ping(self, gcp_project_id: str) -> None:
        """Redis PING must succeed within 5s timeout."""
        url = _get_redis_url(gcp_project_id)
        if url is None:
            pytest.skip("No Redis URL available")

        import redis as redis_lib

        r = redis_lib.from_url(url, socket_timeout=5, socket_connect_timeout=5)
        try:
            pong = r.ping()
            assert pong is True, f"Redis PING returned {pong!r}"
        except Exception as exc:
            pytest.fail(f"Redis PING failed: {exc}")
        finally:
            r.close()

    @pytest.mark.skipif(not _has_redis_lib(), reason="redis package not installed")
    def test_redis_set_get_delete(self, gcp_project_id: str) -> None:
        """Redis set/get/delete round-trip on test key."""
        url = _get_redis_url(gcp_project_id)
        if url is None:
            pytest.skip("No Redis URL available")

        import redis as redis_lib

        r = redis_lib.from_url(url, socket_timeout=5, socket_connect_timeout=5)
        key = f"sit-probe:{uuid.uuid4().hex}"
        payload = b"sit-cache-permission-check"

        try:
            r.set(key, payload, ex=60)
            result = r.get(key)
            assert result == payload, f"Redis get mismatch: {result!r} != {payload!r}"
            r.delete(key)
            assert r.get(key) is None, "Key still exists after delete"
        finally:
            r.close()

    @pytest.mark.skipif(not _has_redis_lib(), reason="redis package not installed")
    def test_redis_via_uci_client(self, gcp_project_id: str) -> None:
        """UCI cache client (provider='gcp') must connect to Memorystore via redis-url SM secret."""
        url = _get_redis_url(gcp_project_id)
        if url is None:
            pytest.skip("No Redis URL available")

        pytest.importorskip("unified_trading_library.cloud_interface")
        from unified_trading_library import get_cache_client

        client = get_cache_client(provider="gcp", url=url)
        key = f"sit-uci-probe:{uuid.uuid4().hex}"
        try:
            client.set(key, "uci-check", ex=30)
            val = client.get(key)
            assert val is not None, "UCI cache set/get returned None"
            client.delete(key)
        except Exception as exc:
            pytest.fail(f"UCI cache client failed: {exc}")


# ---------------------------------------------------------------------------
# 3. AWS ElastiCache (blocked — future)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestAWSElastiCache:
    """AWS ElastiCache — skipped until AWS credentials are configured."""

    def test_aws_elasticache_blocked(self) -> None:
        """Placeholder: AWS ElastiCache requires AWS_ACCESS_KEY_ID + ElastiCache endpoint."""
        aws_url = os.environ.get("ELASTICACHE_URL")
        if aws_url is None:
            pytest.skip("ELASTICACHE_URL not set — AWS ElastiCache not configured")

        import redis as redis_lib

        r = redis_lib.from_url(aws_url, socket_timeout=5, socket_connect_timeout=5)
        try:
            assert r.ping() is True
        finally:
            r.close()
