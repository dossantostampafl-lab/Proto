import pytest

from services.validation.perturbation import apply_signal_returns, delay_signal, timestamp_shuffle


def test_delay_signal_moves_information_forward_without_lookahead() -> None:
    signal = [1.0, 2.0, 3.0, 4.0]

    assert delay_signal(signal, 2) == [0.0, 0.0, 1.0, 2.0]
    assert delay_signal(signal, 0) == signal


def test_delay_beyond_series_length_returns_fill_only() -> None:
    assert delay_signal([1.0, 2.0], 5, fill_value=-1.0) == [-1.0, -1.0]


def test_negative_delay_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        delay_signal([1.0], -1)


def test_timestamp_shuffle_is_seeded_and_distribution_preserving() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]

    first = timestamp_shuffle(values, seed=7)
    second = timestamp_shuffle(values, seed=7)

    assert first == second
    assert sorted(first) == values
    assert first != values


def test_apply_signal_returns_requires_alignment() -> None:
    assert apply_signal_returns([1.0, -1.0], [0.02, 0.01]) == [0.02, -0.01]

    with pytest.raises(ValueError, match="equal length"):
        apply_signal_returns([1.0], [0.01, 0.02])
