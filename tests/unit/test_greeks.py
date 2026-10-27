from services.analytics.greeks import calculate_synthetic_greeks


def test_synthetic_greeks_are_finite_and_model_scoped() -> None:
    result = calculate_synthetic_greeks(
        market_probability=0.52,
        volatility=0.28,
        imbalance=0.05,
    )

    assert result.market_probability_delta > 0.0
    assert result.volatility_vega <= 0.0
    assert result.imbalance_kappa > 0.0
    assert result.time_theta == 0.0
    assert result.model_version == "baseline-logit-v0"
    assert result.feature_version == "microstructure-v0"


def test_synthetic_greeks_reject_invalid_bump() -> None:
    try:
        calculate_synthetic_greeks(
            market_probability=0.5,
            volatility=0.2,
            imbalance=0.0,
            bump_size=0.0,
        )
    except ValueError as error:
        assert "bump_size" in str(error)
    else:
        raise AssertionError("expected invalid bump_size to fail")


def test_synthetic_greeks_reject_inputs_outside_the_model_domain() -> None:
    import math

    import pytest

    for kwargs in (
        {"volatility": -1e-4},  # used to collapse the vega bump to zero width
        {"volatility": math.nan},
        {"market_probability": 1.5},
        {"market_probability": math.inf},
        {"imbalance": 2.0},
    ):
        values = {"market_probability": 0.5, "volatility": 0.2, "imbalance": 0.0, **kwargs}
        with pytest.raises(ValueError):
            calculate_synthetic_greeks(**values)
