from datetime import UTC, datetime, timedelta

import pytest

from services.replay import ReplayEngine, ReplayEvent, ReplayPhase, ReplaySession


def _event(
    event_id: str,
    *,
    observed_at: datetime,
    phase: ReplayPhase,
    stream: str,
    sequence: int,
) -> ReplayEvent:
    return ReplayEvent(
        event_id=event_id,
        observed_at=observed_at,
        phase=phase,
        stream=stream,
        sequence=sequence,
        event_type=event_id,
        payload={"event_id": event_id},
    )


def test_same_timestamp_orders_by_phase_before_fill() -> None:
    timestamp = datetime(2026, 8, 31, 17, 0, tzinfo=UTC)
    session = ReplaySession(
        session_id="phase-order",
        events=(
            _event(
                "fill",
                observed_at=timestamp,
                phase=ReplayPhase.FILL,
                stream="execution",
                sequence=1,
            ),
            _event(
                "market",
                observed_at=timestamp,
                phase=ReplayPhase.MARKET_DATA,
                stream="market",
                sequence=1,
            ),
            _event(
                "risk",
                observed_at=timestamp,
                phase=ReplayPhase.RISK,
                stream="risk",
                sequence=1,
            ),
        ),
    )

    assert [event.event_id for event in ReplayEngine(session).ordered_events] == [
        "market",
        "risk",
        "fill",
    ]


def test_future_events_are_not_visible_to_replay_clock() -> None:
    observed_at = datetime(2026, 8, 31, 17, 0, tzinfo=UTC)
    session = ReplaySession(
        session_id="anti-lookahead",
        events=(
            _event(
                "past",
                observed_at=observed_at,
                phase=ReplayPhase.MARKET_DATA,
                stream="market",
                sequence=1,
            ),
            _event(
                "future",
                observed_at=observed_at + timedelta(seconds=1),
                phase=ReplayPhase.MARKET_DATA,
                stream="market",
                sequence=2,
            ),
        ),
    )

    visible = ReplayEngine(session).events_visible_at(observed_at)

    assert [event.event_id for event in visible] == ["past"]


def test_same_session_has_stable_fingerprint_and_order() -> None:
    observed_at = datetime(2026, 8, 31, 17, 0, tzinfo=UTC)
    session = ReplaySession(
        session_id="deterministic",
        seed=42,
        events=(
            _event(
                "b",
                observed_at=observed_at,
                phase=ReplayPhase.ORDER,
                stream="orders",
                sequence=2,
            ),
            _event(
                "a",
                observed_at=observed_at,
                phase=ReplayPhase.ORDER,
                stream="orders",
                sequence=1,
            ),
        ),
    )

    first = ReplayEngine(session)
    second = ReplayEngine(session)

    assert first.ordered_events == second.ordered_events
    assert first.fingerprint() == second.fingerprint()


def test_duplicate_or_non_monotonic_stream_sequence_is_rejected() -> None:
    observed_at = datetime(2026, 8, 31, 17, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="non-monotonic sequence"):
        ReplaySession(
            session_id="bad-sequence",
            events=(
                _event(
                    "first",
                    observed_at=observed_at,
                    phase=ReplayPhase.MARKET_DATA,
                    stream="market",
                    sequence=1,
                ),
                _event(
                    "second",
                    observed_at=observed_at + timedelta(seconds=1),
                    phase=ReplayPhase.MARKET_DATA,
                    stream="market",
                    sequence=1,
                ),
            ),
        )
