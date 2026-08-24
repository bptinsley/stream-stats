import math
import random
from statistics import StatisticsError, mean, pvariance

import pytest

from stream_stats import WindowStatistics


def test_all_statistics_and_automatic_eviction():
    window = WindowStatistics(3)

    assert window.add(2) is None
    assert window.add(4) is None
    assert window.add(9) is None
    assert window.add(5) == 2.0

    assert window.count == 3
    assert window.sum == 18.0
    assert window.min == 4.0
    assert window.max == 9.0
    assert window.mean == 6.0
    assert window.variance == pytest.approx(14 / 3)
    assert window.std == pytest.approx(math.sqrt(14 / 3))
    assert window.standard_deviation == window.std
    window._validate()


def test_percentiles_use_linear_interpolation_and_midrank():
    window = WindowStatistics(5)
    for value in [1, 2, 2, 4, 9]:
        window.add(value)

    assert window.percentile(0) == 1
    assert window.percentile(25) == 2
    assert window.percentile(50) == 2
    assert window.percentile(75) == 4
    assert window.percentile(100) == 9
    assert window.percentile_of(0) == 0
    assert window.percentile_of(2) == 40
    assert window.percentile_of(3) == 60
    assert window.percentile_of(10) == 100


def test_removal_preserves_arrival_order_with_duplicates():
    window = WindowStatistics(5)
    for value in [3, 1, 3, 2, 3]:
        window.add(value)

    window.remove(3)
    assert window._values_in_order() == [1, 3, 2, 3]
    assert window.remove_oldest() == 1
    assert window._values_in_order() == [3, 2, 3]
    window._validate()


def test_clear_reuses_preallocated_storage():
    window = WindowStatistics(4)
    storage_ids = tuple(
        id(storage)
        for storage in (
            window._ring,
            window._tree.keys,
            window._tree.left,
            window._tree.subtree_counts,
        )
    )
    for value in [1, 2, 3, 4]:
        window.add(value)

    window.clear()

    assert window.count == 0
    assert window.sum == 0.0
    assert storage_ids == tuple(
        id(storage)
        for storage in (
            window._ring,
            window._tree.keys,
            window._tree.left,
            window._tree.subtree_counts,
        )
    )
    assert len(window._tree.free_indexes) == 4
    window._validate()


def test_invalid_add_is_transactional():
    window = WindowStatistics(2)
    window.add(1)
    window.add(2)

    with pytest.raises(ValueError):
        window.add(float("nan"))

    assert window._values_in_order() == [1, 2]
    window._validate()


@pytest.mark.parametrize("size", [0, -1, 1.5, True])
def test_window_size_validation(size):
    with pytest.raises(ValueError):
        WindowStatistics(size)


@pytest.mark.parametrize("value", [True, None, object()])
def test_value_type_validation(value):
    with pytest.raises(TypeError):
        WindowStatistics(2).add(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_finite_value_validation(value):
    with pytest.raises(ValueError):
        WindowStatistics(2).add(value)


def test_empty_window_behavior():
    window = WindowStatistics(2)
    assert window.count == 0
    assert window.sum == 0.0
    for get_value in (
        lambda: window.min,
        lambda: window.max,
        lambda: window.mean,
        lambda: window.variance,
        lambda: window.std,
        lambda: window.percentile(50),
        lambda: window.percentile_of(1),
        window.remove_oldest,
    ):
        with pytest.raises(StatisticsError):
            get_value()


def test_missing_arbitrary_value_does_not_mutate_state():
    window = WindowStatistics(3)
    for value in [1, 2, 3]:
        window.add(value)
    with pytest.raises(ValueError, match="not present"):
        window.remove(4)
    assert window._values_in_order() == [1, 2, 3]
    window._validate()


def test_random_mutations_match_a_list_oracle():
    rng = random.Random(20260824)
    window = WindowStatistics(17)
    expected: list[float] = []

    for _ in range(2_000):
        action = rng.random()
        if action < 0.7 or not expected:
            value = float(rng.randint(-100, 100))
            evicted = expected.pop(0) if len(expected) == 17 else None
            expected.append(value)
            assert window.add(value) == evicted
        elif action < 0.85:
            assert window.remove_oldest() == expected.pop(0)
        else:
            value = rng.choice(expected)
            expected.remove(value)
            window.remove(value)

        window._validate()
        assert window._values_in_order() == expected
        assert window.count == len(expected)
        assert window.sum == pytest.approx(math.fsum(expected), abs=1e-10)
        if expected:
            assert window.min == min(expected)
            assert window.max == max(expected)
            assert window.mean == pytest.approx(mean(expected), abs=1e-10)
            assert window.variance == pytest.approx(pvariance(expected), abs=1e-10)


def test_large_offset_variance_is_stable():
    window = WindowStatistics(4)
    values = [1_000_000_000.0 + offset for offset in (0, 1, 2, 3)]
    for value in values:
        window.add(value)
    assert window.variance == pytest.approx(pvariance(values), rel=1e-12)


def test_mean_avoids_overflow_for_opposite_extreme_values():
    window = WindowStatistics(2)
    window.add(1e308)
    window.add(-1e308)
    assert window.sum == 0.0
    assert window.mean == 0.0
    assert window.variance == math.inf


@pytest.mark.parametrize(
    "values",
    ([3, 2, 1], [1, 2, 3], [3, 1, 2], [1, 3, 2]),
)
def test_rotation_shapes_preserve_invariants(values):
    window = WindowStatistics(3)
    for value in values:
        window.add(value)
    window._validate()
    assert [window.percentile(p) for p in (0, 50, 100)] == [1, 2, 3]
