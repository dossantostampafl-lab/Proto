import math

import pytest

from services.features.toxicity import RollingVPIN


def test_vpin_is_one_for_fully_one_sided_bucket() -> None:
    estimator = RollingVPIN(bucket_volume=10.0, window_buckets=4)

    assert estimator.update(10.0) == pytest.approx(1.0)
    assert estimator.completed_bucket_count == 1


def test_vpin_is_zero_for_balanced_bucket() -> None:
    estimator = RollingVPIN(bucket_volume=10.0, window_buckets=4)

    assert estimator.update(5.0) is None
    assert estimator.update(-5.0) == pytest.approx(0.0)


def test_trade_is_split_across_bucket_boundaries_deterministically() -> None:
    estimator = RollingVPIN(bucket_volume=10.0, window_buckets=4)

    first = estimator.update(15.0)
    assert first == pytest.approx(1.0)
    assert estimator.completed_bucket_count == 1

    second = estimator.update(-5.0)
    assert second == pytest.approx(0.5)
    assert estimator.completed_bucket_count == 2


def test_window_discards_oldest_bucket() -> None:
    estimator = RollingVPIN(bucket_volume=10.0, window_buckets=2)

    estimator.update(10.0)
    estimator.update(-5.0)
    estimator.update(5.0)
    assert estimator.current == pytest.approx(0.5)

    estimator.update(-10.0)
    assert estimator.completed_bucket_count == 2
    assert estimator.current == pytest.approx(0.5)


def test_nonfinite_and_invalid_configuration_fail_closed() -> None:
    with pytest.raises(ValueError):
        RollingVPIN(bucket_volume=0.0)
    with pytest.raises(ValueError):
        RollingVPIN(bucket_volume=10.0, window_buckets=0)

    estimator = RollingVPIN(bucket_volume=10.0)
    with pytest.raises(ValueError):
        estimator.update(math.nan)
    with pytest.raises(ValueError):
        estimator.update(math.inf)
