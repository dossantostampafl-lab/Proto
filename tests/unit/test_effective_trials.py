from __future__ import annotations

import pytest

from services.validation import effective_number_of_trials

BASE = (0.01, -0.005, 0.012, -0.002, 0.009, -0.004)


def test_single_trial_has_one_effective_trial() -> None:
    report = effective_number_of_trials((BASE,))

    assert report.declared_trials == 1
    assert report.implied_independent_trials == 1.0
    assert report.effective_independent_trials == 1
    assert report.average_pairwise_correlation == 0.0
    assert report.pair_count == 0


def test_identical_trials_collapse_to_one_independent_trial() -> None:
    report = effective_number_of_trials((BASE, BASE, BASE))

    assert report.declared_trials == 3
    assert report.average_pairwise_correlation == pytest.approx(1.0)
    assert report.implied_independent_trials == pytest.approx(1.0)
    assert report.effective_independent_trials == 1
    assert report.pair_count == 3


def test_partially_correlated_trials_reduce_search_burden() -> None:
    trial_two = (0.008, -0.004, 0.011, -0.001, 0.007, -0.003)
    trial_three = (0.002, -0.008, 0.006, 0.004, 0.001, -0.003)

    report = effective_number_of_trials((BASE, trial_two, trial_three))

    assert 1.0 < report.implied_independent_trials < 3.0
    assert 1 < report.effective_independent_trials < report.declared_trials


def test_negative_average_correlation_cannot_exceed_declared_trials() -> None:
    inverse = tuple(-value for value in BASE)

    report = effective_number_of_trials((BASE, inverse))

    assert report.average_pairwise_correlation == pytest.approx(-1.0)
    assert report.implied_independent_trials == 2.0
    assert report.effective_independent_trials == 2


def test_fractional_implied_trial_count_rounds_up_conservatively() -> None:
    trial_two = (0.009, -0.003, 0.010, -0.004, 0.008, -0.002)

    report = effective_number_of_trials((BASE, trial_two))

    assert 1.0 < report.implied_independent_trials <= 2.0
    assert report.effective_independent_trials == 2


def test_trial_accounting_rejects_malformed_or_unusable_evidence() -> None:
    with pytest.raises(ValueError, match="at least one trial"):
        effective_number_of_trials(())

    with pytest.raises(ValueError, match="at least three"):
        effective_number_of_trials(((0.1, 0.2),))

    with pytest.raises(ValueError, match="equal length"):
        effective_number_of_trials((BASE, BASE[:-1]))

    with pytest.raises(ValueError, match="finite"):
        effective_number_of_trials((BASE, (*BASE[:-1], float("nan"))))

    constant = (0.01,) * len(BASE)
    with pytest.raises(ValueError, match="non-zero variance"):
        effective_number_of_trials((BASE, constant))


def test_trial_accounting_is_deterministic() -> None:
    trial_two = (0.006, -0.002, 0.009, -0.003, 0.005, -0.001)
    evidence = (BASE, trial_two)

    assert effective_number_of_trials(evidence) == effective_number_of_trials(evidence)
