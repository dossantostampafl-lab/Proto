import pytest

from apps.api.app.live_sequence import LiveSequenceState


def test_live_sequence_state_tracks_acceptance_and_rejections() -> None:
    state = LiveSequenceState()

    assert state.previous("BTC") is None
    state.accept("BTC", 10)
    state.record_rejection("BTC", "duplicate")
    state.record_rejection("BTC", "regression")

    assert state.previous("BTC") == 10
    assert state.last_sequence_snapshot() == {"BTC": 10}
    total, by_symbol = state.rejection_snapshot(("BTC", "ETH"))
    assert total == 2
    assert by_symbol == {
        "BTC": {"duplicate": 1, "regression": 1, "total": 2},
        "ETH": {"duplicate": 0, "regression": 0, "total": 0},
    }


def test_live_sequence_state_reset_is_connection_scoped() -> None:
    state = LiveSequenceState()
    state.accept("BTC", 7)
    state.record_rejection("BTC", "duplicate")

    state.reset()

    assert state.last_sequence_snapshot() == {}
    assert state.rejection_snapshot(("BTC",)) == (
        0,
        {"BTC": {"duplicate": 0, "regression": 0, "total": 0}},
    )


def test_live_sequence_state_rejects_unknown_rejection_reason() -> None:
    state = LiveSequenceState()

    with pytest.raises(ValueError, match="unsupported live sequence rejection reason"):
        state.record_rejection("BTC", "other")
