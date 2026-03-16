"""Sports domain scenario definitions.

Five realistic scenarios covering sports betting market events:
1. pregame_to_inplay — transition from pre-game odds to in-play trading
2. halftime_odds_shift — halftime break causing major odds re-pricing
3. cancellation — match cancellation requiring position unwind
4. late_lineup_change — late team news moving odds pre-kickoff
5. walkover — opponent withdrawal / walkover (void market)

Each scenario returns a list of :class:`ScenarioEvent` instances with realistic
timestamps and data payloads. No live services required.
"""

from __future__ import annotations

from tests.scenarios.framework import (
    ScenarioAssertion,
    ScenarioDomain,
    ScenarioEvent,
    ScenarioPlaybook,
)

_DOMAIN = ScenarioDomain.SPORTS


def _pregame_to_inplay() -> ScenarioPlaybook:
    """Pre-game market transitions to in-play with live odds updates."""
    return ScenarioPlaybook(
        name="sports_pregame_to_inplay",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="market_state",
                data={"match_id": "EPL-2026-0315", "state": "pregame", "home_odds": "2.10", "away_odds": "3.40"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="market_state",
                data={"match_id": "EPL-2026-0315", "state": "suspended", "reason": "kickoff_imminent"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=3000,
                event_type="market_state",
                data={"match_id": "EPL-2026-0315", "state": "inplay", "home_odds": "2.05", "away_odds": "3.50"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=5000,
                event_type="match_event",
                data={"match_id": "EPL-2026-0315", "event": "goal", "team": "home", "minute": "12"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=5500,
                event_type="odds_update",
                data={"match_id": "EPL-2026-0315", "home_odds": "1.45", "away_odds": "6.50", "draw_odds": "4.80"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="state", operator="eq", expected="inplay"),
            ScenarioAssertion(field="event", operator="eq", expected="goal"),
            ScenarioAssertion(field="team", operator="eq", expected="home"),
        ],
    )


def _halftime_odds_shift() -> ScenarioPlaybook:
    """Halftime break causing major odds re-pricing after first-half dominance."""
    return ScenarioPlaybook(
        name="sports_halftime_odds_shift",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="match_event",
                data={"match_id": "UCL-2026-QF1", "event": "halftime", "score": "3-0"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=500,
                event_type="market_state",
                data={"match_id": "UCL-2026-QF1", "state": "halftime_break", "suspended": "true"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=900000,
                event_type="odds_update",
                data={"match_id": "UCL-2026-QF1", "home_odds": "1.02", "away_odds": "40.00", "draw_odds": "20.00"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=900500,
                event_type="market_state",
                data={"match_id": "UCL-2026-QF1", "state": "second_half", "suspended": "false"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="state", operator="eq", expected="second_half"),
            ScenarioAssertion(field="score", operator="eq", expected="3-0"),
            ScenarioAssertion(field="suspended", operator="eq", expected="false"),
        ],
    )


def _cancellation() -> ScenarioPlaybook:
    """Match cancellation (e.g. weather) requiring full position unwind."""
    return ScenarioPlaybook(
        name="sports_cancellation",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="market_state",
                data={"match_id": "NFL-2026-W12", "state": "pregame", "home_odds": "1.90"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="bet_placed",
                data={"match_id": "NFL-2026-W12", "side": "home", "stake": "500.00", "odds": "1.90"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=5000,
                event_type="match_event",
                data={"match_id": "NFL-2026-W12", "event": "cancellation", "reason": "severe_weather"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=5500,
                event_type="market_void",
                data={
                    "match_id": "NFL-2026-W12",
                    "void_type": "full",
                    "refund_status": "processed",
                    "refund": "500.00",
                },
            ),
        ],
        assertions=[
            ScenarioAssertion(field="event", operator="eq", expected="cancellation"),
            ScenarioAssertion(field="reason", operator="eq", expected="severe_weather"),
            ScenarioAssertion(field="refund_status", operator="eq", expected="processed"),
        ],
    )


def _late_lineup_change() -> ScenarioPlaybook:
    """Late team news (star player injured) moving odds shortly before kickoff."""
    return ScenarioPlaybook(
        name="sports_late_lineup_change",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="odds_update",
                data={"match_id": "EPL-2026-0316", "home_odds": "1.80", "away_odds": "4.00"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=600000,
                event_type="lineup_change",
                data={
                    "match_id": "EPL-2026-0316",
                    "team": "home",
                    "player_out": "Haaland",
                    "player_in": "Alvarez",
                    "reason": "injury",
                },
            ),
            ScenarioEvent(
                timestamp_offset_ms=600500,
                event_type="odds_update",
                data={"match_id": "EPL-2026-0316", "home_odds": "2.20", "away_odds": "3.20"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=601000,
                event_type="risk_reassess",
                data={"match_id": "EPL-2026-0316", "action": "reduce_stake", "old_edge": "5.2", "new_edge": "1.8"},
            ),
        ],
        assertions=[
            ScenarioAssertion(field="reason", operator="eq", expected="injury"),
            ScenarioAssertion(field="action", operator="eq", expected="reduce_stake"),
            ScenarioAssertion(field="player_out", operator="eq", expected="Haaland"),
        ],
    )


def _walkover() -> ScenarioPlaybook:
    """Opponent withdrawal / walkover voiding the match market."""
    return ScenarioPlaybook(
        name="sports_walkover",
        domain=_DOMAIN,
        events=[
            ScenarioEvent(
                timestamp_offset_ms=0,
                event_type="market_state",
                data={"match_id": "ATP-2026-R16", "state": "pregame", "p1_odds": "1.50", "p2_odds": "2.60"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=1000,
                event_type="bet_placed",
                data={"match_id": "ATP-2026-R16", "side": "p1", "stake": "200.00", "odds": "1.50"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=3600000,
                event_type="match_event",
                data={"match_id": "ATP-2026-R16", "event": "walkover", "winner": "p1", "reason": "opponent_withdrawal"},
            ),
            ScenarioEvent(
                timestamp_offset_ms=3600500,
                event_type="market_void",
                data={
                    "match_id": "ATP-2026-R16",
                    "void_type": "walkover",
                    "refund_status": "processed",
                    "refund": "200.00",
                },
            ),
        ],
        assertions=[
            ScenarioAssertion(field="event", operator="eq", expected="walkover"),
            ScenarioAssertion(field="void_type", operator="eq", expected="walkover"),
            ScenarioAssertion(field="refund_status", operator="eq", expected="processed"),
        ],
    )


def get_scenarios() -> list[ScenarioPlaybook]:
    """Return all Sports scenarios."""
    return [
        _pregame_to_inplay(),
        _halftime_odds_shift(),
        _cancellation(),
        _late_lineup_change(),
        _walkover(),
    ]
