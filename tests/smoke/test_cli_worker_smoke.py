"""CLI worker regression guards.

UEI event constant names — ensures canonical names are stable.
Service import smoke tests removed: each repo's own QG validates importability.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.code_test


@pytest.mark.smoke
def test_uei_event_constants() -> None:
    """Regression guard: verify canonical event names are correct in UEI."""
    from unified_trading_library import STANDARD_LIFECYCLE_EVENTS

    # Must be present (canonical names)
    assert "MEMORY_THRESHOLD_REACHED" in STANDARD_LIFECYCLE_EVENTS, (
        "MEMORY_THRESHOLD_REACHED must be in STANDARD_LIFECYCLE_EVENTS"
    )
    assert "CPU_THRESHOLD_REACHED" in STANDARD_LIFECYCLE_EVENTS, (
        "CPU_THRESHOLD_REACHED must be in STANDARD_LIFECYCLE_EVENTS"
    )
    assert "DISK_THRESHOLD_REACHED" in STANDARD_LIFECYCLE_EVENTS, (
        "DISK_THRESHOLD_REACHED must be in STANDARD_LIFECYCLE_EVENTS"
    )

    # Must NOT be present (deprecated name)
    assert "SERVICE_MEMORY_CRITICAL" not in STANDARD_LIFECYCLE_EVENTS, (
        "SERVICE_MEMORY_CRITICAL has been renamed to MEMORY_THRESHOLD_REACHED — "
        "update any references in your service code"
    )
