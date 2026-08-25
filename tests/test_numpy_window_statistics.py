import pytest

np = pytest.importorskip("numpy")

from stream_stats import NumpyWindowStatistics, WindowStatistics


def test_numpy_baseline_matches_optimized_implementation():
    baseline = NumpyWindowStatistics(5)
    optimized = WindowStatistics(5)

    for value in [3, 1, 3, 8, 2, 5, -1]:
        assert baseline.add(value) == optimized.add(value)
        assert baseline.count == optimized.count
        assert baseline.sum == pytest.approx(optimized.sum)
        assert baseline.min == optimized.min
        assert baseline.max == optimized.max
        assert baseline.mean == pytest.approx(optimized.mean)
        assert baseline.variance == pytest.approx(optimized.variance)
        assert baseline.std == pytest.approx(optimized.std)
        for percentile in (0, 10, 25, 50, 90, 100):
            assert baseline.percentile(percentile) == pytest.approx(
                optimized.percentile(percentile)
            )


def test_numpy_baseline_removals_and_midrank():
    baseline = NumpyWindowStatistics(5)
    for value in [1, 2, 2, 4, 9]:
        baseline.add(value)
    assert baseline.percentile_of(2) == 40
    baseline.remove(2)
    assert baseline._values_in_order() == [1, 2, 4, 9]
    assert baseline.remove_oldest() == 1
    assert baseline._values_in_order() == [2, 4, 9]


def test_numpy_clear_and_alias():
    baseline = NumpyWindowStatistics(2)
    baseline.add(1)
    baseline.add(3)
    assert baseline.standard_deviation == baseline.std
    baseline.clear()
    assert baseline.count == 0
    assert baseline.sum == 0.0
