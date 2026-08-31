from services.validation import (
    deflated_sharpe_ratio,
    expected_max_sharpe_under_null,
    probability_of_backtest_overfitting,
)


def test_expected_max_sharpe_increases_with_trials() -> None:
    assert expected_max_sharpe_under_null(1) == 0.0
    assert expected_max_sharpe_under_null(100) > expected_max_sharpe_under_null(10)


def test_deflated_sharpe_penalizes_multiple_trials() -> None:
    returns = (
        0.020,
        0.015,
        -0.004,
        0.018,
        0.011,
        -0.003,
        0.016,
        0.009,
        0.014,
        -0.002,
        0.013,
        0.010,
    )

    one_trial = deflated_sharpe_ratio(returns, trials=1)
    many_trials = deflated_sharpe_ratio(returns, trials=100)

    assert 0.0 <= many_trials <= one_trial <= 1.0


def test_pbo_is_low_for_strategy_that_generalizes_consistently() -> None:
    robust = tuple(0.010 + (index % 3) * 0.001 for index in range(16))
    weak = tuple(-0.005 + (index % 2) * 0.001 for index in range(16))
    noisy = tuple(0.003 if index % 2 == 0 else -0.004 for index in range(16))

    pbo = probability_of_backtest_overfitting((robust, weak, noisy), segments=4)

    assert 0.0 <= pbo <= 0.5


def test_pbo_detects_unstable_winner_selection() -> None:
    first = (
        0.04,
        0.04,
        0.04,
        0.04,
        -0.03,
        -0.03,
        -0.03,
        -0.03,
    )
    second = (
        -0.03,
        -0.03,
        -0.03,
        -0.03,
        0.04,
        0.04,
        0.04,
        0.04,
    )

    pbo = probability_of_backtest_overfitting((first, second), segments=4)

    assert pbo >= 0.5
