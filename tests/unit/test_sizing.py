from services.portfolio.sizing import SizingInput, SizingMethod, size_position


def base_input() -> SizingInput:
    return SizingInput(
        capital=100_000,
        max_fraction=0.02,
        volatility=0.30,
        target_volatility=0.15,
        net_edge=0.05,
        confidence=0.80,
        market_probability=0.50,
        model_probability=0.60,
        hard_notional_cap=1_500,
    )


def test_fixed_fractional_is_hard_capped() -> None:
    result = size_position(SizingMethod.FIXED_FRACTIONAL, base_input())
    assert result.notional == 1_500
    assert result.capped is True


def test_volatility_adjusted_reduces_size_when_vol_is_high() -> None:
    fixed = size_position(SizingMethod.FIXED_FRACTIONAL, base_input())
    adjusted = size_position(SizingMethod.VOLATILITY_ADJUSTED, base_input())
    assert adjusted.fraction < fixed.fraction


def test_capped_kelly_is_research_only_and_severely_limited() -> None:
    result = size_position(SizingMethod.CAPPED_KELLY_RESEARCH_ONLY, base_input())
    assert 0 <= result.fraction <= 0.02
    assert result.notional <= 1_500
