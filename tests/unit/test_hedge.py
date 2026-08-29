from services.hedge.core import HedgeRequest, build_simulated_hedge


def test_simulated_hedge_removes_unwanted_directional_exposure() -> None:
    plan = build_simulated_hedge(
        HedgeRequest(
            desired_alpha_exposure=10_000,
            current_directional_exposure=25_000,
            hedge_ratio=1.0,
            max_hedge_notional=20_000,
        )
    )
    assert plan.simulated_only is True
    assert plan.target_hedge_notional == -15_000
    assert plan.residual_directional_exposure == 0


def test_hedge_respects_hard_notional_cap() -> None:
    plan = build_simulated_hedge(
        HedgeRequest(
            desired_alpha_exposure=0,
            current_directional_exposure=50_000,
            hedge_ratio=1.0,
            max_hedge_notional=5_000,
        )
    )
    assert plan.target_hedge_notional == -5_000
    assert plan.residual_directional_exposure == 45_000
