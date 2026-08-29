import pytest

from services.market_data.core import DataQualityMonitor


@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan")])
def test_stale_threshold_must_be_positive_and_finite(value: float) -> None:
    with pytest.raises(ValueError, match="stale_after_seconds"):
        DataQualityMonitor(stale_after_seconds=value)


@pytest.mark.parametrize("value", [-1.0, float("inf"), float("nan")])
def test_price_jump_threshold_must_be_non_negative_and_finite(value: float) -> None:
    with pytest.raises(ValueError, match="max_relative_price_jump"):
        DataQualityMonitor(max_relative_price_jump=value)


@pytest.mark.parametrize("value", [-1.0, float("inf"), float("nan")])
def test_future_skew_threshold_must_be_non_negative_and_finite(value: float) -> None:
    with pytest.raises(ValueError, match="max_future_skew_seconds"):
        DataQualityMonitor(max_future_skew_seconds=value)


def test_zero_is_valid_for_non_negative_quality_thresholds() -> None:
    monitor = DataQualityMonitor(
        max_relative_price_jump=0.0,
        max_future_skew_seconds=0.0,
    )

    assert monitor.max_relative_price_jump == 0.0
    assert monitor.max_future_skew_seconds == 0.0
