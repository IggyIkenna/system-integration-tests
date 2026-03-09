"""UEI event dispatch structural test."""

from unified_events_interface import log_event, setup_events


def test_log_event_importable() -> None:
    assert callable(log_event)
    assert callable(setup_events)
