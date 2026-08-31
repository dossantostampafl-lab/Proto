from services.validation import ParameterPoint, parameter_stability, regime_robustness


def test_regime_robustness_exposes_weak_regime() -> None:
    returns = (0.02, 0.01, 0.015, -0.03, -0.02, -0.01, 0.01, 0.012, 0.008)
    regimes = (
        "bull",
        "bull",
        "bull",
        "bear",
        "bear",
        "bear",
        "sideways",
        "sideways",
        "sideways",
    )

    report = regime_robustness(returns, regimes)

    assert len(report.regimes) == 3
    assert report.profitable_regime_fraction == 2 / 3
    assert report.worst_regime_return < 0.0
    assert 0.0 <= report.robustness_score <= 1.0


def test_parameter_stability_rewards_plateau() -> None:
    stable = parameter_stability(
        (
            ParameterPoint(parameter=1.0, score=0.90),
            ParameterPoint(parameter=1.5, score=0.98),
            ParameterPoint(parameter=2.0, score=1.00),
            ParameterPoint(parameter=2.5, score=0.97),
            ParameterPoint(parameter=3.0, score=0.91),
        ),
        relative_tolerance=0.10,
    )

    fragile = parameter_stability(
        (
            ParameterPoint(parameter=1.0, score=0.20),
            ParameterPoint(parameter=1.5, score=0.25),
            ParameterPoint(parameter=2.0, score=1.00),
            ParameterPoint(parameter=2.5, score=0.22),
            ParameterPoint(parameter=3.0, score=0.18),
        ),
        relative_tolerance=0.10,
    )

    assert stable.stability_score > fragile.stability_score
    assert stable.local_neighbor_fraction == 1.0
    assert fragile.local_neighbor_fraction == 0.0


def test_parameter_stability_rejects_duplicate_parameters() -> None:
    try:
        parameter_stability(
            (
                ParameterPoint(parameter=1.0, score=0.1),
                ParameterPoint(parameter=1.0, score=0.2),
                ParameterPoint(parameter=2.0, score=0.3),
            )
        )
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("expected duplicate parameter rejection")
