import math

import pytest

from stream_stats import BackendUnavailableError, Window


def test_window_calculates_all_statistics_and_evicts_old_values():
    window = Window(
        3,
        ("count", "sum", "min", "max", "mean", "variance", "std"),
    )

    snapshots = list(window.map([2, 4, 9, 5]))

    assert snapshots[0].as_dict() == {
        "count": 1,
        "sum": 2.0,
        "min": 2.0,
        "max": 2.0,
        "mean": 2.0,
        "variance": 0.0,
        "std": 0.0,
    }
    assert snapshots[-1].count == 3
    assert snapshots[-1].sum == 18.0
    assert snapshots[-1].min == 4.0
    assert snapshots[-1].max == 9.0
    assert snapshots[-1].mean == 6.0
    assert snapshots[-1].variance == pytest.approx(14 / 3)
    assert snapshots[-1].std == pytest.approx(math.sqrt(14 / 3))


def test_duplicate_extrema_survive_one_eviction():
    window = Window(2, ("min", "max"))
    list(window.map([3, 3]))
    result = window.push(4)
    assert result.min == 3
    assert result.max == 4


def test_variance_is_stable_for_large_values():
    window = Window(3, ("variance",))
    result = list(window.map([1_000_000_000, 1_000_000_001, 1_000_000_002]))[-1]
    assert result.variance == pytest.approx(2 / 3)


def test_reset_clears_state():
    window = Window(2, ("count", "mean"))
    list(window.map([1, 2]))
    window.reset()
    assert window.push(10).as_dict() == {"count": 1, "mean": 10.0}


@pytest.mark.parametrize("size", [0, -1, 1.5, True])
def test_size_must_be_a_positive_integer(size):
    with pytest.raises(ValueError):
        Window(size)


@pytest.mark.parametrize("value", [True, None, object()])
def test_values_must_be_real_numbers(value):
    with pytest.raises(TypeError):
        Window(2).push(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_values_must_be_finite(value):
    with pytest.raises(ValueError):
        Window(2).push(value)


def test_unknown_statistic_is_rejected():
    with pytest.raises(ValueError, match="unsupported statistics: median"):
        Window(2, ("median",))


def test_known_unbuilt_backend_has_actionable_error():
    with pytest.raises(BackendUnavailableError, match="build backends/rust"):
        Window(2, backend="rust")
