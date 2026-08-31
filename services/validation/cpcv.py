from __future__ import annotations

from itertools import combinations

from .core import PurgedFold


def _contiguous_groups(sample_count: int, group_count: int) -> tuple[tuple[int, ...], ...]:
    base = sample_count // group_count
    remainder = sample_count % group_count
    groups: list[tuple[int, ...]] = []
    cursor = 0
    for group_index in range(group_count):
        size = base + (1 if group_index < remainder else 0)
        group = tuple(range(cursor, cursor + size))
        groups.append(group)
        cursor += size
    return tuple(groups)


def combinatorial_purged_cv_splits(
    sample_count: int,
    *,
    group_count: int,
    test_group_count: int,
    purge_size: int = 0,
    embargo_size: int = 0,
) -> tuple[PurgedFold, ...]:
    """Generate CPCV folds over contiguous groups with purge and embargo boundaries.

    Test groups are selected combinatorially. Training observations in the selected test groups,
    in the `purge_size` observations immediately before each selected group, and in the
    `embargo_size` observations immediately after each selected group are excluded.
    """

    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    if group_count < 2 or group_count > sample_count:
        raise ValueError("group_count must be between 2 and sample_count")
    if test_group_count <= 0 or test_group_count >= group_count:
        raise ValueError("test_group_count must be between 1 and group_count - 1")
    if purge_size < 0 or embargo_size < 0:
        raise ValueError("purge_size and embargo_size must be non-negative")

    groups = _contiguous_groups(sample_count, group_count)
    folds: list[PurgedFold] = []

    for selected in combinations(range(group_count), test_group_count):
        test_indices = tuple(index for group_index in selected for index in groups[group_index])
        excluded = set(test_indices)

        for group_index in selected:
            group = groups[group_index]
            start = group[0]
            end = group[-1]
            excluded.update(range(max(0, start - purge_size), start))
            excluded.update(range(end + 1, min(sample_count, end + 1 + embargo_size)))

        train_indices = tuple(index for index in range(sample_count) if index not in excluded)
        if not train_indices:
            raise ValueError("purge/embargo configuration removes the entire training fold")

        folds.append(PurgedFold(train_indices=train_indices, test_indices=test_indices))

    return tuple(folds)
