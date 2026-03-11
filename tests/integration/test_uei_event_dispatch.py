"""UEI event dispatch structural test."""

import pytest
from unified_events_interface import log_event, setup_events

pytestmark = pytest.mark.code_test


def test_log_event_importable() -> None:
    assert callable(log_event)
    assert callable(setup_events)
