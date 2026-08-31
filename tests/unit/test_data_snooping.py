from __future__ import annotations

import pytest

from services.validation.data_snooping import superior_predictive_ability


SAMPLE_COUNT = 64
BENCHMARK = (0.0,) * SAMPLE_COUNT
STRONG = tuple(
    0.015 + (0.002 if index % 4 == 0 else -0.001 if index % 4 == 1 else 0.0)
    for index in range(SAMPLE_COUNT)
)
WEAK = tuple(0.0005 if index % 2 == 0 else -0.0004 for index in range(SAMPLE_COUNT))
BAD = tuple(
    -0.005 + (0.001 if index % 3 == 0 else 0.0)
    for index in range(SAMPLE_COUNT)
)
NOISE_ONE = tuple(0.001 if index % 2 == 0 else -0.001 for index in range(SAMPLE_COUNT))
NOISE_TWO = tuple(
    0.0008 if index % 3 == 0 else -0.0004
    for index in range(SAMPLE_COUNT)
)
NOISE_THREE = tuple(
    -0.0002 + (0.0007 if index % 5 == 0 else 0.0)
    for index in range(SAMPLE_COUNT)
)


def test_spa_detects_strong_family_level_superiority() -> None:
    report = superior_predictive_ability(
        (STRONG, WEAK, BAD),
        BENCHMARK,
        simulations=500,
        block_size=4,
        seed=11,
    )

    assert report.best_strategy_index == 0
    assert report.best_mean_excess_return > 0.01
    assert report.reality_check_p_value < 0.05
    assert report.spa_consistent_p_value < 0.05
    assert report.spa_lower_p_value <= report.spa_consistent_p_value
    assert report.spa_consistent_p_value <= report.spa_upper_p_value
    assert report.reality_check_p_value == report.spa_upper_p_value


def test_spa_does_not_promote_noise_family() -> None:
    report = superior_predictive_ability(
        (NOISE_ONE, NOISE_TWO, NOISE_THREE),
        BENCHMARK,
        simulations=500,
        block_size=4,
        seed=11,
    )

    assert report.reality_check_p_value > 0.10
    assert report.spa_consistent_p_value > 0.10


def test_spa_is_deterministic_for_fixed_seed() -> None:
    first = superior_predictive_ability(
        (STRONG, WEAK, BAD),
        BENCHMARK,
        simulations=200,
        block_size=4,
        seed=17,
    )
    second = superior_predictive_ability(
        (STRONG, WEAK, BAD),
        BENCHMARK,
        simulations=200,
        block_size=4,
        seed=17,
    )

    assert first == second


def test_spa_preserves_family_metadata_and_bounds() -> None:
    report = superior_predictive_ability(
        (STRONG, WEAK, BAD),
        BENCHMARK,
        simulations=200,
        block_size=8,
        seed=5,
    )

    assert report.strategy_count == 3
    assert report.sample_count == SAMPLE_COUNT
    assert 1 <= report.consistent_strategy_count <= report.strategy_count
    assert report.bootstrap == "stationary"
    assert report.bootstrap_simulations == 200
    assert report.block_size == 8
    assert report.seed == 5
    for p_value in (
        report.reality_check_p_value,
        report.spa_consistent_p_value,
        report.spa_lower_p_value,
        report.spa_upper_p_value,
    ):
        assert 0.0 < p_value <= 1.0


def test_spa_rejects_malformed_evidence() -> None:
    with pytest.raises(ValueError, match="at least two strategies"):
        superior_predictive_ability((STRONG,), BENCHMARK)

    with pytest.raises(ValueError, match="equal length"):
        superior_predictive_ability((STRONG, WEAK[:-1]), BENCHMARK)

    with pytest.raises(ValueError, match="finite"):
        malformed = (*WEAK[:-1], float("nan"))
        superior_predictive_ability((STRONG, malformed), BENCHMARK)

    with pytest.raises(ValueError, match="simulations must be at least 100"):
        superior_predictive_ability(
            (STRONG, WEAK),
            BENCHMARK,
            simulations=99,
        )

    with pytest.raises(ValueError, match="block_size"):
        superior_predictive_ability(
            (STRONG, WEAK),
            BENCHMARK,
            block_size=SAMPLE_COUNT + 1,
        )
