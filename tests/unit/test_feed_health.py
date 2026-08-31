from datetime import UTC, datetime, timedelta

from services.market_data.health import FeedHealthState, FeedHealthTracker


def _now() -> datetime:
    return datetime(2026, 8, 31, 14, 30, tzinfo=UTC)


def test_feed_health_degrades_by_age_and_blocks_stale_risk() -> None:
    tracker = FeedHealthTracker()
    now = _now()

    healthy = tracker.evaluate(observed_at=now, now=now)
    degraded = tracker.evaluate(observed_at=now - timedelta(seconds=2), now=now)
    stale = tracker.evaluate(observed_at=now - timedelta(seconds=6), now=now)

    assert healthy.state is FeedHealthState.HEALTHY
    assert healthy.risk_multiplier == 1.0
    assert degraded.state is FeedHealthState.DEGRADED
    assert degraded.risk_multiplier == 0.5
    assert stale.state is FeedHealthState.STALE
    assert stale.allow_new_risk is False


def test_dark_feed_requires_clean_recovery_sequence() -> None:
    tracker = FeedHealthTracker(recovery_clean_updates=2)
    now = _now()

    dark = tracker.evaluate(observed_at=now - timedelta(seconds=20), now=now)
    first_clean = tracker.evaluate(observed_at=now, now=now)
    second_clean = tracker.evaluate(observed_at=now, now=now)

    assert dark.state is FeedHealthState.DARK
    assert dark.risk_multiplier == 0.0
    assert first_clean.state is FeedHealthState.RECOVERING
    assert first_clean.allow_new_risk is False
    assert second_clean.state is FeedHealthState.HEALTHY
    assert second_clean.allow_new_risk is True


def test_invalid_payload_forces_dark_state() -> None:
    tracker = FeedHealthTracker()
    now = _now()

    snapshot = tracker.evaluate(observed_at=now, now=now, data_valid=False)

    assert snapshot.state is FeedHealthState.DARK
    assert snapshot.allow_new_risk is False
