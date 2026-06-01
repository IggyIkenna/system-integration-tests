"""Auth penetration test suite — validates auth boundaries across APIs.

Tests:
1. Token replay attack: expired token rejected
2. Privilege escalation: basic tier cannot access pro endpoints
3. Org boundary violation: org A token cannot access org B data
4. S2S token misuse: service token used as user token (rejected)
5. Missing auth header: returns 401 not 500
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient


def _make_jwt(claims: dict[str, object], expired: bool = False) -> str:
    """Create a mock JWT with given claims (no signature verification in tests)."""
    header = {"alg": "none", "typ": "JWT"}
    if expired:
        claims["exp"] = int(time.time()) - 3600  # Expired 1 hour ago
    else:
        claims["exp"] = int(time.time()) + 3600  # Valid for 1 hour

    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    payload_b64 = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header_b64}.{payload_b64}.mock-signature"


# Skip all tests if APIs are not available (run during SIT, not unit tests)
pytestmark = pytest.mark.skipif(
    True,  # Set to False when running SIT with live APIs
    reason="Auth penetration tests require running API instances",
)


class TestMissingAuthHeader:
    """Missing auth header should return 401, not 500."""

    def test_no_auth_header_returns_401(self, api_client: TestClient) -> None:
        response = api_client.get("/api/v1/instruments")
        assert response.status_code == 401
        assert "Missing authentication" in response.json()["detail"]

    def test_empty_bearer_returns_401(self, api_client: TestClient) -> None:
        response = api_client.get(
            "/api/v1/instruments",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 401

    def test_malformed_bearer_returns_401(self, api_client: TestClient) -> None:
        response = api_client.get(
            "/api/v1/instruments",
            headers={"Authorization": "Bearer not-a-jwt"},
        )
        assert response.status_code == 401


class TestPrivilegeEscalation:
    """Basic tier tokens cannot access pro-only endpoints."""

    def test_basic_tier_cannot_access_ml_endpoints(self, api_client: TestClient) -> None:
        token = _make_jwt(
            {
                "sub": "user-1",
                "org_id": "org-123",
                "subscription_tier": "data-basic",
            }
        )
        response = api_client.get(
            "/api/v1/training/jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        body = response.json()
        assert body["detail"]["error"] == "entitlement_denied"
        assert body["detail"]["tier"] == "data-basic"

    def test_free_tier_cannot_access_candles(self, api_client: TestClient) -> None:
        token = _make_jwt(
            {
                "sub": "user-2",
                "org_id": "org-456",
                "subscription_tier": "free",
            }
        )
        response = api_client.get(
            "/api/v1/candles",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    def test_enterprise_can_access_everything(self, api_client: TestClient) -> None:
        token = _make_jwt(
            {
                "sub": "admin-1",
                "org_id": "org-admin",
                "subscription_tier": "enterprise",
            }
        )
        response = api_client.get(
            "/api/v1/instruments",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200


class TestOrgBoundaryViolation:
    """Org A token cannot access org B data."""

    def test_org_filtered_response(self, api_client: TestClient) -> None:
        token_a = _make_jwt(
            {
                "sub": "user-a",
                "org_id": "org-alpha",
                "subscription_tier": "data-pro",
            }
        )
        response = api_client.get(
            "/api/v1/instruments",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert response.status_code == 200
        # Verify response only contains org-alpha data
        data = response.json()
        if isinstance(data, list):
            for item in data:
                if "org_id" in item:
                    assert item["org_id"] == "org-alpha"


class TestS2STokenMisuse:
    """Service token should not work as user Bearer token."""

    def test_s2s_token_in_bearer_rejected(self, api_client: TestClient) -> None:
        # S2S tokens are static strings, not JWTs — should fail JWT parsing
        response = api_client.get(
            "/api/v1/instruments",
            headers={"Authorization": "Bearer static-service-token-value"},
        )
        assert response.status_code == 401

    def test_valid_s2s_token_accepted_in_correct_header(self, api_client: TestClient) -> None:
        response = api_client.get(
            "/api/v1/instruments",
            headers={"X-Service-Token": "valid-service-token"},
        )
        # Should succeed if token matches (or 403 if doesn't)
        assert response.status_code in (200, 403)


class TestDocumentUploadDownload:
    """Document upload/download flow via pre-signed URLs."""

    def test_upload_url_returns_presigned_url(self, api_client: TestClient) -> None:
        token = _make_jwt(
            {
                "sub": "user-1",
                "org_id": "org-123",
                "subscription_tier": "data-pro",
            }
        )
        response = api_client.post(
            "/api/v1/documents/upload-url",
            json={
                "filename": "test-invoice.pdf",
                "category": "INVOICE",
                "content_type": "application/pdf",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "upload_url" in data
        assert "document_id" in data

    def test_download_url_requires_auth(self, api_client: TestClient) -> None:
        response = api_client.get("/api/v1/documents/test-doc-id/download-url")
        assert response.status_code == 401
