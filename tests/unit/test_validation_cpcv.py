from math import comb

import pytest

from services.validation.cpcv import combinatorial_purged_cv_splits


def test_cpcv_generates_expected_number_of_combinations() -> None:
    folds = combinatorial_purged_cv_splits(
        20,
        group_count=5,
        test_group_count=2,
    )

    assert len(folds) == comb(5, 2)
    assert all(fold.train_indices for fold in folds)
    assert all(fold.test_indices for fold in folds)


def test_cpcv_purge_and_embargo_remove_boundary_observations() -> None:
    folds = combinatorial_purged_cv_splits(
        12,
        group_count=3,
        test_group_count=1,
        purge_size=1,
        embargo_size=1,
    )

    middle = next(fold for fold in folds if fold.test_indices == (4, 5, 6, 7))

    assert 3 not in middle.train_indices
    assert 8 not in middle.train_indices
    assert set(middle.test_indices).isdisjoint(middle.train_indices)


def test_cpcv_preserves_all_test_observations_in_selected_groups() -> None:
    folds = combinatorial_purged_cv_splits(
        10,
        group_count=4,
        test_group_count=2,
    )

    assert any(fold.test_indices == (0, 1, 2, 3, 4, 5) for fold in folds)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sample_count": 0, "group_count": 2, "test_group_count": 1},
        {"sample_count": 5, "group_count": 1, "test_group_count": 1},
        {"sample_count": 5, "group_count": 6, "test_group_count": 1},
        {"sample_count": 5, "group_count": 3, "test_group_count": 0},
        {"sample_count": 5, "group_count": 3, "test_group_count": 3},
        {"sample_count": 5, "group_count": 3, "test_group_count": 1, "purge_size": -1},
    ],
)
def test_cpcv_rejects_invalid_geometry(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        combinatorial_purged_cv_splits(**kwargs)
