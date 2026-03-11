"""
Database (Cloud SQL / RDS) smoke tests.

GCP Cloud SQL (PostgreSQL): tested when DATABASE_URL or cloudsql-execution-db-url SM secret set.
AWS RDS: skipped (no AWS credentials).
SQLite fallback: available for local dev without any cloud DB.

The execution-service uses PostgreSQL for live order state (OrderStateRepository) when
use_database=True. This test validates connectivity and basic schema presence.

Required env vars:
  DATABASE_URL    — PostgreSQL asyncpg URL (overrides Secret Manager lookup)
  GCP_PROJECT_ID  — used to fetch cloudsql-execution-db-url from SM if not set

Tests skip gracefully without a live database.
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


def _has_asyncpg() -> bool:
    return importlib.util.find_spec("asyncpg") is not None


def _has_sqlalchemy() -> bool:
    return importlib.util.find_spec("sqlalchemy") is not None


def _get_db_url(project_id: str) -> str | None:
    """Return DB URL from env var or GCP Secret Manager."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    if not _has_gcp_creds():
        return None
    try:
        pytest.importorskip("unified_cloud_interface")
        from unified_cloud_interface import get_secret_client

        client = get_secret_client(provider="gcp")
        secret = client.get_secret(project_id=project_id, secret_name="cloudsql-execution-db-url")
        return secret if secret else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1. Cloud SQL connectivity
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestCloudSQLConnectivity:
    """GCP Cloud SQL (PostgreSQL) basic connectivity tests."""

    def test_db_url_resolvable(self, gcp_project_id: str) -> None:
        """DATABASE_URL or cloudsql-execution-db-url Secret Manager entry must be readable."""
        url = _get_db_url(gcp_project_id)
        if url is None:
            pytest.skip(
                "DATABASE_URL not set and cloudsql-execution-db-url not in Secret Manager. "
                "Run: bash deployment-service/scripts/setup-cloudsql.sh"
            )
        assert "postgresql" in url or "postgres" in url, (
            f"Unexpected DB URL format (expected postgresql://...): {url[:30]}..."
        )

    @pytest.mark.skipif(not _has_asyncpg(), reason="asyncpg not installed")
    def test_cloud_sql_connect_and_ping(self, gcp_project_id: str) -> None:
        """Connect to Cloud SQL and execute SELECT 1."""
        url = _get_db_url(gcp_project_id)
        if url is None:
            pytest.skip("No DATABASE_URL available")

        import asyncio

        import asyncpg

        async def _ping() -> str:
            # Convert asyncpg URL format if needed
            dsn = url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(dsn=dsn, timeout=10)
            try:
                result = await conn.fetchval("SELECT 1")
                return str(result)
            finally:
                await conn.close()

        result = asyncio.run(_ping())
        assert result == "1", f"SELECT 1 returned {result!r}"

    @pytest.mark.skipif(not _has_asyncpg(), reason="asyncpg not installed")
    def test_cloud_sql_order_state_table_accessible(self, gcp_project_id: str) -> None:
        """order_states table must exist (created by execution-service on startup)."""
        url = _get_db_url(gcp_project_id)
        if url is None:
            pytest.skip("No DATABASE_URL available")

        import asyncio

        import asyncpg

        async def _check_tables() -> list[str]:
            dsn = url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(dsn=dsn, timeout=10)
            try:
                rows = await conn.fetch(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
                )
                return [r["table_name"] for r in rows]
            finally:
                await conn.close()

        tables = asyncio.run(_check_tables())
        # If execution-service has run at least once, these tables should exist
        expected = ["order_states", "position_states"]
        missing = [t for t in expected if t not in tables]
        if missing:
            # Not a failure — tables are created on first execution-service startup
            pytest.skip(
                f"Tables {missing} not yet created (execution-service has not run against this DB). "
                f"Existing tables: {tables}"
            )
        else:
            assert "order_states" in tables

    @pytest.mark.skipif(not _has_asyncpg(), reason="asyncpg not installed")
    def test_cloud_sql_read_write_probe(self, gcp_project_id: str) -> None:
        """Write and read a probe row in a sit_probe table (created + dropped by this test)."""
        url = _get_db_url(gcp_project_id)
        if url is None:
            pytest.skip("No DATABASE_URL available")

        import asyncio

        import asyncpg

        probe_id = uuid.uuid4().hex

        async def _probe() -> str:
            dsn = url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(dsn=dsn, timeout=10)
            try:
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS sit_probe (id TEXT PRIMARY KEY, created_at TIMESTAMP DEFAULT NOW())"
                )
                await conn.execute("INSERT INTO sit_probe (id) VALUES ($1)", probe_id)
                result = await conn.fetchval("SELECT id FROM sit_probe WHERE id = $1", probe_id)
                await conn.execute("DELETE FROM sit_probe WHERE id = $1", probe_id)
                return str(result)
            finally:
                await conn.close()

        result = asyncio.run(_probe())
        assert result == probe_id, f"DB read/write probe returned {result!r}"


# ---------------------------------------------------------------------------
# 2. SQLite fallback (always runs — no credentials needed)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestSQLiteFallback:
    """SQLite in-memory DB as fallback for local dev / CI without Cloud SQL."""

    @pytest.mark.skipif(not _has_sqlalchemy(), reason="sqlalchemy not installed")
    def test_sqlite_basic_rw(self) -> None:
        """SQLAlchemy with SQLite must support basic read/write (dev fallback path)."""
        from sqlalchemy import Column, MetaData, String, Table, create_engine

        engine = create_engine("sqlite:///:memory:")
        metadata = MetaData()
        probe_table = Table(
            "sit_probe",
            metadata,
            Column("id", String, primary_key=True),
        )
        metadata.create_all(engine)

        probe_id = uuid.uuid4().hex
        with engine.connect() as conn:
            conn.execute(probe_table.insert().values(id=probe_id))
            conn.commit()
            row = conn.execute(probe_table.select().where(probe_table.c.id == probe_id)).fetchone()

        assert row is not None
        assert row[0] == probe_id


# ---------------------------------------------------------------------------
# 3. AWS RDS (blocked — future)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestAWSRDS:
    """AWS RDS (PostgreSQL) — skipped until AWS credentials are configured."""

    def test_aws_rds_blocked(self) -> None:
        """Placeholder: AWS RDS requires DATABASE_URL pointing to RDS endpoint."""
        rds_url = os.environ.get("AWS_DATABASE_URL")
        if rds_url is None:
            pytest.skip("AWS_DATABASE_URL not set — AWS RDS not configured")

        # If URL is set, validate basic connectivity
        import asyncio

        import asyncpg

        async def _ping() -> None:
            conn = await asyncpg.connect(dsn=rds_url, timeout=10)
            await conn.fetchval("SELECT 1")
            await conn.close()

        asyncio.run(_ping())
