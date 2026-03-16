"""Medium-priority journey: Log query — filter -> search -> paginate.

Validates the log querying flow through logs-dashboard-ui + batch-audit-api:
    1. GET logs with a filter (request sent)
    2. Verify response has structured log entries (response received)
    3. Paginate to confirm consistent total count (state updated)

Priority: MEDIUM — runs in weekly SIT.
"""

from __future__ import annotations

from typing import cast

import httpx
import pytest

from tests.real_flow.conftest import FlowServiceURLs, TriadResult

pytestmark = [pytest.mark.real_flow, pytest.mark.medium, pytest.mark.e2e]

# Candidate log-query API paths (tried in order until one succeeds).
_LOG_PATHS = ("/api/logs", "/api/audit/logs", "/api/entries")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_log_endpoint(
    client: httpx.Client,
    base_url: str,
    params: dict[str, str],
) -> httpx.Response | None:
    """Try each candidate log path and return the first non-404 response."""
    for path in _LOG_PATHS:
        resp = client.get(f"{base_url}{path}", params=params, timeout=15.0)
        if resp.status_code != 404:
            return resp
    return None


def _extract_total(body: dict[str, object], fallback_items: list[object]) -> int:
    """Extract total count from a log-query response body."""
    raw = body.get("total", body.get("total_count", body.get("count", len(fallback_items))))
    return int(str(raw))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def batch_audit_api(flow_urls: FlowServiceURLs) -> str:
    """Batch audit API base URL, skipping if not reachable."""
    url = flow_urls.batch_audit_api
    try:
        with httpx.Client(timeout=5.0) as probe:
            resp = probe.get(f"{url}/health")
            if resp.status_code != 200:
                pytest.skip(f"batch-audit-api at {url} returned {resp.status_code}")
    except httpx.ConnectError:
        pytest.skip(f"batch-audit-api not reachable at {url}")
    return url


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.medium
def test_log_filter_search_paginate(
    flow_client: httpx.Client,
    batch_audit_api: str,
    triad: TriadResult,
) -> None:
    """Query logs with filters, then paginate results.

    Triad:
        1. Request sent: GET /api/logs with query filters
        2. Response valid: structured response with items and pagination metadata
        3. State updated: page 2 request consistent with total from page 1
    """
    # Phase 1: Filtered search
    page1_params = {"service": "instruments-service", "level": "INFO", "limit": "5", "offset": "0"}
    search_resp = _try_log_endpoint(flow_client, batch_audit_api, page1_params)
    triad.request_sent = True

    if search_resp is None:
        pytest.skip("Log query endpoint not available at /api/logs, /api/audit/logs, or /api/entries")

    # Phase 2: Validate response structure
    assert search_resp.status_code == 200, f"Log search failed: {search_resp.status_code} — {search_resp.text}"
    body = cast(dict[str, object], search_resp.json())
    items = body.get("items", body.get("entries", body.get("logs", body.get("results", []))))
    assert isinstance(items, list), f"Expected log entries list, got {type(items)}"
    total = _extract_total(body, cast(list[object], items))
    triad.response_valid = True

    # Phase 3: Pagination consistency — request page 2
    _verify_pagination(flow_client, batch_audit_api, total)
    triad.state_updated = True
    triad.assert_triad("log-query: filter -> search -> paginate")


def _verify_pagination(client: httpx.Client, base_url: str, page1_total: int) -> None:
    """Verify page 2 total is consistent with page 1 total."""
    if page1_total <= 5:
        return  # Not enough results to paginate

    page2_params = {"service": "instruments-service", "level": "INFO", "limit": "5", "offset": "5"}
    page2_resp = _try_log_endpoint(client, base_url, page2_params)
    if page2_resp is None or page2_resp.status_code != 200:
        return

    page2_body = cast(dict[str, object], page2_resp.json())
    page2_items = page2_body.get(
        "items", page2_body.get("entries", page2_body.get("logs", page2_body.get("results", [])))
    )
    fallback = cast(list[object], page2_items) if isinstance(page2_items, list) else []
    page2_total = _extract_total(page2_body, fallback)
    assert page1_total == page2_total, f"Pagination total mismatch: page1={page1_total}, page2={page2_total}"


@pytest.mark.medium
def test_log_query_health(
    flow_client: httpx.Client,
    batch_audit_api: str,
    triad: TriadResult,
) -> None:
    """Verify the batch-audit-api is healthy and responds to basic queries.

    Triad:
        1. Request sent: GET /health
        2. Response valid: status=healthy or status=ok
        3. State updated: second health check consistent
    """
    # Phase 1
    health_resp = flow_client.get(f"{batch_audit_api}/health", timeout=10.0)
    triad.request_sent = True

    # Phase 2
    assert health_resp.status_code == 200, f"Health check failed: {health_resp.status_code} — {health_resp.text}"
    body = cast(dict[str, object], health_resp.json())
    status = str(body.get("status", ""))
    assert status in ("healthy", "ok", "up"), f"Unexpected health status: {status}"
    triad.response_valid = True

    # Phase 3: Consistency
    health_resp2 = flow_client.get(f"{batch_audit_api}/health", timeout=10.0)
    assert health_resp2.status_code == 200
    body2 = cast(dict[str, object], health_resp2.json())
    assert body2.get("status") == body.get("status"), "Health status changed between reads"

    triad.state_updated = True
    triad.assert_triad("log-query: health check consistency")
